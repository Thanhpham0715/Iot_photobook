#!/usr/bin/env python3
"""
Gesture Photobooth - DEMO chay tren PC (webcam laptop)
=====================================================
Muc dich: xem truoc toan bo luong AI + Photobooth truoc khi tach thanh
Raspberry Pi (chi stream camera) <-> PC Server (AI + Engine).

Chuc nang:
  - MediaPipe Gesture Recognizer: Open_Palm / Thumb_Up / Victory / Pointing_Up / Closed_Fist
  - Pinch-zoom (ngon giua/ap ut/ut co lai, ngon cai va tro tao pinch)
               anh xa tuyet doi (log) + One Euro Filter + hysteresis
  - YOLO11n-pose + ByteTrack: nhan nguoi, giu ID, chon 1 controller (tuy chon)
  - State machine: IDLE -> READY -> COUNTDOWN -> CAPTURING -> RESULT
  - Debounce: confidence, giu N frame, cooldown, release-to-rearm
  - Filter OpenCV don gian, luu anh vao thu muc photos/

Cu chi:
  Open_Palm   (IDLE)      : bat dau, khoa nguoi dieu khien
  Thumb_Up    (READY)     : chup (dem 3-2-1)
  Victory     (READY)     : filter tiep theo
  Pointing_Up (READY)     : filter truoc do
  Closed_Fist (COUNTDOWN) : huy dem nguoc
  Pinch-zoom  (READY)     : cuon ngon giua/ap ut/ut, mo/khep ngon cai - ngon tro
                            de zoom 1.0x -> 2.5x

Phim: q = thoat, r = reset controller
"""
import argparse
import math
import os
import sys
import time
import urllib.request
from datetime import datetime

import cv2
import numpy as np

# --------------------------------------------------------------------------
# Cau hinh
# --------------------------------------------------------------------------
MODEL_URLS = [
    "https://storage.googleapis.com/mediapipe-tasks/gesture_recognizer/gesture_recognizer.task",
    "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task",
]
MODEL_PATH = os.path.join("models", "gesture_recognizer.task")
PHOTO_DIR = "photos"

HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15),
    (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17),
]

ZOOM_ENTER_FRAMES = 3   # so frame giu tu the zoom truoc khi bat dau
ZOOM_GRACE_FRAMES = 3   # so frame cho phep rot tu the ma khong tha zoom

IDLE, READY, COUNTDOWN, CAPTURING, RESULT = "IDLE", "READY", "COUNTDOWN", "CAPTURING", "RESULT"


# --------------------------------------------------------------------------
# Tien ich
# --------------------------------------------------------------------------
def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def put(img, text, org, scale=0.6, color=(255, 255, 255), thick=1):
    """Ve chu co vien den (cv2.putText khong ho tro tieng Viet co dau)."""
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def ensure_model():
    if os.path.exists(MODEL_PATH):
        return MODEL_PATH
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    for url in MODEL_URLS:
        try:
            print(f"[INFO] Dang tai model: {url}")
            urllib.request.urlretrieve(url, MODEL_PATH)
            print(f"[INFO] Da luu: {MODEL_PATH}")
            return MODEL_PATH
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] Tai that bai ({e}), thu URL tiep theo...")
    sys.exit("Khong tai duoc model. Hay tai thu cong gesture_recognizer.task vao thu muc models/")


# --------------------------------------------------------------------------
# Filter + Zoom (Photobooth Engine)
# --------------------------------------------------------------------------
def f_none(img):
    return img


def f_gray(img):
    return cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)


def f_sepia(img):
    k = np.array([[0.272, 0.534, 0.131],
                  [0.349, 0.686, 0.168],
                  [0.393, 0.769, 0.189]], dtype=np.float32)  # thu tu BGR
    return cv2.transform(img, k)


def f_negative(img):
    return 255 - img


def f_cartoon(img):
    gray = cv2.medianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 7)
    edges = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 9, 7)
    color = cv2.bilateralFilter(img, 9, 75, 75)
    return cv2.bitwise_and(color, cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR))


def f_warm(img):
    out = img.astype(np.int16)
    out[:, :, 2] += 35   # R
    out[:, :, 0] -= 20   # B
    return np.clip(out, 0, 255).astype(np.uint8)


def f_pixel(img):
    h, w = img.shape[:2]
    small = cv2.resize(img, (max(w // 24, 1), max(h // 24, 1)), interpolation=cv2.INTER_LINEAR)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)


FILTERS = [("Original", f_none), ("Gray", f_gray), ("Sepia", f_sepia), ("Warm", f_warm),
           ("Negative", f_negative), ("Cartoon", f_cartoon), ("Pixel", f_pixel)]


def apply_zoom(img, z):
    if z <= 1.001:
        return img
    h, w = img.shape[:2]
    nw, nh = int(w / z), int(h / z)
    x0, y0 = (w - nw) // 2, (h - nh) // 2
    return cv2.resize(img[y0:y0 + nh, x0:x0 + nw], (w, h), interpolation=cv2.INTER_LINEAR)


# --------------------------------------------------------------------------
# Gesture logic
# --------------------------------------------------------------------------
class GestureDebouncer:
    """Confidence + giu N frame + cooldown + release-to-rearm.
    Giu 1 cu chi lien tuc chi phat ra DUNG 1 su kien."""

    def __init__(self, hold_frames=6, min_score=0.6, cooldown=0.8, lost_frames=5):
        self.hold_frames, self.min_score = hold_frames, min_score
        self.cooldown, self.lost_frames = cooldown, lost_frames
        self.cand, self.count, self.missing = None, 0, 0
        self.armed, self.last_fire = True, 0.0

    def update(self, label, score, now):
        valid = label not in (None, "None") and score >= self.min_score
        if not valid:
            self.missing += 1
            if self.missing >= self.lost_frames:   # tha tay ra -> cho phep ban lai
                self.cand, self.count, self.armed = None, 0, True
            return None
        self.missing = 0
        if label != self.cand:
            self.cand, self.count, self.armed = label, 1, True
            return None
        self.count += 1
        if self.armed and self.count >= self.hold_frames and now - self.last_fire >= self.cooldown:
            self.armed, self.last_fire = False, now
            return label
        return None

    def reset(self):
        self.cand, self.count, self.missing, self.armed = None, 0, 0, True


def pinch_ratio(pts, world=None):
    """Khoang cach ngon cai(4) - ngon tro(8) / co ban tay (0 -> 9).
    Uu tien toa do 3D 'world' (met) cua MediaPipe: khong bi sai lech khi
    xoay/nghieng tay hoac tay xa-gan camera. Fallback ve 2D."""
    if world is not None:
        d = lambda i, j: math.dist(world[i], world[j])
        return d(4, 8) / max(d(0, 9), 1e-6)
    return dist(pts[4], pts[8]) / max(dist(pts[0], pts[9]), 1e-6)


def zoom_pose_info(pts, engaged=False):
    """Tu the zoom = 3 ngon cuoi co lai + ngon cai & ngon tro DUOI RA (mo hoac cham nhau).
    Bat buoc ngon tro phai duoi ra de khong nham voi Thumb_Up / Closed_Fist.
    engaged=True -> nguong long hon (hysteresis) de khong bi rot tu the khi dang zoom."""
    wrist = pts[0]
    size = max(dist(pts[0], pts[9]), 1e-6)
    folded = all(dist(pts[tip], wrist) < dist(pts[pip], wrist)
                 for tip, pip in ((12, 10), (16, 14), (20, 18)))
    thumb_out = dist(pts[4], pts[9]) / size > (0.50 if engaged else 0.65)
    index_out = dist(pts[8], wrist) > (0.95 if engaged else 1.05) * dist(pts[5], wrist)
    return folded and thumb_out and index_out, folded, thumb_out, index_out


class OneEuro:
    """One Euro Filter: dung yen -> muot het rung, chuyen dong nhanh -> gan nhu khong tre."""

    def __init__(self, min_cutoff=1.0, beta=5.0, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x, self.dx = None, 0.0

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, dt):
        if self.x is None:
            self.x, self.dx = x, 0.0
            return x
        dt = max(dt, 1e-3)
        self.dx += self._alpha(self.d_cutoff, dt) * ((x - self.x) / dt - self.dx)
        cutoff = self.min_cutoff + self.beta * abs(self.dx)
        self.x += self._alpha(cutoff, dt) * (x - self.x)
        return self.x

    def reset(self):
        self.x, self.dx = None, 0.0


class ZoomController:
    """Anh xa TUYET DOI: do mo ngon cai - ngon tro  ->  muc zoom.
      khep sat (pinch)  = zmin (1.0x, thu nho)
      mo rong het co    = zmax (2.5x, phong to)
    - Zoom theo thang log => cung do dich ngon tay cho cam giac zoom deu.
    - OneEuro loc rung, gioi han toc do (max_speed) de vao tu the khong bi giat.
    - Khoang ratio [lo, hi] tu mo rong theo tay nguoi dung (calibration tu dong)."""

    def __init__(self, zmin=1.0, zmax=2.5, r_lo=0.18, r_hi=1.05,
                 max_speed=5.0, edge=0.04):
        self.zmin, self.zmax = zmin, zmax
        self.lo, self.hi = r_lo, r_hi
        self.max_speed, self.edge = max_speed, edge
        self.filter = OneEuro()
        self.zoom = self.target = zmin

    def update(self, ratio, dt):
        dt = min(max(dt, 1e-3), 0.1)
        f = self.filter(ratio, dt)
        self.lo = max(0.08, min(self.lo, f))      # chi mo rong, khong thu hep
        self.hi = min(1.60, max(self.hi, f))
        t = (f - self.lo) / max(self.hi - self.lo, 1e-6)
        t = min(max((t - self.edge) / (1.0 - 2.0 * self.edge), 0.0), 1.0)  # cham dau/cuoi la dat min/max
        self.target = self.zmin * (self.zmax / self.zmin) ** t
        cur, tgt = math.log(self.zoom), math.log(self.target)
        step = self.max_speed * dt
        cur += max(-step, min(step, tgt - cur))
        self.zoom = math.exp(cur)

    def release(self):
        self.filter.reset()


# --------------------------------------------------------------------------
# Person detection + controller selection
# --------------------------------------------------------------------------
class PersonDetector:
    def __init__(self, model_name, imgsz=640, device=None):
        from ultralytics import YOLO  # import tre de demo chay duoc ca khi khong cai ultralytics
        self.model = YOLO(model_name)
        self.imgsz, self.device = imgsz, device

    def detect(self, frame):
        kw = dict(persist=True, classes=[0], imgsz=self.imgsz, conf=0.4, verbose=False,
                  tracker="bytetrack.yaml")
        if self.device:
            kw["device"] = self.device
        res = self.model.track(frame, **kw)[0]
        persons = []
        if res.boxes is None or len(res.boxes) == 0:
            return persons
        xyxy = res.boxes.xyxy.cpu().numpy()
        ids = res.boxes.id.int().cpu().tolist() if res.boxes.id is not None else [None] * len(xyxy)
        kxy = res.keypoints.xy.cpu().numpy() if res.keypoints is not None else None
        kc = (res.keypoints.conf.cpu().numpy()
              if res.keypoints is not None and res.keypoints.conf is not None else None)
        for i, (box, pid) in enumerate(zip(xyxy, ids)):
            wrists = []
            if kxy is not None and len(kxy) > i:
                for k in (9, 10):  # co tay trai / phai (COCO)
                    x, y = kxy[i][k]
                    c = float(kc[i][k]) if kc is not None else 1.0
                    wrists.append((float(x), float(y), c))
            persons.append(dict(id=int(pid) if pid is not None else None,
                                box=tuple(float(v) for v in box), wrists=wrists))
        return persons


def assign_owner(wrist_xy, persons):
    """Ban tay thuoc nguoi nao: co tay nam trong bbox (mo rong) va gan keypoint co tay nhat."""
    best, best_d = None, 1e12
    for p in persons:
        x1, y1, x2, y2 = p["box"]
        m = 0.15 * (x2 - x1)
        if not (x1 - m <= wrist_xy[0] <= x2 + m and y1 - m <= wrist_xy[1] <= y2 + m):
            continue
        ds = [dist(wrist_xy, (x, y)) for x, y, c in p["wrists"] if c > 0.3]
        d = min(ds) if ds else dist(wrist_xy, ((x1 + x2) / 2, (y1 + y2) / 2)) * 2
        if d < best_d:
            best, best_d = p["id"], d
    return best


class ControllerManager:
    def __init__(self, release_after=2.0):
        self.id, self.last_seen, self.release_after = None, 0.0, release_after

    def update(self, present_ids, now):
        if self.id is None:
            return
        if self.id in present_ids:
            self.last_seen = now
        elif now - self.last_seen > self.release_after:
            self.id = None

    def lock(self, pid, now):
        self.id, self.last_seen = pid, now


# --------------------------------------------------------------------------
# Photobooth state machine
# --------------------------------------------------------------------------
class Photobooth:
    def __init__(self, countdown_s=3, result_s=4):
        self.state, self.filter_idx = IDLE, 0
        self.countdown_s, self.result_s = countdown_s, result_s
        self.countdown_end, self.result_until = 0.0, 0.0
        self.result_img, self.result_path = None, ""

    def to_idle(self):
        self.state = IDLE

    def on_gesture(self, g, now):
        """AI chi bao cu chi; Booth moi la noi xac thuc va doi trang thai."""
        if self.state == IDLE and g == "Open_Palm":
            self.state = READY
        elif self.state == READY:
            if g == "Thumb_Up":
                self.state, self.countdown_end = COUNTDOWN, now + self.countdown_s
            elif g == "Victory":
                self.filter_idx = (self.filter_idx + 1) % len(FILTERS)
            elif g == "Pointing_Up":
                self.filter_idx = (self.filter_idx - 1) % len(FILTERS)
        elif self.state == COUNTDOWN and g == "Closed_Fist":
            self.state = READY  # chi huy duoc khi dang COUNTDOWN
        elif self.state == RESULT:
            if g == "Thumb_Up":
                self.state, self.countdown_end = COUNTDOWN, now + self.countdown_s
            elif g == "Closed_Fist":
                self.state = READY

    def tick(self, now, clean_frame):
        if self.state == COUNTDOWN and now >= self.countdown_end:
            self.state = CAPTURING
            os.makedirs(PHOTO_DIR, exist_ok=True)
            self.result_path = os.path.join(
                PHOTO_DIR, f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg")
            if not cv2.imwrite(self.result_path, clean_frame):
                raise OSError(f"Could not save captured photo to {self.result_path}")
            self.result_img = clean_frame.copy()
            self.result_until = now + self.result_s
            self.state = RESULT
            print(f"[CAPTURE] Da luu {self.result_path}")
        elif self.state == RESULT and now >= self.result_until:
            self.state = READY

    def render(self, view, zoom, now, zoom_active=False, zoom_target=1.0):
        out = (self.result_img if self.state == RESULT and self.result_img is not None else view).copy()
        h, w = out.shape[:2]
        put(out, f"State: {self.state}", (12, 28), 0.7, (0, 255, 255), 2)
        put(out, f"Filter: {FILTERS[self.filter_idx][0]}   Zoom: {zoom:.2f}x", (12, 58), 0.6)
        if self.state == IDLE:
            put(out, "Show OPEN PALM to start", (w // 2 - 170, h // 2), 0.9, (0, 255, 0), 2)
            put(out, "Hold one open palm to select the controller", (12, h - 20), 0.55)
        elif self.state == COUNTDOWN:
            n = max(1, math.ceil(self.countdown_end - now))
            put(out, str(n), (w // 2 - 40, h // 2 + 40), 4.0, (0, 0, 255), 8)
            put(out, "FIST = cancel", (w // 2 - 80, h - 30), 0.8, (255, 255, 255), 2)
        elif self.state == READY:
            put(out, "Thumb Up: capture | Victory: next filter | Pointing Up: previous filter",
                (12, h - 42), 0.5)
            put(out, "Zoom: curl middle/ring/pinky; open thumb-index = zoom in, pinch = zoom out",
                (12, h - 20), 0.5)
            bx, by0, by1 = w - 40, 90, h - 90          # thanh zoom ben phai
            cv2.rectangle(out, (bx, by0), (bx + 14, by1), (0, 0, 0), -1)
            cv2.rectangle(out, (bx, by0), (bx + 14, by1), (255, 255, 255), 1)

            def zy(z):
                f = math.log(max(z, 1.0)) / math.log(2.5)
                return int(by1 - min(max(f, 0.0), 1.0) * (by1 - by0))
            cv2.rectangle(out, (bx + 1, zy(zoom)), (bx + 13, by1 - 1),
                          (0, 255, 0) if zoom_active else (160, 160, 160), -1)
            if zoom_active:
                cv2.line(out, (bx - 8, zy(zoom_target)), (bx + 22, zy(zoom_target)), (0, 200, 255), 2)
        elif self.state == RESULT:
            put(out, f"Saved: {self.result_path}", (12, h - 20), 0.6, (0, 255, 0), 2)
            put(out, "Thumb Up: retake | Closed Fist: back to READY", (12, h - 44), 0.5)
        return out


# --------------------------------------------------------------------------
# Debug drawing
# --------------------------------------------------------------------------
def draw_debug(frame, persons, hands, ctrl_id, chosen, info_lines):
    dbg = frame.copy()
    for p in persons:
        x1, y1, x2, y2 = map(int, p["box"])
        is_ctrl = ctrl_id is not None and ctrl_id == p["id"]
        cv2.rectangle(dbg, (x1, y1), (x2, y2), (0, 255, 0) if is_ctrl else (140, 140, 140), 2)
        pid_label = p["id"] if p["id"] is not None else "?"
        put(dbg, f"ID {pid_label}" + (" [CONTROLLER]" if is_ctrl else ""),
            (x1, max(y1 - 8, 16)), 0.55,
            (0, 255, 0) if is_ctrl else (200, 200, 200))
    for hnd in hands:
        pts = [(int(x), int(y)) for x, y in hnd["pts"]]
        color = (0, 200, 255) if hnd is chosen else (255, 120, 0)
        for a, b in HAND_CONNECTIONS:
            cv2.line(dbg, pts[a], pts[b], color, 2)
        for pt in pts:
            cv2.circle(dbg, pt, 3, (255, 255, 255), -1)
        cv2.line(dbg, pts[4], pts[8], (0, 0, 255), 2)  # doan do khoang cach pinch
        put(dbg, f"{hnd['label']} {hnd['score']:.2f} ({hnd['side']}) owner={hnd['owner']}",
            (pts[0][0] - 40, pts[0][1] + 25), 0.5, color)
    for i, line in enumerate(info_lines):
        put(dbg, line, (10, 24 + 22 * i), 0.55)
    return dbg


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def parse_args():
    ap = argparse.ArgumentParser(description="Gesture Photobooth demo (webcam PC)")
    ap.add_argument("--camera", type=int, default=0, help="chi so camera (mac dinh 0)")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--no-yolo", action="store_true", help="tat nhan nguoi, chi dung MediaPipe")
    ap.add_argument("--yolo-model", default="yolo11n-pose.pt")
    ap.add_argument("--yolo-every", type=int, default=2, help="chay YOLO moi N frame")
    ap.add_argument("--device", default=None, help="vd: cpu, cuda:0, mps")
    ap.add_argument("--hold-frames", type=int, default=6, help="so frame giu cu chi de xac nhan")
    ap.add_argument("--min-score", type=float, default=0.6)
    return ap.parse_args()


def main():
    args = parse_args()

    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    options = vision.GestureRecognizerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=ensure_model()),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=4,
        min_hand_detection_confidence=0.5,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    recognizer = vision.GestureRecognizer.create_from_options(options)

    detector = None
    if not args.no_yolo:
        try:
            detector = PersonDetector(args.yolo_model, device=args.device)
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] Khong khoi tao duoc YOLO ({e}). Chay che do --no-yolo.")

    cap = cv2.VideoCapture(args.camera)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if not cap.isOpened():
        sys.exit(f"Khong mo duoc camera {args.camera}")

    booth = Photobooth()
    deb = GestureDebouncer(hold_frames=args.hold_frames, min_score=args.min_score)
    zoom = ZoomController()
    ctrl = ControllerManager()

    persons, frame_idx, last_ts = [], 0, 0
    zoom_count, zoom_miss, zoom_engaged = 0, 0, False
    fps, t_prev = 0.0, time.monotonic()
    last_fired = ""
    selection_owner = None

    while True:
        ok, frame = cap.read()
        if not ok:
            print("[ERROR] Doc camera that bai")
            break
        frame = cv2.flip(frame, 1)  # che do guong
        now = time.monotonic()
        h, w = frame.shape[:2]

        # ---- 1. Person detection + tracking ----
        if detector is not None and frame_idx % max(args.yolo_every, 1) == 0:
            persons = detector.detect(frame)

        # ---- 2. Hand + gesture ----
        ts = max(int(now * 1000), last_ts + 1)
        last_ts = ts
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        res = recognizer.recognize_for_video(mp_img, ts)

        hands = []
        for i, lms in enumerate(res.hand_landmarks):
            pts = [(lm.x * w, lm.y * h) for lm in lms]
            if res.gestures and i < len(res.gestures) and res.gestures[i]:
                g = res.gestures[i][0]
                label, score = g.category_name, float(g.score)
            else:
                label, score = "None", 0.0
            side = res.handedness[i][0].category_name if res.handedness else "?"
            owner = assign_owner(pts[0], persons) if detector is not None else "solo"
            world = None
            wl = getattr(res, "hand_world_landmarks", None)
            if wl and i < len(wl) and wl[i]:
                world = [(lm.x, lm.y, lm.z) for lm in wl[i]]
            hands.append(dict(pts=pts, world=world, label=label, score=score, side=side, owner=owner))

        # ---- 3. Chon controller ----
        if detector is not None:
            present = {p["id"] for p in persons}
        else:
            present = {"solo"} if hands else set()

        ctrl.update(present, now)
        if ctrl.id is None and booth.state != IDLE:
            booth.to_idle()  # controller roi di -> giai phong quyen

        if ctrl.id is None:
            candidates = [hd for hd in hands
                          if hd["label"] == "Open_Palm" and hd["score"] >= 0.7
                          and hd["owner"] is not None]
            chosen = max(candidates, key=lambda hd: hd["score"]) if candidates else None
            owner = chosen["owner"] if chosen is not None else None
            if owner != selection_owner:
                deb.reset()
                selection_owner = owner
        else:
            mine = [hd for hd in hands if hd["owner"] == ctrl.id]
            chosen = max(mine, key=lambda hd: hd["score"]) if mine else None
            selection_owner = None

        # ---- 4. Gesture -> action ----
        fired, zoom_active, ratio = None, False, 0.0
        pose_dbg = (False, False, False)
        if chosen is not None:
            in_pose, folded, thumb_out, index_out = zoom_pose_info(chosen["pts"], zoom_engaged)
            ratio = pinch_ratio(chosen["pts"], chosen.get("world"))
            pose_dbg = (folded, thumb_out, index_out)
            if in_pose:
                zoom_miss = 0
                if booth.state == READY:
                    zoom_count += 1
                    if zoom_count >= ZOOM_ENTER_FRAMES:
                        zoom_active = zoom_engaged = True
                        zoom.update(ratio, now - t_prev)
                else:
                    zoom_count, zoom_engaged = 0, False
                    zoom.release()
                deb.update(None, 0.0, now)
            else:
                zoom_miss += 1
                if zoom_miss > ZOOM_GRACE_FRAMES:   # mat tu the that su -> tha zoom
                    zoom_count, zoom_engaged = 0, False
                    zoom.release()
                    fired = deb.update(chosen["label"], chosen["score"], now)
                else:                               # rot 1-2 frame: giu nguyen zoom
                    deb.update(None, 0.0, now)
        else:
            zoom_miss += 1
            if zoom_miss > ZOOM_GRACE_FRAMES:
                zoom_count, zoom_engaged = 0, False
                zoom.release()
            deb.update(None, 0.0, now)

        if fired:
            last_fired = f"{fired} @ {datetime.now().strftime('%H:%M:%S')}"
            print(f"[GESTURE] {fired} (state={booth.state})")
            if (booth.state == IDLE and fired == "Open_Palm" and chosen is not None
                    and chosen["owner"] is not None):
                ctrl.lock(chosen["owner"], now)
                selection_owner = None
                print(f"[CONTROLLER] Khoa nguoi dieu khien: {ctrl.id}")
            booth.on_gesture(fired, now)

        # ---- 5. Photobooth engine: zoom -> filter -> capture ----
        view = FILTERS[booth.filter_idx][1](apply_zoom(frame, zoom.zoom))
        booth.tick(now, view)
        preview = booth.render(view, zoom.zoom, now, zoom_active, zoom.target)

        # ---- 6. Hien thi ----
        fps = 0.9 * fps + 0.1 / max(now - t_prev, 1e-6)
        t_prev = now
        info = [
            f"FPS: {fps:4.1f}   persons: {len(persons) if detector else 'off'}   hands: {len(hands)}",
            f"Controller: {ctrl.id}   Last event: {last_fired}",
            f"Pinch ratio: {ratio:.2f}   folded={pose_dbg[0]} thumb_out={pose_dbg[1]} "
            f"index_out={pose_dbg[2]}   "
            f"zoom_mode={zoom_active}",
        ]
        cv2.imshow("Photobooth Preview", preview)
        cv2.imshow("AI Debug", draw_debug(frame, persons, hands, ctrl.id, chosen, info))

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("r"):
            ctrl.id = None
            booth.to_idle()
        frame_idx += 1

    cap.release()
    recognizer.close()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

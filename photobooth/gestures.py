"""MediaPipe gestures and center-face ownership; no person detector or YOLO."""
import math
import time
from pathlib import Path
import cv2


def dist(a, b):
    return math.hypot(a[0]-b[0], a[1]-b[1])


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



def choose_hand(hands, faces, width):
    """Accept only hands closest to the center face, within its reach.

    Face geometry is an ownership heuristic, not skeletal person tracking.
    Reject ambiguous assignments rather than letting two people control at once.
    """
    if not faces:
        return None
    center=min(range(len(faces)), key=lambda i: abs(faces[i][0]+faces[i][2]/2-width/2))
    candidates=[]
    for hand in hands:
        wrist=hand['pts'][0]
        distances=[]
        for x,y,w,h in faces:
            # Hands can extend sideways and down from the face.
            d=math.hypot((wrist[0]-x-w/2)/w, (wrist[1]-y-h)/h)
            distances.append(d)
        order=sorted(range(len(faces)),key=lambda i: distances[i])
        if order[0]!=center or distances[center]>2.8:
            continue
        if len(order)>1 and distances[order[1]]-distances[center]<.35:
            continue
        candidates.append(hand)
    return max(candidates,key=lambda hand: hand['score']) if candidates else None


class GestureInput:
    def __init__(self, model_path, enabled=True):
        self.model_path=Path(model_path)
        self.enabled=enabled
        self.recognizer=None
        self.error=None
        self.attempted=False
        self.debounce=GestureDebouncer(hold_frames=4)
        self.zoom=ZoomController()
        self.active=False
        self.zoom_count=0
        self.zoom_missing=0
        self.timestamp=0
        self.previous=0
        self.last_event=None
        self.diagnostic={}
        self.events=[]

    def reset(self):
        self.last_event=None
        self.diagnostic={}
        self.events=[]
        self.active=False
        self.debounce.reset()
        self.zoom=ZoomController()
        self.zoom_count=self.zoom_missing=0
        self.previous=0

    def process(self, frame, faces, ready):
        if not self.enabled:
            return None, None
        if not self.attempted:
            self.attempted=True
            try:
                import mediapipe as mp
                from mediapipe.tasks import python
                from mediapipe.tasks.python import vision
                self.mp=mp
                self.recognizer=vision.GestureRecognizer.create_from_options(
                    vision.GestureRecognizerOptions(
                        base_options=python.BaseOptions(model_asset_path=str(self.model_path)),
                        running_mode=vision.RunningMode.VIDEO, num_hands=6))
            except Exception as exc:
                self.error=f'Cử chỉ chưa sẵn sàng: {exc}'
        if self.recognizer is None:
            return None, None
        now=time.monotonic()
        dt=now-self.previous if self.previous else .125
        self.previous=now
        self.timestamp=max(self.timestamp+1,int(now*1000))
        try:
            image=self.mp.Image(image_format=self.mp.ImageFormat.SRGB,
                data=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB))
            result=self.recognizer.recognize_for_video(image,self.timestamp)
        except Exception as exc:
            self.error=f'Lỗi nhận cử chỉ: {exc}'
            self.reset()
            return None, None
        self.error=None
        h,w=frame.shape[:2]
        hands=[]
        for i, landmarks in enumerate(result.hand_landmarks):
            category=result.gestures[i][0] if result.gestures[i] else None
            world=result.hand_world_landmarks[i] if result.hand_world_landmarks else None
            hands.append(dict(pts=[(p.x*w,p.y*h) for p in landmarks],
                world=[(p.x,p.y,p.z) for p in world] if world else None,
                label=category.category_name if category else None,
                score=category.score if category else 0))
        chosen=choose_hand(hands,faces,w)
        message=('Chưa thấy khuôn mặt' if not faces else
                 'Chưa thấy bàn tay' if not hands else
                 'Tay chưa thuộc người ở giữa hoặc vị trí còn mơ hồ' if chosen is None else
                 'Đang theo dõi tay của người ở giữa')
        self.diagnostic=dict(label=chosen['label'] if chosen else None,
            score=round(float(chosen['score']),3) if chosen else 0,
            hands=len(hands),faces=len(faces),message=message,zooming=False)
        if chosen is not None and self.active and ready and zoom_pose_info(chosen['pts'],self.zoom_count>=3)[0]:
            self.diagnostic["zooming"]=True
            self.zoom_count+=1
            self.zoom_missing=0
            self.debounce.update(None,0,now)
            if self.zoom_count>=3:
                self.zoom.update(pinch_ratio(chosen['pts'],chosen['world']),dt)
                return None,self.zoom.zoom
            return None,None
        self.zoom_missing+=1
        if self.zoom_missing<=3 and self.zoom_count:
            self.debounce.update(None,0,now)
            return None,None
        self.zoom_count=0
        self.zoom.release()
        event=self.debounce.update(chosen['label'] if chosen else None,
            chosen['score'] if chosen else 0,now)
        if event:
            self.last_event=event
        return event,None

    def close(self):
        if self.recognizer:
            self.recognizer.close()
            self.recognizer=None
        self.attempted=False
        self.reset()

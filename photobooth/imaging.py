import json
import math
import time
from pathlib import Path
import cv2
import numpy as np
from PIL import Image


class Imaging:
    def __init__(self, assets, demo=False, camera=0):
        self.assets, self.demo, self.index = Path(assets), demo, camera
        self.cap = None
        self.error = None
        self.jpeg = None
        self.frame = None
        self.updated = 0
        self.raw = None
        self.zoom = 1.0
        self.faces = []
        self.face_angles = []
        self.overlay = {}
        self.frame_cache = {}
        self.filters = json.loads((self.assets / 'filters/filters.json').read_text())
        for key, item in self.filters.items():
            self.overlay[key] = np.array(Image.open(self.assets / 'filters' / item['file']).convert('RGBA'))[:, :, [2, 1, 0, 3]]
        
        # Mô hình nhận diện khuôn mặt OpenCV
        cascade = self.assets / 'models/haarcascade_frontalface_default.xml'
        if not cascade.exists() and hasattr(cv2, 'data'):
            cascade = Path(cv2.data.haarcascades) / cascade.name
        self.detector = cv2.CascadeClassifier(str(cascade))
        if self.detector.empty():
            raise RuntimeError('Không tìm được mô hình khuôn mặt OpenCV')
            
        # Mô hình nhận diện mắt để tính góc nghiêng mặt (Rotation without Cropping)
        eye_cascade = self.assets / 'models/haarcascade_eye.xml'
        if not eye_cascade.exists() and hasattr(cv2, 'data'):
            eye_cascade = Path(cv2.data.haarcascades) / eye_cascade.name
        self.eye_detector = cv2.CascadeClassifier(str(eye_cascade)) if eye_cascade.exists() else None
        if self.eye_detector and self.eye_detector.empty():
            self.eye_detector = None
            
        self.counter = 0
        self.last_attempt = 0

    def options(self):
        return [dict(id='none', label='Tự nhiên')] + [dict(id=k, label=v['label']) for k,v in self.filters.items()] + [dict(id='mono',label='Đen trắng'), dict(id='sepia',label='Hoài cổ')]

    @staticmethod
    def rotate_without_cropping(image, angle):
        """
        Thuật toán xoay ảnh bảo toàn kích thước (Rotation without Cropping):
        Xoay ảnh RGBA góc `angle` (độ) quanh tâm mà không bị xén góc.
        Tự động tính toán khung bao mới (bounding box) mở rộng để giữ trọn vẹn sticker.
        """
        if abs(angle) < 0.5:
            return image
        h, w = image.shape[:2]
        cx, cy = w / 2.0, h / 2.0
        M = cv2.getRotationMatrix2D((cx, cy), -angle, 1.0)
        cos = np.abs(M[0, 0])
        sin = np.abs(M[0, 1])
        new_w = max(1, int(np.ceil(h * sin + w * cos)))
        new_h = max(1, int(np.ceil(h * cos + w * sin)))
        M[0, 2] += (new_w / 2.0) - cx
        M[1, 2] += (new_h / 2.0) - cy
        return cv2.warpAffine(
            image, M, (new_w, new_h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0, 0)
        )

    @staticmethod
    def blend(frame, overlay, x, y, width=None):
        """
        Kỹ thuật trộn ảnh kênh Alpha tối ưu hóa (Optimized Alpha Blending).
        """
        if width is not None and width != overlay.shape[1]:
            height = max(1, round(overlay.shape[0] * width / overlay.shape[1]))
            overlay = cv2.resize(overlay, (max(1, width), height))
        height, width = overlay.shape[:2]
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(frame.shape[1], x + width), min(frame.shape[0], y + height)
        if x1 <= x0 or y1 <= y0:
            return
        crop = overlay[y0-y:y1-y, x0-x:x1-x]
        a = crop[:, :, 3:4].astype(np.float32) / 255.0
        frame[y0:y1, x0:x1] = (crop[:, :, :3] * a + frame[y0:y1, x0:x1] * (1.0 - a)).astype(np.uint8)

    def _detect_eyes_and_angles(self, frame):
        """
        Xác định góc nghiêng (Roll Angle) cho từng khuôn mặt dựa trên trục 2 mắt.
        """
        if not self.faces or not self.eye_detector:
            self.face_angles = [0.0] * len(self.faces)
            return
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        new_angles = []
        for i, (x, y, w, h) in enumerate(self.faces):
            prev_angle = self.face_angles[i] if i < len(self.face_angles) else 0.0
            angle = prev_angle
            roi_h = int(h * 0.55)
            roi_gray = gray[y:y+roi_h, x:x+w]
            eyes = self.eye_detector.detectMultiScale(
                roi_gray, scaleFactor=1.1, minNeighbors=3,
                minSize=(max(10, int(w * 0.1)), max(10, int(h * 0.1))),
                maxSize=(int(w * 0.45), int(h * 0.45))
            )
            if len(eyes) >= 2:
                eyes = sorted(eyes, key=lambda e: e[0])
                e1, e2 = eyes[0], eyes[-1]
                p1 = (e1[0] + e1[2] / 2.0, e1[1] + e1[3] / 2.0)
                p2 = (e2[0] + e2[2] / 2.0, e2[1] + e2[3] / 2.0)
                dx = p2[0] - p1[0]
                dy = p2[1] - p1[1]
                if dx > w * 0.2:
                    measured = math.degrees(math.atan2(dy, dx))
                    if abs(measured) > 45:
                        measured = 45.0 if measured > 0 else -45.0
                    # Bộ lọc làm mượt chống rung giật góc nghiêng
                    angle = 0.65 * prev_angle + 0.35 * measured
            new_angles.append(round(angle, 1))
        self.face_angles = new_angles

    def read(self, effects, frame_id='none'):
        if isinstance(effects, str):
            effects = [] if effects == 'none' else [effects]
        if self.demo:
            frame = np.full((480, 640, 3), (47, 32, 22), dtype=np.uint8)
            cv2.circle(frame, (320, 240), 100, (151, 197, 226), -1)
            cv2.circle(frame, (287, 220), 10, (35, 35, 35), -1)
            cv2.circle(frame, (353, 220), 10, (35, 35, 35), -1)
            cv2.ellipse(frame, (320, 260), (40, 23), 0, 0, 180, (35, 35, 35), 4)
            cv2.putText(frame, 'DEMO - NOT LIVE CAMERA', (20, 450), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (235, 235, 235), 1)
            self.faces = [(220, 140, 200, 200)]
            self.face_angles = [0.0]
        else:
            if self.cap is None:
                if time.monotonic() - self.last_attempt < 2:
                    return False
                self.last_attempt = time.monotonic()
                self.cap = cv2.VideoCapture(self.index)
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ok, frame = self.cap.read()
            if not ok:
                self.close()
                self.error = 'Không đọc được webcam. Kiểm tra USB và chỉ số camera.'
                return False
            frame = cv2.flip(frame, 1)
            if self.counter % 3 == 0:
                small = cv2.resize(frame, (320, 240))
                found = self.detector.detectMultiScale(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), 1.15, 5, minSize=(35, 35))
                sx, sy = frame.shape[1] / 320.0, frame.shape[0] / 240.0
                self.faces = [(int(x * sx), int(y * sy), int(w * sx), int(h * sy)) for x, y, w, h in found]
                self._detect_eyes_and_angles(frame)
        self.raw = frame.copy()
        self.counter += 1
        
        if 'mono' in effects:
            frame = cv2.cvtColor(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        elif 'sepia' in effects:
            frame = np.clip(cv2.transform(frame.astype(np.float32), np.array([[.131, .534, .272], [.168, .686, .349], [.189, .769, .393]], dtype=np.float32)), 0, 255).astype(np.uint8)
            
        for filter_id in effects:
            if filter_id not in self.overlay:
                continue
            config = self.filters[filter_id]
            overlay_base = self.overlay[filter_id]
            for i, (x, y, w, h) in enumerate(self.faces):
                angle = self.face_angles[i] if i < len(self.face_angles) else 0.0
                target_w = max(1, round(w * config['width']))
                target_h = max(1, round(overlay_base.shape[0] * target_w / overlay_base.shape[1]))
                scaled = cv2.resize(overlay_base, (target_w, target_h))
                
                # Áp dụng thuật toán xoay ảnh không bị cắt góc
                if abs(angle) >= 1.0:
                    rotated = self.rotate_without_cropping(scaled, angle)
                else:
                    rotated = scaled
                    
                # Tính vị trí tâm dán sticker theo góc nghiêng của đầu
                cx0 = x + w / 2.0
                cy0 = y + h * 0.5
                dy0 = h * (config['anchor_y'] - 0.5)
                rad = math.radians(angle)
                cx = cx0 - dy0 * math.sin(rad)
                cy = cy0 + dy0 * math.cos(rad)
                
                rx = round(cx - rotated.shape[1] / 2.0)
                ry = round(cy - rotated.shape[0] / 2.0)
                self.blend(frame, rotated, rx, ry)
                
        if self.zoom > 1.001:
            h, w = frame.shape[:2]
            nw, nh = max(1, int(w / self.zoom)), max(1, int(h / self.zoom))
            x, y = (w - nw) // 2, (h - nh) // 2
            frame = cv2.resize(frame[y:y+nh, x:x+nw], (w, h))
            
        ok, encoded = cv2.imencode('.jpg', self.with_frame(frame, frame_id), [cv2.IMWRITE_JPEG_QUALITY, 78])
        if ok:
            self.frame, self.jpeg, self.updated, self.error = frame, encoded.tobytes(), time.monotonic(), None
        return ok

    def close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.frame, self.jpeg = None, None
        self.faces = []
        self.face_angles = []
        self.raw = None
        self.zoom = 1.0

    def collage(self, frames, frame_id):
        w, h = 360, 270
        canvas = np.full((h * len(frames) + 32 * (len(frames) + 1), w + 64, 3), 245, dtype=np.uint8)
        for i, im in enumerate(frames):
            canvas[32 + i * (h + 32) : 32 + i * (h + 32) + h, 32 : 32 + w] = cv2.resize(im, (w, h))
        return self.with_frame(canvas, frame_id)

    def with_frame(self, image, frame_id):
        if frame_id == 'none':
            return image
        key = (frame_id, image.shape[1], image.shape[0])
        if key not in self.frame_cache:
            path = self.assets / 'frames' / frame_id
            with Image.open(path) as source:
                overlay = np.array(source.convert('RGBA'))[:, :, [2, 1, 0, 3]]
            overlay = cv2.resize(overlay, (image.shape[1], image.shape[0]))
            # Tìm vùng mở trong suốt bao quanh tâm
            mask = (overlay[:, :, 3] < 32).astype(np.uint8)
            _, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
            label = labels[image.shape[0] // 2, image.shape[1] // 2]
            if not label:
                raise ValueError('Khung phải có vùng giữa trong suốt: ' + frame_id)
            x, y, w, h, _ = stats[label]
            if len(self.frame_cache) >= 16:
                self.frame_cache.clear()
            self.frame_cache[key] = (overlay, (int(x), int(y), int(w), int(h)))
        overlay, (x, y, w, h) = self.frame_cache[key]
        result = np.full_like(image, 245)
        scale = min(w / image.shape[1], h / image.shape[0])
        iw, ih = max(1, round(image.shape[1] * scale)), max(1, round(image.shape[0] * scale))
        ox, oy = x + (w - iw) // 2, y + (h - ih) // 2
        result[oy:oy+ih, ox:ox+iw] = cv2.resize(image, (iw, ih))
        self.blend(result, overlay, 0, 0, result.shape[1])
        return result

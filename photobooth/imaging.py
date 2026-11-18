import json
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
        self.overlay = {}
        self.frame_cache = {}
        self.filters = json.loads((self.assets / 'filters/filters.json').read_text())
        for key, item in self.filters.items():
            self.overlay[key] = np.array(Image.open(self.assets / 'filters' / item['file']).convert('RGBA'))[:, :, [2, 1, 0, 3]]
        cascade = self.assets / 'models/haarcascade_frontalface_default.xml'
        if not cascade.exists() and hasattr(cv2, 'data'):
            cascade = Path(cv2.data.haarcascades) / cascade.name
        self.detector = cv2.CascadeClassifier(str(cascade))
        if self.detector.empty():
            raise RuntimeError('Không tìm được mô hình khuôn mặt OpenCV')
        self.counter = 0
        self.last_attempt = 0

    def options(self):
        return [dict(id='none', label='Tự nhiên')] + [dict(id=k, label=v['label']) for k,v in self.filters.items()] + [dict(id='mono',label='Đen trắng'), dict(id='sepia',label='Hoài cổ')]

    @staticmethod
    def blend(frame, overlay, x, y, width):
        height = max(1, round(overlay.shape[0] * width / overlay.shape[1]))
        resized = cv2.resize(overlay, (max(1,width), height))
        x0, y0 = max(0,x), max(0,y)
        x1, y1 = min(frame.shape[1], x+width), min(frame.shape[0], y+height)
        if x1 <= x0 or y1 <= y0:
            return
        crop = resized[y0-y:y1-y, x0-x:x1-x]
        a = crop[:,:,3:4].astype(np.float32)/255
        frame[y0:y1,x0:x1] = (crop[:,:,:3]*a + frame[y0:y1,x0:x1]*(1-a)).astype(np.uint8)

    def read(self, effects, frame_id='none'):
        if isinstance(effects,str): effects=[] if effects=='none' else [effects]
        if self.demo:
            frame = np.full((480,640,3), (47,32,22), dtype=np.uint8)
            cv2.circle(frame,(320,240),100,(151,197,226),-1)
            cv2.circle(frame,(287,220),10,(35,35,35),-1)
            cv2.circle(frame,(353,220),10,(35,35,35),-1)
            cv2.ellipse(frame,(320,260),(40,23),0,0,180,(35,35,35),4)
            cv2.putText(frame,'DEMO - NOT LIVE CAMERA',(20,450),cv2.FONT_HERSHEY_SIMPLEX,.6,(235,235,235),1)
            self.faces = [(220,140,200,200)]
        else:
            if self.cap is None:
                if time.monotonic()-self.last_attempt < 2:
                    return False
                self.last_attempt=time.monotonic()
                self.cap = cv2.VideoCapture(self.index)
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,640)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT,480)
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE,1)
            ok, frame = self.cap.read()
            if not ok:
                self.close()
                self.error = 'Không đọc được webcam. Kiểm tra USB và chỉ số camera.'
                return False
            frame = cv2.flip(frame,1)
            if self.counter % 3 == 0:
                small = cv2.resize(frame,(320,240))
                found = self.detector.detectMultiScale(cv2.cvtColor(small,cv2.COLOR_BGR2GRAY),1.15,5,minSize=(35,35))
                sx,sy=frame.shape[1]/320,frame.shape[0]/240
                self.faces = [(int(x*sx),int(y*sy),int(w*sx),int(h*sy)) for x,y,w,h in found]
        self.raw = frame.copy()
        self.counter += 1
        if 'mono' in effects:
            frame=cv2.cvtColor(cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY),cv2.COLOR_GRAY2BGR)
        elif 'sepia' in effects:
            frame=np.clip(cv2.transform(frame.astype(np.float32),np.array([[.131,.534,.272],[.168,.686,.349],[.189,.769,.393]],dtype=np.float32)),0,255).astype(np.uint8)
        for filter_id in effects:
            if filter_id not in self.overlay: continue
            config=self.filters[filter_id]
            for x,y,w,h in self.faces:
                width=round(w*config['width'])
                overlay=self.overlay[filter_id]
                height=round(overlay.shape[0]*width/overlay.shape[1])
                self.blend(frame,overlay,round(x+w/2-width/2),round(y+h*config['anchor_y']-height/2),width)
        if self.zoom > 1.001:
            h,w=frame.shape[:2]
            nw,nh=max(1,int(w/self.zoom)),max(1,int(h/self.zoom))
            x,y=(w-nw)//2,(h-nh)//2
            frame=cv2.resize(frame[y:y+nh,x:x+nw],(w,h))
        ok, encoded = cv2.imencode('.jpg',self.with_frame(frame,frame_id),[cv2.IMWRITE_JPEG_QUALITY,78])
        if ok:
            self.frame, self.jpeg, self.updated, self.error = frame, encoded.tobytes(), time.monotonic(), None
        return ok

    def close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap=None
        self.frame, self.jpeg = None, None
        self.faces=[]
        self.raw=None
        self.zoom=1.0

    def collage(self, frames, frame_id):
        w,h=360,270
        canvas=np.full((h*len(frames)+32*(len(frames)+1),w+64,3),245,dtype=np.uint8)
        for i,im in enumerate(frames):
            canvas[32+i*(h+32):32+i*(h+32)+h,32:32+w] = cv2.resize(im,(w,h))
        return self.with_frame(canvas,frame_id)

    def with_frame(self, image, frame_id):
        if frame_id=='none': return image
        key=(frame_id,image.shape[1],image.shape[0])
        if key not in self.frame_cache:
            path=self.assets/'frames'/frame_id
            with Image.open(path) as source:
                overlay=np.array(source.convert('RGBA'))[:,:,[2,1,0,3]]
            overlay=cv2.resize(overlay,(image.shape[1],image.shape[0]))
            # Find the transparent opening containing the center, excluding outside corners.
            mask=(overlay[:,:,3]<32).astype(np.uint8)
            _, labels, stats, _=cv2.connectedComponentsWithStats(mask)
            label=labels[image.shape[0]//2,image.shape[1]//2]
            if not label:
                raise ValueError('Khung phải có vùng giữa trong suốt: '+frame_id)
            x,y,w,h,_=stats[label]
            if len(self.frame_cache)>=16: self.frame_cache.clear()
            self.frame_cache[key]=(overlay,(int(x),int(y),int(w),int(h)))
        overlay,(x,y,w,h)=self.frame_cache[key]
        result=np.full_like(image,245)
        scale=min(w/image.shape[1],h/image.shape[0])
        iw,ih=max(1,round(image.shape[1]*scale)),max(1,round(image.shape[0]*scale))
        ox,oy=x+(w-iw)//2,y+(h-ih)//2
        result[oy:oy+ih,ox:ox+iw]=cv2.resize(image,(iw,ih))
        self.blend(result,overlay,0,0,result.shape[1])
        return result

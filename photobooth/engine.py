import shutil
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
import cv2
from .hardware import Hardware
from .imaging import Imaging
from .gestures import GestureInput


class Booth:
    def __init__(self, store, assets, demo=False, hardware=False, camera=0, skip_sensor=False, led_pin=18):
        self.store=store
        self.store.recover()
        self.camera=Imaging(assets,demo,camera)
        self.gestures=GestureInput(assets/'models/gesture_recognizer.task',enabled=not demo)
        self.hardware=Hardware(hardware,skip_sensor,led_pin)
        self.skip_sensor=skip_sensor
        self.demo=demo
        self.lock=threading.RLock()
        self.shutdown=threading.Event()
        self.state='stopped'
        self.owner=None
        self.lease=0
        self.effects=[]
        self.frame='none'
        self.sid=None
        self.frames=[]
        self.files=[]
        self.countdown=0
        self.next_shot=0
        self.last_seen=0
        self.near_count=0
        self.distance=None
        self.last_cleanup=0
        self.thread=None
        if self.hardware.button:
            self.hardware.button.when_pressed=self.toggle

    def run(self):
        self.thread=threading.Thread(target=self._loop,daemon=True,name='booth-worker')
        self.thread.start()

    def toggle(self):
        self.start() if self.state=='stopped' else self.stop()

    def start(self):
        with self.lock:
            if self.state!='stopped': return
            if self.hardware.error: raise ValueError(self.hardware.error)
            self.state='ready' if self.skip_sensor else 'waiting'
            self.hardware.light(self.skip_sensor)
            self.last_seen=0
            self.near_count=0
            self.store.log('Admin/nút vật lý: Start')

    def stop(self):
        with self.lock:
            if self.state=='capturing': self._finish('interrupted')
            self.state='stopped'
            self.owner=None
            self.countdown=0
            self.hardware.light(False)
            self.camera.close()
            self.gestures.reset()
            self.store.log('Admin/nút vật lý: Stop')

    def claim(self, owner):
        with self.lock:
            if self.state not in ('ready','capturing'): raise ValueError('Booth chưa sẵn sàng')
            now=time.monotonic()
            if self.owner and self.owner!=owner and (self.state=='capturing' or self.lease>now):
                raise ValueError('Một người dùng khác đang điều khiển booth')
            if self.owner!=owner:
                default=self.store.settings()['default_filter']
                self.effects=[] if default=='none' else [default]
                self.frame='none'
            if self.owner!=owner:
                self.gestures.reset()
                self.camera.zoom=1.0
            self.owner=owner
            self.lease=now+120

    def select(self, owner, effect, frame):
        with self.lock:
            self._owner(owner)
            if self.state!='ready': raise ValueError('Không thể đổi hiệu ứng khi đang chụp')
            self.effects=list(effect) if isinstance(effect,list) else ([] if effect=='none' else [effect])
            self.frame=frame

    def _owner(self, owner):
        if self.owner!=owner or (self.lease<time.monotonic() and self.state!='capturing'):
            raise ValueError('Hãy nhận quyền điều khiển trước')
        self.lease=time.monotonic()+120

    def capture(self, owner):
        with self.lock:
            self._owner(owner)
            if self.state!='ready': raise ValueError('Booth chưa sẵn sàng hoặc đang chụp')
            if self.camera.frame is None or time.monotonic()-self.camera.updated>2:
                raise ValueError('Chưa có hình camera mới')
            if shutil.disk_usage(self.store.root).free<100*1024*1024:
                raise ValueError('Bộ nhớ còn dưới 100 MB. Liên hệ admin để dọn ảnh.')
            self.sid=uuid.uuid4().hex
            self.frames=[]
            self.files=[]
            self.capture_config=self.store.settings()
            self.store.create(self.sid,owner)
            self.state='capturing'
            self.countdown=self.capture_config['countdown']
            self.next_shot=time.monotonic()+self.countdown
            self.store.log('Bắt đầu phiên '+self.sid[:8])
            return self.sid

    def _write(self, name, image):
        path=self.store.photos/name
        if not cv2.imwrite(str(path),image):
            raise RuntimeError('Không lưu được ảnh: '+name)
        self.files.append(name)
        self.store.finish(self.sid,'capturing',self.files)

    def _finish(self, status):
        self.store.finish(self.sid,status,self.files)
        self.store.log('Phiên '+self.sid[:8]+': '+status)
        self.state='ready'
        self.countdown=0
        self.lease=time.monotonic()+120

    def tick(self, now=None):
        with self.lock:
            now=time.monotonic() if now is None else now
            cfg=self.store.settings()
            if self.state=='stopped': return
            self.distance=self.hardware.distance()
            near=self.skip_sensor or (self.distance is not None and 0<self.distance<cfg['distance_m'])
            self.near_count=self.near_count+1 if near else 0
            if self.near_count>=3:
                self.last_seen=now
                if self.state=='waiting':
                    self.state='ready'
                    self.hardware.light(True)
                    self.store.log('Có vật thể gần: bật LED và camera')
            if not self.skip_sensor and self.state=='ready' and now-self.last_seen>cfg['absence_seconds']:
                self.state='waiting'
                self.owner=None
                self.gestures.reset()
                self.hardware.light(False)
                self.camera.close()
            if self.state not in ('ready','capturing'): return
            if not self.camera.read(self.effects,self.frame):
                if self.state=='capturing': self._finish('failed')
                return
            self._gestures()
            if self.state=='capturing':
                self.countdown=max(0,int(self.next_shot-now+.999))
                if now>=self.next_shot:
                    self.frames.append(self.camera.frame.copy())
                    self._write(f'{self.sid}_{len(self.frames)}.jpg',self.camera.with_frame(self.frames[-1],self.frame))
                    if len(self.frames)>=self.capture_config['photo_count']:
                        self._write(f'{self.sid}_strip.jpg',self.camera.collage(self.frames,self.frame))
                        self._finish('complete')
                    else:
                        self.next_shot=now+self.capture_config['countdown']

    def _gestures(self):
        # A web owner is required so captured images retain their private owner.
        if not self.owner or (self.lease<time.monotonic() and self.state!='capturing'):
            self.gestures.reset()
            self.camera.zoom=1.0
            return
        event,zoom=self.gestures.process(self.camera.raw,self.camera.faces,self.state=='ready')
        if zoom is not None:
            self.camera.zoom=zoom
        if not event:
            return
        message='Cử chỉ không có lệnh trong trạng thái hiện tại'
        if event=='Open_Palm' and self.state=='ready':
            self.gestures.active=True
            message='Đã bật điều khiển cử chỉ'
        if not self.gestures.active:
            self._gesture_log(event,'Chưa kích hoạt: hãy giơ bàn tay mở trước')
            return
        if event=='Thumb_Up' and self.state=='ready':
            try:
                self.capture(self.owner)
                message='Đã bắt đầu đếm ngược chụp'
            except ValueError as exc:
                message=str(exc)
        elif event=='Closed_Fist' and self.state=='capturing':
            self._finish('interrupted')
            message='Đã hủy phần còn lại của phiên chụp'
        elif event in ('Victory','Pointing_Up') and self.state=='ready':
            options=[o['id'] for o in self.camera.options()
                     if o['id'] in self.store.settings()['enabled_filters']]
            current=self.effects[0] if self.effects else 'none'
            index=options.index(current) if current in options else 0
            effect=options[(index+(1 if event=='Victory' else -1))%len(options)]
            self.effects=[] if effect=='none' else [effect]
            message='Đã đổi hiệu ứng: '+effect
        self._gesture_log(event,message)

    def _gesture_log(self, event, message):
        record=dict(time=datetime.now().astimezone().isoformat(timespec='seconds'),
                    label=event,message=message)
        self.gestures.events.append(record)
        self.gestures.events[:]=self.gestures.events[-8:]
        print(f"[GESTURE] {record['time']} {event}: {message}",flush=True)

    def _loop(self):
        while not self.shutdown.is_set():
            begin=time.monotonic()
            try:
                self.tick()
                if begin-self.last_cleanup>3600:
                    self.cleanup()
                    self.last_cleanup=begin
            except Exception as exc:
                with self.lock:
                    self.camera.error=str(exc)
                    if self.state=='capturing': self._finish('failed')
                self.store.log('Lỗi worker: '+str(exc))
            self.shutdown.wait(max(.01,.125-(time.monotonic()-begin)))

    def cleanup(self):
        with self.lock:
            cutoff=datetime.now(timezone.utc)-timedelta(days=self.store.settings()['retention_days'])
            for item in self.store.sessions():
                if item['status']!='capturing' and datetime.fromisoformat(item['created'])<cutoff:
                    self.store.delete(item['id'])

    def status(self, owner=None, admin=False):
        with self.lock:
            if self.owner==owner: self.lease=time.monotonic()+120
            return dict(state=self.state, led=self.hardware.led_on, distance_m=round(self.distance,2) if self.distance is not None else None,
                camera_ready=self.camera.frame is not None, error=self.camera.error or self.hardware.error,
                gesture_enabled=self.gestures.enabled, gesture_active=self.gestures.active,
                gesture_error=self.gestures.error, gesture_last=self.gestures.last_event, zoom=self.camera.zoom,
                gesture_diagnostic=dict(self.gestures.diagnostic) if admin or self.owner==owner else {},
                gesture_events=list(self.gestures.events) if admin or self.owner==owner else [],
                demo=self.demo, hardware=self.hardware.enabled, skip_sensor=self.skip_sensor, owned=self.owner==owner,
                busy=bool(self.owner and self.owner!=owner and (self.lease>time.monotonic() or self.state=='capturing')),
                filter=self.effects[0] if self.effects else 'none', selected_filters=list(self.effects), frame=self.frame, countdown=self.countdown, taken=len(self.frames) if self.state=='capturing' else 0,
                total=self.store.settings()['photo_count'], free_mb=shutil.disk_usage(self.store.root).free//(1024*1024) if admin else None)

    def close(self):
        self.shutdown.set()
        if self.thread: self.thread.join(timeout=5)
        self.stop()
        self.gestures.close()
        self.hardware.close()

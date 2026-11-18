import json
import tempfile
import time
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from werkzeug.security import generate_password_hash
from photobooth import create_app


class SystemTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        (self.root/'credentials.json').write_text(json.dumps(dict(secret='test-secret',password_hash=generate_password_hash('test-password-123'))))
        self.app=create_app(self.root,demo=True,start_worker=False)
        self.client=self.app.test_client()
        self.other=self.app.test_client()
        self.booth=self.app.extensions['booth']
        self.store=self.app.extensions['store']
        for c in (self.client,self.other): c.get('/')

    def tearDown(self):
        self.booth.close()
        self.store.db.close()
        self.tmp.cleanup()

    def post(self,path,data=None,client=None):
        c=client or self.client
        with c.session_transaction() as s: token=s['csrf']
        return c.post(path,json=data or {},headers={'X-CSRF-Token':token})

    def login(self):
        self.assertEqual(self.post('/api/login',{'password':'test-password-123'}).status_code,200)

    def ready(self):
        self.booth.start()
        self.booth.hardware.simulated_distance=.6
        for _ in range(3): self.booth.tick()
        self.assertEqual(self.booth.state,'ready')
        self.assertTrue(self.booth.hardware.led_on)

    def test_skip_sensor_start_and_stop(self):
        from photobooth import ROOT
        from photobooth.engine import Booth
        self.booth.close()
        self.booth=Booth(self.store,ROOT/'assets',demo=True,skip_sensor=True)
        self.booth.start()
        self.assertEqual(self.booth.state,'ready')
        self.assertTrue(self.booth.hardware.led_on)
        self.assertIsNone(self.booth.hardware.distance())
        self.booth.tick(time.monotonic()+1000)
        self.assertEqual(self.booth.state,'ready')
        self.assertIsNotNone(self.booth.camera.frame)
        self.booth.stop()
        self.assertEqual(self.booth.state,'stopped')
        self.assertFalse(self.booth.hardware.led_on)

    def test_skip_sensor_does_not_initialize_hcsr04(self):
        from unittest.mock import patch
        from photobooth.hardware import Hardware
        with patch('gpiozero.LED'), patch('gpiozero.Button'), patch('gpiozero.DistanceSensor') as sensor:
            hw=Hardware(enabled=True,skip_sensor=True)
            sensor.assert_not_called()
            self.assertIsNone(hw.error)
            self.assertIsNone(hw.distance())
            hw.close()

    def test_auth_and_csrf(self):
        self.assertEqual(self.client.post('/api/control',json={'action':'start'}).status_code,403)
        self.assertEqual(self.post('/api/control',{'action':'start'}).status_code,403)
        self.assertEqual(self.post('/api/login',{'password':'wrong'}).status_code,403)
        self.login()
        self.assertEqual(self.post('/api/control',{'action':'start'}).status_code,200)
        self.assertEqual(self.client.get('/api/events').status_code,200)
        self.assertEqual(self.other.get('/api/events').status_code,403)

    def test_full_capture_and_privacy(self):
        self.ready()
        self.assertEqual(self.post('/api/claim').status_code,200)
        self.assertEqual(self.post('/api/claim',client=self.other).status_code,409)
        self.assertEqual(self.other.get('/api/preview.jpg').status_code,403)
        self.assertEqual(self.client.get('/api/preview.jpg').mimetype,'image/jpeg')
        self.assertEqual(self.post('/api/select',{'filter':'glasses','frame':'black_glossy.png'}).status_code,200)
        self.assertEqual(self.post('/api/capture').status_code,200)
        self.assertEqual(self.post('/api/capture').status_code,409)
        self.assertEqual(self.post('/api/release').status_code,409)
        for _ in range(4):
            self.booth.next_shot=time.monotonic()-1
            self.booth.tick()
        items=self.client.get('/api/sessions').json
        self.assertEqual(items[0]['status'],'complete')
        self.assertEqual(len(items[0]['files']),5)
        name=items[0]['files'][-1]
        response=self.client.get('/media/'+name+'?download=1')
        self.assertEqual(response.status_code,200)
        self.assertIn('attachment',response.headers['Content-Disposition'])
        response.close()
        self.assertEqual(self.other.get('/api/sessions').json,[])
        self.assertEqual(self.other.get('/media/'+name).status_code,404)
        self.login()
        with self.client.session_transaction() as s: token=s['csrf']
        self.assertEqual(self.client.delete('/api/sessions/'+items[0]['id'],headers={'X-CSRF-Token':token}).status_code,200)
        self.assertFalse((self.store.photos/name).exists())

    def test_absence_and_stop(self):
        self.ready()
        self.booth.hardware.simulated_distance=2.5
        self.booth.last_seen=time.monotonic()-31
        self.booth.tick()
        self.assertEqual(self.booth.state,'waiting')
        self.assertFalse(self.booth.hardware.led_on)
        self.assertIsNone(self.booth.camera.frame)
        self.ready()
        self.post('/api/claim')
        self.post('/api/capture')
        self.booth.next_shot=time.monotonic()-1
        self.booth.tick()
        self.booth.stop()
        item=self.store.sessions()[0]
        self.assertEqual(item['status'],'interrupted')
        self.assertEqual(len(item['files']),1)
        self.assertEqual(self.booth.state,'stopped')
        self.assertFalse(self.booth.hardware.led_on)

    def test_settings_validation_and_persistence(self):
        self.login()
        settings=self.store.settings()
        bad=dict(settings,photo_count=999)
        self.assertEqual(self.post('/api/settings',bad).status_code,400)
        good=dict(settings,photo_count=2,countdown=1)
        self.assertEqual(self.post('/api/settings',good).status_code,200)
        self.assertEqual(self.store.settings()['photo_count'],2)
        self.ready()
        self.post('/api/claim')
        self.assertEqual(self.post('/api/select',{'filter':'none','frame':'../../etc/passwd'}).status_code,400)
        self.post('/api/capture')
        self.assertEqual(self.post('/api/settings',good).status_code,409)
        for _ in range(2):
            self.booth.next_shot=time.monotonic()-1
            self.booth.tick()
        self.assertEqual(len(self.store.sessions()[0]['files']),3)

    def test_expired_lease_and_recovery(self):
        self.ready()
        self.post('/api/claim')
        self.booth.lease=0
        self.assertEqual(self.post('/api/claim',client=self.other).status_code,200)
        self.assertEqual(self.post('/api/capture').status_code,409)
        self.store.create('restart-session','owner')
        self.store.recover()
        self.assertEqual(self.store.sessions()[0]['status'],'interrupted')

    def test_camera_failure_and_disabled_filter(self):
        self.ready()
        self.post('/api/claim')
        self.store.configure({'enabled_filters':['none'], 'default_filter':'none'})
        self.assertEqual(self.post('/api/select',{'filter':'hat','frame':'none'}).status_code,400)
        self.post('/api/capture')
        self.booth.camera.read=lambda *args: False
        self.booth.tick()
        self.assertEqual(self.store.sessions()[0]['status'],'failed')
        self.assertEqual(self.booth.state,'ready')

    def test_absence_during_capture_finishes_sequence(self):
        self.ready()
        self.post('/api/claim')
        self.post('/api/capture')
        self.booth.hardware.simulated_distance=2.5
        self.booth.last_seen=time.monotonic()-31
        for _ in range(4):
            self.booth.next_shot=time.monotonic()-1
            self.booth.tick()
        self.assertEqual(self.store.sessions()[0]['status'],'complete')
        self.booth.tick()
        self.assertEqual(self.booth.state,'waiting')
        self.assertFalse(self.booth.hardware.led_on)

    def test_retention_and_pages(self):
        self.assertEqual(self.client.get('/').status_code,200)
        self.assertEqual(self.client.get('/admin').status_code,200)
        self.assertIn("default-src 'self'",self.client.get('/').headers['Content-Security-Policy'])
        self.store.create('old','owner')
        self.store.finish('old','complete',['old.jpg'])
        (self.store.photos/'old.jpg').write_bytes(b'test')
        old=(datetime.now(timezone.utc)-timedelta(days=10)).isoformat()
        self.store.db.execute('UPDATE sessions SET created=? WHERE id=?',(old,'old'))
        self.store.db.commit()
        self.booth.cleanup()
        self.assertFalse(self.store.sessions())
        self.assertFalse((self.store.photos/'old.jpg').exists())

    def test_rotation_without_cropping(self):
        import numpy as np
        from photobooth.imaging import Imaging
        img = np.zeros((100, 200, 4), dtype=np.uint8)
        img[20:80, 20:180] = (255, 0, 0, 255)
        same = Imaging.rotate_without_cropping(img, 0.0)
        self.assertEqual(same.shape, img.shape)
        rot45 = Imaging.rotate_without_cropping(img, 45.0)
        self.assertGreater(rot45.shape[0], img.shape[0])
        self.assertGreater(rot45.shape[1], img.shape[1])
        self.assertGreater(np.sum(rot45[:, :, 3] > 0), 0)
        self.booth.camera.face_angles = [25.0]
        ok = self.booth.camera.read(['glasses', 'hat'], 'none')
        self.assertTrue(ok)
        self.assertIsNotNone(self.booth.camera.frame)


if __name__=='__main__': unittest.main()

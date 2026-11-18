import unittest
from unittest.mock import patch
import test_system
from photobooth.gestures import choose_hand, GestureDebouncer


class OwnershipTest(unittest.TestCase):
    def test_only_center_face_hands_accepted(self):
        faces=[(40,80,90,90),(270,80,100,100),(510,80,90,90)]
        left=dict(pts=[(100,240)],score=.99)
        middle=dict(pts=[(320,240)],score=.7)
        right=dict(pts=[(550,240)],score=.99)
        self.assertIs(choose_hand([left,middle,right],faces,640),middle)
        self.assertIsNone(choose_hand([left,right],faces,640))
        self.assertIsNone(choose_hand([middle],[],640))

    def test_ambiguous_hand_rejected(self):
        self.assertIsNone(choose_hand([dict(pts=[(235,240)],score=.99)],
            [(120,80,100,100),(250,80,100,100)],640))

    def test_hold_does_not_repeat(self):
        deb=GestureDebouncer(hold_frames=3)
        events=[deb.update('Victory',.9,t) for t in range(10)]
        self.assertEqual(events.count('Victory'),1)


class GestureSystemTest(unittest.TestCase):
    setUp=test_system.SystemTest.setUp
    tearDown=test_system.SystemTest.tearDown
    ready=test_system.SystemTest.ready
    post=test_system.SystemTest.post

    def test_gesture_actions_use_existing_session(self):
        self.ready()
        self.post('/api/claim')
        self.store.configure({'photo_count':2})
        with patch.object(self.booth.gestures,'process') as process:
            process.return_value=('Thumb_Up',None)
            self.booth.tick()
            self.assertEqual(self.booth.state,'ready')
            process.return_value=('Open_Palm',None)
            self.booth.tick()
            self.assertTrue(self.booth.gestures.active)
            process.return_value=('Victory',None)
            self.booth.tick()
            self.assertEqual(self.booth.effects,['glasses'])
            process.return_value=(None,2.0)
            self.booth.tick()
            self.assertEqual(self.booth.camera.zoom,2.0)
            process.return_value=('Thumb_Up',None)
            self.booth.tick()
            self.assertEqual(self.booth.state,'capturing')
            self.assertEqual(len(self.client.get('/api/sessions').json),1)
            self.assertEqual(self.other.get('/api/sessions').json,[])
            process.return_value=('Closed_Fist',None)
            self.booth.tick()
            self.assertEqual(self.booth.state,'ready')
            self.assertEqual(self.store.sessions()[0]['status'],'interrupted')
        self.post('/api/release')
        self.assertFalse(self.booth.gestures.active)
        self.assertEqual(self.booth.camera.zoom,1.0)

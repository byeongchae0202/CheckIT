import unittest
from realtime import StablePresence, LiveRecognition, inside_region


class PresenceTests(unittest.TestCase):
    def test_region_center_and_boundary(self):
        self.assertTrue(inside_region({'box': [0, 0, 40, 40]}, 100, 100, (.2, .2, .8, .8)))
        self.assertFalse(inside_region({'box': [0, 0, 20, 20]}, 100, 100, (.2, .2, .8, .8)))

    def test_stable_and_interrupted_detection(self):
        tracker = StablePresence()
        self.assertFalse(tracker.observe(['book'], 0))
        self.assertFalse(tracker.observe(['book'], .4))
        self.assertEqual(tracker.observe(['book'], .9), {'book'})
        tracker.observe([], 1)
        self.assertFalse(tracker.observe(['book'], 1.1))
        self.assertFalse(tracker.observe(['book'], 3))

    def test_uncheck_requires_observed_absence(self):
        tracker = StablePresence()
        tracker.block('book')
        for now in (0, .5, 1, 1.5):
            self.assertFalse(tracker.observe(['book'], now))
        for now in (2, 2.5, 3.1):
            tracker.observe([], now)
        for now in (3.2, 3.6):
            self.assertFalse(tracker.observe(['book'], now))
        self.assertEqual(tracker.observe(['book'], 4.1), {'book'})

    def test_pause_or_frame_gap_is_not_absence(self):
        tracker = StablePresence()
        tracker.block('book')
        tracker.observe([], 0)
        tracker.observe([], 5)
        self.assertIn('book', tracker.blocked)
        tracker.clear()
        tracker.observe([], 6)
        self.assertIn('book', tracker.blocked)

    def test_settings_and_manual_uncheck_discard_pending(self):
        live = LiveRecognition(None)
        live.pending = {'book'}
        live.block('book')
        self.assertFalse(live.drain()[0])
        self.assertEqual(live.photo_allowed(['book', 'bottle']), {'bottle'})
        live.reset()
        self.assertEqual(live.photo_allowed(['book']), {'book'})
        live.pending = {'book'}
        live.pause()
        self.assertFalse(live.drain()[0])


class FrameTests(unittest.TestCase):
    def setUp(self):
        try:
            import av
            import numpy as np
        except ImportError:
            self.skipTest('영상 의존성 미설치')
        self.frame = av.VideoFrame.from_ndarray(np.zeros((100, 100, 3), dtype=np.uint8), format='bgr24')

    def test_frames_filter_outside_and_confirm_inside(self):
        class Detector:
            def detect_bgr(self, image, confidence):
                return [dict(class_name='book', box=[30, 30, 60, 60]),
                        dict(class_name='bottle', box=[0, 0, 10, 10])], image
        now = [0]
        live = LiveRecognition(Detector(), clock=lambda: now[0])
        for instant in (0, .4, .9):
            now[0] = instant
            result = live.recv(self.frame)
            self.assertEqual(result.width, 100)
        self.assertEqual(live.drain()[0], {'book'})
        live.pause()
        now[0] = 2
        live.recv(self.frame)
        self.assertFalse(live.drain()[0])

    def test_error_and_inflight_reset_do_not_confirm(self):
        class Detector:
            def detect_bgr(self, image, confidence):
                raise RuntimeError('model failed')
        live = LiveRecognition(Detector())
        live.recv(self.frame)
        ready, error, last = live.drain()
        self.assertFalse(ready)
        self.assertIn('model failed', error)
        self.assertIsNone(last)
        class ResetDetector:
            def detect_bgr(self, image, confidence):
                live.reset()
                return [dict(class_name='book', box=[30, 30, 60, 60])], image
        live.detector = ResetDetector()
        live.last_inference = float('-inf')
        live.recv(self.frame)
        self.assertFalse(live.tracker.seen)


if __name__ == '__main__':
    unittest.main()

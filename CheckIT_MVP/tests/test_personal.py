import importlib.util
from pathlib import Path
import tempfile
import unittest
from personal import PersonalMatcher, decode_photo, prepare_reference
from profiles import ProfileStore


@unittest.skipUnless(importlib.util.find_spec('cv2') and importlib.util.find_spec('PIL'), '사진 비교 의존성 미설치')
class PersonalTests(unittest.TestCase):
    def setUp(self):
        from personal_fixtures import images
        self.raw, self.reference, self.scene = images()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = ProfileStore(Path(directory.name) / 'data.sqlite3')

    def test_perspective_match_negative_and_ambiguity(self):
        import numpy as np
        from personal_fixtures import images
        jpeg = prepare_reference(self.raw)
        matcher = PersonalMatcher([dict(class_name='personal_one', jpeg=jpeg)])
        rows = matcher.match(self.scene)
        self.assertEqual([row['class_name'] for row in rows], ['personal_one'])
        self.assertGreater(rows[0]['inliers'], 10)
        self.assertFalse(matcher.match(np.zeros_like(self.scene)))
        self.assertFalse(matcher.match(images(seed=999)[2]))
        ambiguous = PersonalMatcher([dict(class_name=key, jpeg=jpeg) for key in ('personal_one', 'personal_two')])
        self.assertFalse(ambiguous.match(self.scene))

    def test_bad_or_featureless_photos_and_duplicate_registration(self):
        import cv2
        import numpy as np
        for raw in (b'', b'invalid', cv2.imencode('.png', np.zeros((200, 200, 3), dtype=np.uint8))[1].tobytes()):
            with self.assertRaises(ValueError):
                prepare_reference(raw)
        with self.assertRaises(ValueError):
            self.store.register_personal('default', '지갑', 'WALLET', [self.raw, self.raw])
        self.assertEqual(len(self.store.profile('default')['items']), 3)

    def test_persistence_activation_ownership_and_delete(self):
        key = self.store.register_personal('default', '내 지갑', 'WALLET', [self.raw])
        with self.assertRaises(ValueError):
            self.store.enable_personal('default', key, True)
        self.store.record_personal_test('default', key, True)
        self.store.enable_personal('default', key, True)
        self.assertEqual(len(self.store.reference_photos('default', active_only=True)), 1)
        other = self.store.create_user('다른 사용자')
        photo = self.store.reference_photos('default', key)[0]
        self.store.delete_reference_photo(other, key, photo['id'])
        self.assertEqual(len(self.store.reference_photos('default', key)), 1)
        self.assertFalse(self.store.reference_photos(other))
        reopened = ProfileStore(self.store.path)
        self.assertEqual(len(reopened.reference_photos('default', key)), 1)
        self.assertEqual(len(self.store.reference_photos('default', active_only=True)), 1)
        self.store.delete_item('default', key)
        self.assertFalse(self.store.reference_photos('default', key))

    def test_photo_changes_require_revalidation(self):
        from personal_fixtures import images
        key = self.store.register_personal('default', '개인 책', 'MY BOOK', [self.raw])
        self.store.record_personal_test('default', key, True)
        self.store.enable_personal('default', key, True)
        self.store.add_reference_photos('default', key, [images(8)[0]])
        self.assertFalse(self.store.reference_photos('default', active_only=True))
        with self.assertRaises(ValueError):
            self.store.enable_personal('default', key, True)
        photo = self.store.reference_photos('default', key)[0]
        self.store.delete_reference_photo('default', key, photo['id'])
        self.assertEqual(len(self.store.reference_photos('default', key)), 1)

    def test_personal_result_follows_region_and_stable_confirmation(self):
        import av
        from realtime import LiveRecognition
        matcher = PersonalMatcher([dict(class_name='personal_one', jpeg=prepare_reference(self.raw))])
        class Detector:
            def detect_bgr(self, image, confidence):
                return matcher.match(image), image
        now = [0]
        live = LiveRecognition(Detector(), clock=lambda: now[0])
        for stamp in (0, .4, .9):
            now[0] = stamp
            live.recv(av.VideoFrame.from_ndarray(self.scene, format='bgr24'))
        self.assertEqual(live.drain()[0], {'personal_one'})
        live.block('personal_one')
        for stamp in (1.3, 1.7, 2.1):
            now[0] = stamp
            live.recv(av.VideoFrame.from_ndarray(self.scene, format='bgr24'))
        self.assertFalse(live.drain()[0])
        live.configure((0, 0, .1, .1), .4, True)
        now[0] = 2.5
        live.recv(av.VideoFrame.from_ndarray(self.scene, format='bgr24'))
        self.assertFalse(live.drain()[0])

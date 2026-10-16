import tempfile
from pathlib import Path
import unittest
from profiles import ProfileStore
from checklist import Checklist


class ProfileTests(unittest.TestCase):
    def test_recognition_name_guidance(self):
        from profiles import SUPPORTED, recognition_for_name
        for key, label in SUPPORTED.items():
            self.assertEqual(recognition_for_name(key), key)
            self.assertEqual(recognition_for_name(label), key)
        self.assertEqual(recognition_for_name(' 스마트 폰 '), 'cell phone')
        self.assertEqual(recognition_for_name('LAPTOP'), 'laptop')
        self.assertNotIn('person', SUPPORTED)
        for unknown in ('', '지갑', '열쇠', '책가방', '컵라면', '사람', 'person'):
            self.assertIsNone(recognition_for_name(unknown))

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'profiles.sqlite3'
        self.day = ['2026-10-16']
        self.store = ProfileStore(self.path, day=lambda: self.day[0])

    def test_users_and_persistence(self):
        other = self.store.create_user('다른 사용자')
        key = self.store.add_item(other, '지갑', 'WALLET')
        reopened = ProfileStore(self.path)
        self.assertEqual(len(reopened.profile(other)['items']), 4)
        self.assertEqual(len(reopened.profile('default')['items']), 3)
        self.store.delete_item('default', key)
        self.assertEqual(len(reopened.profile(other)['items']), 4)
        with self.assertRaises(ValueError):
            self.store.create_user('다른 사용자')

    def test_items_are_removed_by_deleting_them(self):
        self.store.delete_item('default', 'umbrella')
        self.assertEqual(len(self.store.active_items(self.store.profile('default'))), 2)
        self.day[0] = '2026-10-17'
        self.assertEqual(len(self.store.active_items(self.store.profile('default'))), 2)

    def test_validation_and_readd_class(self):
        with self.assertRaises(ValueError):
            self.store.add_item('default', '다른 책', 'BOOK', 'book')
        for name, lcd, category in [('', 'X', None), ('지갑', '한글', None),
                                    ('지갑', 'X' * 17, None), ('지갑', 'WALLET', 'wallet')]:
            with self.assertRaises(ValueError):
                self.store.add_item('default', name, lcd, category)
        self.store.delete_item('default', 'book')
        self.assertEqual(self.store.add_item('default', '책', 'BOOK', 'book'), 'book')
        self.store.add_item('default', '지갑', 'WALLET')
        with self.assertRaises(ValueError):
            self.store.add_item('default', '지갑', 'WALLET')

    def test_scheduled_item_appears_only_on_selected_day(self):
        key = self.store.add_item('default', '여권', 'PASSPORT', active_on='2026-10-18')
        self.assertNotIn(key, {row['class_name'] for row in self.store.profile('default')['items']})
        self.assertEqual(self.store.scheduled_items('default', '2026-10-18')[0]['active_on'], '2026-10-18')
        self.store.add_item('default', '충전기', 'CHARGER', active_on='2026-10-18')
        self.assertEqual(len(self.store.scheduled_items('default', '2026-10-18')), 2)
        self.store.add_item('default', '배낭', 'BACKPACK', 'backpack', active_on='2026-10-19')
        self.assertEqual(len(self.store.scheduled_items('default', '2026-10-18')), 2)
        self.day[0] = '2026-10-17'
        self.assertNotIn(key, {row['class_name'] for row in self.store.profile('default')['items']})
        self.day[0] = '2026-10-18'
        self.assertIn(key, {row['class_name'] for row in self.store.active_items(self.store.profile('default'))})
        self.day[0] = '2026-10-19'
        self.assertNotIn(key, {row['class_name'] for row in self.store.profile('default')['items']})
        self.assertEqual(self.store.scheduled_items('default', '2026-10-18'), [])
        self.store.add_item('default', '여권', 'PASSPORT')
        self.assertIn('여권', {row['name'] for row in self.store.profile('default')['items']})

    def test_scheduled_item_rejects_invalid_or_past_dates(self):
        for value in ('not-a-date', '2026-10-15', '2026-2-3'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.add_item('default', '여권', 'PASSPORT', active_on=value)
        self.assertEqual(len(self.store.profile('default')['items']), 3)

    def test_settings_isolation_and_reload(self):
        other = self.store.create_user('사용자2')
        settings = {'confidence': .7, 'roi_x': [20, 70], 'roi_y': [10, 90]}
        self.store.save_settings(other, settings)
        self.assertEqual(ProfileStore(self.path).profile(other)['settings'], settings)
        self.assertEqual(self.store.profile('default')['settings']['confidence'], .4)
        with self.assertRaises(ValueError):
            self.store.save_settings(other, dict(settings, roi_x=[50, 50]))

    def test_list_mutation_preserves_remaining_and_empty_is_not_ready(self):
        checklist = Checklist()
        checklist.start()
        checklist.mark('book')
        self.store.delete_item('default', 'umbrella')
        checklist.set_items(self.store.active_items(self.store.profile('default')))
        self.assertIn('book', checklist.confirmed)
        self.store.delete_item('default', 'book')
        checklist.set_items(self.store.active_items(self.store.profile('default')))
        self.assertNotIn('book', checklist.confirmed)
        checklist.set_items([])
        self.assertEqual(checklist.state, 'EMPTY')
        checklist.set_items(self.store.active_items(self.store.profile('default')), reset=True)
        self.assertFalse(checklist.confirmed)


if __name__ == '__main__':
    unittest.main()

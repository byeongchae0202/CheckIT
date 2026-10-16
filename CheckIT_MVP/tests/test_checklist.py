import unittest
from checklist import Checklist


class ChecklistTests(unittest.TestCase):
    def setUp(self):
        self.c = Checklist()

    def test_default_items(self):
        self.assertEqual([i['class_name'] for i in self.c.items], ['bottle', 'book', 'umbrella'])

    def test_missing_and_explicit_finish(self):
        self.c.start()
        self.c.apply_detections(['bottle', 'book', 'bottle', 'person'])
        self.assertEqual(self.c.state, 'SCANNING')
        self.assertEqual(len(self.c.history), 2)
        self.assertEqual([i['class_name'] for i in self.c.missing], ['umbrella'])
        self.assertEqual(self.c.finish(), 'MISSING')

    def test_ready(self):
        self.c.start()
        self.c.apply_detections(['bottle', 'book', 'umbrella'])
        self.assertEqual(self.c.state, 'READY')
        self.assertEqual(self.c.finish(), 'READY')

    def test_cancel_manual_and_reset(self):
        self.c.start()
        self.c.mark('book')
        self.c.apply_detections(['book'])
        self.assertEqual(self.c.confirmed['book'], 'manual')
        self.c.mark('book', checked=False)
        self.assertNotIn('book', self.c.confirmed)
        self.c.reset()
        self.assertEqual((self.c.state, self.c.confirmed, self.c.history), ('IDLE', {}, []))

    def test_duplicate_start_preserves_progress(self):
        self.c.start()
        self.c.mark('bottle')
        self.assertFalse(self.c.start())
        self.assertIn('bottle', self.c.confirmed)

    def test_invalid_transition(self):
        with self.assertRaises(ValueError):
            self.c.finish()
        with self.assertRaises(ValueError):
            self.c.mark('book')
        self.c.start()
        with self.assertRaises(ValueError):
            self.c.mark('wallet')

    def test_uncheck_after_ready_and_restart(self):
        self.c.start()
        self.c.apply_detections(['bottle', 'book', 'umbrella'])
        self.c.mark('book', checked=False)
        self.assertEqual(self.c.state, 'SCANNING')
        self.assertEqual([i['class_name'] for i in self.c.missing], ['book'])
        self.c.mark('book')
        self.assertEqual(self.c.state, 'READY')
        self.c.start(force=True)
        self.assertEqual(self.c.confirmed, {})

    def test_empty_list_is_not_ready(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'items.json'
            path.write_text('[]', encoding='utf-8')
            empty = Checklist(path)
            empty.start()
            self.assertEqual(empty.state, 'EMPTY')


if __name__ == '__main__':
    unittest.main()

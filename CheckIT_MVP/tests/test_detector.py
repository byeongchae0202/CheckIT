import unittest
from detector import same_object_box


class RegisteredObjectTests(unittest.TestCase):
    def test_label_inside_bottle_is_same_object(self):
        self.assertTrue(same_object_box([0, 0, 100, 300], [20, 150, 80, 230]))

    def test_separate_bottle_is_preserved(self):
        self.assertFalse(same_object_box([0, 0, 100, 300], [120, 0, 200, 250]))

    def test_small_edge_overlap_is_preserved(self):
        self.assertFalse(same_object_box([0, 0, 100, 300], [90, 0, 190, 300]))

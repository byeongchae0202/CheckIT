import unittest
from regions import load_regions, RegionIndex
from weather import DEFAULT_WEATHER, validate_settings


class RegionTests(unittest.TestCase):
    def test_official_snapshot_and_grid(self):
        index = load_regions()
        self.assertEqual(len(index.records), 3838)
        self.assertEqual(len(index.provinces()), 17)
        record = index.resolve('서울특별시', '종로구', '청운효자동')
        self.assertEqual(record['code'], '1111051500')
        self.assertEqual((record['nx'], record['ny']), (60, 127))
        self.assertEqual(RegionIndex.name(record), '서울특별시 종로구 청운효자동')

    def test_hierarchy_never_crosses_parent(self):
        index = load_regions()
        self.assertIn('종로구', index.districts('서울특별시'))
        self.assertNotIn('종로구', index.districts('부산광역시'))
        self.assertIsNone(index.resolve('부산광역시', '종로구', '청운효자동'))
        for record in index.records:
            self.assertTrue(1 <= record['nx'] <= 149 and 1 <= record['ny'] <= 253)
            self.assertIsNotNone(index.resolve(record['province'], record['district'], record['town']))

    def test_selected_code_controls_name_and_grid(self):
        settings = validate_settings(dict(DEFAULT_WEATHER, region_code='1111051500', nx=1, ny=1, region_name='임의 이름'))
        self.assertEqual((settings['nx'], settings['ny']), (60, 127))
        self.assertEqual(settings['region_name'], '서울특별시 종로구 청운효자동')
        with self.assertRaises(ValueError):
            validate_settings(dict(DEFAULT_WEATHER, region_code='invalid'))

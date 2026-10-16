"""인터넷 연결 없이 공식 전국 지역·격자를 선택한다."""
from functools import lru_cache
import json
from pathlib import Path


class RegionIndex:
    def __init__(self, records):
        self.records = records
        self.by_code = {record['code']: record for record in records}
        if len(self.by_code) != len(records):
            raise ValueError('중복 지역 코드입니다.')

    def provinces(self):
        return sorted({record['province'] for record in self.records})

    def districts(self, province):
        return sorted({record['district'] for record in self.records if record['province'] == province and record['district']})

    def towns(self, province, district):
        return sorted({record['town'] for record in self.records
                       if record['province'] == province and record['district'] == district and record['town']})

    def resolve(self, province, district, town):
        return next((record for record in self.records
                     if (record['province'], record['district'], record['town']) == (province, district, town)), None)

    @staticmethod
    def name(record):
        return ' '.join(record[key] for key in ('province', 'district', 'town') if record[key])


@lru_cache(maxsize=1)
def load_regions():
    path = Path(__file__).resolve().parent / 'data' / 'weather_regions.json'
    return RegionIndex(json.loads(path.read_text(encoding='utf-8'))['regions'])

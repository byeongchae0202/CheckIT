"""공식 단기예보 참고자료 ZIP의 지역·격자 XLSX를 JSON으로 변환한다."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile


def build(archive, output):
    raw = Path(archive).read_bytes()
    with ZipFile(io.BytesIO(raw)) as guide:
        member = next(name for name in guide.namelist() if name.endswith('.xlsx'))
        with ZipFile(io.BytesIO(guide.read(member))) as workbook:
            ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            strings = [''.join(t.text or '' for t in cell.findall('.//s:t', ns))
                       for cell in ET.fromstring(workbook.read('xl/sharedStrings.xml'))]
            sheet = ET.fromstring(workbook.read('xl/worksheets/sheet1.xml'))
            records = []
            for row in sheet.findall('.//s:row', ns)[1:]:
                values = {}
                for cell in row:
                    column = re.sub(r'\d', '', cell.attrib['r'])
                    value = cell.findtext('s:v', '', ns)
                    values[column] = strings[int(value)] if cell.attrib.get('t') == 's' else value
                if values.get('A') != 'kor' or not values.get('C'):
                    continue
                record = {'code': values['B'], 'province': values['C'], 'district': values.get('D', ''),
                          'town': values.get('E', ''), 'nx': int(values['F']), 'ny': int(values['G'])}
                if not re.fullmatch(r'\d{10}', record['code']) or not 1 <= record['nx'] <= 149 or not 1 <= record['ny'] <= 253:
                    raise ValueError('공식 지역 자료의 코드·격자 범위를 확인해주세요.')
                records.append(record)
    if len(records) < 3000 or len({record['code'] for record in records}) != len(records):
        raise ValueError('전국 지역 자료의 크기 또는 중복 코드를 확인해주세요.')
    result = {'source': 'https://www.data.go.kr/data/15084084/openapi.do',
              'source_title': '기상청41_단기예보 조회서비스_오픈API활용가이드_2609.zip / 격자·위경도(2607).xlsx',
              'retrieved_on': '2026-10-16', 'archive_sha256': hashlib.sha256(raw).hexdigest(),
              'license': '공공누리 제1유형: 출처표시', 'regions': records}
    Path(output).write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print('REGIONS', len(records), 'PROVINCES', len({record['province'] for record in records}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('archive')
    parser.add_argument('output')
    args = parser.parse_args()
    build(args.archive, args.output)

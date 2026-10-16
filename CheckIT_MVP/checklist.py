"""사진·영상 사이에 확인 결과를 누적하고 모두 확인하면 자동 완료한다."""
import json
from datetime import datetime, timezone
from config import ITEMS_FILE


class Checklist:
    def __init__(self, path=ITEMS_FILE):
        self.items = json.loads(path.read_text(encoding='utf-8'))
        keys = [item['class_name'] for item in self.items]
        if len(set(keys)) != len(keys):
            raise ValueError('준비물 클래스는 중복될 수 없습니다.')
        self.reset()

    def reset(self):
        self.confirmed = {}  # class -> auto 또는 manual
        self.history = []
        self.state = 'IDLE'

    def set_items(self, items, reset=False):
        keys = [item['class_name'] for item in items]
        if len(set(keys)) != len(keys):
            raise ValueError('준비물 클래스는 중복될 수 없습니다.')
        self.items = list(items)
        if reset:
            self.reset()
        else:
            self.confirmed = {key: source for key, source in self.confirmed.items() if key in keys}
        self.state = 'EMPTY' if not self.items else 'SCANNING' if self.missing else 'READY'

    def start(self, force=False):
        if self.state == 'SCANNING' and not force:
            return False
        self.reset()
        self.state = 'SCANNING' if self.items else 'EMPTY'
        return True

    def mark(self, class_name, source='manual', checked=True):
        if self.state not in ('SCANNING', 'READY', 'MISSING'):
            raise ValueError('검사를 먼저 시작하세요.')
        if class_name not in {i['class_name'] for i in self.items}:
            raise ValueError('알 수 없는 준비물입니다.')
        if source not in ('auto', 'manual'):
            raise ValueError('확인 출처가 올바르지 않습니다.')
        if checked:
            # 중복 인식이 수동 확인 기록을 덮어쓰지 않는다.
            if class_name in self.confirmed and source == 'auto':
                return
            self.confirmed[class_name] = source
        else:
            self.confirmed.pop(class_name, None)
        self.state = 'SCANNING' if self.missing else 'READY'
        self.history.append(dict(class_name=class_name, source=source,
                                 checked=checked,
                                 time=datetime.now(timezone.utc).isoformat()))

    def apply_detections(self, names):
        required = {i['class_name'] for i in self.items if i.get('automatic', True)}
        for name in sorted(set(names) & required):
            self.mark(name, 'auto')

    @property
    def missing(self):
        return [i for i in self.items if i['class_name'] not in self.confirmed]

    def finish(self):
        if self.state not in ('SCANNING', 'READY', 'MISSING'):
            raise ValueError('진행 중인 검사가 없습니다.')
        self.state = 'MISSING' if self.missing else 'READY'
        return self.state

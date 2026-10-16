"""사용자별 목록·설정과 당일 제외를 로컬 SQLite에 저장한다."""
import json
import sqlite3
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from contextlib import contextmanager
from config import ITEMS_FILE

DEFAULT_SETTINGS = {'confidence': 0.4, 'roi_x': [15, 85], 'roi_y': [15, 85]}
# yolo11n.pt의 COCO 80종. 저장 키는 모델의 영문 클래스명과 일치해야 한다.
SUPPORTED = dict(zip(
    ('person,bicycle,car,motorcycle,airplane,bus,train,truck,boat,traffic light,fire hydrant,stop sign,parking meter,bench,'
     'bird,cat,dog,horse,sheep,cow,elephant,bear,zebra,giraffe,backpack,umbrella,handbag,tie,suitcase,frisbee,skis,snowboard,'
     'sports ball,kite,baseball bat,baseball glove,skateboard,surfboard,tennis racket,bottle,wine glass,cup,fork,knife,spoon,bowl,'
     'banana,apple,sandwich,orange,broccoli,carrot,hot dog,pizza,donut,cake,chair,couch,potted plant,bed,dining table,toilet,'
     'tv,laptop,mouse,remote,keyboard,cell phone,microwave,oven,toaster,sink,refrigerator,book,clock,vase,scissors,teddy bear,hair drier,toothbrush').split(','),
    ('사람,자전거,자동차,오토바이,비행기,버스,기차,트럭,보트,신호등,소화전,정지 표지판,주차 요금기,벤치,'
     '새,고양이,개,말,양,소,코끼리,곰,얼룩말,기린,백팩,우산,핸드백,넥타이,여행 가방,원반,스키,스노보드,'
     '스포츠 공,연,야구 방망이,야구 글러브,스케이트보드,서핑보드,테니스 라켓,물병,와인잔,컵,포크,칼,숟가락,그릇,'
     '바나나,사과,샌드위치,오렌지,브로콜리,당근,핫도그,피자,도넛,케이크,의자,소파,화분,침대,식탁,변기,'
     'TV,노트북,마우스,리모컨,키보드,휴대폰,전자레인지,오븐,토스터,싱크대,냉장고,책,시계,꽃병,가위,곰 인형,헤어드라이어,칫솔').split(',')))


SUPPORTED.pop('person', None)


def recognition_for_name(name):
    """지원 표시명·영문명·명시적 별칭만 비교한다. 부분 문자열로 추측하지 않는다."""
    normalized = ''.join(name.casefold().split())
    if not normalized:
        return None
    aliases = {'핸드폰': 'cell phone', '스마트폰': 'cell phone', '휴대전화': 'cell phone',
               '생수병': 'bottle', '물통': 'bottle', '배낭': 'backpack',
               '랩탑': 'laptop', '캐리어': 'suitcase', '텔레비전': 'tv',
               '티비': 'tv', '헤어드라이기': 'hair drier', '드라이기': 'hair drier'}
    for key, label in SUPPORTED.items():
        if normalized in (''.join(key.casefold().split()), ''.join(label.casefold().split())):
            return key
    return aliases.get(normalized)


def today():
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


class ProfileStore:
    def __init__(self, path, day=None):
        self.path, self.day = Path(path), day or (lambda: today())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                    settings TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS items (
                    user_id TEXT NOT NULL REFERENCES users(id),
                    class_name TEXT NOT NULL, name TEXT NOT NULL,
                    lcd_name TEXT NOT NULL, automatic INTEGER NOT NULL,
                    position INTEGER NOT NULL, excluded_on TEXT,
                    PRIMARY KEY(user_id, class_name));
                CREATE TABLE IF NOT EXISTS daily_exclusions (
                    user_id TEXT NOT NULL REFERENCES users(id),
                    day TEXT NOT NULL, class_name TEXT NOT NULL,
                    PRIMARY KEY(user_id, day, class_name));
                CREATE TABLE IF NOT EXISTS personal_photos (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, class_name TEXT NOT NULL,
                    jpeg BLOB NOT NULL, digest TEXT NOT NULL,
                    UNIQUE(user_id, class_name, digest),
                    FOREIGN KEY(user_id, class_name) REFERENCES items(user_id, class_name) ON DELETE CASCADE);
            ''')
            db.execute('BEGIN IMMEDIATE')
            if 'active_on' not in {row['name'] for row in db.execute('PRAGMA table_info(items)')}:
                db.execute('ALTER TABLE items ADD COLUMN active_on TEXT')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(items)')}
            for column in ('personal_enabled', 'personal_validated'):
                if column not in columns:
                    db.execute(f'ALTER TABLE items ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0')
            if not db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                self._create(db, '기본 사용자', 'default')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _create(self, db, name, user_id):
        db.execute('INSERT INTO users VALUES (?, ?, ?)',
                   (user_id, name, json.dumps(DEFAULT_SETTINGS)))
        defaults = json.loads(ITEMS_FILE.read_text(encoding='utf-8'))
        for position, item in enumerate(defaults):
            db.execute('INSERT INTO items (user_id,class_name,name,lcd_name,automatic,position,excluded_on) VALUES (?, ?, ?, ?, 1, ?, NULL)',
                       (user_id, item['class_name'], item['name'], item['class_name'].upper(), position))

    def users(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT id, name FROM users ORDER BY rowid')]

    def create_user(self, name):
        name = name.strip()
        if not name or len(name) > 40:
            raise ValueError('사용자 이름을 1~40자로 입력해주세요.')
        user_id = uuid4().hex
        try:
            with self.connect() as db:
                self._create(db, name, user_id)
        except sqlite3.IntegrityError as exc:
            raise ValueError('같은 이름의 사용자가 있습니다.') from exc
        return user_id

    def profile(self, user_id):
        with self.connect() as db:
            user = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
            if user is None:
                raise ValueError('사용자를 찾을 수 없습니다.')
            rows = [dict(row) for row in db.execute(
                'SELECT * FROM items WHERE user_id=? AND (active_on IS NULL OR active_on=?) ORDER BY position', (user_id, self.day()))]
        return {'id': user_id, 'name': user['name'], 'settings': json.loads(user['settings']),
                'items': rows, 'day': self.day()}

    def active_items(self, profile):
        return [row for row in profile['items'] if row['excluded_on'] != profile['day']]

    def add_item(self, user_id, name, lcd_name, class_name=None):
        name, lcd_name = name.strip(), lcd_name.strip().upper()
        if not name or len(name) > 60:
            raise ValueError('준비물 이름을 1~60자로 입력해주세요.')
        if not 1 <= len(lcd_name) <= 16 or any(not (c.isascii() and (c.isalnum() or c in ' _-')) for c in lcd_name):
            raise ValueError('LCD 이름은 영문·숫자·공백·밑줄·하이픈으로 1~16자 입력해주세요.')
        if class_name is not None and class_name not in SUPPORTED:
            raise ValueError('지원하지 않는 자동 인식 종류입니다.')
        key = class_name or 'manual_' + uuid4().hex
        try:
            with self.connect() as db:
                same_name = db.execute('SELECT class_name,active_on FROM items WHERE user_id=? AND name=?', (user_id, name)).fetchone()
                if same_name and not (same_name['class_name'] == key and same_name['active_on'] is not None):
                    raise ValueError('같은 이름의 준비물이 있습니다.')
                position = db.execute('SELECT COALESCE(MAX(position), -1)+1 FROM items WHERE user_id=?', (user_id,)).fetchone()[0]
                existing = db.execute('SELECT active_on FROM items WHERE user_id=? AND class_name=?', (user_id, key)).fetchone()
                if existing and existing['active_on'] is not None:
                    # 날씨로 임시 추가했던 우산을 사용자가 정식 목록에 추가한다.
                    db.execute('UPDATE items SET name=?,lcd_name=?,automatic=?,active_on=NULL,excluded_on=NULL WHERE user_id=? AND class_name=?',
                               (name, lcd_name, int(class_name is not None), user_id, key))
                else:
                    db.execute('INSERT INTO items (user_id,class_name,name,lcd_name,automatic,position,excluded_on) VALUES (?, ?, ?, ?, ?, ?, NULL)',
                               (user_id, key, name, lcd_name, int(class_name is not None), position))
                db.execute('DELETE FROM daily_exclusions WHERE user_id=? AND day=? AND class_name=?', (user_id, self.day(), key))
        except sqlite3.IntegrityError as exc:
            raise ValueError('이미 등록된 자동 인식 종류이거나 사용자가 없습니다.') from exc
        return key

    def delete_item(self, user_id, key):
        with self.connect() as db:
            row = db.execute('SELECT name FROM items WHERE user_id=? AND class_name=?', (user_id, key)).fetchone()
            if row and (key == 'umbrella' or row['name'] == '우산'):
                db.execute('INSERT OR IGNORE INTO daily_exclusions VALUES (?, ?, ?)', (user_id, self.day(), 'umbrella'))
            db.execute('DELETE FROM items WHERE user_id=? AND class_name=?', (user_id, key))

    def exclude(self, user_id, key, excluded=True):
        with self.connect() as db:
            db.execute('UPDATE items SET excluded_on=? WHERE user_id=? AND class_name=?',
                       (self.day() if excluded else None, user_id, key))
            if excluded:
                db.execute('INSERT OR IGNORE INTO daily_exclusions VALUES (?, ?, ?)', (user_id, self.day(), key))
            else:
                db.execute('DELETE FROM daily_exclusions WHERE user_id=? AND day=? AND class_name=?', (user_id, self.day(), key))

    def ensure_weather_umbrella(self, user_id, day):
        if day != self.day():
            return False
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM daily_exclusions WHERE user_id=? AND day=? AND class_name=?',
                          (user_id, day, 'umbrella')).fetchone():
                return False
            row = db.execute('SELECT * FROM items WHERE user_id=? AND (class_name=? OR name=?)',
                             (user_id, 'umbrella', '우산')).fetchone()
            if row:
                if row['excluded_on'] == day or row['active_on'] in (None, day):
                    return False
                db.execute('UPDATE items SET active_on=?,excluded_on=NULL WHERE user_id=? AND class_name=?',
                           (day, user_id, row['class_name']))
            else:
                position = db.execute('SELECT COALESCE(MAX(position),-1)+1 FROM items WHERE user_id=?', (user_id,)).fetchone()[0]
                db.execute('INSERT INTO items (user_id,class_name,name,lcd_name,automatic,position,excluded_on,active_on) VALUES (?, ?, ?, ?, 1, ?, NULL, ?)',
                           (user_id, 'umbrella', '우산', 'UMBRELLA', position, day))
            return True

    def save_weather(self, user_id, settings):
        from weather import validate_settings
        settings = validate_settings(settings)
        with self.connect() as db:
            existing = json.loads(db.execute('SELECT settings FROM users WHERE id=?', (user_id,)).fetchone()[0])
            existing['weather'] = settings
            db.execute('UPDATE users SET settings=? WHERE id=?', (json.dumps(existing), user_id))

    def save_settings(self, user_id, settings):
        if not 0.05 <= settings['confidence'] <= 0.95:
            raise ValueError('신뢰도 범위가 올바르지 않습니다.')
        for axis in ('roi_x', 'roi_y'):
            low, high = settings[axis]
            if not 0 <= low < high <= 100:
                raise ValueError('검사 구역의 폭은 0보다 커야 합니다.')
        with self.connect() as db:
            existing = json.loads(db.execute('SELECT settings FROM users WHERE id=?', (user_id,)).fetchone()[0])
            existing.update({key: settings[key] for key in DEFAULT_SETTINGS})
            db.execute('UPDATE users SET settings=? WHERE id=?',
                       (json.dumps(existing), user_id))

    def register_personal(self, user_id, name, lcd_name, photos):
        from personal import prepare_reference
        name, lcd_name = name.strip(), lcd_name.strip().upper()
        if not name or len(name) > 60:
            raise ValueError('물건 이름을 1~60자로 입력해주세요.')
        if not 1 <= len(lcd_name) <= 16 or any(not (c.isascii() and (c.isalnum() or c in ' _-')) for c in lcd_name):
            raise ValueError('LCD 이름은 영문·숫자·공백·밑줄·하이픈 1~16자입니다.')
        if not 1 <= len(photos) <= 8:
            raise ValueError('등록 사진은 1~8장을 선택해주세요.')
        prepared = [prepare_reference(raw) for raw in photos]
        if len({hashlib.sha256(raw).hexdigest() for raw in prepared}) != len(prepared):
            raise ValueError('같은 사진이 중복됐습니다. 다른 각도의 사진을 선택해주세요.')
        key = 'personal_' + uuid4().hex
        with self.connect() as db:
            if db.execute('SELECT 1 FROM items WHERE user_id=? AND name=?', (user_id, name)).fetchone():
                raise ValueError('같은 이름의 준비물이 있습니다.')
            position = db.execute('SELECT COALESCE(MAX(position),-1)+1 FROM items WHERE user_id=?', (user_id,)).fetchone()[0]
            db.execute('INSERT INTO items (user_id,class_name,name,lcd_name,automatic,position) VALUES (?, ?, ?, ?, 0, ?)',
                       (user_id, key, name, lcd_name, position))
            for raw in prepared:
                db.execute('INSERT INTO personal_photos VALUES (?, ?, ?, ?, ?)',
                           (uuid4().hex, user_id, key, raw, hashlib.sha256(raw).hexdigest()))
        return key

    def reference_photos(self, user_id, key=None, active_only=False):
        sql = 'SELECT p.* FROM personal_photos p JOIN items i ON p.user_id=i.user_id AND p.class_name=i.class_name WHERE p.user_id=?'
        params = [user_id]
        if key is not None:
            sql += ' AND p.class_name=?'
            params.append(key)
        if active_only:
            sql += ' AND i.personal_enabled=1 AND (i.excluded_on IS NULL OR i.excluded_on<>?) AND (i.active_on IS NULL OR i.active_on=?)'
            params.extend((self.day(), self.day()))
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql + ' ORDER BY p.rowid', params)]

    def add_reference_photos(self, user_id, key, photos):
        from personal import prepare_reference
        if not photos or len(photos) > 8:
            raise ValueError('사진은 1~8장을 선택해주세요.')
        prepared = [prepare_reference(raw) for raw in photos]
        try:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT * FROM items WHERE user_id=? AND class_name=?', (user_id, key)).fetchone()
                if row is None or not key.startswith(('personal_', 'manual_')):
                    raise ValueError('개인 물건을 찾을 수 없습니다.')
                count = db.execute('SELECT COUNT(*) FROM personal_photos WHERE user_id=? AND class_name=?', (user_id, key)).fetchone()[0]
                if count + len(prepared) > 8:
                    raise ValueError('물건 하나에 사진은 최대 8장입니다.')
                for raw in prepared:
                    db.execute('INSERT INTO personal_photos VALUES (?, ?, ?, ?, ?)',
                               (uuid4().hex, user_id, key, raw, hashlib.sha256(raw).hexdigest()))
                db.execute('UPDATE items SET automatic=0,personal_enabled=0,personal_validated=0 WHERE user_id=? AND class_name=?', (user_id, key))
        except sqlite3.IntegrityError:
            raise ValueError('이미 등록한 사진입니다. 다른 사진을 선택해주세요.') from None

    def delete_reference_photo(self, user_id, key, photo_id):
        with self.connect() as db:
            removed = db.execute('DELETE FROM personal_photos WHERE user_id=? AND class_name=? AND id=?', (user_id, key, photo_id)).rowcount
            if removed:
                db.execute('UPDATE items SET automatic=0,personal_enabled=0,personal_validated=0 WHERE user_id=? AND class_name=?', (user_id, key))

    def record_personal_test(self, user_id, key, matched):
        with self.connect() as db:
            db.execute('UPDATE items SET personal_validated=? WHERE user_id=? AND class_name=? AND EXISTS (SELECT 1 FROM personal_photos p WHERE p.user_id=items.user_id AND p.class_name=items.class_name)',
                       (int(matched), user_id, key))
            if not matched and key.startswith(('personal_', 'manual_')):
                db.execute('UPDATE items SET automatic=0,personal_enabled=0 WHERE user_id=? AND class_name=?', (user_id, key))

    def enable_personal(self, user_id, key, enabled):
        with self.connect() as db:
            row = db.execute('SELECT personal_validated FROM items WHERE user_id=? AND class_name=?', (user_id, key)).fetchone()
            if row is None or not key.startswith(('personal_', 'manual_')):
                raise ValueError('개인 물건을 찾을 수 없습니다.')
            if enabled and (not row['personal_validated'] or not db.execute('SELECT 1 FROM personal_photos WHERE user_id=? AND class_name=?', (user_id, key)).fetchone()):
                raise ValueError('등록 사진으로 인식 시험을 먼저 통과해주세요.')
            db.execute('UPDATE items SET automatic=?,personal_enabled=? WHERE user_id=? AND class_name=?',
                       (int(enabled), int(enabled), user_id, key))

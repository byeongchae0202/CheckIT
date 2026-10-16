"""기상청 공개 API 조회. 인증키와 요청 URL은 오류·결과에 포함하지 않는다."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time
from urllib.parse import unquote, urlencode
from urllib.request import urlopen

KST = timezone(timedelta(hours=9))
DEFAULT_WEATHER = {'enabled': False, 'region_name': '', 'nx': 60, 'ny': 127,
                   'mode': 'either', 'use_pop': True, 'pop_threshold': 60, 'region_code': ''}
MODES = {'current': '현재 비', 'forecast': '오늘 남은 시간 비 예보', 'either': '현재 비 또는 오늘 비 예보'}


class WeatherError(Exception):
    pass


class NoForecast(WeatherError):
    pass


def validate_settings(settings):
    result = dict(DEFAULT_WEATHER, **settings)
    if result['region_code']:
        from regions import load_regions, RegionIndex
        record = load_regions().by_code.get(result['region_code'])
        if record is None:
            raise ValueError('선택한 지역을 찾을 수 없습니다. 지역을 다시 선택해주세요.')
        result.update(region_name=RegionIndex.name(record), nx=record['nx'], ny=record['ny'])
    result['region_name'] = result['region_name'].strip()
    if len(result['region_name']) > 60 or (result['enabled'] and not result['region_name']):
        raise ValueError('조회 지역 이름을 1~60자로 입력해주세요.')
    if not isinstance(result['nx'], int) or not 1 <= result['nx'] <= 149:
        raise ValueError('예보 격자 X는 1~149입니다.')
    if not isinstance(result['ny'], int) or not 1 <= result['ny'] <= 253:
        raise ValueError('예보 격자 Y는 1~253입니다.')
    if result['mode'] not in MODES or not 10 <= result['pop_threshold'] <= 100:
        raise ValueError('비 판단 기준 또는 강수확률이 올바르지 않습니다.')
    return {key: result[key] for key in DEFAULT_WEATHER}


def publication(now, forecast=False):
    # 발표 직후 자료 생성 지연을 피하기 위해 15분 여유를 둔다.
    available = now.astimezone(KST) - timedelta(minutes=15)
    if not forecast:
        return available.replace(minute=0, second=0, microsecond=0)
    candidates = [available.replace(hour=h, minute=0, second=0, microsecond=0)
                  for h in (2, 5, 8, 11, 14, 17, 20, 23)]
    previous = (available - timedelta(days=1)).replace(hour=23, minute=0, second=0, microsecond=0)
    return max([previous] + [candidate for candidate in candidates if candidate <= available])


def current_rain(rows):
    for row in rows:
        if row.get('category') == 'PTY':
            try:
                value = int(row['obsrValue'])
            except (KeyError, ValueError, TypeError):
                break
            if value not in (0, 1, 2, 3, 5, 6, 7):
                break
            return value in (1, 2, 5, 6)
    raise WeatherError('현재 강수형태 자료가 없거나 올바르지 않습니다.')


def forecast_details(rows, now, use_pop, threshold):
    date, hour = now.astimezone(KST).strftime('%Y%m%d'), now.astimezone(KST).hour
    slots = {}
    for row in rows:
        if row.get('category') not in ('PTY', 'POP') or row.get('fcstDate') != date:
            continue
        try:
            stamp = str(row['fcstTime'])
            if len(stamp) != 4 or stamp[2:] != '00':
                raise ValueError
            slot = int(stamp[:2])
            value = int(row['fcstValue'])
            if not 0 <= slot <= 23:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise WeatherError('예보 시간 또는 강수 자료 형식이 올바르지 않습니다.') from None
        if slot >= hour:
            slots.setdefault(slot, {})[row['category']] = value
    if not slots:
        raise NoForecast('오늘 남은 시간의 예보 자료가 없습니다.')
    # 일부 시간만 내려온 자료를 '비 없음'으로 해석하지 않는다.
    if min(slots) > min(hour + 1, 23) or set(slots) != set(range(min(slots), 24)):
        raise WeatherError('오늘 예보 시간대의 자료가 불완전합니다.')
    details = []
    for hour, values in sorted(slots.items()):
        if values.get('PTY') not in (0, 1, 2, 3, 4):
            raise WeatherError('예보 강수형태 자료가 없거나 올바르지 않습니다.')
        if use_pop and ('POP' not in values or not 0 <= values['POP'] <= 100):
            raise WeatherError('강수확률 자료가 없거나 올바르지 않습니다.')
        probability = values.get('POP')
        if probability is not None and not 0 <= probability <= 100:
            probability = None
        details.append(ForecastHour(hour, values['PTY'] in (1, 2, 4), probability))
    return tuple(details)


def forecast_rain(rows, now, use_pop, threshold):
    return any(slot.rain or (use_pop and slot.probability is not None and slot.probability >= threshold)
               for slot in forecast_details(rows, now, use_pop, threshold))


@dataclass(frozen=True)
class ForecastHour:
    hour: int
    rain: bool
    probability: int | None


@dataclass(frozen=True)
class WeatherResult:
    rain: bool
    checked_at: str
    references: tuple
    current_rain: bool | None = None
    forecast: tuple = ()


def format_hours(hours):
    groups = []
    for hour in sorted(set(hours)):
        if groups and hour == groups[-1][1] + 1:
            groups[-1][1] = hour
        else:
            groups.append([hour, hour])
    return ', '.join(f'{start}시' if start == end else f'{start}~{end}시'
                     for start, end in groups[:3]) + (' 등' if len(groups) > 3 else '')


def weather_message(result, settings, now=None):
    now = (now or datetime.now(KST)).astimezone(KST)
    if result.checked_at[:10] != now.date().isoformat():
        return '날짜가 바뀌었어요. 오늘 날씨를 다시 확인해주세요.'
    forecast = getattr(result, 'forecast', ())
    observed = getattr(result, 'current_rain', None)
    slots = [slot for slot in forecast if slot.hour >= now.hour]
    rain_hours = [slot.hour for slot in slots if slot.rain]
    probable = [slot for slot in slots if slot.probability is not None and slot.probability >= settings['pop_threshold']]
    parts = []
    if observed:
        parts.append('현재 관측자료에 비가 있어요.')
    if rain_hours:
        parts.append('오늘 ' + format_hours(rain_hours) + ' 비 예보가 있어요.')
    elif probable and settings['use_pop']:
        parts.append('오늘 ' + format_hours(slot.hour for slot in probable) +
                     f' 강수확률이 최대 {max(slot.probability for slot in probable)}%예요.')
    if parts:
        return ' '.join(parts) + ' 우산도 잊지 마세요.'
    if result.rain and not forecast and observed is None:
        return '설정한 비 조건에 해당해요. 우산도 잊지 마세요.'
    if slots:
        probabilities = [slot.probability for slot in slots if slot.probability is not None]
        if probabilities and max(probabilities) >= settings['pop_threshold']:
            return f'오늘 남은 시간의 강수확률은 최대 {max(probabilities)}%예요.'
        return '오늘 남은 시간에 비 예보가 없어요.' + (f' 최대 강수확률은 {max(probabilities)}%예요.' if probabilities else '')
    if forecast:
        return '조회한 예보 시간이 지났어요. 날씨를 새로고침해주세요.'
    if observed is None:
        return '조회한 자료는 설정한 우산 추가 조건에 해당하지 않아요.'
    return '현재 관측자료에 비는 없어요. 오늘 비 예보는 조회하지 않았어요.'


class WeatherClient:
    def __init__(self, transport=None):
        self.transport = transport or self._open

    @staticmethod
    def _open(url):
        with urlopen(url, timeout=8) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise WeatherError('기상청 응답이 너무 큽니다.')
        return raw

    def _rows(self, operation, key, settings, base):
        rows = []
        for page in range(1, 9):
            query = urlencode({'serviceKey': unquote(key.strip()), 'pageNo': page,
                               'numOfRows': 1000, 'dataType': 'JSON',
                               'base_date': base.strftime('%Y%m%d'), 'base_time': base.strftime('%H%M'),
                               'nx': settings['nx'], 'ny': settings['ny']})
            url = 'https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/' + operation + '?' + query
            try:
                payload = json.loads(self.transport(url))['response']
                code = str(payload['header']['resultCode'])
                if code not in ('00', '0'):
                    # 제공기관 메시지에는 요청 내용이 섞일 수 있어 출력하지 않는다.
                    raise WeatherError('기상청 API 오류입니다. 인증키·활용 승인·호출 한도와 자료 제공 시각을 확인해주세요.')
                body = payload['body']
                batch = body['items']['item']
                if isinstance(batch, dict):
                    batch = [batch]
                total = int(body['totalCount'])
                if not isinstance(batch, list) or not batch or total <= 0:
                    raise ValueError
                for row in batch:
                    if not isinstance(row, dict) or row.get('baseDate') != base.strftime('%Y%m%d') or row.get('baseTime') != base.strftime('%H%M'):
                        raise ValueError
                    if int(row['nx']) != settings['nx'] or int(row['ny']) != settings['ny']:
                        raise ValueError
                rows.extend(batch)
                if len(rows) >= total:
                    return rows
            except WeatherError:
                raise
            except (ValueError, KeyError, TypeError):
                raise WeatherError('기상청 응답이 비어 있거나 형식·지역·발표 시각이 올바르지 않습니다. 인증키와 조회 설정을 확인해주세요.') from None
            except Exception:
                raise WeatherError('날씨 서버에 연결하지 못했거나 응답 시간이 초과됐습니다. 잠시 후 다시 시도해주세요.') from None
        raise WeatherError('예보 자료를 모두 받지 못했습니다.')

    def fetch(self, settings, key, now=None):
        settings = validate_settings(settings)
        now = (now or datetime.now(KST)).astimezone(KST)
        if not key.strip():
            raise WeatherError('기상청 인증키를 설정해주세요.')
        decisions, references = [], []
        observed, details = None, ()
        if settings['mode'] in ('current', 'either'):
            base = publication(now)
            observed = current_rain(self._rows('getUltraSrtNcst', key, settings, base))
            decisions.append(observed)
            references.append('실황 ' + base.strftime('%m/%d %H:%M'))
        if settings['mode'] in ('forecast', 'either'):
            base = publication(now, forecast=True)
            try:
                details = forecast_details(self._rows('getVilageFcst', key, settings, base), now,
                                         settings['use_pop'], settings['pop_threshold'])
            except NoForecast:
                # 23시 발표는 다음 날부터 시작할 수 있어 직전 발표를 확인한다.
                base -= timedelta(hours=3)
                details = forecast_details(self._rows('getVilageFcst', key, settings, base), now,
                                         settings['use_pop'], settings['pop_threshold'])
            decisions.append(any(slot.rain or (settings['use_pop'] and slot.probability is not None and slot.probability >= settings['pop_threshold']) for slot in details))
            references.append('예보 ' + base.strftime('%m/%d %H:%M'))
        return WeatherResult(any(decisions), now.isoformat(), tuple(references), observed, details)


def request_token(user_id, day, settings, key):
    return (user_id, day, json.dumps(settings, sort_keys=True), hashlib.sha256(key.encode()).hexdigest())


class WeatherRunner:
    """HTTP 대기로 카메라·체크리스트가 멈추지 않도록 백그라운드 조회한다."""
    def __init__(self, executor=None):
        self.executor = executor
        self.future = None
        self.job_token = None
        self.token = None
        self.result = None
        self.error = ''
        self.next_due = 0

    def tick(self, token, fetch, force=False, now=None):
        now = time.monotonic() if now is None else now
        if token != self.token:
            self.token, self.result, self.error, self.next_due = token, None, '', 0
        if self.future is not None and self.future.done():
            if self.job_token == token and token is not None:
                try:
                    self.result = self.future.result()
                    self.error = ''
                    self.next_due = now + 600
                except WeatherError as exc:
                    self.result = None
                    self.error = str(exc)
                    self.next_due = now + 60
                except Exception:
                    self.result = None
                    self.error = '날씨 조회 처리에 실패했습니다. 다시 시도해주세요.'
                    self.next_due = now + 60
            self.future = None
        if token is not None and self.future is None and (force or now >= self.next_due):
            if self.executor is None:
                self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='checkit-weather')
            self.job_token = token
            self.future = self.executor.submit(fetch)
            self.result, self.error = None, ''
        return self.result, self.error, self.future is not None

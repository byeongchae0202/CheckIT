from concurrent.futures import Future
from datetime import datetime
import json
import tempfile
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlparse
from weather import (DEFAULT_WEATHER, KST, WeatherClient, WeatherError, WeatherResult,
                     WeatherRunner, current_rain, forecast_rain, publication, request_token,
                     ForecastHour, weather_message, format_hours)
from profiles import ProfileStore


def forecast_rows(date='20261016', start=10, rain_hour=None, probability=0):
    return [dict(category=category, fcstDate=date, fcstTime=f'{hour:02d}00', fcstValue=str(value))
            for hour in range(start, 24)
            for category, value in [('PTY', 1 if hour == rain_hour else 0), ('POP', probability)]]


class WeatherTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 16, 10, 30, tzinfo=KST)

    def test_publications_cross_midnight_and_delay(self):
        early = datetime(2026, 10, 16, 0, 5, tzinfo=KST)
        self.assertEqual(publication(early).strftime('%Y%m%d%H%M'), '202610152300')
        self.assertEqual(publication(early, True).strftime('%Y%m%d%H%M'), '202610152300')
        boundary = datetime(2026, 10, 16, 2, 10, tzinfo=KST)
        self.assertEqual(publication(boundary, True).hour, 23)
        self.assertEqual(publication(boundary.replace(minute=20), True).hour, 2)

    def test_brief_message_groups_hours_and_distinguishes_probability(self):
        result = WeatherResult(True, self.now.isoformat(), (), False,
            tuple(ForecastHour(hour, True, 70) for hour in (15, 16, 17, 20)))
        self.assertEqual(weather_message(result, DEFAULT_WEATHER, self.now),
                         '오늘 15~17시, 20시 비 예보가 있어요. 우산도 잊지 마세요.')
        result = WeatherResult(True, self.now.isoformat(), (), False, (ForecastHour(15, False, 80),))
        message = weather_message(result, DEFAULT_WEATHER, self.now)
        self.assertIn('15시 강수확률이 최대 80%', message)
        self.assertNotIn('비 예보가 있어요', message)
        self.assertEqual(format_hours([15, 15, 16, 20]), '15~16시, 20시')

    def test_message_does_not_show_past_or_unqueried_forecasts(self):
        result = WeatherResult(True, self.now.isoformat(), (), False, (ForecastHour(9, True, 80), ForecastHour(11, False, 0)))
        self.assertNotIn('9시', weather_message(result, DEFAULT_WEATHER, self.now))
        self.assertIn('비 예보가 없어요', weather_message(result, DEFAULT_WEATHER, self.now))
        current_only = WeatherResult(False, self.now.isoformat(), (), False)
        self.assertIn('비 예보는 조회하지 않았어요', weather_message(current_only, DEFAULT_WEATHER, self.now))
        self.assertIn('날짜가 바뀌었어요', weather_message(result, DEFAULT_WEATHER, self.now.replace(day=17)))

    def test_current_and_forecast_rules(self):
        self.assertTrue(current_rain([{'category': 'PTY', 'obsrValue': '2'}]))
        self.assertFalse(current_rain([{'category': 'PTY', 'obsrValue': '3'}]))
        self.assertTrue(forecast_rain(forecast_rows(rain_hour=18), self.now, False, 60))
        self.assertTrue(forecast_rain(forecast_rows(probability=60), self.now, True, 60))
        self.assertFalse(forecast_rain(forecast_rows(probability=50), self.now, True, 60))
        past = forecast_rows(start=0, rain_hour=2)
        self.assertFalse(forecast_rain(past, self.now, False, 60))

    def test_missing_or_invalid_data_is_failure(self):
        for rows in ([], [{'category': 'PTY', 'obsrValue': '-999'}]):
            with self.assertRaises(WeatherError):
                current_rain(rows)
        for rows in ([], forecast_rows()[:-2], forecast_rows(start=20), [r for r in forecast_rows() if r['category'] == 'PTY']):
            with self.assertRaises(WeatherError):
                forecast_rain(rows, self.now, True, 60)

    def test_client_validates_response_and_does_not_expose_key(self):
        requests = []
        def transport(url):
            requests.append(url)
            query = parse_qs(urlparse(url).query)
            rows = forecast_rows(rain_hour=18) if 'getVilageFcst' in url else [dict(category='PTY', obsrValue='0')]
            for row in rows:
                row.update(baseDate=query['base_date'][0], baseTime=query['base_time'][0], nx=60, ny=127)
            return json.dumps({'response': {'header': {'resultCode': '00'},
                'body': {'totalCount': len(rows), 'items': {'item': rows}}}}).encode()
        result = WeatherClient(transport).fetch(DEFAULT_WEATHER, 'demo%2Bkey%3D', self.now)
        self.assertTrue(result.rain)
        self.assertEqual([slot.hour for slot in result.forecast if slot.rain], [18])
        self.assertEqual(len(requests), 2)
        self.assertEqual(parse_qs(urlparse(requests[0]).query)['serviceKey'], ['demo+key='])
        client = WeatherClient(lambda url: json.dumps({'response': {'header': {'resultCode': '30', 'resultMsg': 'demo-secret'}}}))
        with self.assertRaises(WeatherError) as failure:
            client.fetch(DEFAULT_WEATHER, 'demo-secret', self.now)
        self.assertNotIn('demo-secret', str(failure.exception))
        def broken(url):
            raise OSError(url)
        with self.assertRaises(WeatherError) as failure:
            WeatherClient(broken).fetch(DEFAULT_WEATHER, 'demo-secret', self.now)
        self.assertNotIn('demo-secret', str(failure.exception))

    def test_wrong_grid_and_truncated_pages_fail(self):
        def transport(url):
            query = parse_qs(urlparse(url).query)
            row = dict(category='PTY', obsrValue='0', nx=1, ny=1,
                       baseDate=query['base_date'][0], baseTime=query['base_time'][0])
            return json.dumps({'response': {'header': {'resultCode': '00'},
                'body': {'totalCount': 1, 'items': {'item': [row]}}}})
        with self.assertRaises(WeatherError):
            WeatherClient(transport).fetch(DEFAULT_WEATHER, 'demo', self.now)
        def truncated(url):
            query = parse_qs(urlparse(url).query)
            rows = [dict(category='PTY', obsrValue='0', nx=60, ny=127,
                         baseDate=query['base_date'][0], baseTime=query['base_time'][0])] if query['pageNo'] == ['1'] else []
            return json.dumps({'response': {'header': {'resultCode': '00'},
                'body': {'totalCount': 2, 'items': {'item': rows}}}})
        with self.assertRaises(WeatherError):
            WeatherClient(truncated).fetch(DEFAULT_WEATHER, 'demo', self.now)

    def test_late_forecast_uses_previous_issue(self):
        calls = []
        now = self.now.replace(hour=23, minute=40)
        def transport(url):
            query = parse_qs(urlparse(url).query)
            base = query['base_time'][0]
            calls.append(base)
            rows = forecast_rows(date='20261017', start=0) if base == '2300' else forecast_rows(start=23, rain_hour=23)
            for row in rows:
                row.update(baseDate=query['base_date'][0], baseTime=base, nx=60, ny=127)
            return json.dumps({'response': {'header': {'resultCode': '00'},
                'body': {'totalCount': len(rows), 'items': {'item': rows}}}})
        result = WeatherClient(transport).fetch(dict(DEFAULT_WEATHER, mode='forecast'), 'demo', now)
        self.assertTrue(result.rain)
        self.assertEqual(calls, ['2300', '2000'])


class ManualExecutor:
    def __init__(self):
        self.jobs = []

    def submit(self, fetch):
        future = Future()
        self.jobs.append((future, fetch))
        return future


class RunnerTests(unittest.TestCase):
    def test_result_expiry_and_stale_user_response(self):
        executor = ManualExecutor()
        runner = WeatherRunner(executor)
        good = WeatherResult(True, '2026-10-16T10:30:00+09:00', ())
        runner.tick('user1', lambda: good, now=0)
        runner.tick('user2', lambda: good, now=1)
        executor.jobs[0][0].set_result(good)
        result, error, loading = runner.tick('user2', lambda: good, now=2)
        self.assertIsNone(result)
        self.assertTrue(loading)
        executor.jobs[1][0].set_result(good)
        self.assertEqual(runner.tick('user2', lambda: good, now=3)[0], good)
        self.assertFalse(runner.tick('user2', lambda: good, now=500)[2])
        self.assertTrue(runner.tick('user2', lambda: good, now=604)[2])

    def test_failure_never_becomes_no_rain(self):
        executor = ManualExecutor()
        runner = WeatherRunner(executor)
        runner.tick('user', lambda: None, now=0)
        executor.jobs[0][0].set_exception(WeatherError('연결 실패'))
        result, error, loading = runner.tick('user', lambda: None, now=1)
        self.assertIsNone(result)
        self.assertEqual(error, '연결 실패')
        self.assertFalse(loading)
        self.assertFalse(runner.tick('user', lambda: None, now=30)[2])
        self.assertTrue(runner.tick('user', lambda: None, now=62)[2])
        self.assertNotIn('secret', repr(request_token('user', 'date', {}, 'secret')))


class UmbrellaTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.day = ['2026-10-15']
        self.store = ProfileStore(Path(directory.name) / 'data.sqlite3', day=lambda: self.day[0])
        self.store.delete_item('default', 'umbrella')
        self.day[0] = '2026-10-16'

    def test_daily_umbrella_dedup_delete_and_next_day(self):
        self.assertTrue(self.store.ensure_weather_umbrella('default', self.day[0]))
        self.assertFalse(self.store.ensure_weather_umbrella('default', self.day[0]))
        self.store.delete_item('default', 'umbrella')
        self.assertFalse(self.store.ensure_weather_umbrella('default', self.day[0]))
        self.assertEqual(len(self.store.active_items(self.store.profile('default'))), 2)
        self.day[0] = '2026-10-17'
        self.assertEqual(len(self.store.active_items(self.store.profile('default'))), 2)
        self.assertFalse(self.store.ensure_weather_umbrella('default', '2026-10-16'))
        self.assertTrue(self.store.ensure_weather_umbrella('default', self.day[0]))

    def test_delete_suppresses_readd_and_explicit_add_is_permanent(self):
        self.store.ensure_weather_umbrella('default', self.day[0])
        self.store.delete_item('default', 'umbrella')
        self.assertFalse(self.store.ensure_weather_umbrella('default', self.day[0]))
        self.day[0] = '2026-10-17'
        self.store.ensure_weather_umbrella('default', self.day[0])
        self.store.add_item('default', '우산', 'UMBRELLA', 'umbrella')
        self.day[0] = '2026-10-18'
        self.assertEqual(len(self.store.active_items(self.store.profile('default'))), 3)

    def test_weather_and_camera_settings_do_not_overwrite_each_other(self):
        self.store.save_weather('default', dict(DEFAULT_WEATHER, enabled=True, region_name='내 지역'))
        self.store.save_settings('default', {'confidence': .6, 'roi_x': [10, 80], 'roi_y': [20, 90]})
        settings = self.store.profile('default')['settings']
        self.assertTrue(settings['weather']['enabled'])
        self.assertEqual(settings['confidence'], .6)
        self.assertNotIn('serviceKey', json.dumps(settings))


if __name__ == '__main__':
    unittest.main()

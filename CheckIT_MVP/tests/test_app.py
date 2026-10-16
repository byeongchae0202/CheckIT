import importlib.util
from pathlib import Path
from types import SimpleNamespace
from datetime import date
import time
import unittest
import tempfile
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('streamlit') and importlib.util.find_spec('streamlit_webrtc'),
                     '웹앱 의존성 미설치')
class AppTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db_path = Path(directory.name) / 'profiles.sqlite3'
        setting = patch('config.PROFILE_DB', self.db_path)
        setting.start()
        self.addCleanup(setting.stop)
        key_setting = patch('config.get_weather_key', return_value='')
        key_setting.start()
        self.addCleanup(key_setting.stop)
        camera_setting = patch('camera.usb_device', return_value={'id': 'test-usb', 'label': 'USB CAMERA'})
        camera_setting.start()
        self.addCleanup(camera_setting.stop)

    def test_video_uses_selected_usb_camera_and_waits_when_missing(self):
        from streamlit.testing.v1 import AppTest
        context = SimpleNamespace(state=SimpleNamespace(playing=False))
        with patch('streamlit_webrtc.webrtc_streamer', return_value=context) as streamer:
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run()
            self.assertFalse(app.exception)
            options = streamer.call_args.kwargs
            self.assertEqual(options['media_stream_constraints']['video']['deviceId'], {'exact': 'test-usb'})
            self.assertTrue(options['desired_playing_state'])
            app.checkbox(key='camera_enabled').uncheck().run()
            self.assertFalse(streamer.call_args.kwargs['desired_playing_state'])
            with patch('camera.usb_device', return_value=None):
                app.checkbox(key='camera_enabled').check().run()
                self.assertFalse(streamer.call_args.kwargs['desired_playing_state'])
                self.assertFalse(app.session_state.live.enabled)
            self.assertFalse(app.exception)

    def test_personal_enable_detection_and_deletion(self):
        from streamlit.testing.v1 import AppTest
        from profiles import ProfileStore
        from personal_fixtures import images
        store = ProfileStore(self.db_path)
        key = store.register_personal('default', '내 지갑', 'MY WALLET', [images()[0]])
        context = SimpleNamespace(state=SimpleNamespace(playing=False))
        with patch('streamlit_webrtc.webrtc_streamer', return_value=context):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run()
            app.selectbox(key='personal_manage_default').select(key).run()
            self.assertTrue(app.button(key='enable_personal_' + key).disabled)
            store.record_personal_test('default', key, True)
            app.run()
            app.button(key='enable_personal_' + key).click().run()
            self.assertIsNotNone(app.session_state.detector.personal)
            context.state.playing = True
            app.session_state.live.pending = {key}
            app.session_state.live.last_frame = time.monotonic()
            app.run()
            self.assertEqual(app.session_state.checklist.confirmed[key], 'auto')
            app.button(key='delete_personal_' + key).click().run()
            self.assertFalse(store.reference_photos('default', key))
            self.assertNotIn(key, app.session_state.checklist.confirmed)
            self.assertFalse(app.exception)

    def test_user_crud_preferences_and_day_change(self):
        from streamlit.testing.v1 import AppTest
        from profiles import ProfileStore
        context = SimpleNamespace(state=SimpleNamespace(playing=False))
        with patch('streamlit_webrtc.webrtc_streamer', return_value=context):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run()
            app.checkbox(key='mark_book').check().run()
            app.text_input(key='new_item_name').set_value('지갑')
            app.text_input(key='new_lcd_name').set_value('WALLET')
            next(b for b in app.button if b.label == '준비물 추가').click().run()
            self.assertEqual(len(app.session_state.checklist.items), 4)
            self.assertIn('book', app.session_state.checklist.confirmed)
            app.button(key='exclude_umbrella').click().run()
            self.assertEqual(len(app.session_state.checklist.items), 3)
            app.slider(key='confidence').set_value(.65).run()
            self.assertEqual(app.session_state.live.region, (0.0, 0.0, 1.0, 1.0))
            self.assertFalse(any('검사 범위' in slider.label for slider in app.slider))
            app.text_input(key='new_user_name').set_value('민수')
            next(b for b in app.button if b.label == '사용자 만들기').click().run()
            self.assertEqual(app.session_state.profile['name'], '민수')
            self.assertFalse(app.session_state.checklist.confirmed)
            self.assertEqual(app.slider(key='confidence').value, .4)
            app.selectbox(key='user_id').select('default').run()
            self.assertEqual(app.slider(key='confidence').value, .65)
            self.assertEqual(app.session_state.live.region, (0.0, 0.0, 1.0, 1.0))
            self.assertEqual(len(app.session_state.checklist.items), 3)
            app.button(key='new_check').click().run()
            self.assertEqual(len(app.session_state.checklist.items), 3)
            with patch('profiles.today', return_value='2099-01-02'):
                app.run()
                self.assertEqual(len(app.session_state.checklist.items), 3)
                self.assertFalse(app.session_state.checklist.confirmed)
            app.button(key='delete_book').click().run()
            self.assertNotIn('book', {row['class_name'] for row in app.session_state.checklist.items})
            self.assertFalse(app.exception)

    def test_future_item_is_shown_only_on_selected_day(self):
        from streamlit.testing.v1 import AppTest
        from profiles import ProfileStore
        day = ['2026-10-16']
        context = SimpleNamespace(state=SimpleNamespace(playing=False))
        with patch('profiles.today', side_effect=lambda: day[0]), patch('streamlit_webrtc.webrtc_streamer', return_value=context):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run()
            app.date_input(key='new_item_date_default').set_value(date(2026, 10, 18)).run()
            app.text_input(key='new_item_name').set_value('bicycle').run()
            self.assertFalse(any('YOLO 지원 목록에 있어요' in element.value for element in app.success))
            self.assertFalse(any('일치하는 YOLO 지원 종류를 찾지 못했어요' in element.value for element in app.info))
            app.text_input(key='new_item_name').set_value('여권')
            app.text_input(key='new_lcd_name').set_value('PASSPORT')
            next(button for button in app.button if button.label == '준비물 추가').click().run()
            self.assertFalse(app.exception)

            app.text_input(key='new_item_name').set_value('충전기')
            app.text_input(key='new_lcd_name').set_value('CHARGER')
            next(button for button in app.button if button.label == '준비물 추가').click().run()

            store = ProfileStore(self.db_path)
            scheduled = store.scheduled_items('default', '2026-10-18')
            self.assertEqual({row['name'] for row in scheduled}, {'여권', '충전기'})
            scheduled_keys = {row['class_name'] for row in scheduled}
            self.assertTrue(scheduled_keys.isdisjoint({row['class_name'] for row in app.session_state.checklist.items}))

            day[0] = '2026-10-17'
            app.run()
            self.assertTrue(scheduled_keys.isdisjoint({row['class_name'] for row in app.session_state.checklist.items}))
            day[0] = '2026-10-18'
            app.run()
            self.assertTrue(scheduled_keys.issubset({row['class_name'] for row in app.session_state.checklist.items}))
            self.assertFalse(app.exception)

    def test_weather_adds_today_only_and_failure_is_visible(self):
        from datetime import date, timedelta
        from streamlit.testing.v1 import AppTest
        from profiles import ProfileStore, today
        from weather import DEFAULT_WEATHER, WeatherResult, ForecastHour
        store = ProfileStore(self.db_path, day=lambda: (date.fromisoformat(today()) - timedelta(days=1)).isoformat())
        store.delete_item('default', 'umbrella')
        store.save_weather('default', dict(DEFAULT_WEATHER, enabled=True, region_name='테스트 지역'))
        class Runner:
            result = WeatherResult(True, today() + 'T10:00:00+09:00', ('실황 10:00',), False, (ForecastHour(23, True, 70),))
            error = ''
            def tick(self, token, fetch, **kwargs):
                return self.result, self.error, False
        runner = Runner()
        context = SimpleNamespace(state=SimpleNamespace(playing=False))
        with patch('streamlit_webrtc.webrtc_streamer', return_value=context), patch('config.get_weather_key', return_value='test-placeholder'):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'))
            app.session_state.weather_runner = runner
            app.run()
            self.assertTrue(any('오늘 23시 비 예보가 있어요' in info.value for info in app.info))
            self.assertIn('umbrella', {item['class_name'] for item in app.session_state.checklist.items})
            self.assertEqual(next(item for item in app.session_state.profile['items'] if item['class_name'] == 'umbrella')['active_on'], today())
            app.button(key='exclude_umbrella').click().run()
            runner.result = WeatherResult(True, today() + 'T10:01:00+09:00', ())
            app.run()
            self.assertNotIn('umbrella', {item['class_name'] for item in app.session_state.checklist.items})
            runner.result, runner.error = None, '연결 실패'
            app.run()
            self.assertTrue(any('날씨 조회 실패' in warning.value for warning in app.warning))
            self.assertFalse(any('조건에 해당하지 않습니다' in caption.value for caption in app.caption))
            self.assertFalse(app.exception)

    def test_region_picker_saves_grid_and_resets_children(self):
        from streamlit.testing.v1 import AppTest
        from profiles import ProfileStore
        context = SimpleNamespace(state=SimpleNamespace(playing=False))
        with patch('streamlit_webrtc.webrtc_streamer', return_value=context):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run()
            app.selectbox(key='w_province_default').select('서울특별시').run()
            app.selectbox(key='w_district_default_서울특별시').select('종로구').run()
            app.selectbox(key='w_town_default_서울특별시_종로구').select('청운효자동').run()
            next(button for button in app.button if button.label == '날씨 설정 저장').click().run()
            settings = ProfileStore(self.db_path).profile('default')['settings']['weather']
            self.assertEqual(settings['region_code'], '1111051500')
            self.assertEqual((settings['nx'], settings['ny']), (60, 127))
            app.selectbox(key='w_province_default').select('부산광역시').run()
            self.assertIsNone(app.selectbox(key='w_district_default_부산광역시').value)
            next(button for button in app.button if button.label == '날씨 설정 저장').click().run()
            self.assertTrue(any('지역을 먼저 선택' in error.value for error in app.error))
            self.assertEqual(ProfileStore(self.db_path).profile('default')['settings']['weather']['region_code'], '1111051500')
            self.assertFalse(app.exception)

    def test_live_results_manual_override_and_reset(self):
        from streamlit.testing.v1 import AppTest
        context = SimpleNamespace(state=SimpleNamespace(playing=True))
        # AppTest는 WebRTC 세션 매니저를 제공하지 않는다. 영상 콜백은
        # test_realtime에서 실제 AV 프레임으로 별도 검증한다.
        with patch('streamlit_webrtc.webrtc_streamer', return_value=context):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run()
            self.assertFalse(app.exception)
            live = app.session_state.live
            live.pending = {'bottle', 'book', 'umbrella'}
            live.last_frame = time.monotonic()
            app.run()
            self.assertEqual(app.session_state.checklist.state, 'READY')
            self.assertEqual(app.session_state.controller.outputs['LCD'], ['READY!', 'ALL CHECKED'])
            app.checkbox(key='mark_book').uncheck().run()
            self.assertEqual(app.session_state.checklist.state, 'SCANNING')
            self.assertEqual(app.session_state.controller.outputs['LCD'], ['BOOK', ''])
            self.assertIn('book', live.tracker.blocked)
            app.checkbox(key='camera_enabled').uncheck().run()
            self.assertEqual(len(app.session_state.checklist.confirmed), 2)
            self.assertFalse(live.enabled)
            app.button(key='new_check').click().run()
            self.assertFalse(app.session_state.checklist.confirmed)
            self.assertEqual(app.session_state.controller.outputs['LCD'], ['BOTTLE', 'BOOK'])
            self.assertFalse(live.tracker.blocked)
            self.assertFalse(app.exception)

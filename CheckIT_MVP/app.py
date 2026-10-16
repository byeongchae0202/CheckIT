import json
import time
import sqlite3
import inspect
from datetime import datetime
import streamlit as st
import streamlit.components.v1 as components
from checklist import Checklist
from detector import Detector
from arduino_controller import ArduinoController
from realtime import LiveRecognition
from profiles import ProfileStore, SUPPORTED, today
from config import PROFILE_DB, get_weather_key
from weather import WeatherClient, WeatherRunner, DEFAULT_WEATHER, MODES, request_token, weather_message
from regions import load_regions, RegionIndex
from personal_ui import render_personal
from camera import usb_device, usb_photo

st.set_page_config(page_title='CheckIT · 외출 준비', page_icon='🎒', layout='wide', initial_sidebar_state='collapsed')
st.markdown('''<style>
.block-container {max-width: 1440px; padding-top: 2rem; padding-bottom: 2rem;}
h1 {letter-spacing: -.04em;}
h2, h3 {letter-spacing: -.025em;}
.st-key-weather_card, .st-key-camera_card, .st-key-checklist_card {background: #fff; border-radius: 14px;}
[data-testid="stCheckbox"] {min-height: 44px;}
[data-testid="stButton"] button {min-height: 44px; border-radius: 9px;}
[data-testid="stAlert"] {border-radius: 10px;}
@media (max-width: 640px) {
  .block-container {padding: 1rem;}
  h1 {font-size: 2rem;}
}
</style>''', unsafe_allow_html=True)


def card(name):
    options = {'border': True}
    if 'key' in inspect.signature(st.container).parameters:
        options['key'] = name
    return st.container(**options)
if 'checklist' not in st.session_state:
    st.session_state.checklist = Checklist()
    st.session_state.controller = ArduinoController()
    st.session_state.detector = Detector()
    st.session_state.analysis = None
    st.session_state.notice = ''
checklist = st.session_state.checklist
controller = st.session_state.controller
if 'live' not in st.session_state or type(st.session_state.live) is not LiveRecognition:
    previous_live = st.session_state.get('live')
    blocked = {}
    if previous_live is not None:
        previous_live.pause()
        with previous_live.lock:
            blocked = dict(previous_live.tracker.blocked)
        st.session_state.camera_generation = st.session_state.get('camera_generation', 0) + 1
        st.session_state.live_context = None
    st.session_state.live = LiveRecognition(st.session_state.detector)
    st.session_state.live.tracker.blocked = blocked
live = st.session_state.live
if 'weather_runner' not in st.session_state:
    st.session_state.weather_runner = WeatherRunner()
try:
    if 'profiles' not in st.session_state:
        st.session_state.profiles = ProfileStore(PROFILE_DB)
    store = st.session_state.profiles
except (OSError, sqlite3.Error) as exc:
    st.error(f'사용자 저장소를 열지 못했습니다. 저장 경로와 권한을 확인해주세요. ({exc})')
    st.stop()
st.session_state.user_id = 'default'


def sync():
    controller.send_checklist(checklist.state, checklist.items, checklist.confirmed)


def action(event):
    if event == 'START':
        if checklist.start():
            live.reset()
            sync()
    elif event == 'CHECK' and checklist.state == 'SCANNING':
        checklist.finish()
        sync()
    elif event == 'NEW':
        checklist.start(force=True)
        live.reset()
        st.session_state.analysis = None
        st.session_state.notice = ''
        sync()


def toggle_item(key):
    if not st.session_state['mark_' + key]:
        live.block(key)
    checklist.mark(key, 'manual', st.session_state['mark_' + key])
    sync()


def load_profile(reset=False):
    profile = store.profile(st.session_state.user_id)
    live.pause()
    checklist.set_items(store.active_items(profile), reset=reset)
    live.reset()
    st.session_state.detector.set_personal_references(store.reference_photos(st.session_state.user_id, active_only=True))
    st.session_state.profile = profile
    st.session_state.analysis = None
    st.session_state.notice = ''
    st.session_state.displayed_checks = {}
    if reset:
        for key, value in profile['settings'].items():
            st.session_state[key] = tuple(value) if key.startswith('roi_') else value
    sync()


def save_preferences():
    try:
        store.save_settings(st.session_state.user_id, {
            key: st.session_state[key] for key in ('confidence', 'roi_x', 'roi_y')})
    except (ValueError, OSError, sqlite3.Error) as exc:
        st.session_state.preference_error = str(exc)
    else:
        st.session_state.preference_error = ''


def restart_live_camera():
    global live
    old = st.session_state.live
    old.pause()
    with old.lock:
        blocked = dict(old.tracker.blocked)
    live = LiveRecognition(st.session_state.detector)
    live.tracker.blocked = blocked
    st.session_state.live = live
    st.session_state.camera_generation = st.session_state.get('camera_generation', 0) + 1
    st.session_state.live_context = None


def select_weather_region(settings, suffix):
    try:
        index = load_regions()
    except (OSError, ValueError):
        st.error('지역 목록을 읽지 못했습니다. 격자 직접 입력으로 설정하거나 지역 데이터 파일을 확인해주세요.')
        return None
    saved = index.by_code.get(settings.get('region_code', ''))
    if settings.get('region_code') and saved is None:
        st.warning('저장된 지역이 지역 목록에 없습니다. 다시 선택해주세요.')
    provinces = [None, *index.provinces()]
    province = st.selectbox('시·도', provinces,
        index=provinces.index(saved['province']) if saved else 0,
        format_func=lambda value: value or '시·도를 선택해주세요', key='w_province_' + suffix)
    if province is None:
        return None
    districts = index.districts(province)
    if districts:
        choices = [None, '', *districts]
        default = saved['district'] if saved and saved['province'] == province else None
        district = st.selectbox('시·군·구', choices, index=choices.index(default) if default in choices else 0,
            format_func=lambda value: '시·군·구를 선택해주세요' if value is None else value or '시·도 대표 지점',
            key='w_district_' + suffix + '_' + province)
        if district is None:
            return None
    else:
        district = ''
    towns = index.towns(province, district)
    choices = [None, '', *towns]
    default = saved['town'] if saved and (saved['province'], saved['district']) == (province, district) else None
    town = st.selectbox('읍·면·동', choices, index=choices.index(default) if default in choices else 0,
        format_func=lambda value: '읍·면·동을 선택해주세요' if value is None else value or '대표 지점',
        key='w_town_' + suffix + '_' + province + '_' + district)
    if town is None:
        return None
    record = index.resolve(province, district, town)
    if record is None:
        st.warning('해당 지역의 대표 격자가 없습니다. 다른 지역을 선택해주세요.')
    else:
        st.caption('선택한 지역: ' + RegionIndex.name(record) + ' · 저장하면 적용됩니다.')
    return record


if checklist.state == 'IDLE':
    action('START')
st.title('🎒 CheckIT')
st.caption('빠뜨림 없이, 가볍게 출발하세요. 카메라로 확인하거나 직접 체크할 수 있어요.')
st.caption('지역·준비물 설정은 왼쪽 메뉴(☰)에서 변경할 수 있어요.')
previous = st.session_state.get('profile')
if previous is None or previous['id'] != st.session_state.user_id or previous['day'] != today():
    load_profile(reset=True)
else:
    latest = store.profile(st.session_state.user_id)
    if latest['items'] != previous['items']:
        load_profile()
# Windows 임베디드 Python에서도 별도 시간대 데이터 없이 한국 날짜를 표시한다.
from datetime import timezone, timedelta
st.caption(datetime.now(timezone(timedelta(hours=9))).strftime('%Y년 %m월 %d일') + ' · Asia/Seoul')
with st.sidebar:
    st.header('장치 설정')
    mode = st.radio('동작 모드', ['시뮬레이션', '실제 Arduino'])
    if mode == '시뮬레이션' and not controller.simulation:
        controller.use_simulation()
        sync()
    elif mode == '실제 Arduino' and controller.simulation:
        controller.disconnect()
        controller.simulation = False
    if mode == '실제 Arduino':
        port = st.text_input('COM 포트', 'COM3')
        if st.button('연결 / 재연결'):
            controller.connect(port.strip())
        if st.button('연결 해제'):
            controller.disconnect()
    confidence = st.slider('AI 신뢰도 기준', 0.05, 0.95, step=0.05, key='confidence', on_change=save_preferences)
    st.caption('장치가 연결되지 않아도 준비물을 직접 확인할 수 있습니다.')
    if st.session_state.get('preference_error'):
        st.warning('설정 저장 실패: ' + st.session_state.preference_error)
    with st.expander('날씨·우산 설정'):
        weather_settings = dict(DEFAULT_WEATHER, **st.session_state.profile['settings'].get('weather', {}))
        weather_suffix = st.session_state.user_id
        region_method = st.radio('지역 설정 방식', ['지역 선택', '격자 직접 입력'],
            index=0 if weather_settings['region_code'] or not weather_settings['region_name'] else 1,
            key='w_region_method_' + weather_suffix)
        selected_region = select_weather_region(weather_settings, weather_suffix) if region_method == '지역 선택' else None
        with st.form('weather_settings_' + weather_suffix):
            auto_umbrella = st.checkbox('우산 자동 추가', value=weather_settings['enabled'], key='w_enabled_' + weather_suffix)
            if region_method == '격자 직접 입력':
                region_name = st.text_input('날씨 지역 이름', value=weather_settings['region_name'], max_chars=60, key='w_region_' + weather_suffix)
                st.caption('지역 이름은 표시용이며 조회 지역은 입력한 격자 X·Y로 결정됩니다.')
                nx = st.number_input('예보 격자 X', min_value=1, max_value=149, value=weather_settings['nx'], step=1, key='w_nx_' + weather_suffix)
                ny = st.number_input('예보 격자 Y', min_value=1, max_value=253, value=weather_settings['ny'], step=1, key='w_ny_' + weather_suffix)
            weather_mode = st.selectbox('비 판단 기준', list(MODES), index=list(MODES).index(weather_settings['mode']), format_func=MODES.get, key='w_mode_' + weather_suffix)
            use_pop = st.checkbox('예보 강수확률도 기준에 포함', value=weather_settings['use_pop'], key='w_pop_' + weather_suffix)
            pop_threshold = st.slider('우산 추가 강수확률 (%)', 10, 100, value=weather_settings['pop_threshold'], step=10, key='w_threshold_' + weather_suffix)
            if st.form_submit_button('날씨 설정 저장'):
                try:
                    if region_method == '지역 선택':
                        if selected_region is None:
                            raise ValueError('조회할 지역을 먼저 선택해주세요.')
                        region_name, nx, ny = RegionIndex.name(selected_region), selected_region['nx'], selected_region['ny']
                    store.save_weather(st.session_state.user_id, {'enabled': auto_umbrella, 'region_name': region_name,
                        'nx': int(nx), 'ny': int(ny), 'mode': weather_mode, 'use_pop': use_pop, 'pop_threshold': pop_threshold,
                        'region_code': selected_region['code'] if region_method == '지역 선택' else ''})
                    st.session_state.profile = store.profile(st.session_state.user_id)
                    st.rerun()
                except (ValueError, OSError, sqlite3.Error) as exc:
                    st.error(str(exc))
        st.markdown('[기상청 단기예보 조회서비스·지역 격자 안내](https://www.data.go.kr/data/15084084/openapi.do)')
    with st.expander('준비물 관리'):
        st.caption('날짜를 선택하고 그날 준비할 물건을 하나씩 추가하세요. 추가한 항목은 선택한 날짜의 체크리스트에 나타납니다.')
        schedule_date = st.date_input('준비 날짜', value=datetime.fromisoformat(today()).date(),
                                      min_value=datetime.fromisoformat(today()).date(),
                                      key='new_item_date_' + st.session_state.user_id)
        schedule_day = schedule_date.isoformat()
        with st.form('add_item', clear_on_submit=True, enter_to_submit=False):
            item_name = st.text_input('준비물 이름', max_chars=60, key='new_item_name')
            lcd_name = st.text_input('LCD 영문 이름', max_chars=16, key='new_lcd_name')
            recognition = st.selectbox('확인 방식', [None, *SUPPORTED],
                                       format_func=lambda key: '직접 체크' if key is None else SUPPORTED[key] + ' 자동 인식 (' + key + ')')
            st.caption('사람을 제외한 YOLO 기본 모델의 79종을 선택할 수 있습니다. 같은 종류의 다른 물건도 인식하며, 지갑·열쇠 등 목록에 없는 물건은 개인 사진 등록을 이용해주세요.')
            st.caption('개인 물건 사진은 메인 화면 아래의 내 물건 등록·관리에서 등록할 수 있습니다.')
            if st.form_submit_button('준비물 추가'):
                try:
                    store.add_item(st.session_state.user_id, item_name, lcd_name, recognition, schedule_day)
                    load_profile()
                    st.rerun()
                except (ValueError, OSError, sqlite3.Error) as exc:
                    st.error(str(exc))
        scheduled_items = store.scheduled_items(st.session_state.user_id, schedule_day)
        if scheduled_items:
            st.caption(f'{schedule_date:%m월 %d일}에 표시할 준비물')
            for scheduled in scheduled_items:
                st.write(scheduled['name'])
                if st.button('삭제', key='cancel_scheduled_' + scheduled['class_name']):
                    store.delete_item(st.session_state.user_id, scheduled['class_name'])
                    st.rerun()
        components.html('''<script>
        // 이 입력 영역의 텍스트 필드만 처리한다. 한글 조합 Enter는 유지한다.
        const doc = window.parent.document;
        const slot = '__checkitItemEnter';
        if (window.parent[slot]) doc.removeEventListener('keydown', window.parent[slot], true);
        const handler = event => {
          if (event.key !== 'Enter' || event.isComposing || event.keyCode === 229) return;
          const label = event.target.getAttribute('aria-label');
          let next;
          if (label === '준비물 이름') next = doc.querySelector('input[aria-label="LCD 영문 이름"]');
          if (label === 'LCD 영문 이름') {
            const form = event.target.closest('[data-testid="stForm"]');
            next = form && form.querySelector('[role="combobox"]');
          }
          if (!next) return;
          event.preventDefault();
          event.stopImmediatePropagation();
          next.focus();
        };
        window.parent[slot] = handler;
        doc.addEventListener('keydown', handler, true);
        </script>''', height=0)
        for managed in st.session_state.profile['items']:
            if managed['active_on'] is not None:
                continue
            item_key = managed['class_name']
            st.write(managed['name'])
            try:
                if st.button('삭제', key='delete_' + item_key):
                    store.delete_item(st.session_state.user_id, item_key)
                    load_profile()
                    st.rerun()
            except (ValueError, OSError, sqlite3.Error) as exc:
                st.error(str(exc))


def weather_summary():
    if st.session_state.profile['day'] != today():
        st.rerun()
    settings = dict(DEFAULT_WEATHER, **st.session_state.profile['settings'].get('weather', {}))
    service_key = get_weather_key()
    runner = st.session_state.weather_runner
    token = request_token(st.session_state.user_id, today(), settings, service_key) if settings['region_name'] and service_key else None
    force = st.button('날씨 새로고침', key='refresh_weather', disabled=token is None)
    result, error, loading = runner.tick(token, lambda: WeatherClient().fetch(settings, service_key), force=force)
    if not settings['region_name']:
        st.info('날씨 지역을 설정해주세요. 사이드바의 날씨·우산 설정에서 변경할 수 있습니다.')
    elif not service_key:
        st.info('날씨 서비스가 아직 준비되지 않았어요. 우산 필요 여부는 직접 확인해주세요.')
    elif loading:
        st.info('날씨를 확인하고 있어요. 준비물 확인은 계속할 수 있습니다.')
    elif error:
        st.warning('날씨 조회 실패: ' + error + ' 비가 오지 않는다는 뜻은 아닙니다. 우산 필요 여부를 직접 확인해주세요.')
    elif result is not None:
        marker = (token, result.checked_at)
        if settings['enabled'] and result.rain and st.session_state.get('weather_applied') != marker:
            try:
                changed = store.ensure_weather_umbrella(st.session_state.user_id, today())
                st.session_state.weather_applied = marker
                if changed:
                    load_profile()
                    st.rerun()
            except (ValueError, OSError, sqlite3.Error):
                st.warning('날씨 조건은 충족했지만 우산을 저장하지 못했습니다. 목록을 직접 확인해주세요.')
        profile = st.session_state.profile
        st.info(weather_message(result, settings))
        if result.rain and not settings['enabled']:
            st.caption('우산 자동 추가는 꺼져 있어요. 준비물 목록에서 직접 확인해주세요.')
        st.caption(settings['region_name'] + ' · 조회 ' + result.checked_at[11:16] + ' · ' + ' / '.join(result.references) + ' · 출처: 기상청')


# 연결 위젯은 유지하고 카메라 상태만 주기 갱신한다.
def render_workspace():
    with card('weather_card'):
        st.markdown('**오늘의 날씨**')
        weather_summary()
    camera, items = st.columns([3, 2], gap='large')
    def render_camera():
        st.subheader('준비물 카메라')
        camera_enabled = st.checkbox('카메라 사용', value=True, key='camera_enabled')
        st.button('실시간 연결 초기화', key='restart_camera', on_click=restart_live_camera)
        st.caption('카메라를 허용하면 자동 인식합니다. 중지하려면 카메라 사용을 해제하세요.')
        st.caption('카메라 화면 전체에서 인식합니다. 자동 확인은 최소 3회·0.8초 연속 탐지가 필요합니다.')
        device = usb_device()
        st.session_state.usb_camera = device
        live_enabled = camera_enabled and bool(checklist.items) and device is not None
        live.configure((0.0, 0.0, 1.0, 1.0), confidence, live_enabled)
        try:
            from streamlit_webrtc import webrtc_streamer, WebRtcMode
            camera_options = {}
            if 'media_toggle_controls' in inspect.signature(webrtc_streamer).parameters:
                camera_options['media_toggle_controls'] = False
            if 'video_html_attrs' in inspect.signature(webrtc_streamer).parameters:
                camera_options['video_html_attrs'] = {
                    'autoPlay': True, 'muted': True, 'playsInline': True,
                    'controls': True, 'style': {'width': '100%'},
                }
            context = webrtc_streamer(
                key='checkit_usb_camera_' + (device['id'] if device else 'pending') + '_' + str(st.session_state.get('camera_generation', 0)), mode=WebRtcMode.SENDRECV,
                desired_playing_state=live_enabled,
                rtc_configuration={'iceServers': []},
                media_stream_constraints={'video': {
                    'deviceId': {'exact': device['id']} if device else {'exact': 'usb-camera-pending'},
                    'width': {'ideal': 640}, 'height': {'ideal': 480}, 'frameRate': {'ideal': 10},
                }, 'audio': False},
                video_frame_callback=live.recv, on_video_ended=live.pause,
                async_processing=True,
                **camera_options,
                translations={'start': '카메라 다시 연결', 'stop': '카메라 중지',
                              'device_ask_permission': '브라우저에서 카메라 권한을 허용해주세요.',
                              'device_access_denied': '카메라 권한이 거부됐어요. 브라우저 설정에서 허용하거나 사진·수동 확인을 이용하세요.',
                              'device_not_available': '사용할 수 있는 카메라가 없어요. 사진·수동 확인을 이용하세요.'},
            )
            st.session_state.live_context = context
            if context.state.playing:
                st.caption('● 영상 연결됨 · 카메라에 준비물을 보여주세요.')
            elif camera_enabled:
                st.info('카메라 연결 대기 중입니다. 권한을 허용해주세요. 연결이 안 되면 카메라 사용을 껐다 켜거나 사진·수동 확인을 이용하세요.')
            else:
                st.caption('인식 중지 · 확인한 항목은 유지됩니다.')
        except ImportError:
            live.pause()
            st.warning('실시간 카메라 의존성이 없습니다. requirements.txt를 설치한 뒤 다시 실행해주세요. 사진·수동 확인은 사용할 수 있습니다.')
        with st.expander('사진으로 확인'):
            st.caption('사진은 전체 이미지에서 즉시 확인합니다. 수동 해제한 종류는 영상에서 1초 이상 사라진 뒤 재확인하거나 직접 체크해주세요.')
            source = st.radio('사진 입력 방식', ['이미지 업로드', '카메라 촬영'], horizontal=True)
            if source == '이미지 업로드':
                photo = st.file_uploader('사진 선택 (JPG / PNG / WEBP)', type=['jpg', 'jpeg', 'png', 'webp'])
            else:
                if camera_enabled:
                    st.info('사진 촬영 전 위의 카메라 사용을 꺼주세요. 실시간 영상과 사진 촬영이 같은 카메라를 동시에 사용하지 않도록 합니다.')
                    photo = None
                else:
                    photo = usb_photo(device, key='checklist_usb_photo')
            if st.button('사진 분석 실행', disabled=not checklist.items, key='analyze'):
                if photo is None:
                    st.warning('먼저 사진을 촬영하거나 업로드하세요.')
                else:
                    try:
                        with st.spinner('사진 분석 중…'):
                            rows, annotated = st.session_state.detector.detect(photo.getvalue(), confidence)
                            checklist.apply_detections(live.photo_allowed(row['class_name'] for row in rows))
                            st.session_state.analysis = (rows, annotated)
                            st.session_state.notice = '' if rows else '탐지 결과가 없습니다. 다른 사진이나 수동 확인을 사용하세요.'
                        sync()
                        st.rerun()
                    except RuntimeError as exc:
                        st.error(str(exc))
                        controller.send_state('ERROR')
            if st.session_state.notice:
                st.info(st.session_state.notice)
            if st.session_state.analysis is not None:
                rows, annotated = st.session_state.analysis
                st.image(annotated, caption='준비물 탐지·등록 사진 비교 결과')
                with st.expander('탐지 상세'):
                    st.dataframe(rows)

    @st.fragment(run_every=0.5)
    def poll_camera():
        if st.session_state.profile['day'] != today():
            st.rerun()
        before_poll = (dict(checklist.confirmed), checklist.state, repr(controller.outputs), controller.error)
        for event in controller.poll():
            action(event)
        ready, error, last_frame = live.drain()
        context = st.session_state.get('live_context')
        playing = context is not None and context.state.playing
        if st.session_state.get('camera_enabled'):
            camera_status = live.status()
            st.caption(f"카메라 진단 · 연결: {'연결됨' if playing else '대기'} · 수신 프레임: {camera_status['영상 프레임 수']} · 분석: {'진행 중' if camera_status['분석 중'] else '대기'}")
            if not playing:
                st.info('실시간 영상과 사진 촬영은 위에서 선택한 USB 카메라를 사용합니다. 연결되지 않으면 다른 앱의 카메라 사용을 종료하고 ‘실시간 연결 초기화’를 눌러주세요.')
        if playing and last_frame is not None and time.monotonic() - last_frame <= 2 and not error:
            before = dict(checklist.confirmed)
            checklist.apply_detections(ready)
            if checklist.confirmed != before:
                sync()
        if error:
            st.warning(error + ' 다시 시도하거나 직접 체크해주세요.')
        elif playing and live.status()['영상 프레임 수'] == 0:
            st.info('영상 연결은 됐지만 프레임을 기다리고 있어요. 계속되면 카메라 사용을 껐다 켜거나 사진·수동 확인을 이용하세요.')
        elif playing and (last_frame is None or time.monotonic() - last_frame > 2):
            st.info('영상을 분석하고 있어요. 최초 실행에는 모델 로딩 시간이 필요합니다.')
        elif playing:
            st.caption('● 실시간 분석 중')
        after_poll = (dict(checklist.confirmed), checklist.state, repr(controller.outputs), controller.error)
        runner = st.session_state.weather_runner
        future = getattr(runner, 'future', None)
        weather_changed = future is not None and future.done()
        weather_due = getattr(runner, 'token', None) is not None and future is None and time.monotonic() >= runner.next_due
        if before_poll != after_poll or weather_changed or weather_due:
            st.rerun()

    with camera, card('camera_card'):
        render_camera()
        poll_camera()

    def dashboard():
        st.subheader('오늘의 준비물')
        total = len(checklist.items)
        st.write(f'{len(checklist.confirmed)}/{total} 확인')
        if total:
            st.progress(len(checklist.confirmed) / total)
        for item in checklist.items:
            key = item['class_name']
            source = checklist.confirmed.get(key)
            displayed = st.session_state.setdefault('displayed_checks', {})
            if 'mark_' + key not in st.session_state or displayed.get(key) != bool(source):
                st.session_state['mark_' + key] = bool(source)
                displayed[key] = bool(source)
            st.checkbox('~~' + item['name'] + '~~' if source else item['name'],
                        key='mark_' + key, on_change=toggle_item, args=(key,))
            st.caption({'auto': '자동 인식', 'manual': '직접 확인'}.get(source, '미확인'))
        if not total:
            st.info('오늘 검사할 준비물이 없어요.')
        elif checklist.state == 'READY':
            st.success('모든 준비물을 확인했어요. 출발 준비 완료!')
        else:
            st.info('확인해주세요: ' + ', '.join(i['name'] for i in checklist.missing))
        st.button('새 검사', key='new_check', on_click=action, args=('NEW',), type='primary')
        st.caption('새 검사를 누르면 현재 확인 결과를 초기화합니다.')
        connected = controller.serial is not None
        booting = connected and time.monotonic() < controller.ready_at
        st.caption('장치: ' + ('시뮬레이션' if controller.simulation else '부팅 중' if booting else '연결됨' if connected else '미연결'))
        st.caption('LCD 전송 내용 · ' + controller.outputs['LCD 수신 확인'])
        st.code('\n'.join(line.ljust(16) for line in controller.outputs['LCD']), language=None)
        if controller.error:
            st.warning(controller.error)
        with st.expander('장치 상태 / 통신 기록'):
            st.caption('수신 확인은 명령 처리 확인이며 LCD 연결이나 실제 화면을 측정한 결과는 아닙니다.')
            st.json(live.status())
            st.json(controller.outputs)
            st.button('현재 상태 재전송', on_click=sync, disabled=not controller.simulation and (not connected or booting))
            st.code('\n'.join(controller.log) or '통신 기록 없음')
        with st.expander('이번 검사 확인 기록'):
            st.dataframe(checklist.history)
            st.download_button('기록 JSON 저장', json.dumps(checklist.history, ensure_ascii=False, indent=2), 'checkit_history.json', 'application/json')


    with items, card('checklist_card'):
        dashboard()

render_workspace()
with st.expander('내 물건 등록·관리'):
    render_personal(store, st.session_state.user_id, live.region, load_profile)
st.caption('목록·설정·등록 사진은 이 PC에 저장합니다. 검사 영상·시험 사진은 별도 저장하지 않으며 확인 결과는 새 브라우저 세션에서 초기화됩니다.')

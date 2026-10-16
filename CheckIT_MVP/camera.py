"""Browser-local USB camera selection shared by video and photos."""
import base64
import binascii
from io import BytesIO
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components


_camera = components.declare_component('usb_camera', path=str(Path(__file__).with_name('camera_frontend')))


def usb_device():
    generation = st.session_state.get('camera_generation', 0)
    result = _camera(mode='select', generation=generation, key='usb_camera_device', default=None)
    if not result:
        return None
    if result.get('error'):
        st.error('USB 카메라 연결 실패: ' + result['error'])
        return None
    if result.get('generation') != generation:
        return None
    device = result.get('device')
    if not isinstance(device, dict) or not device.get('id') or not device.get('label'):
        st.error('USB 카메라 장치 정보를 읽지 못했습니다.')
        return None
    return device


def usb_photo(device, key):
    if not device:
        st.info('USB 카메라를 연결하고 카메라 권한을 허용해주세요.')
        return None
    result = _camera(mode='photo', device=device, key=key, default=None)
    if not result:
        return None
    if result.get('error'):
        st.error('USB 사진 촬영 실패: ' + result['error'])
        return None
    if result.get('device_id') != device['id'] or not result.get('photo'):
        return None
    try:
        return BytesIO(base64.b64decode(result['photo'], validate=True))
    except (ValueError, binascii.Error):
        st.error('촬영한 사진 데이터를 읽지 못했습니다. 다시 촬영해주세요.')
        return None

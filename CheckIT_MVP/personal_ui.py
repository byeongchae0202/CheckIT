"""개인 물건 사진 등록·인식 시험 화면."""
import sqlite3
import streamlit as st
from personal import PersonalMatcher, decode_photo
from realtime import inside_region


def render_personal(store, user_id, region, refresh):
    st.subheader('내 물건 등록')
    st.caption('등록한 물건·사진·자동 확인 설정은 현재 사용자에게 저장되어 앱을 다시 실행해도 유지됩니다. 왼쪽 메뉴에서 등록할 때와 같은 사용자를 선택해주세요.')
    st.caption('물건을 크게 촬영한 사진을 여러 각도로 등록하세요. 글자·무늬가 있는 물건에 적합합니다. 무지·반사·심한 가림은 직접 체크로 확인해주세요.')
    with st.form('register_personal_' + user_id, clear_on_submit=True):
        name = st.text_input('개인 물건 이름', max_chars=60, key='personal_name_' + user_id)
        lcd_name = st.text_input('개인 물건 LCD 영문 이름', max_chars=16, key='personal_lcd_' + user_id)
        photos = st.file_uploader('등록 사진 (최대 8장)', type=['jpg', 'jpeg', 'png', 'webp'], accept_multiple_files=True, key='personal_register_' + user_id)
        st.caption('등록 사진은 이 PC의 사용자 저장소에 저장합니다. 다른 물건이나 배경이 많이 보이지 않도록 물건 위주로 잘라 등록해주세요.')
        if st.form_submit_button('개인 물건 등록'):
            try:
                store.register_personal(user_id, name, lcd_name, [photo.getvalue() for photo in photos])
                refresh()
                st.rerun()
            except (ValueError, OSError, sqlite3.Error) as exc:
                st.error(str(exc))
    profile = store.profile(user_id)
    candidates = {item['class_name']: item for item in profile['items'] if item['class_name'].startswith(('personal_', 'manual_'))}
    if not candidates:
        st.info('등록한 개인 물건이 없어요. 사진을 등록한 뒤 아래에서 인식을 시험할 수 있습니다.')
        return
    options = [None, *candidates]
    widget_key = 'personal_manage_' + user_id
    if st.session_state.get(widget_key) not in options:
        st.session_state[widget_key] = None
    selected = st.selectbox('관리할 개인 물건', options,
                            format_func=lambda key: candidates[key]['name'] if key else '물건을 선택해주세요',
                            placeholder='물건을 선택해주세요', key=widget_key)
    if not selected:
        return
    item = candidates[selected]
    test_key = 'personal_test_' + user_id + '_' + selected
    st.caption('자동 확인 켜짐' if item['personal_enabled'] else '직접 체크 · 인식 시험 후 자동 확인을 켤 수 있습니다.')
    references = store.reference_photos(user_id, selected)
    st.caption(f'저장된 사진 {len(references)}장 · 이 PC에 저장됨')
    for reference in references:
        preview, actions = st.columns([1, 3])
        preview.image(reference['jpeg'], width=130)
        if actions.button('이 사진 삭제', key='delete_photo_' + reference['id']):
            store.delete_reference_photo(user_id, selected, reference['id'])
            st.session_state.pop(test_key, None)
            refresh()
            st.rerun()
    with st.form('add_personal_photos_' + selected, clear_on_submit=True):
        more = st.file_uploader('다른 각도의 사진 추가', type=['jpg', 'jpeg', 'png', 'webp'], accept_multiple_files=True, key='more_photos_' + selected)
        if st.form_submit_button('사진 추가'):
            try:
                store.add_reference_photos(user_id, selected, [photo.getvalue() for photo in more])
                st.session_state.pop(test_key, None)
                refresh()
                st.rerun()
            except (ValueError, OSError, sqlite3.Error) as exc:
                st.error(str(exc))
    st.markdown('**검사 구역에서 인식 시험**')
    st.caption('실제 검사 구역에 물건을 놓고 장면을 촬영하세요. 등록 때와 다른 사진으로 시험한 뒤, 다른 물건에서는 잘못 인식되지 않는지도 확인해주세요.')
    source = st.radio('시험 사진 입력', ['사진 업로드', '카메라 촬영'], key='personal_test_source_' + selected, horizontal=True)
    if source == '사진 업로드':
        photo = st.file_uploader('검사 장면 사진', type=['jpg', 'jpeg', 'png', 'webp'], key='personal_scene_' + selected)
    else:
        photo = st.camera_input('검사 장면 촬영', key='personal_camera_' + selected)
    if st.button('개인 물건 인식 시험', key='test_personal_' + selected, disabled=not references):
        if photo is None:
            st.warning('검사 장면 사진을 먼저 선택해주세요.')
        else:
            try:
                import cv2
                _, image = decode_photo(photo.getvalue())
                rows = PersonalMatcher(store.reference_photos(user_id)).match(image)
                height, width = image.shape[:2]
                matched = [row for row in rows if row['class_name'] == selected and inside_region(row, width, height, region)]
                store.record_personal_test(user_id, selected, bool(matched))
                for row in matched:
                    x1, y1, x2, y2 = map(int, row['box'])
                    cv2.rectangle(image, (x1, y1), (x2, y2), (0, 180, 80), 3)
                left, top, right, bottom = region
                cv2.rectangle(image, (int(left * width), int(top * height)), (int(right * width), int(bottom * height)), (0, 190, 220), 2)
                st.session_state[test_key] = (bool(matched), cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
                refresh()
                st.rerun()
            except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
                st.error('인식 시험 실패: ' + str(exc))
    if test_key in st.session_state:
        matched, image = st.session_state[test_key]
        st.image(image, caption='초록색: 등록 물건 비교 결과 · 노란색: 검사 구역')
        if matched:
            st.success('검사 구역에서 인식됐어요. 다른 각도와 다른 물건도 시험한 뒤 자동 확인을 켜주세요.')
        else:
            st.warning('검사 구역에서 확인하지 못했어요. 사진·각도·조명을 바꾸거나 직접 체크해주세요.')
    # 시험 후 새로 읽어 활성화 조건을 확인한다.
    current = next(row for row in store.profile(user_id)['items'] if row['class_name'] == selected)
    if st.button('자동 확인 끄기' if current['personal_enabled'] else '자동 확인 켜기',
                 key='enable_personal_' + selected,
                 disabled=not current['personal_enabled'] and not current['personal_validated']):
        store.enable_personal(user_id, selected, not current['personal_enabled'])
        refresh()
        st.rerun()
    if st.button('개인 물건과 등록 사진 모두 삭제', key='delete_personal_' + selected):
        store.delete_item(user_id, selected)
        st.session_state.pop(test_key, None)
        refresh()
        st.rerun()

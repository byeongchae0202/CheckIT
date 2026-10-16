"""YOLO는 실제 분석 요청 때만 로드한다. 수동 시연에는 모델이 필요 없다."""
from config import MODEL_FILE
from threading import Lock


def same_object_box(first, second):
    """Also handle a label match contained inside a full bottle detection."""
    from personal import overlap
    left, top = max(first[0], second[0]), max(first[1], second[1])
    right, bottom = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    smaller = min(max(0, first[2] - first[0]) * max(0, first[3] - first[1]),
                  max(0, second[2] - second[0]) * max(0, second[3] - second[1]))
    return overlap(first, second) > .3 or intersection / max(1, smaller) >= .65


class Detector:
    def __init__(self):
        self.model = None
        self.lock = Lock()
        self.personal = None
        self.personal_labels = {}

    def set_personal_references(self, references):
        from personal import PersonalMatcher
        matcher = PersonalMatcher(references) if references else None
        with self.lock:
            self.personal = matcher
            self.personal_labels = {r["class_name"]: (r.get("name", "REGISTERED ITEM"), r.get("lcd_name", "REGISTERED ITEM")) for r in references}

    def detect_bgr(self, bgr, confidence=0.4):
        """영상 프레임과 사진이 같은 모델을 안전하게 공유한다."""
        if not 0.05 <= confidence <= 0.95:
            raise ValueError('신뢰도는 0.05~0.95 범위여야 합니다.')
        with self.lock:
            if self.model is None:
                from ultralytics import YOLO
                MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
                self.model = YOLO(str(MODEL_FILE))
            # 사진·실시간 영상 모두 사람은 결과와 주석 이미지에서 제외한다.
            allowed_classes = [index for index, name in self.model.names.items() if name != 'person']
            result = self.model.predict(bgr, conf=confidence, verbose=False, classes=allowed_classes)[0]
            rows = [{'class_name': result.names[int(box.cls.item())],
                     'confidence': float(box.conf.item()),
                     'box': box.xyxy[0].tolist()} for box in result.boxes]
            if self.personal is not None:
                personal_rows = self.personal.match(bgr)
                # Retry on bottle candidates at a larger local scale when the
                # full-frame matcher could not identify the registered product.
                if not personal_rows:
                    height, width = bgr.shape[:2]
                    for candidate in rows:
                        if candidate['class_name'] not in ('bottle', 'vase'):
                            continue
                        x1, y1, x2, y2 = candidate['box']
                        padding = max(x2 - x1, y2 - y1) * .2
                        left, top = max(0, int(x1 - padding)), max(0, int(y1 - padding))
                        right, bottom = min(width, int(x2 + padding)), min(height, int(y2 + padding))
                        if right <= left or bottom <= top:
                            continue
                        for match in self.personal.match(bgr[top:bottom, left:right]):
                            a, b, c, d = match['box']
                            match['box'] = [a + left, b + top, c + left, d + top]
                            if same_object_box(candidate['box'], match['box']):
                                personal_rows.append(match)
                for row in personal_rows:
                    row["name"] = self.personal_labels.get(row["class_name"], ("REGISTERED ITEM", "REGISTERED ITEM"))[0]
                # A registered object takes precedence only at its matched location.
                keep = [i for i, row in enumerate(rows)
                        if not any(same_object_box(row['box'], match['box'])
                                   for match in personal_rows)]
                rows = [rows[i] for i in keep] + personal_rows
                result.boxes = result.boxes[keep]
            annotated = result.plot()
            if self.personal is not None:
                import cv2
                for row in rows:
                    if row.get('kind') == 'personal':
                        x1, y1, x2, y2 = map(int, row['box'])
                        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 180, 80), 2)
                        cv2.putText(annotated, self.personal_labels.get(row['class_name'], ('REGISTERED ITEM', 'REGISTERED ITEM'))[1], (x1, max(18, y1)), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 180, 80), 1)
            return rows, annotated

    def detect(self, image_bytes, confidence=0.4):
        if not 0.05 <= confidence <= 0.95:
            raise ValueError('신뢰도는 0.05~0.95 범위여야 합니다.')
        try:
            import io
            import numpy as np
            import cv2
            from PIL import Image, ImageOps
            image = Image.open(io.BytesIO(image_bytes))
            if image.width * image.height > 25_000_000:
                raise ValueError('이미지는 2,500만 픽셀 이하로 업로드하세요.')
            rgb = np.asarray(ImageOps.exif_transpose(image).convert('RGB'))
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            rows, annotated = self.detect_bgr(bgr, confidence)
            return rows, cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
        except Exception as exc:
            if getattr(exc, 'winerror', None) == 4551 or 'WinError 4551' in str(exc):
                raise RuntimeError(
                    'Windows 애플리케이션 제어 정책이 AI 라이브러리(PyTorch DLL) 실행을 '
                    '차단했습니다(WinError 4551). 사진 문제가 아닙니다. '
                    '학교·회사 관리 PC라면 관리자에게 차단 기록과 DLL 경로를 전달해 '
                    '승인된 실행 환경을 요청하세요. 현재는 수동 확인을 사용할 수 있습니다. '
                    f'상세: {exc}'
                ) from exc
            raise RuntimeError('사진 분석 실패: 이미지 형식, 인터넷/모델 다운로드, '
                               f'설치 환경을 확인하세요. ({exc})') from exc

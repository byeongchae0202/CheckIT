"""YOLO는 실제 분석 요청 때만 로드한다. 수동 시연에는 모델이 필요 없다."""
from config import MODEL_FILE
from threading import Lock


class Detector:
    def __init__(self):
        self.model = None
        self.lock = Lock()
        self.personal = None

    def set_personal_references(self, references):
        from personal import PersonalMatcher
        matcher = PersonalMatcher(references) if references else None
        with self.lock:
            self.personal = matcher

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
                rows.extend(self.personal.match(bgr))
            annotated = result.plot()
            if self.personal is not None:
                import cv2
                for row in rows:
                    if row.get('kind') == 'personal':
                        x1, y1, x2, y2 = map(int, row['box'])
                        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 180, 80), 2)
                        cv2.putText(annotated, 'REGISTERED ITEM', (x1, max(18, y1)), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 180, 80), 1)
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

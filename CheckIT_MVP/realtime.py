"""영상 콜백은 Streamlit/체크리스트를 건드리지 않고 결과만 전달한다."""
from threading import Lock
from time import monotonic


def inside_region(row, width, height, region):
    x1, y1, x2, y2 = row['box']
    cx, cy = (x1 + x2) / (2 * width), (y1 + y2) / (2 * height)
    left, top, right, bottom = region
    return left <= cx <= right and top <= cy <= bottom


class StablePresence:
    def __init__(self, duration=0.8, samples=3, max_gap=1.0, absent_duration=1.0):
        self.duration, self.samples = duration, samples
        self.max_gap, self.absent_duration = max_gap, absent_duration
        self.seen = {}
        self.blocked = {}
        self.last_time = None

    def clear(self):
        self.seen.clear()
        self.last_time = None
        # 중지/오류는 물건이 실제로 사라진 증거가 아니다.
        self.blocked = {key: None for key in self.blocked}

    def block(self, key):
        self.blocked[key] = None
        self.seen.pop(key, None)

    def observe(self, names, now):
        names = set(names)
        if self.last_time is not None and now - self.last_time > self.max_gap:
            self.clear()
        self.last_time = now
        for key in list(self.blocked):
            if key in names:
                self.blocked[key] = None
            elif self.blocked[key] is None:
                self.blocked[key] = now
            elif now - self.blocked[key] >= self.absent_duration:
                del self.blocked[key]
        for key in set(self.seen) - names:
            del self.seen[key]
        ready = set()
        for key in names - self.blocked.keys():
            first, last, count = self.seen.get(key, (now, now, 0))
            count += 1
            self.seen[key] = (first, now, count)
            if count >= self.samples and now - first >= self.duration:
                ready.add(key)
        return ready


class LiveRecognition:
    def __init__(self, detector, clock=monotonic):
        self.detector, self.clock = detector, clock
        self.lock = Lock()
        self.tracker = StablePresence()
        self.region = (0.15, 0.15, 0.85, 0.85)
        self.confidence = 0.4
        self.enabled = True
        self.revision = 0
        self.pending = set()
        self.error = ''
        self.last_frame = None
        self.last_inference = float('-inf')
        self.busy = False
        self.received = 0

    def status(self):
        with self.lock:
            return {'영상 프레임 수': self.received, '분석 허용': self.enabled,
                    '분석 중': self.busy, '분석 완료': self.last_frame is not None}

    def configure(self, region, confidence, enabled):
        region = tuple(region)
        if not (0 <= region[0] < region[2] <= 1 and 0 <= region[1] < region[3] <= 1):
            raise ValueError('검사 구역의 가로·세로 범위가 올바르지 않습니다.')
        with self.lock:
            if (region, confidence, enabled) != (self.region, self.confidence, self.enabled):
                self.region, self.confidence, self.enabled = region, confidence, enabled
                self.revision += 1
                self.pending.clear()
                self.tracker.clear()

    def reset(self):
        with self.lock:
            self.revision += 1
            self.tracker = StablePresence()
            self.pending.clear()
            self.error = ''

    def block(self, key):
        with self.lock:
            self.revision += 1
            self.pending.discard(key)
            self.tracker.block(key)

    def photo_allowed(self, names):
        with self.lock:
            # 사진 한 장은 연속 부재를 입증하지 못한다.
            return set(names) - self.tracker.blocked.keys()

    def drain(self):
        with self.lock:
            ready, self.pending = self.pending, set()
            return ready, self.error, self.last_frame

    def pause(self):
        self.configure(self.region, self.confidence, False)

    def recv(self, frame):
        import av
        import cv2
        image = frame.to_ndarray(format='bgr24')
        now = self.clock()
        with self.lock:
            self.received += 1
            region, confidence, revision = self.region, self.confidence, self.revision
            process = self.enabled and not self.busy and now - self.last_inference >= 0.2
            if process:
                self.busy = True
                self.last_inference = now
        if process:
            try:
                rows, _ = self.detector.detect_bgr(image, confidence)
                height, width = image.shape[:2]
                names = {r['class_name'] for r in rows if inside_region(r, width, height, region)}
                with self.lock:
                    if revision == self.revision and self.enabled:
                        self.pending.update(self.tracker.observe(names, self.clock()))
                        self.error = ''
                        self.last_frame = self.clock()
                for row in rows:
                    color = (40, 180, 40) if inside_region(row, width, height, region) else (130, 130, 130)
                    x1, y1, x2, y2 = map(int, row['box'])
                    cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
                    label = 'PERSONAL ITEM' if row.get('kind') == 'personal' else row['class_name']
                    cv2.putText(image, label, (x1, max(18, y1)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
            except Exception as exc:
                with self.lock:
                    if revision == self.revision:
                        self.error = f'자동 인식 실패: {exc}'
                        self.pending.clear()
                        self.tracker.clear()
            finally:
                with self.lock:
                    self.busy = False
        height, width = image.shape[:2]
        left, top, right, bottom = region
        cv2.rectangle(image, (int(left * width), int(top * height)),
                      (int(right * width), int(bottom * height)), (0, 190, 220), 3)
        cv2.putText(image, 'CHECK AREA', (int(left * width), max(20, int(top * height) - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 190, 220), 2)
        return av.VideoFrame.from_ndarray(image, format='bgr24')

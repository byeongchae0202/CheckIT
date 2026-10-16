"""등록 사진의 ORB 특징과 RANSAC 기하 검증으로 특정 물건을 비교한다."""
import io
from threading import Lock


def decode_photo(raw):
    from PIL import Image, ImageOps
    import numpy as np
    import cv2
    if not raw or len(raw) > 10 * 1024 * 1024:
        raise ValueError('사진은 10MB 이하로 등록해주세요.')
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.width * source.height > 25_000_000:
                raise ValueError('사진은 2,500만 픽셀 이하로 등록해주세요.')
            image = ImageOps.exif_transpose(source).convert('RGB')
            image.thumbnail((1024, 1024))
            if min(image.size) < 80:
                raise ValueError('사진의 가로·세로는 최소 80픽셀이어야 합니다.')
            buffer = io.BytesIO()
            image.save(buffer, format='JPEG', quality=90)
            return buffer.getvalue(), cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    except ValueError:
        raise
    except Exception:
        raise ValueError('읽을 수 없는 사진입니다. JPG·PNG·WEBP 사진을 사용해주세요.') from None


def prepare_reference(raw):
    import cv2
    jpeg, image = decode_photo(raw)
    points, descriptors = cv2.ORB_create(nfeatures=1500).detectAndCompute(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), None)
    if descriptors is None or len(points) < 30:
        raise ValueError('사진의 특징이 부족합니다. 글자·무늬가 보이도록 물건을 크게 촬영해주세요.')
    return jpeg


def overlap(a, b):
    left, top, right, bottom = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    area_a, area_b = (a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])
    return intersection / max(1, area_a + area_b - intersection)


class PersonalMatcher:
    def __init__(self, references=()):
        import cv2
        self.lock = Lock()
        self.orb = cv2.ORB_create(nfeatures=1500)
        self.references = []
        for reference in references:
            _, image = decode_photo(reference['jpeg'])
            points, descriptors = self.orb.detectAndCompute(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), None)
            if descriptors is not None and len(points) >= 30:
                self.references.append((reference['class_name'], image.shape[:2], points, descriptors))

    def match(self, image):
        import cv2
        import numpy as np
        with self.lock:
            height, width = image.shape[:2]
            scale = min(1.0, 800 / max(height, width))
            scene = cv2.resize(image, None, fx=scale, fy=scale) if scale < 1 else image
            points, descriptors = self.orb.detectAndCompute(cv2.cvtColor(scene, cv2.COLOR_BGR2GRAY), None)
            if descriptors is None or len(points) < 12:
                return []
            matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
            best = {}
            for key, (rh, rw), reference_points, reference_descriptors in self.references:
                pairs = matcher.knnMatch(reference_descriptors, descriptors, k=2)
                good = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < .75 * pair[1].distance]
                # 여러 특징이 같은 지점에 몰리는 대응은 제외한다.
                good = list({match.trainIdx: match for match in sorted(good, key=lambda match: match.distance, reverse=True)}.values())
                if len(good) < 12:
                    continue
                source = np.float32([reference_points[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
                target = np.float32([points[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
                matrix, mask = cv2.findHomography(source, target, cv2.RANSAC, 4.0)
                if matrix is None or mask is None:
                    continue
                inliers = int(mask.sum())
                if inliers < 10 or inliers / len(good) < .55:
                    continue
                reference_inliers = source[mask.ravel().astype(bool)].reshape(-1, 2)
                coverage = cv2.contourArea(cv2.convexHull(reference_inliers)) / (rw * rh)
                if coverage < .12:
                    continue
                corners = np.float32([[0, 0], [rw - 1, 0], [rw - 1, rh - 1], [0, rh - 1]]).reshape(-1, 1, 2)
                projected = cv2.perspectiveTransform(corners, matrix).reshape(-1, 2) / scale
                if not np.isfinite(projected).all() or not cv2.isContourConvex(projected.astype(np.float32)):
                    continue
                area = abs(cv2.contourArea(projected.astype(np.float32)))
                if not .005 * width * height <= area <= .9 * width * height:
                    continue
                if (projected[:, 0] < -10).any() or (projected[:, 0] > width + 10).any() or (projected[:, 1] < -10).any() or (projected[:, 1] > height + 10).any():
                    continue
                left, top = np.maximum(projected.min(axis=0), (0, 0))
                right, bottom = np.minimum(projected.max(axis=0), (width, height))
                row = dict(class_name=key, confidence=round(inliers / len(good), 3),
                           box=[float(left), float(top), float(right), float(bottom)], kind='personal', inliers=inliers)
                if key not in best or best[key]['inliers'] < inliers:
                    best[key] = row
            rows = list(best.values())
            ambiguous = set()
            for i, first in enumerate(rows):
                for second in rows[i + 1:]:
                    if overlap(first['box'], second['box']) > .5:
                        # 같은 위치가 여러 등록품과 일치하면 모두 미확인으로 둔다.
                        ambiguous.update((first['class_name'], second['class_name']))
            return [row for row in rows if row['class_name'] not in ambiguous]

"""실제 개인 사진 대신 쓰는 재현 가능한 특징점·장면 테스트 자료."""
def images(seed=7):
    import cv2
    import numpy as np
    random = np.random.default_rng(seed)
    reference = np.full((260, 340, 3), 235, dtype=np.uint8)
    for _ in range(100):
        center = tuple(int(value) for value in random.integers([15, 15], [325, 245]))
        color = tuple(int(value) for value in random.integers(0, 200, 3))
        cv2.circle(reference, center, int(random.integers(3, 12)), color, -1)
    cv2.putText(reference, 'CHECKIT ' + str(seed), (25, 130), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 3)
    source = np.float32([[0, 0], [339, 0], [339, 259], [0, 259]])
    target = np.float32([[155, 110], [475, 130], [455, 385], [145, 360]])
    matrix = cv2.getPerspectiveTransform(source, target)
    scene = cv2.warpPerspective(reference, matrix, (640, 480), borderValue=(100, 100, 100))
    raw = cv2.imencode('.png', reference)[1].tobytes()
    return raw, reference, scene

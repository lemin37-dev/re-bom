"""배경 제거 마스크: 털색 비율을 셀 때 바닥·벽·케이지를 빼려고 쓴다.

내려받을 모델 없이 OpenCV GrabCut 만 쓴다(사진 가운데에 동물이 있다고 가정).
결과 마스크는 color_ratio(..., mask=) 에 그대로 넘긴다. 전경 255, 배경 0.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

WORK_SIZE = 256  # 계산을 빠르게 하려고 긴 변을 이 크기로 줄여서 자른다
MIN_FOREGROUND = 0.05  # 전경이 이보다 적으면 실패로 보고 None


def grabcut_mask(
    image: Image.Image | str | Path,
    *,
    margin: float = 0.05,
    iterations: int = 5,
) -> Image.Image | None:
    """GrabCut 전경 마스크(원본 크기, L 모드). 전경을 거의 못 찾으면 None.

    margin: 가장자리 몇 %를 '확실한 배경'으로 둘지. 동물이 사진 끝까지 차 있으면 작게 준다.
    """
    import cv2  # 선택 의존성이라 여기서 불러온다

    img = Image.open(image) if isinstance(image, (str, Path)) else image
    img = img.convert("RGB")
    w, h = img.size
    scale = WORK_SIZE / max(w, h)
    small = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.BILINEAR)
    arr = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2BGR)
    sh, sw = arr.shape[:2]
    dx, dy = max(1, int(sw * margin)), max(1, int(sh * margin))
    rect = (dx, dy, sw - 2 * dx, sh - 2 * dy)

    mask = np.zeros((sh, sw), np.uint8)
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(arr, mask, rect, bgd, fgd, iterations, cv2.GC_INIT_WITH_RECT)
    except cv2.error:
        return None
    fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    if fg.mean() / 255 < MIN_FOREGROUND:
        return None
    return Image.fromarray(fg, "L").resize((w, h), Image.Resampling.NEAREST)

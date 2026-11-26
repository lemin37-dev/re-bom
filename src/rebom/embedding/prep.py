"""Gemini 에 보내기 전 사진 처리. 로컬 벤치마크(H1·I2)와 같은 규칙이다.

- 빈 사진: 회색조로 바꾸고 256×256 으로 줄여 픽셀 표준편차가 BLANK_STD 미만이면 보내지 않는다.
- 보낼 사진: EXIF 회전 보정 → RGB → 긴 변 1024 이하로 축소(늘리지 않음) → JPEG 품질 90 → image/jpeg.
"""
from __future__ import annotations

import io
import math
from typing import Sequence

PREP_VERSION = "exif-rgb-max1024-jpeg90"  # parquet 에 같이 남겨 전처리가 바뀌면 구분한다
MAX_SIDE = 1024
JPEG_QUALITY = 90
BLANK_SIDE = 256
BLANK_STD = 4.0
MIME = "image/jpeg"


def _open(data: bytes):
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(data))
    img.load()
    return ImageOps.exif_transpose(img)


def blank_std(data: bytes) -> float:
    """회색조 256×256 픽셀 표준편차. 단색·거의 빈 사진은 0 에 가깝다."""
    from PIL import ImageStat

    gray = _open(data).convert("L").resize((BLANK_SIDE, BLANK_SIDE))
    return ImageStat.Stat(gray).stddev[0]


def is_blank(data: bytes) -> bool:
    return blank_std(data) < BLANK_STD


def to_jpeg(data: bytes) -> bytes:
    img = _open(data).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))  # 작은 사진은 그대로 둔다
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY)
    return out.getvalue()


def l2_normalize(vec: Sequence[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vec))
    if n == 0:
        raise ValueError("길이 0 벡터")
    return [x / n for x in vec]

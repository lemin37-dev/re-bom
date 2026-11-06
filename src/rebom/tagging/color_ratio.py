"""털색 비율: 사진 픽셀 색을 기본 색(PALETTE)으로 나눠 센다.

Jev 확률은 '어느 보기가 맞는지'의 확신이라 면적 비율로 쓸 수 없어서 따로 구한다.
mask 가 없으면 가운데 영역만 쓰고, 배경 제거 마스크(background.grabcut_mask)가 있으면 mask 로 넘긴다.
"""
from __future__ import annotations

import colorsys
from pathlib import Path

from PIL import Image

from .photo_tags import PALETTE

MIN_SHARE = 0.05  # 이보다 적은 색은 잡음으로 보고 버린다


def pixel_color(r: int, g: int, b: int) -> str | None:
    """RGB 한 점 → 기본 색 이름. 초록·파랑처럼 털색이 아닌 것은 None(배경으로 보고 뺌)."""
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    deg = h * 360
    if v < 0.2:
        return "검정"
    if s < 0.15:
        if v > 0.8:
            return "흰색"
        return "검정" if v < 0.3 else "회색"
    if deg >= 330 or deg < 50:
        if v < 0.55:
            return "갈색"
        if s > 0.55 and deg < 35:
            return "주황"
        return "크림·황토"
    if deg < 70 and s < 0.5:
        return "크림·황토"
    if 180 <= deg < 260 and s < 0.3:  # 푸른빛이 도는 회색(청회색 고양이)
        return "회색"
    return None


def color_ratio(
    image: Image.Image | str | Path,
    *,
    center: float = 0.7,
    size: int = 96,
    mask: Image.Image | None = None,
) -> dict[str, float]:
    """사진의 털색 비율 {색: 0~1}. 합은 1. 셀 수 있는 픽셀이 없으면 {}.

    center: 가운데 몇 % 영역만 쓸지(배경을 줄이려고). mask가 있으면 center 대신 mask>0 인 곳만 쓴다.
    """
    img = Image.open(image) if isinstance(image, (str, Path)) else image
    img = img.convert("RGB")
    if mask is None:
        w, h = img.size
        dx, dy = int(w * (1 - center) / 2), int(h * (1 - center) / 2)
        img = img.crop((dx, dy, w - dx, h - dy))
    # NEAREST: 경계에서 섞인 가짜 색이 생기지 않게
    img = img.resize((size, size), Image.Resampling.NEAREST)
    m = mask.convert("L").resize((size, size), Image.Resampling.NEAREST) if mask is not None else None

    counts = dict.fromkeys(PALETTE, 0)
    pixels = img.load()
    mp = m.load() if m is not None else None
    for y in range(size):
        for x in range(size):
            if mp is not None and mp[x, y] == 0:
                continue
            name = pixel_color(*pixels[x, y])
            if name:
                counts[name] += 1
    total = sum(counts.values())
    if not total:
        return {}
    shares = {k: c / total for k, c in counts.items() if c / total >= MIN_SHARE}
    kept = sum(shares.values())
    return {k: v / kept for k, v in sorted(shares.items(), key=lambda kv: -kv[1])}

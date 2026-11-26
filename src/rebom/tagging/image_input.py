"""모델에 보낼 사진 준비. 평가(vision_tags.prepare_image)와 같다: EXIF 회전만 바로잡고, 회전 정보가 없으면 원본 그대로.

크기를 줄이거나 다시 압축하지 않는다(해상도는 요청의 media_resolution 으로 정한다). 400px 썸네일은 쓰지 않는다.

MAX_IMAGE_BYTES 를 넘는 사진은 보내지 않고 실패로 둔다. base64 로 늘면 요청 크기 한도(인라인 20MB)에 걸리기 때문이다.
평가 원본 1,191장은 중앙값 290KB, 최대 1.1MB 였다(2026-11-20 측정).
"""
from __future__ import annotations

import io

MAX_IMAGE_BYTES = 10 * 1024 * 1024


class ImageTooLargeError(ValueError):
    pass


def sniff_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


def prepare_image(data: bytes) -> tuple[bytes, str]:
    """(bytes, mime). 회전된 사진만 바로 세워 JPEG(품질 92)로 다시 쓴다. 너무 크면 ImageTooLargeError."""
    from PIL import Image, ImageOps

    mime = sniff_mime(data)
    with Image.open(io.BytesIO(data)) as im:
        orientation = im.getexif().get(0x0112, 1)
        if orientation in (None, 1):
            return _within_limit(data), mime
        fixed = ImageOps.exif_transpose(im).convert("RGB")
    buf = io.BytesIO()
    fixed.save(buf, format="JPEG", quality=92)
    return _within_limit(buf.getvalue()), "image/jpeg"


def _within_limit(data: bytes) -> bytes:
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageTooLargeError(f"IMAGE_TOO_LARGE {len(data)} bytes > {MAX_IMAGE_BYTES}")
    return data

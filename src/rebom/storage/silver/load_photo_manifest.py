"""A팀 사진 Manifest를 B팀 Silver 입력으로 변환한다."""

import json
import re
from pathlib import Path, PurePosixPath


def load_photo_manifest(path):
    path = Path(path)
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))

    run_id = manifest.get("run_id")
    bronze_run_id = manifest.get("bronze_run_id")
    status = manifest.get("status")
    files = manifest.get("files")

    if not run_id or not bronze_run_id:
        raise ValueError("사진 실행 ID 또는 Bronze 실행 ID 누락")

    if status not in {"ok", "aborted", "in_progress"}:
        raise ValueError(f"알 수 없는 사진 실행 상태: {status}")

    if not isinstance(files, list):
        raise ValueError("사진 files 형식 오류")

    rows = {}
    conflicts = []

    for item in files:
        no = str(item.get("desertionNo") or "").strip()
        slot = item.get("slot")
        original = item.get("original")
        serving = item.get("serving")

        if not no or type(slot) is not int or not 1 <= slot <= 8:
            raise ValueError("사진 공고번호 또는 슬롯 오류")

        if not isinstance(original, str) or not isinstance(serving, dict):
            raise ValueError("사진 경로 정보 오류")

        if re.fullmatch(r"[0-9a-fA-F]{64}", str(item.get("sha256") or "")) is None:
            raise ValueError("사진 SHA-256 형식 오류")

        expected_original = f"bronze/rescue/images/{no}/popfile{slot}."
        expected_image = f"images/{no}/{slot}.webp"
        expected_thumb = f"images/{no}/{slot}_thumb.webp"

        if (
            not original.startswith(expected_original)
            or PurePosixPath(original).name != f"popfile{slot}.{item.get('ext')}"
            or serving.get("image") != expected_image
            or serving.get("thumb") != expected_thumb
        ):
            raise ValueError("사진 경로와 공고번호·슬롯 불일치")

        row = {
            "desertion_no": no,
            "image_slot": slot,
            "original_s3_key": original,
            "serving_s3_key": expected_image,
            "thumb_s3_key": expected_thumb,
            "sha256": item["sha256"].lower(),
        }

        key = (no, slot)

        if key in rows and rows[key] != row:
            conflicts.append(key)
        else:
            rows[key] = row

    if conflicts:
        raise ValueError(f"사진 중복 충돌: {conflicts[:5]}")

    return {
        "run_id": run_id,
        "bronze_run_id": bronze_run_id,
        "status": status,
        "rows": list(rows.values()),
        "updated_at": manifest.get("updated_at"),
    }

"""실행별 처리 상태를 JSON에 원자적으로 기록한다."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


STAGES = (
    "PREPARED",
    "NOTICE_APPLIED",
    "HISTORY_APPLIED",
    "COMPLETED",
)


def journal_path(run_id, directory):
    if not run_id or not all(
        c.isascii() and (c.isalnum() or c in "-_")
        for c in run_id
    ):
        raise ValueError("잘못된 run_id")

    return Path(directory) / f"{run_id}.json"


def read_run(run_id, directory):
    path = journal_path(run_id, directory)

    if not path.exists():
        return None

    record = json.loads(path.read_text(encoding="utf-8"))

    if record.get("run_id") != run_id:
        raise ValueError("실행 기록 ID 불일치")

    if record.get("stage") not in STAGES:
        raise ValueError("잘못된 실행 상태")

    return record


def write_run(run_id, directory, stage):
    if stage not in STAGES:
        raise ValueError("알 수 없는 처리 상태")

    path = journal_path(run_id, directory)
    path.parent.mkdir(parents=True, exist_ok=True)

    previous = read_run(run_id, directory)

    if previous is not None:
        old_index = STAGES.index(previous["stage"])
        new_index = STAGES.index(stage)

        if new_index < old_index:
            raise ValueError("처리 상태를 이전 단계로 되돌릴 수 없습니다.")

        if new_index > old_index + 1:
            raise ValueError("중간 처리 단계를 건너뛸 수 없습니다.")

        if new_index == old_index:
            return previous

    elif stage != "PREPARED":
        raise ValueError("최초 실행은 PREPARED부터 시작해야 합니다.")

    record = {
        "run_id": run_id,
        "stage": stage,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    # 같은 디렉터리에 임시 파일을 만든 뒤 교체한다.
    # 도중에 실패해도 기존 기록 파일의 부분 쓰기를 방지한다.
    fd, temporary = tempfile.mkstemp(
        prefix=".run-",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(record, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())

        os.replace(temporary, path)

    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

    return record

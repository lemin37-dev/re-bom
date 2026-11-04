"""A팀 Bronze 파일의 무결성을 검증하고 Silver 입력을 준비한다."""

import gzip
import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from rebom.storage.silver.validate_manifest import validate_partition


# A팀 Bronze 수집 계약: 원본 데이터 3종만 허용한다.
# events와 lookup은 별도 Silver 이벤트 단계에서 처리한다.
REQUIRED_KINDS = {"updated", "list_notice", "list_protect"}
ALLOWED_KINDS = REQUIRED_KINDS

# 원본에 들어오더라도 Silver 적재 전에 차단할 필드다.
FORBIDDEN_FIELDS = {"careOwnerNm", "callName", "callTel"}


def sha256_file(path):
    # 체크섬은 압축을 푼 내용이 아니라 실제 .gz 파일 바이트로 계산한다.
    # 파일을 한 번에 메모리로 읽지 않고 일정한 크기로 나누어 처리한다.
    digest = hashlib.sha256()

    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def read_jsonl_gz(path):
    # JSONL은 한 줄에 하나의 JSON 객체가 저장된 형식이다.
    # 잘못된 JSON이나 배열이 섞이면 적재하지 않는다.
    rows = []

    with gzip.open(path, "rt", encoding="utf-8-sig") as f:
        for line_number, line in enumerate(f, 1):
            if not line.strip():
                raise ValueError(f"{path.name}:{line_number} 빈 줄 발견")

            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"{path.name}:{line_number} JSON 파싱 실패"
                ) from e

            if not isinstance(row, dict):
                raise ValueError("JSONL 항목은 객체여야 합니다.")

            # A팀 수집 단계에서 제외하기로 한 개인정보 필드를 검사한다.
            forbidden = FORBIDDEN_FIELDS.intersection(row)

            if forbidden:
                raise ValueError(
                    f"{path.name}: 저장 금지 필드 발견: {sorted(forbidden)}"
                )

            rows.append(row)

    return rows


def load_bronze_run(manifest_path):
    # manifest와 원본 파일은 같은 실행 폴더에 있어야 한다.
    # 실제 파일을 열기 전 실행 메타와 파일 목록부터 검사한다.
    manifest_path = Path(manifest_path)
    base_dir = manifest_path.parent.resolve()

    with manifest_path.open(encoding="utf-8-sig") as f:
        manifest = json.load(f)

    if manifest.get("status") != "ok":
        raise ValueError("정상 완료되지 않은 수집 실행입니다.")

    run_id = manifest.get("run_id")
    fetched_at = manifest.get("fetched_at")

    # A팀에서 확정한 한국시간 + 무작위 6자리 접미사 형식이다.
    if not isinstance(run_id, str) or not re.fullmatch(
        r"\d{8}T\d{6}-[0-9a-fA-F]{6}", run_id
    ):
        raise ValueError("run_id 형식 오류")

    try:
        observed = datetime.fromisoformat(fetched_at)
    except (TypeError, ValueError) as e:
        raise ValueError("fetched_at 형식 오류") from e

    if observed.utcoffset() != timedelta(hours=9):
        raise ValueError("fetched_at의 한국시간 정보가 올바르지 않습니다.")

    if observed.strftime("%Y%m%dT%H%M%S") != run_id.split("-")[0]:
        raise ValueError("run_id와 fetched_at이 일치하지 않습니다.")

    # 파일 경로의 실행 ID가 다른 경우도 잘못된 입력으로 처리한다.
    if base_dir.name.startswith("run_id="):
        if base_dir.name != f"run_id={run_id}":
            raise ValueError("파일 경로와 manifest run_id가 다릅니다.")

    files = manifest.get("files")

    if not isinstance(files, list):
        raise ValueError("manifest.files가 목록이 아닙니다.")

    rows_by_kind = {}
    seen_kinds = set()

    for item in files:
        if not isinstance(item, dict):
            raise ValueError("manifest.files 항목 오류")

        kind = item.get("kind")
        filename = item.get("file")
        expected_count = item.get("record_count")
        expected_checksum = item.get("checksum")

        if kind not in ALLOWED_KINDS or kind in seen_kinds:
            raise ValueError(f"파일 종류가 중복되거나 잘못됐습니다: {kind}")

        seen_kinds.add(kind)

        # 상대경로만 허용하고, 상위 폴더 접근이나 임의 파일 열기를 막는다.
        if filename != f"{kind}.jsonl.gz":
            raise ValueError(f"허용되지 않은 파일명: {filename}")

        if type(expected_count) is not int or expected_count < 0:
            raise ValueError(f"{kind}: record_count 형식 오류")

        if not isinstance(expected_checksum, str) or not re.fullmatch(
            r"[0-9a-fA-F]{64}", expected_checksum
        ):
            raise ValueError(f"{kind}: checksum 형식 오류")

        file_path = (base_dir / filename).resolve()

        if file_path.parent != base_dir or not file_path.is_file():
            raise ValueError(f"{kind}: 입력 파일이 없거나 경로가 잘못됐습니다.")

        actual_checksum = sha256_file(file_path)

        if actual_checksum.lower() != expected_checksum.lower():
            raise ValueError(f"{kind}: CHECKSUM_MISMATCH")

        rows = read_jsonl_gz(file_path)

        if len(rows) != expected_count:
            raise ValueError(f"{kind}: RECORD_COUNT_MISMATCH")

        rows_by_kind[kind] = rows

    if not REQUIRED_KINDS.issubset(seen_kinds):
        raise ValueError("필수 Bronze 파일이 누락됐습니다.")

    # 파티션은 A/B 계약의 기존 검사 함수를 재사용한다.
    # 불완전 구간이 있다는 이유로 모든 수집 공고를 버리지는 않는다.
    # 대신 해당 구간의 부재만으로 종료를 추정하지 않는다.
    partitions = manifest.get("partitions")

    if not isinstance(partitions, list) or not partitions:
        raise ValueError("partitions가 없습니다.")

    incomplete = []

    for partition in partitions:
        if not isinstance(partition, dict):
            raise ValueError("partition 항목이 객체가 아닙니다.")

        complete, reason = validate_partition(
            result_code=partition.get("result_code"),
            start_date=partition.get("start_date"),
            end_date=partition.get("end_date"),
            total_count=partition.get("total_count"),
            received_count=partition.get("received_count"),
        )

        if partition.get("complete") is not complete:
            raise ValueError("A/B complete 판정 불일치")

        if partition.get("reason") != reason:
            raise ValueError("A/B reason 판정 불일치")

        if not complete:
            incomplete.append(partition)

    # 샘플용 manifest는 원본 전체 구간의 건수를 보존할 수 있다.
    # 따라서 partitions의 total_count를 샘플 파일의 행 수와
    # 직접 비교하지 않는다. 파일 자체의 건수는 files에서 검증한다.
    return {
        "run_id": run_id,
        "fetched_at": fetched_at,
        "rows_by_kind": rows_by_kind,
        "partitions": partitions,
        "incomplete_partitions": incomplete,
        "reprocess_ids": manifest.get("reprocess_ids", []),
    }

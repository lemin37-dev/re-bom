"""A팀 수집 manifest를 B팀의 실제 검증 함수로 확인하는 테스트."""

import json
import re
import subprocess
from datetime import datetime

from rebom.storage.silver.validate_manifest import validate_partition


# 1. 아직 병합되지 않은 PR #10의 manifest 샘플을 읽는다.
# git fetch로 받아 둔 FETCH_HEAD에서 파일 내용만 추출한다.
# 기존 브랜치를 바꾸거나 로컬 data/ 디렉터리를 수정하지 않는다.
manifest_path = "data/sample/ingest/manifest_baseline_20261102.json"

result = subprocess.run(
    ["git", "show", f"FETCH_HEAD:{manifest_path}"],
    capture_output=True,
    check=True,
)

manifest = json.loads(result.stdout.decode("utf-8-sig"))


# 2. 수집 실행 자체가 정상 종료됐는지 검사한다.
# 중단된 실행의 결과는 정상적인 Silver 입력으로 취급하지 않는다.
if manifest.get("status") != "ok":
    raise RuntimeError("수집 실행이 정상 완료되지 않았습니다.")

run_id = manifest.get("run_id")
fetched_at = manifest.get("fetched_at")

if not isinstance(run_id, str) or not run_id:
    raise ValueError("run_id가 없습니다.")

if not isinstance(fetched_at, str):
    raise ValueError("fetched_at이 없습니다.")

# fetched_at에는 +09:00 시간대가 포함돼야 한다.
fetched_datetime = datetime.fromisoformat(fetched_at)

if fetched_datetime.utcoffset() is None:
    raise ValueError("fetched_at에 시간대가 없습니다.")

if fetched_datetime.strftime("%Y%m%dT%H%M%S") != run_id.split("-")[0]:
    raise ValueError("run_id와 fetched_at이 일치하지 않습니다.")

print("RUN_ID:", run_id)
print("FETCHED_AT:", fetched_at)


# 3. manifest에 기록된 파일 목록을 확인한다.
# 파일 크기나 checksum이 실제 내용과 일치하는지까지
# 검증하려면 원본 JSONL.gz 파일이 추가로 필요하다.
files = manifest.get("files")

if not isinstance(files, list):
    raise ValueError("files 형식이 올바르지 않습니다.")

for item in files:
    if not isinstance(item, dict):
        raise ValueError("files 항목이 객체가 아닙니다.")

    if not isinstance(item.get("record_count"), int):
        raise ValueError("record_count가 정수가 아닙니다.")

    if item["record_count"] < 0:
        raise ValueError("record_count가 음수입니다.")

    checksum = item.get("checksum")

    if not isinstance(checksum, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", checksum
    ):
        raise ValueError("SHA-256 checksum 형식이 올바르지 않습니다.")

print("MANIFEST_FILES:", len(files))


# 4. A팀의 21개 수집 구간을 기존 B팀 함수로 재검증한다.
# 필드 이름이 기존 함수의 매개변수와 일치하므로 그대로 전달한다.
partitions = manifest.get("partitions")

if not isinstance(partitions, list) or not partitions:
    raise ValueError("partitions가 없거나 비어 있습니다.")

required_kinds = {"updated", "list_notice", "list_protect"}
actual_kinds = set()

verified = []
incomplete = []

for index, partition in enumerate(partitions, start=1):
    if not isinstance(partition, dict):
        raise ValueError(f"구간 {index}: 객체가 아닙니다.")

    kind = partition.get("kind")

    if kind not in required_kinds:
        raise ValueError(f"구간 {index}: 알 수 없는 kind={kind}")

    actual_kinds.add(kind)

    # A팀이 제공한 앞의 다섯 필드를 B팀 기존 함수에 전달한다.
    complete, reason = validate_partition(
        result_code=partition.get("result_code"),
        start_date=partition.get("start_date"),
        end_date=partition.get("end_date"),
        total_count=partition.get("total_count"),
        received_count=partition.get("received_count"),
    )

    # A팀이 계산한 결과와 B팀 검증 결과가 달라지면 중단한다.
    if partition.get("complete") is not complete:
        raise RuntimeError(
            f"구간 {index}: A/B complete 판정 불일치"
        )

    if partition.get("reason") != reason:
        raise RuntimeError(
            f"구간 {index}: A/B reason 판정 불일치"
        )

    # 재처리 목록에는 공고번호 문자열만 들어와야 한다.
    reprocess_ids = partition.get("reprocess_ids")

    if not isinstance(reprocess_ids, list) or not all(
        isinstance(value, str) and value
        for value in reprocess_ids
    ):
        raise ValueError(f"구간 {index}: reprocess_ids 형식 오류")

    verified.append(partition)

    if not complete:
        incomplete.append(partition)

    print(
        f"PARTITION {index:02d}: "
        f"{kind} "
        f"{partition['start_date']}~{partition['end_date']} "
        f"{partition['received_count']}/{partition['total_count']} "
        f"{reason}"
    )

if actual_kinds != required_kinds:
    raise RuntimeError("필수 수집 종류가 누락됐습니다.")


# 5. 사라진 공고의 종료 판정 가능 여부를 결정하는 함수를 만든다.
#
# 같은 발생일을 포함하는 notice와 protect 구간이
# 모두 완전해야 목록 부재만으로 종료 판정이 가능하다.
#
# 이 함수는 종료 처리를 수행하지 않는다.
# 오직 종료 판정의 근거가 충분한지 확인한다.
def can_confirm_missing_from_partitions(happen_date):
    covering = {}

    for partition in verified:
        if partition["kind"] not in ("list_notice", "list_protect"):
            continue

        if partition["start_date"] <= happen_date <= partition["end_date"]:
            kind = partition["kind"]

            if kind in covering:
                raise RuntimeError(f"중복된 {kind} 조회 구간")

            covering[kind] = partition

    return (
        set(covering) == {"list_notice", "list_protect"}
        and all(p["complete"] for p in covering.values())
    )


# 6. 실제 샘플의 정상 구간과 불완전 구간을 시험한다.
# 2019년은 두 보호중 목록이 모두 완전한 구간이다.
# 2022년과 2025년은 protect 구간의 수신 건수가 부족하다.
tests = [
    ("2019-01-01", True),
    ("2022-01-01", False),
    ("2025-01-01", False),
]

for happen_date, expected in tests:
    actual = can_confirm_missing_from_partitions(happen_date)

    print(
        "MISSING_CONFIRMATION:",
        happen_date,
        "ALLOWED" if actual else "BLOCKED",
    )

    if actual != expected:
        raise RuntimeError(
            f"{happen_date}: 종료 판정 안전성 검증 실패"
        )


# 7. 11월 2일 기준선 샘플의 알려진 결과와 비교한다.
# 샘플이 변경되면 예상값도 별도로 검토해야 한다.
complete_count = len(verified) - len(incomplete)

print("TOTAL_PARTITIONS:", len(verified))
print("COMPLETE_PARTITIONS:", complete_count)
print("INCOMPLETE_PARTITIONS:", len(incomplete))

if (len(verified), complete_count, len(incomplete)) != (21, 19, 2):
    raise RuntimeError("기준선 샘플의 구간 개수가 예상과 다릅니다.")

if any(p["kind"] != "list_protect" for p in incomplete):
    raise RuntimeError("예상하지 못한 종류의 불완전 구간입니다.")


# 8. 첫 기준선 실행에서는 사라짐·복귀 이벤트가 없었다.
# 따라서 이 테스트는 실제 이벤트 처리 성공을 의미하지 않는다.
print("EVENT_INTEGRATION:", "NOT_TESTED")
print("ICEBERG_WRITE:", "NOT_PERFORMED")
print("RESULT: PASS")

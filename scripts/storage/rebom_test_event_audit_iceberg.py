"""A팀 이벤트 처리 결과와 수집 메타데이터의 Iceberg 저장 테스트."""

import gzip
import json
import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pyarrow as pa

from pyiceberg.catalog import load_catalog
from pyiceberg.schema import Schema
from pyiceberg.types import NestedField, StringType

from rebom.storage.silver.resolve_ingest_events import resolve_ingest_events


# 1. 테스트에서 사용할 수집 실행 정보를 정의한다.
#
# run_id는 A팀에서 확정한 형식과 같은 구조로 작성한다.
# 실제 수집 실행 ID가 아니라 테스트용 가상 실행 ID다.
#
# fetched_at은 수집 시각이다.
# observed_on 같은 날짜 정보와 달리 시각·시간대까지 포함한다.
run_id = "20261102T160000-mock01"
fetched_at = "2026-11-02T16:00:00+09:00"
source_path = "mock/events.jsonl.gz"

if not re.fullmatch(r"\d{8}T\d{6}-[0-9a-zA-Z]{6}", run_id):
    raise ValueError("run_id 형식 오류")

fetched = datetime.fromisoformat(fetched_at)

if fetched.utcoffset() != timedelta(hours=9):
    raise ValueError("fetched_at은 한국 시간이어야 합니다.")

if fetched.strftime("%Y%m%dT%H%M%S") != run_id.split("-")[0]:
    raise ValueError("run_id와 fetched_at 시각 불일치")


# 2. 종료·복귀·충돌 상황을 구성한다.
#
# A: 공고가 사라졌고 종료 근거가 있음
# B: 사라짐 이벤트와 실제 종료 공고가 동시에 존재함
# C: 사라짐 이벤트가 있지만 현재 보호중 상태임
# D: 종료에서 보호중으로 복귀함
#
# 실제 A팀 Bronze 데이터가 아닌 테스트용 데이터다.
previous = [
    {"desertionNo": "A", "processState": "보호중"},
    {"desertionNo": "B", "processState": "보호중"},
    {"desertionNo": "C", "processState": "보호중"},
    {"desertionNo": "D", "processState": "종료(입양)"},
]

current = [
    {"desertionNo": "B", "processState": "종료(입양)"},
    {"desertionNo": "C", "processState": "보호중"},
    {"desertionNo": "D", "processState": "보호중"},
]

events = [
    {
        "event": "vanished",
        "desertionNo": "A",
        "confirmed_by": "complete_range",
        "detected_at": fetched_at,
    },
    {
        "event": "vanished",
        "desertionNo": "B",
        "confirmed_by": "lookup",
        "detected_at": fetched_at,
    },
    {
        "event": "vanished",
        "desertionNo": "C",
        "confirmed_by": "complete_range",
        "detected_at": fetched_at,
    },
    {
        "event": "returned",
        "desertionNo": "D",
        "detected_at": fetched_at,
    },
]


# 3. A팀 형식의 events.jsonl.gz를 임시 폴더에 만든다.
# 이 파일은 테스트 종료 후 자동 삭제된다.
# 실제 Bronze 폴더와 기존 수집 상태 파일은 건드리지 않는다.
with tempfile.TemporaryDirectory(prefix="rebom_events_") as tmp:
    event_file = Path(tmp) / "events.jsonl.gz"

    with gzip.open(event_file, "wt", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    loaded_events = []

    with gzip.open(event_file, "rt", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue

            event = json.loads(line)

            if not isinstance(event, dict):
                raise ValueError(f"이벤트 {line_number}: JSON 객체가 아닙니다.")

            loaded_events.append(event)

print("EVENTS_READ:", len(loaded_events))


# 4. 기존 B팀 이벤트 분류 함수를 호출한다.
# confirmed_missing_ids에는 실제 현재 스냅샷에 없는 A만 들어간다.
resolved = resolve_ingest_events(
    previous,
    current,
    loaded_events,
)

if resolved["confirmed_missing_ids"] != ["A"]:
    raise RuntimeError("확정 누락 공고 분류 실패")

if resolved["actual_closed_ids"] != ["B"]:
    raise RuntimeError("실제 종료 공고 분류 실패")

if resolved["returned_ids"] != ["D"]:
    raise RuntimeError("보호중 복귀 분류 실패")

print("CONFIRMED_MISSING:", resolved["confirmed_missing_ids"])
print("ACTUAL_CLOSED:", resolved["actual_closed_ids"])
print("RETURNED:", resolved["returned_ids"])
print("CONFLICTS:", len(resolved["conflicts"]))


# 5. 공고별 B팀 처리 결과를 만든다.
#
# confirmed_missing: 실제 공고가 없어 추정 종료 처리 가능
# actual_closed: 실제 종료 상태가 있어 일반 상태 변경
# returned: 보호중 복귀
# conflict: 이벤트와 현재 공고 상태가 충돌
#
# 충돌도 기록한다. 운영자가 문제를 확인할 근거가 되기 때문이다.
decisions = {}

for key in resolved["confirmed_missing_ids"]:
    decisions[key] = ("confirmed_missing", None)

for key in resolved["actual_closed_ids"]:
    decisions[key] = ("actual_closed", None)

for key in resolved["returned_ids"]:
    decisions[key] = ("returned", None)

for key, event_kind, reason in resolved["conflicts"]:
    decisions[key] = ("conflict", reason)


# 6. 수집 메타데이터와 이벤트 처리 결과를 연결한다.
#
# confirmed_by는 A팀이 어떤 방식으로 사라짐을 확인했는지 뜻한다.
# run_id는 어느 수집 실행에서 나온 이벤트인지 추적하는 값이다.
#
# 이 감사 테이블은 Silver 상태 이력 테이블과 별도로 저장한다.
audit_rows = []

for event in loaded_events:
    key = str(event["desertionNo"])
    resolution, conflict_reason = decisions[key]

    audit_rows.append({
        "run_id": run_id,
        "desertion_no": key,
        "event_type": event["event"],
        "fetched_at": fetched_at,
        "detected_at": event.get("detected_at"),
        "confirmed_by": event.get("confirmed_by"),
        "resolution": resolution,
        "conflict_reason": conflict_reason,
        "source_path": source_path,
    })

if len(audit_rows) != 4:
    raise RuntimeError("이벤트 감사 기록 건수 오류")

print("AUDIT_PREPARED:", len(audit_rows))


# 7. 기존 로컬 Iceberg Catalog에 연결한다.
# 실습용 별도 테이블을 만들고 기존 상태 이력은 변경하지 않는다.
base = Path(os.environ["LOCALAPPDATA"]) / "rebom-iceberg"

catalog = load_catalog(
    "rebom_local",
    type="sql",
    uri=f"sqlite:///{(base / 'catalog.db').as_posix()}",
    warehouse=str(base / "warehouse"),
)

table_name = "rebom_silver.silver_ingest_event_audit_test"

# Iceberg 컬럼 구조를 명시한다.
#
# 첫 네 컬럼은 필수값이다.
# fetched_at, detected_at은 v0 테스트에서 원문 문자열로 보존한다.
# 추후 운영 스키마에서는 timestamp with time zone을 검토한다.
audit_schema = Schema(
    NestedField(1, "run_id", StringType(), required=True),
    NestedField(2, "desertion_no", StringType(), required=True),
    NestedField(3, "event_type", StringType(), required=True),
    NestedField(4, "fetched_at", StringType(), required=True),
    NestedField(5, "detected_at", StringType(), required=False),
    NestedField(6, "confirmed_by", StringType(), required=False),
    NestedField(7, "resolution", StringType(), required=True),
    NestedField(8, "conflict_reason", StringType(), required=False),
    NestedField(9, "source_path", StringType(), required=True),
)

if not catalog.table_exists(table_name):
    catalog.create_table(table_name, schema=audit_schema)
    print("CREATED_TABLE:", table_name)

table = catalog.load_table(table_name)

# 이미 같은 이름의 테이블이 있다면 스키마를 먼저 확인한다.
if table.schema() != audit_schema:
    raise RuntimeError("기존 테스트 테이블 스키마가 예상과 다릅니다.")

arrow = pa.Table.from_pylist(
    audit_rows,
    schema=table.schema().as_arrow(),
)


# 8. 같은 실행·공고·이벤트 종류를 기준으로 UPSERT한다.
#
# run_id가 같으면 재실행해도 동일 감사 기록을 갱신·유지한다.
# 새로운 run_id는 다른 수집 실행이므로 별도의 감사 기록으로 남긴다.
#
# 이 규칙은 실행별 감사 기록용이며
# 상태 이력의 최종 멱등성 키를 확정한 것은 아니다.
result = table.upsert(
    arrow,
    join_cols=["run_id", "desertion_no", "event_type"],
)

print("UPSERT_INSERTED:", result.rows_inserted)
print("UPSERT_UPDATED:", result.rows_updated)


# 9. Iceberg에서 다시 읽어 실제 저장된 결과를 검증한다.
stored = catalog.load_table(table_name).scan().to_arrow().to_pylist()

def index_rows(rows):
    indexed = {}

    for row in rows:
        key = (
            row["run_id"],
            row["desertion_no"],
            row["event_type"],
        )

        if key in indexed:
            raise RuntimeError("중복된 감사 기록 발견")

        indexed[key] = row

    return indexed


if index_rows(stored) != index_rows(audit_rows):
    raise RuntimeError("감사 기록 저장 결과 불일치")

print("VERIFIED_AUDIT_ROWS:", len(stored))


# 10. 같은 실행을 다시 수행해 중복 삽입 여부를 확인한다.
rerun = catalog.load_table(table_name).upsert(
    arrow,
    join_cols=["run_id", "desertion_no", "event_type"],
)

print("RERUN_INSERTED:", rerun.rows_inserted)
print("RERUN_UPDATED:", rerun.rows_updated)

if rerun.rows_inserted != 0 or rerun.rows_updated != 0:
    raise RuntimeError("동일 실행 재처리에서 불필요한 변경 발생")

final_rows = catalog.load_table(table_name).scan().to_arrow().to_pylist()

if index_rows(final_rows) != index_rows(audit_rows):
    raise RuntimeError("재실행 후 감사 기록 불일치")

print("FINAL_AUDIT_ROWS:", len(final_rows))
print("ACTUAL_A_TEAM_EVENTS:", "NOT_TESTED")
print("PRODUCTION_STATUS_HISTORY:", "NOT_MODIFIED")
print("RESULT: PASS")

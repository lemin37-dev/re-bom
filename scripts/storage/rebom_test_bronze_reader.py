"""Bronze JSONL.gz 읽기, 중복 제거, Silver 정제 테스트."""

import gzip
import json
import tempfile
from pathlib import Path

from rebom.storage.silver.transform_notice import transform_notice
from rebom.storage.silver.transform_image import transform_images
from rebom.storage.silver.transform_shelter import transform_shelters


# 1. 샘플 데이터 두 개를 읽는다.
# 실제 A팀 파일이 아직 없으므로 테스트 입력을 직접 구성한다.
sample_dir = Path("data/sample/incremental")

with (sample_dir / "t0_2026-10-28.json").open(encoding="utf-8-sig") as f:
    previous = json.load(f)

with (sample_dir / "truth_2026-10-30.json").open(encoding="utf-8-sig") as f:
    current = json.load(f)

if len(previous) != 87 or len(current) != 115:
    raise RuntimeError("샘플 데이터 건수가 예상과 다릅니다.")


# 2. JSONL.gz 파일을 만드는 함수다.
# JSONL은 JSON 객체를 한 줄에 하나씩 저장하는 형식이다.
# gzip은 이를 압축하는 방식이며 확장자는 .jsonl.gz가 된다.
def write_jsonl_gz(path, records):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# 3. A팀 Bronze 파일을 읽는 함수다.
# 한 줄씩 JSON 객체로 변환하므로 일반 JSON 배열 파일과 다르다.
def read_jsonl_gz(path):
    records = []

    with gzip.open(path, "rt", encoding="utf-8-sig") as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"{path.name}:{line_number} JSON 형식 오류"
                ) from e

            if not isinstance(record, dict):
                raise ValueError(
                    f"{path.name}:{line_number} JSON 객체가 아닙니다."
                )

            records.append(record)

    return records


# 4. 같은 공고가 여러 파일에 나올 수 있다.
# 이번 모의 데이터에서는 updated에 최신 115건이 들어가고
# list_notice에는 이전 87건이 들어가도록 구성한다.
#
# 실제 A팀 데이터에서의 우선순위는 별도 계약·검증이 필요하다.
# 여기서는 최신 정답 스냅샷이 담긴 updated를 우선하는
# 테스트 전용 규칙만 사용한다.
source_priority = {
    "list_notice": 1,
    "list_protect": 2,
    "updated": 3,
}


def merge_records(files):
    merged = {}
    duplicates = 0

    for kind in ("list_notice", "list_protect", "updated"):
        records = read_jsonl_gz(files[kind])

        print(f"BRONZE_READ_{kind.upper()}:", len(records))

        for record in records:
            key = str(record.get("desertionNo") or "").strip()

            if not key:
                raise ValueError("desertionNo가 없는 공고입니다.")

            if key in merged:
                duplicates += 1

                old_kind, _ = merged[key]

                # 테스트 규칙상 우선순위가 높은 소스를 채택한다.
                # 실제 운영에서는 동일 실행의 내용 충돌·수집 시각과
                # 상태 변경 규칙을 검증한 뒤 결정해야 한다.
                if source_priority[kind] < source_priority[old_kind]:
                    continue

            merged[key] = (kind, record)

    return merged, duplicates


# 5. 임시 폴더에서만 압축 파일을 생성한다.
# 프로젝트의 data/bronze나 기존 Iceberg 테이블은 변경하지 않는다.
with tempfile.TemporaryDirectory(prefix="rebom_bronze_test_") as tmp:
    temp_dir = Path(tmp)

    files = {
        kind: temp_dir / f"{kind}.jsonl.gz"
        for kind in source_priority
    }

    # 이전 시점 데이터를 오래된 목록의 예시로 사용한다.
    write_jsonl_gz(files["list_notice"], previous)

    # 현재 시점에서 20건을 추려 중복된 목록을 흉내 낸다.
    write_jsonl_gz(files["list_protect"], current[:20])

    # 현재 정답 스냅샷 115건을 최신 데이터로 사용한다.
    write_jsonl_gz(files["updated"], current)

    merged, duplicates = merge_records(files)


# 6. 공고번호 기준으로 병합했으므로 115건만 남아야 한다.
if len(merged) != 115:
    raise RuntimeError(f"병합 후 공고 건수 오류: {len(merged)}")

print("BRONZE_DUPLICATES:", duplicates)
print("BRONZE_UNIQUE_NOTICES:", len(merged))


# 7. B팀에서 이미 만든 정제 함수를 재사용한다.
# source_type도 전달하므로 수집 출처를 활용할 수 있다.
silver_notices = []
silver_images = []
raw_records = []

for kind, raw in merged.values():
    silver_notices.append(transform_notice(raw, source_type=kind))
    silver_images.extend(transform_images(raw))
    raw_records.append(raw)

silver_shelters, conflicts, missing_keys = transform_shelters(raw_records)


# 8. 모든 Silver 공고번호가 고유한지 확인한다.
silver_ids = [row["desertion_no"] for row in silver_notices]

if len(silver_ids) != len(set(silver_ids)):
    raise RuntimeError("Silver 공고번호 중복이 발생했습니다.")


# 9. 병합 결과와 실제 10월 30일 정답 데이터를 비교한다.
# 이번 모의 테스트에서는 모든 공고의 최종 내용이 일치해야 한다.
expected = {
    str(row["desertionNo"]): transform_notice(row)
    for row in current
}

actual = {
    row["desertion_no"]: row
    for row in silver_notices
}

if actual != expected:
    raise RuntimeError("Silver 결과가 현재 정답 스냅샷과 다릅니다.")


# 10. 적재 전 단계의 결과만 출력한다.
# 보호소 충돌은 임의 수정하지 않고 검토 대상으로 남긴다.
print("SILVER_NOTICE_ROWS:", len(silver_notices))
print("SILVER_IMAGE_ROWS:", len(silver_images))
print("SILVER_SHELTER_ROWS:", len(silver_shelters))
print("SHELTER_CONFLICTS:", len(conflicts))
print("SHELTER_MISSING_KEYS:", len(missing_keys))
print("CONTENT_VERIFICATION: PASS")
print("ICEBERG_WRITE: NOT_PERFORMED")
print("RESULT: PASS")

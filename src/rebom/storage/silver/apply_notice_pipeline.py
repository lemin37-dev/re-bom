"""검증된 Bronze 공고 증분을 전용 Iceberg 테스트 테이블에 반영한다."""

import os
from pathlib import Path

import pyarrow as pa
from pyiceberg.catalog import load_catalog

from rebom.storage.silver.run_notice_pipeline import run_notice_pipeline


# 이 모듈에서는 정식 Silver 테이블을 쓰기 대상으로 허용하지 않는다.
SOURCE_TABLE = "rebom_silver.silver_animal_notice"
TEST_TABLE = "rebom_silver.silver_animal_notice_pipeline_apply_test"


def get_local_catalog():
    # 기존 로컬 Iceberg 환경과 동일한 Catalog를 사용한다.
    # SQLite Catalog는 테이블 메타데이터를 관리하고,
    # warehouse는 실제 Iceberg 데이터 파일을 저장한다.
    base = Path(os.environ["LOCALAPPDATA"]) / "rebom-iceberg"

    return load_catalog(
        "rebom_local",
        type="sql",
        uri=f"sqlite:///{(base / 'catalog.db').as_posix()}",
        warehouse=str(base / "warehouse"),
    )


def index_rows(rows):
    # Iceberg 공고를 desertion_no 기준으로 비교한다.
    indexed = {}

    for row in rows:
        key = str(row.get("desertion_no") or "").strip()

        if not key or key in indexed:
            raise ValueError(f"Silver 공고 키 오류: {key}")

        indexed[key] = row

    return indexed


def execute_notice_pipeline(manifest_path, apply=False):
    # 1. 허용된 테스트 테이블만 사용한다.
    # 정식 테이블은 조회만 허용하고 쓰기는 하지 않는다.
    catalog = get_local_catalog()

    if not catalog.table_exists(TEST_TABLE):
        raise RuntimeError(
            "전용 테스트 테이블이 없습니다. 먼저 테스트 초기화를 수행하세요."
        )

    source = catalog.load_table(SOURCE_TABLE)
    table = catalog.load_table(TEST_TABLE)

    if table.schema() != source.schema():
        raise RuntimeError("테스트 테이블과 Silver 스키마가 다릅니다.")

    # 2. Iceberg에 이미 저장된 공고를 읽는다.
    # 부분 증분에는 전체 공고가 없으므로 기존 값을 반드시 조회해야 한다.
    existing_rows = table.scan().to_arrow().to_pylist()
    existing_index = index_rows(existing_rows)

    # 3. 이전에 만든 공통 파이프라인을 재사용한다.
    # 내부 순서:
    # manifest 및 checksum 검증
    # -> Bronze 중복·충돌 판정
    # -> Silver 신규·변경분 생성
    prepared = run_notice_pipeline(manifest_path, existing_rows)

    if not prepared["ready"]:
        # 충돌이 있으면 해당 실행 전체에 쓰기를 수행하지 않는다.
        return {
            "ready": False,
            "applied": False,
            "run_id": prepared["run_id"],
            "conflicts": prepared["conflicts"],
            "existing_count": len(existing_rows),
        }

    delta_rows = prepared["delta_rows"]

    result = {
        "ready": True,
        "applied": False,
        "run_id": prepared["run_id"],
        "existing_count": len(existing_rows),
        "prepared_insert": prepared["insert_count"],
        "prepared_update": prepared["update_count"],
        "prepared_unchanged": prepared["unchanged_count"],
        "incomplete_partitions": len(prepared["incomplete_partitions"]),
        "expected_final_count": len(existing_rows) + prepared["insert_count"],
    }

    # 4. 기본 모드는 DRY RUN이다.
    # 조회와 변경분 계산까지만 하고 Iceberg는 변경하지 않는다.
    if not apply:
        result["mode"] = "DRY_RUN"
        return result

    # 5. 실제 쓰기 대상은 코드에 고정된 테스트 테이블 하나뿐이다.
    # 운영용 silver_animal_notice 테이블은 절대 수정하지 않는다.
    if not delta_rows:
        result["mode"] = "APPLY"
        result["applied"] = True
        result["rows_inserted"] = 0
        result["rows_updated"] = 0
        return result

    arrow = pa.Table.from_pylist(
        delta_rows,
        schema=table.schema().as_arrow(),
    )

    # 6. INSERT와 UPDATE를 하나의 UPSERT로 실행한다.
    # desertion_no가 동일하면 기존 행과 비교하고 변경된 값만 갱신한다.
    write_result = table.upsert(
        arrow,
        join_cols=["desertion_no"],
        when_matched_update_all=True,
        when_not_matched_insert_all=True,
    )

    # 7. 적재 후 실제 Iceberg에서 다시 읽어 전체 내용을 확인한다.
    # 새로운 데이터가 저장됐는지뿐 아니라 기존 공고가 유지됐는지도 확인한다.
    expected_index = dict(existing_index)
    expected_index.update(index_rows(delta_rows))

    stored_rows = catalog.load_table(TEST_TABLE).scan().to_arrow().to_pylist()
    stored_index = index_rows(stored_rows)

    if stored_index != expected_index:
        raise RuntimeError(
            "Iceberg 적재 후 전체 데이터 검증 실패: 수동 조사 필요"
        )

    if len(stored_rows) != result["expected_final_count"]:
        raise RuntimeError("Iceberg 최종 건수가 예상과 다릅니다.")

    result["mode"] = "APPLY"
    result["applied"] = True
    result["rows_inserted"] = write_result.rows_inserted
    result["rows_updated"] = write_result.rows_updated
    result["verified_final_count"] = len(stored_rows)

    return result

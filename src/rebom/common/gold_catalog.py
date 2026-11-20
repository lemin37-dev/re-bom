"""S3 Gold(Iceberg, Glue DB rebom_gold) 모델 결과 테이블: 스키마, 환경별 이름·위치, 테이블 만들기.

테이블 세 개 (docs/data_contract.md 13절):
- gold_animal_tag        태그 한 개가 한 행. 다시 태깅하면 (desertion_no, task) 단위로 통째 교체(overwrite)
- gold_inference_log     사진·질문지당 한 행. (desertion_no, task, version, input_hash) 로 upsert
- gold_model_request_log 모델 요청 한 번이 한 행. append 만, dt(요청 UTC 날짜) 파티션

테이블 만들기·칸 추가(create_tables)는 클라우드 리소스를 바꾸는 일이라 사람이 PC 관리자 프로필로 직접 실행한다:
  python -m rebom.common.gold_catalog --env test --create --profile rebom
노드 역할에는 glue:CreateTable 이 없어 파드에서는 만들 수 없다(읽기·쓰기만).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

DATA_BUCKET = "rebom-data-559987919299"
DATABASE = "rebom_gold"


@dataclass(frozen=True)
class GoldEnvironment:
    name: str
    database: str
    suffix: str  # 테이블 이름 뒤에 붙는 값 (test 는 _test)
    location_prefix: str  # s3://버킷/gold/...

    def table(self, base: str) -> str:
        return f"{self.database}.{base}{self.suffix}"

    def location(self, base: str) -> str:
        return f"{self.location_prefix.rstrip('/')}/{base}{self.suffix}"

    def work_prefix(self) -> str:
        """실행 중간 파일(대상 목록) 위치. 버킷 안 키 접두사."""
        return f"gold/_work{self.suffix}/photo_tags"


ENVIRONMENTS = {
    # Silver 가 아직 test(DRY_RUN) 환경이라 Gold 도 test 로 먼저 쓴다.
    "test": GoldEnvironment("test", DATABASE, "_test", f"s3://{DATA_BUCKET}/gold/_test/iceberg"),
    "prod": GoldEnvironment("prod", DATABASE, "", f"s3://{DATA_BUCKET}/gold/iceberg"),
}

TAG_TABLE = "gold_animal_tag"
INFERENCE_LOG_TABLE = "gold_inference_log"
REQUEST_LOG_TABLE = "gold_model_request_log"

INFERENCE_LOG_KEYS = ("desertion_no", "task", "version", "input_hash")


def get_gold_environment(name: str = "test") -> GoldEnvironment:
    try:
        return ENVIRONMENTS[name]
    except KeyError:
        raise ValueError(f"UNKNOWN_GOLD_ENVIRONMENT: {name}") from None


def schemas():
    """pyiceberg 스키마와 파티션 스펙. pyiceberg 는 storage extra 에만 있어 함수 안에서 import 한다."""
    from pyiceberg.partitioning import PartitionField, PartitionSpec
    from pyiceberg.schema import Schema
    from pyiceberg.transforms import IdentityTransform
    from pyiceberg.types import (
        BooleanType,
        DoubleType,
        IntegerType,
        LongType,
        NestedField,
        StringType,
        TimestamptzType,
    )

    def f(i, name, typ, required=False):
        return NestedField(field_id=i, name=name, field_type=typ, required=required)

    tag = Schema(
        f(1, "desertion_no", StringType(), True),
        f(2, "tag_code", StringType(), True),
        f(3, "source", StringType(), True),
        f(4, "tagger_version", StringType(), True),
        f(5, "task", StringType(), True),
        f(6, "model", StringType()),
        f(7, "prompt_version", StringType()),
        f(8, "image_slot", IntegerType()),
        f(9, "source_hash", StringType()),
        f(10, "searchable", BooleanType()),
        f(11, "displayable", BooleanType()),
        f(12, "rank", IntegerType()),
        f(13, "similarity", IntegerType()),
        f(14, "detail", StringType()),
        f(15, "gold_run_id", StringType()),
        f(16, "gold_updated_at", TimestamptzType()),
        identifier_field_ids=[1, 2, 3, 4],
    )
    inference = Schema(
        f(1, "desertion_no", StringType(), True),
        f(2, "task", StringType(), True),
        f(3, "version", StringType(), True),
        f(4, "input_hash", StringType(), True),
        f(5, "image_slot", IntegerType()),
        f(6, "status", StringType()),
        f(7, "attempts", IntegerType()),
        f(8, "tag_count", IntegerType()),
        f(9, "last_error", StringType()),
        f(10, "gold_run_id", StringType()),
        f(11, "gold_updated_at", TimestamptzType()),
        identifier_field_ids=[1, 2, 3, 4],
    )
    request = Schema(
        f(1, "request_id", StringType()),
        f(2, "desertion_no", StringType()),
        f(3, "task", StringType()),
        f(4, "prompt_version", StringType()),
        f(5, "run_no", IntegerType()),
        f(6, "requested_model", StringType()),
        f(7, "response_model", StringType()),
        f(8, "provider", StringType()),
        f(9, "canonical_slug", StringType()),
        f(10, "media_resolution", StringType()),
        f(11, "prompt_tokens", IntegerType()),
        f(12, "completion_tokens", IntegerType()),
        f(13, "reasoning_tokens", IntegerType()),
        f(14, "cost_usd", DoubleType()),
        f(15, "latency_ms", LongType()),
        f(16, "attempts", IntegerType()),
        f(17, "status", StringType()),
        f(18, "error_code", StringType()),
        f(19, "raw_answer", StringType()),
        f(20, "requested_at", TimestamptzType(), True),
        f(21, "gold_run_id", StringType()),
        f(22, "dt", StringType(), True),  # requested_at 의 UTC 날짜 YYYY-MM-DD (파티션)
        f(23, "temperature", DoubleType()),
        f(24, "reasoning_effort", StringType()),
        f(25, "finish_reason", StringType()),
        f(26, "native_finish_reason", StringType()),
    )
    # day 변환 파티션은 pyiceberg-core 가 있어야 써져서, 날짜 문자열 칸으로 identity 파티션을 둔다
    request_spec = PartitionSpec(PartitionField(source_id=22, field_id=1000, transform=IdentityTransform(), name="dt"))
    return {
        TAG_TABLE: (tag, PartitionSpec()),
        INFERENCE_LOG_TABLE: (inference, PartitionSpec()),
        REQUEST_LOG_TABLE: (request, request_spec),
    }


def create_tables(env: GoldEnvironment, catalog) -> list[str]:
    """없는 테이블을 만들고, 이미 있는 테이블에는 빠진 칸을 더한다. 바꾼 것 목록("만듦 이름", "칸 추가 이름: 칸들").

    GoldSink 는 테이블의 현재 스키마로 행을 만들어서, 칸이 없으면 새 값이 소리 없이 버려진다. 그래서 칸을 늘린 코드를
    배포하기 전에 이 명령으로 테이블을 맞춘다. 칸을 지우거나 타입을 바꾸지는 않는다.
    """
    changed = []
    if _has_namespace(catalog, env.database):
        existing = {".".join(t) for t in catalog.list_tables(env.database)}
    else:
        catalog.create_namespace(env.database)
        existing = set()
    for base, (schema, spec) in schemas().items():
        name = env.table(base)
        if name not in existing:
            catalog.create_table(name, schema=schema, partition_spec=spec, location=env.location(base))
            changed.append(f"만듦 {name}")
            continue
        table = catalog.load_table(name)
        have = {f.name for f in table.schema().fields}
        missing = [f.name for f in schema.fields if f.name not in have]
        if missing:
            with table.update_schema() as update:
                update.union_by_name(schema)
            changed.append(f"칸 추가 {name}: {', '.join(missing)}")
    return changed


def _has_namespace(catalog, database: str) -> bool:
    return (database,) in {tuple(n) for n in catalog.list_namespaces()}


def main() -> None:
    ap = argparse.ArgumentParser(description="Gold 모델 결과 테이블 확인·생성 (생성은 사람이 직접 실행)")
    ap.add_argument("--env", default="test", choices=sorted(ENVIRONMENTS))
    ap.add_argument("--create", action="store_true", help="없는 테이블을 만들고 빠진 칸을 더한다 (Glue·S3 리소스 변경)")
    ap.add_argument("--profile", default=None, help="AWS 프로필 (PC). 파드에서는 비운다")
    args = ap.parse_args()

    from rebom.storage.silver.aws_catalog import get_aws_catalog

    env = get_gold_environment(args.env)
    catalog = get_aws_catalog(profile=args.profile)
    for base in schemas():
        name = env.table(base)
        try:
            catalog.load_table(name)
            print(f"있음  {name}")
        except Exception:  # noqa: BLE001 - NoSuchTableError 등
            print(f"없음  {name}  → {env.location(base)}")
    if args.create:
        for line in create_tables(env, catalog):
            print(line)


if __name__ == "__main__":
    main()

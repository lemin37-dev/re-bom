"""S3 Gold(Iceberg, Glue DB rebom_gold) 모델 결과 테이블: 스키마, 환경별 이름·위치, 테이블 만들기.

모든 테이블은 공용 키 (subject_type, subject_key) 로 처리 대상을 가리킨다(2026-11-20 결정):
- subject_type = animal → subject_key = 공고번호 (글 태깅·글 임베딩, 사진 없음 기록)
- subject_type = photo  → subject_key = 사진 sha256 (사진 태깅·사진 임베딩). 같은 사진은 공고가 달라도 한 번만 처리하고,
  공고–사진 연결은 Silver 현재 사진 테이블에서 한다.

테이블 세 개 (docs/data_contract.md 13절):
- gold_animal_tag        태그 한 개가 한 행. 같은 대상·같은 질문지 버전으로 다시 태깅할 때만
                         (subject_type, subject_key, task, prompt_version) 범위를 교체(overwrite). 사진이 바뀌면 새 sha256 이
                         새 행이 되고, 질문지 버전을 올려도 이전 행은 남는다(이전 버전 보존)
- gold_inference_log     대상·작업·버전·입력당 한 행. (subject_type, subject_key, task, version, input_hash) 로 upsert
- gold_model_request_log 모델 요청 한 번이 한 행. append 만, dt(요청 UTC 날짜) 파티션

테이블 만들기·칸 추가(create_tables)는 클라우드 리소스를 바꾸는 일이라 사람이 PC 관리자 프로필로 직접 실행한다:
  python -m rebom.common.gold_catalog --env test --create --profile rebom
키 칸이 바뀐 옛 test 테이블은 --recreate 로 지우고 다시 만든다(test 만 허용, 사람이 실행). 노드 역할에는 glue:CreateTable 이
없어 파드에서는 만들 수 없다(읽기·쓰기만).
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

INFERENCE_LOG_KEYS = ("subject_type", "subject_key", "task", "version", "input_hash")
KEY_FIELDS = ("subject_type", "subject_key")  # 세 테이블 공용. 이 칸이 없는 테이블은 옛 스키마


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
        f(1, "subject_type", StringType(), True),
        f(2, "subject_key", StringType(), True),
        f(3, "tag_code", StringType(), True),
        f(4, "source", StringType(), True),
        f(5, "tagger_version", StringType(), True),
        f(6, "task", StringType(), True),
        f(7, "model", StringType()),
        f(8, "prompt_version", StringType(), True),
        f(9, "source_hash", StringType()),
        f(10, "searchable", BooleanType()),
        f(11, "displayable", BooleanType()),
        f(12, "rank", IntegerType()),
        f(13, "similarity", IntegerType()),
        f(14, "detail", StringType()),
        f(15, "gold_run_id", StringType()),
        f(16, "gold_updated_at", TimestamptzType()),
        identifier_field_ids=[1, 2, 6, 8, 3],
    )
    inference = Schema(
        f(1, "subject_type", StringType(), True),
        f(2, "subject_key", StringType(), True),
        f(3, "task", StringType(), True),
        f(4, "version", StringType(), True),
        f(5, "input_hash", StringType(), True),
        f(6, "status", StringType()),
        f(7, "attempts", IntegerType()),
        f(8, "tag_count", IntegerType()),
        f(9, "last_error", StringType()),
        f(10, "gold_run_id", StringType()),
        f(11, "gold_updated_at", TimestamptzType()),
        identifier_field_ids=[1, 2, 3, 4, 5],
    )
    request = Schema(
        f(1, "request_id", StringType()),
        f(2, "subject_type", StringType()),
        f(3, "subject_key", StringType()),
        f(4, "task", StringType()),
        f(5, "prompt_version", StringType()),
        f(6, "run_no", IntegerType()),
        f(7, "requested_model", StringType()),
        f(8, "response_model", StringType()),
        f(9, "provider", StringType()),
        f(10, "canonical_slug", StringType()),
        f(11, "media_resolution", StringType()),
        f(12, "prompt_tokens", IntegerType()),
        f(13, "completion_tokens", IntegerType()),
        f(14, "reasoning_tokens", IntegerType()),
        f(15, "cost_usd", DoubleType()),  # OpenRouter usage.cost 그대로 (임베딩도 같은 칸)
        f(16, "latency_ms", LongType()),
        f(17, "attempts", IntegerType()),
        f(18, "status", StringType()),
        f(19, "error_code", StringType()),
        f(20, "raw_answer", StringType()),
        f(21, "requested_at", TimestamptzType(), True),
        f(22, "gold_run_id", StringType()),
        f(23, "dt", StringType(), True),  # requested_at 의 UTC 날짜 YYYY-MM-DD (파티션)
        f(24, "temperature", DoubleType()),
        f(25, "reasoning_effort", StringType()),
        f(26, "finish_reason", StringType()),
        f(27, "native_finish_reason", StringType()),
    )
    # day 변환 파티션은 pyiceberg-core 가 있어야 써져서, 날짜 문자열 칸으로 identity 파티션을 둔다
    request_spec = PartitionSpec(PartitionField(source_id=23, field_id=1000, transform=IdentityTransform(), name="dt"))
    return {
        TAG_TABLE: (tag, PartitionSpec()),
        INFERENCE_LOG_TABLE: (inference, PartitionSpec()),
        REQUEST_LOG_TABLE: (request, request_spec),
    }


class OldSchemaError(RuntimeError):
    """키 칸(subject_type·subject_key)이 없는 옛 스키마 테이블. 칸 추가로는 맞출 수 없다."""


def create_tables(env: GoldEnvironment, catalog, *, recreate: bool = False) -> list[str]:
    """없는 테이블을 만들고, 이미 있는 테이블에는 빠진 칸을 더한다. 바꾼 것 목록("만듦 이름", "칸 추가 이름: 칸들").

    GoldSink 는 테이블의 현재 스키마로 행을 만들어서, 칸이 없으면 새 값이 소리 없이 버려진다. 그래서 칸을 늘린 코드를
    배포하기 전에 이 명령으로 테이블을 맞춘다. 칸을 지우거나 타입을 바꾸지는 않는다.
    키 칸이 없는 옛 테이블은 OldSchemaError. recreate=True 면 (test 환경만) 지우고 새로 만든다.
    """
    if recreate and env.name != "test":
        raise ValueError("RECREATE_TEST_ONLY: prod 테이블은 지우지 않는다")
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
        if not set(KEY_FIELDS) <= have:
            if not recreate:
                raise OldSchemaError(f"{name}: subject_type·subject_key 칸이 없는 옛 스키마. test 면 --recreate 로 다시 만든다")
            catalog.drop_table(name)
            catalog.create_table(name, schema=schema, partition_spec=spec, location=env.location(base))
            changed.append(f"다시 만듦 {name} (옛 행은 버림)")
            continue
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
    ap.add_argument("--recreate", action="store_true",
                    help="--create 와 함께: 키 칸이 없는 옛 test 테이블을 지우고 다시 만든다 (옛 행은 버림, test 만)")
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
        for line in create_tables(env, catalog, recreate=args.recreate):
            print(line)


if __name__ == "__main__":
    main()

"""S3 Gold(Iceberg, Glue DB rebom_gold) 모델 결과 테이블: 스키마, 환경별 이름·위치, 테이블 만들기.

모든 테이블은 공용 키 (subject_type, subject_key) 로 처리 대상을 가리킨다(2026-11-20 결정):
- subject_type = animal → subject_key = 공고번호 (글 태깅·글 임베딩, 사진 없음 기록)
- subject_type = photo  → subject_key = 사진 sha256 (사진 태깅·사진 임베딩). 같은 사진은 공고가 달라도 한 번만 처리하고,
  공고–사진 연결은 Silver 현재 사진 테이블에서 한다.

태깅 묶음 (docs/data_contract.md 13절):
- gold_animal_tag        태그 한 개가 한 행. 같은 대상·같은 질문지 버전으로 다시 태깅할 때만
                         (subject_type, subject_key, task, prompt_version) 범위를 교체(overwrite). 사진이 바뀌면 새 sha256 이
                         새 행이 되고, 질문지 버전을 올려도 이전 행은 남는다(이전 버전 보존)
- gold_inference_log     대상·작업·버전·입력당 한 행. (subject_type, subject_key, task, version, input_hash) 로 upsert
- gold_model_request_log 모델 요청 한 번이 한 행. append 만, dt(요청 UTC 날짜) 파티션
임베딩 묶음 (docs/data_contract.md 13.4절, 2026-11-21 결정: 처리·요청 기록도 태깅과 따로 둔다):
- gold_image_embedding   사진(sha256) 1장 × 모델 × 전처리 버전이 한 행. 같은 사진을 쓰는 공고·칸은 이 한 행을 같이 쓴다
- gold_text_embedding    공고 글 카드 1개 × 모델 × 템플릿 버전 × 카드 해시가 한 행. 가린 카드 본문(input_text)도 남긴다
- gold_embedding_canary  모델 변화 확인(canary) 1번이 한 행
- gold_embedding_inference_log  태깅 처리 기록과 같은 칸 + detail(판정 근거 JSON, 빈 사진의 회색조 표준편차 등)
- gold_embedding_request_log    태깅 요청 기록과 같은 칸 + batch_size. 요청 1번 = 1행(태깅과 같은 뜻, 11-23).
                                매일 실행은 사진 1장씩이라 batch_size = 1. 묶어 보내면 subject_key = 첫 사진,
                                batch_size = 장수, 비용은 요청 전체 값 (사진은 처리 기록 detail.request_id 로 이음)
두 묶음의 기록 표는 같은 칸 이름·타입이라 Athena 에서 UNION ALL 로 합쳐 볼 수 있다.

테이블 만들기·칸 추가(create_tables)는 클라우드 리소스를 바꾸는 일이라 사람이 PC 관리자 프로필로 직접 실행한다:
  python -m rebom.common.gold_catalog --env test --create --profile rebom
  python -m rebom.common.gold_catalog --env prod --create --group embedding --profile rebom   (임베딩 묶음만)
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
IMAGE_EMBEDDING_TABLE = "gold_image_embedding"
TEXT_EMBEDDING_TABLE = "gold_text_embedding"
EMBEDDING_CANARY_TABLE = "gold_embedding_canary"
EMBEDDING_INFERENCE_LOG_TABLE = "gold_embedding_inference_log"
EMBEDDING_REQUEST_LOG_TABLE = "gold_embedding_request_log"
VERSION_REGISTRY_TABLE = "gold_version_registry"

# --group: 한 묶음의 표만 만들 때. 묶음마다 결과·처리 기록·요청 기록 표를 따로 가진다
GROUPS = {
    "registry": (VERSION_REGISTRY_TABLE,),
    "tagging": (TAG_TABLE, INFERENCE_LOG_TABLE, REQUEST_LOG_TABLE),
    "embedding": (IMAGE_EMBEDDING_TABLE, TEXT_EMBEDDING_TABLE, EMBEDDING_CANARY_TABLE,
                  EMBEDDING_INFERENCE_LOG_TABLE, EMBEDDING_REQUEST_LOG_TABLE),
}

INFERENCE_LOG_KEYS = ("subject_type", "subject_key", "task", "version", "input_hash")
IMAGE_EMBEDDING_KEYS = ("subject_type", "subject_key", "task", "model", "prompt_version")
TEXT_EMBEDDING_KEYS = ("subject_type", "subject_key", "task", "model", "prompt_version", "source_hash")
KEY_FIELDS = ("subject_type", "subject_key")  # 대상 표 공용. 이 칸이 없는 대상 표는 옛 스키마 (canary 는 대상 표가 아님)


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
        FloatType,
        IntegerType,
        ListType,
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
        f(17, "version", StringType()),
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
        f(29, "version", StringType()),  # 28은 임베딩 요청 로그의 batch_size
    )
    # 임베딩 기록 표: 태깅 기록 표와 같은 칸(UNION ALL 가능) 뒤에 임베딩에만 필요한 칸을 더한다
    embedding_inference = Schema(
        *inference.fields,
        f(12, "detail", StringType()),  # 판정 근거 JSON. BLANK: {"gray_std": 1.2, "blank_std": 4.0}
        identifier_field_ids=list(inference.identifier_field_ids),
    )
    embedding_request = Schema(
        *request.fields,
        f(28, "batch_size", IntegerType()),  # 한 요청에 같이 보낸 대상 수 (매일 실행 1). cost_usd·토큰은 요청 전체 값
    )

    def vector(i):
        return f(i, "embedding", ListType(element_id=100, element_type=FloatType(), element_required=True), True)

    # 칸 이름은 태깅 표를 따른다: model, prompt_version(사진은 전처리 버전), source_hash(입력 해시)
    image_embedding = Schema(
        f(1, "subject_type", StringType(), True),  # photo
        f(2, "subject_key", StringType(), True),  # 사진 원본 sha256
        f(3, "task", StringType(), True),  # image_embedding
        f(4, "model", StringType(), True),  # 모델:차원, 예 gemini-embedding-2:768 (규칙 초안의 model_version)
        f(5, "prompt_version", StringType(), True),  # 전처리 버전, 예 exif-rgb-max1024-jpeg90
        f(6, "source_hash", StringType(), True),  # 사진은 subject_key 와 같은 sha256
        vector(7),
        f(8, "gold_run_id", StringType()),
        f(9, "gold_updated_at", TimestamptzType()),
        f(10, "version", StringType()),
        identifier_field_ids=[1, 2, 3, 4, 5],
    )
    text_embedding = Schema(
        f(1, "subject_type", StringType(), True),  # animal
        f(2, "subject_key", StringType(), True),  # 공고번호
        f(3, "task", StringType(), True),  # text_embedding
        f(4, "model", StringType(), True),
        f(5, "prompt_version", StringType(), True),  # 글 템플릿 버전, 예 A2
        f(6, "source_hash", StringType(), True),  # 가린 카드 본문의 sha256 (전화번호 [번호], 관리번호 지움)
        f(7, "input_text", StringType()),  # 모델에 넣은 가린 카드 본문 (보호자 실명·연락처 없음)
        f(8, "source_update_dt", StringType()),  # 카드를 만든 Silver 공고 행의 source_updated_at_raw
        vector(9),
        f(10, "gold_run_id", StringType()),
        f(11, "gold_updated_at", TimestamptzType()),
        identifier_field_ids=[1, 2, 3, 4, 5, 6],
    )
    canary = Schema(
        f(1, "gold_run_id", StringType(), True),  # canary-<시각>
        f(2, "task", StringType(), True),  # image_embedding
        f(3, "model", StringType(), True),
        f(4, "prompt_version", StringType()),
        f(5, "checked_at", TimestamptzType(), True),
        f(6, "passed", BooleanType(), True),
        f(7, "threshold", DoubleType()),
        f(8, "min_cosine", DoubleType()),
        f(9, "photo_count", IntegerType()),
        f(10, "created_reference", BooleanType()),  # 이 확인에서 기준을 새로 만들었는지
        f(11, "reference_created_at", StringType()),
        f(12, "detail", StringType()),  # 사진별 코사인, API 사용량 JSON
        f(13, "gold_updated_at", TimestamptzType()),
        identifier_field_ids=[1],
    )
    # day 변환 파티션은 pyiceberg-core 가 있어야 써져서, 날짜 문자열 칸으로 identity 파티션을 둔다
    request_spec = PartitionSpec(PartitionField(source_id=23, field_id=1000, transform=IdentityTransform(), name="dt"))
    # 모델을 바꿔 새로 백필하면 파일이 모델별로 나뉜다
    model_spec = PartitionSpec(PartitionField(source_id=4, field_id=1000, transform=IdentityTransform(), name="model"))
    version_registry = Schema(
        f(1, "task", StringType(), True),
        f(2, "version", StringType(), True),
        f(3, "config_json", StringType(), True),
        f(4, "config_hash", StringType(), True),
        f(5, "registered_at", TimestamptzType(), True),
        identifier_field_ids=[1, 2],
    )
    return {
        VERSION_REGISTRY_TABLE: (version_registry, PartitionSpec()),
        TAG_TABLE: (tag, PartitionSpec()),
        INFERENCE_LOG_TABLE: (inference, PartitionSpec()),
        REQUEST_LOG_TABLE: (request, request_spec),
        IMAGE_EMBEDDING_TABLE: (image_embedding, model_spec),
        TEXT_EMBEDDING_TABLE: (text_embedding, model_spec),
        EMBEDDING_CANARY_TABLE: (canary, PartitionSpec()),
        EMBEDDING_INFERENCE_LOG_TABLE: (embedding_inference, PartitionSpec()),
        EMBEDDING_REQUEST_LOG_TABLE: (embedding_request, request_spec),
    }


class OldSchemaError(RuntimeError):
    """키 칸(subject_type·subject_key)이 없는 옛 스키마 테이블. 칸 추가로는 맞출 수 없다."""


def create_tables(env: GoldEnvironment, catalog, *, recreate: bool = False, group: str | None = None) -> list[str]:
    """없는 테이블을 만들고, 이미 있는 테이블에는 빠진 칸을 더한다. 바꾼 것 목록("만듦 이름", "칸 추가 이름: 칸들").

    GoldSink 는 테이블의 현재 스키마로 행을 만들어서, 칸이 없으면 새 값이 소리 없이 버려진다. 그래서 칸을 늘린 코드를
    배포하기 전에 이 명령으로 테이블을 맞춘다. 칸을 지우거나 타입을 바꾸지는 않는다.
    키 칸이 없는 옛 테이블은 OldSchemaError. recreate=True 면 (test 환경만) 지우고 새로 만든다.
    group 을 주면 그 묶음(GROUPS)의 표만 다룬다. 비우면 전부.
    """
    if recreate and env.name != "test":
        raise ValueError("RECREATE_TEST_ONLY: prod 테이블은 지우지 않는다")
    if group is not None and group not in GROUPS:
        raise ValueError(f"UNKNOWN_GOLD_GROUP: {group}")
    changed = []
    if _has_namespace(catalog, env.database):
        existing = {".".join(t) for t in catalog.list_tables(env.database)}
    else:
        catalog.create_namespace(env.database)
        existing = set()
    for base, (schema, spec) in schemas().items():
        if group is not None and base not in GROUPS[group]:
            continue
        name = env.table(base)
        if name not in existing:
            catalog.create_table(name, schema=schema, partition_spec=spec, location=env.location(base))
            changed.append(f"만듦 {name}")
            continue
        table = catalog.load_table(name)
        have = {f.name for f in table.schema().fields}
        if set(KEY_FIELDS) <= {f.name for f in schema.fields} and not set(KEY_FIELDS) <= have:
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
    ap.add_argument("--group", default=None, choices=sorted(GROUPS),
                    help="한 묶음(결과·처리 기록·요청 기록)의 표만 확인·생성. 비우면 전부")
    ap.add_argument("--profile", default=None, help="AWS 프로필 (PC). 파드에서는 비운다")
    args = ap.parse_args()

    from rebom.storage.silver.aws_catalog import get_aws_catalog

    env = get_gold_environment(args.env)
    catalog = get_aws_catalog(profile=args.profile)
    for base in schemas():
        if args.group is not None and base not in GROUPS[args.group]:
            continue
        name = env.table(base)
        try:
            catalog.load_table(name)
            print(f"있음  {name}")
        except Exception:  # noqa: BLE001 - NoSuchTableError 등
            print(f"없음  {name}  → {env.location(base)}")
    if args.create:
        for line in create_tables(env, catalog, recreate=args.recreate, group=args.group):
            print(line)


if __name__ == "__main__":
    main()

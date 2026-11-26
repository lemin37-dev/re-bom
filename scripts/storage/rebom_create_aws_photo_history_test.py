"""사진 버전 이력 AWS Iceberg 테스트 테이블 생성."""

import argparse
import os

import boto3
import pyarrow as pa

from rebom.storage.silver.aws_catalog import get_aws_catalog


ACCOUNT = "559987919299"
REGION = "ap-northeast-2"
BUCKET = "rebom-data-559987919299"
TABLE = "rebom_silver.silver_animal_image_version_history_b_test"
LOCATION = (
    f"s3://{BUCKET}/bench_tmp/b-storage-test/iceberg/"
    "silver_animal_image_version_history_b_test"
)


def get_schema():
    s = pa.string()
    i = pa.int32()

    return pa.schema([
        pa.field("desertion_no", s, nullable=False),
        pa.field("image_slot", i, nullable=False),
        pa.field("version", i, nullable=False),
        pa.field("replaces", i),
        pa.field("first_seen_at", s),
        pa.field("source_url", s),
        pa.field("original_s3_key", s),
        pa.field("serving_s3_key", s),
        pa.field("thumb_s3_key", s),
        pa.field("sha256", s),
        pa.field("status", s),
        pa.field("failure_reason", s),
        pa.field("run_id", s),
    ])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--check-existing", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if sum((args.check_config, args.check_existing, args.apply)) != 1:
        parser.error("실행 옵션 하나만 선택하세요")

    if not TABLE.endswith("_b_test"):
        raise RuntimeError("테스트 테이블만 허용합니다")

    expected_prefix = f"s3://{BUCKET}/bench_tmp/b-storage-test/"

    if not LOCATION.startswith(expected_prefix):
        raise RuntimeError("테스트 S3 경로가 아닙니다")

    schema = get_schema()

    print("TABLE:", TABLE)
    print("LOCATION:", LOCATION)
    print("COLUMNS:", len(schema))
    print("ACCOUNT:", ACCOUNT)
    print("REGION:", REGION)

    if args.check_config:
        print("AWS_ACCESS: False")
        print("CONFIG_VALIDATION: PASS")
        return

    session = boto3.Session(
        profile_name=os.getenv("AWS_PROFILE", "rebom-seoul"),
        region_name=REGION,
    )

    identity = session.client("sts").get_caller_identity()

    if identity["Account"] != ACCOUNT:
        raise RuntimeError("AWS 계정 불일치")

    catalog = get_aws_catalog()

    if catalog.table_exists(TABLE):
        existing = catalog.load_table(TABLE)

        if existing.location().rstrip("/") != LOCATION:
            raise RuntimeError("기존 테이블 저장 위치 불일치")

        actual_schema = existing.schema().as_arrow()

        def compatible_type(expected_type, actual_type):
            if expected_type == actual_type:
                return True

            return (
                pa.types.is_string(expected_type)
                and pa.types.is_large_string(actual_type)
            )

        schema_matches = (
            len(schema) == len(actual_schema)
            and all(
                expected_field.name == actual_field.name
                and expected_field.nullable == actual_field.nullable
                and compatible_type(
                    expected_field.type,
                    actual_field.type,
                )
                for expected_field, actual_field
                in zip(schema, actual_schema)
            )
        )

        if not schema_matches:
            raise RuntimeError("기존 테이블 스키마 불일치")

        print("TABLE_ALREADY_EXISTS: PASS")
        return

    if args.check_existing:
        print("TABLE_MISSING: NOT_CREATED")
        return

    created = catalog.create_table(
        TABLE,
        schema=schema,
        location=LOCATION,
    )

    if created.location().rstrip("/") != LOCATION:
        raise RuntimeError("생성된 테이블 저장 위치 불일치")

    print("CREATED:", created.name())
    print("CREATE_RESULT: PASS")


if __name__ == "__main__":
    main()

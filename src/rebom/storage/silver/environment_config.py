"""Silver 실행 환경별 테이블 및 저장소 설정."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SilverEnvironment:
    name: str
    notice_table: str
    history_table: str
    # 칸별 현재 사진 표. rescue_ingest 사진 처리가 쓰고, 태깅·사진 임베딩·글 카드가 읽는다.
    # 이 표는 iceberg_prefix 밖(bench_tmp/b-storage-test/iceberg/)에 있어 table_location() 으로 위치를 만들지 않는다.
    image_table: str
    iceberg_prefix: str
    s3_state_prefix: str

    def table_location(self, table_name: str) -> str:
        return f"{self.iceberg_prefix.rstrip('/')}/{table_name.split('.')[-1]}"


TEST = SilverEnvironment(
    name="test",
    notice_table="rebom_silver.silver_animal_notice_real_20261111_test",
    history_table="rebom_silver.silver_animal_status_history_real_20261111_test",
    image_table="rebom_silver.silver_animal_image_auto_b_test",
    iceberg_prefix=(
        "s3://rebom-data-559987919299/"
        "bench_tmp/b-storage-test/iceberg/real-bronze-20261111"
    ),
    s3_state_prefix="bench_tmp/b-storage-test/journal-recovery-20261111",
)


def get_silver_environment(name: str = "test") -> SilverEnvironment:
    if name == "test":
        return TEST

    if name == "prod":
        raise RuntimeError(
            "PROD_DISABLED: 운영 Glue 테이블 및 S3 경로 미확정"
        )

    raise ValueError(f"UNKNOWN_SILVER_ENVIRONMENT: {name}")

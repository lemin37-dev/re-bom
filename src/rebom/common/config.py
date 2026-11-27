"""환경 변수 설정. 값은 환경 변수 또는 레포 루트의 .env 에서 읽는다 (.env 는 수정하지 않는다)."""
from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SAMPLE_PATH = REPO_ROOT / "data" / "sample" / "rescue_sample.json"


class DataMode(str, Enum):
    """API 가 데이터를 어디서 가져올지."""

    SAMPLE = "sample"  # data/sample/ 의 구조동물 샘플 JSON (현재 기본)
    DB = "db"  # RDS 서비스 표 (S3 → RDS 동기화가 채움, serving/db_repository.py)


class AppEnv(str, Enum):
    """실행 환경. dev 면 검색 응답에 순위 근거(rank·debug)를 붙인다(serving/rank_debug.py). 배포는 기본값 prod."""

    DEV = "dev"
    PROD = "prod"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REBOM_",
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_mode: DataMode = DataMode.SAMPLE
    # REBOM_ENV=dev 면 검색 결과마다 순위 근거를 응답에 넣는다(로컬 개발용). 비우면 prod: 근거 키가 응답에서 빠진다
    env: AppEnv = AppEnv.PROD
    sample_path: Path = DEFAULT_SAMPLE_PATH
    # db 모드 접속. 비우면 Secrets Manager(rebom/dev/rebom-db, REBOM_DB_HOST·PORT 덮어쓰기)로 붙는다. 로컬 시험용
    db_dsn: str | None = None
    # 사진 주소 앞부분. 비우면 /images/... 처럼 같은 도메인(CloudFront /images/*) 기준 상대 주소
    image_base_url: str = ""
    # 쉼표로 구분한 허용 출처. 프론트 개발 서버 기본값
    cors_origins: str = "http://localhost:3000"
    # 운영 화면(/ops) 로그인. 둘 다 있어야 열린다(serving/ops_auth.py). 비밀번호는 해시만, 서명 키는 쿠키용
    ops_password_hash: str | None = None
    ops_session_secret: str | None = None
    ops_cookie_secure: bool = True  # 로컬 http 시험만 false
    ops_session_hours: float = 12
    # 운영 기록의 환경(test|prod). 동기화 기록(REBOM_OPS_ENV)과 같은 값
    ops_env: str = "test"

    @property
    def ops_enabled(self) -> bool:
        return bool(self.ops_password_hash and self.ops_session_secret)

    @property
    def rank_debug(self) -> bool:
        return self.env == AppEnv.DEV

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

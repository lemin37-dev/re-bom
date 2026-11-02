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
    DB = "db"  # 정제·적재된 PostgreSQL(pgvector). 아직 연결 전


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REBOM_",
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_mode: DataMode = DataMode.SAMPLE
    sample_path: Path = DEFAULT_SAMPLE_PATH
    # 쉼표로 구분한 허용 출처. 프론트 개발 서버 기본값
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

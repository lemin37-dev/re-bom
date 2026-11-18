"""사진 자동 처리 스크립트 설정 검증."""

import pytest

from scripts.storage.rebom_auto_process_photo_manifests import (
    get_config,
    validate_config,
)


HISTORY_TABLE = (
    "rebom_silver.silver_animal_image_version_history_b_test"
)
HISTORY_LOCATION = (
    "s3://rebom-data-559987919299/"
    "bench_tmp/b-storage-test/iceberg/"
    "silver_animal_image_version_history_b_test"
)


def clear_history_env(monkeypatch):
    monkeypatch.setenv(
        "REBOM_PHOTO_ICEBERG_TABLE",
        "rebom_silver.silver_animal_image_b_test",
    )
    monkeypatch.setenv(
        "REBOM_PHOTO_ICEBERG_LOCATION",
        "s3://rebom-data-559987919299/"
        "bench_tmp/b-storage-test/iceberg/silver_animal_image_b_test",
    )
    monkeypatch.delenv("REBOM_PHOTO_HISTORY_TABLE", raising=False)
    monkeypatch.delenv("REBOM_PHOTO_HISTORY_LOCATION", raising=False)


def test_photo_auto_config_legacy_mode(monkeypatch):
    clear_history_env(monkeypatch)

    config = get_config()
    validate_config(config)

    assert config["history_table_name"] == ""
    assert config["expected_history_location"] == ""


def test_photo_auto_config_history_mode(monkeypatch):
    clear_history_env(monkeypatch)

    monkeypatch.setenv("REBOM_PHOTO_HISTORY_TABLE", HISTORY_TABLE)
    monkeypatch.setenv(
        "REBOM_PHOTO_HISTORY_LOCATION", HISTORY_LOCATION
    )

    config = get_config()
    validate_config(config)

    assert config["history_table_name"] == HISTORY_TABLE
    assert config["expected_history_location"] == HISTORY_LOCATION


@pytest.mark.parametrize(
    "name,location",
    [
        (HISTORY_TABLE, ""),
        ("", HISTORY_LOCATION),
    ],
)
def test_photo_auto_config_incomplete_pair(
    monkeypatch, name, location
):
    clear_history_env(monkeypatch)

    monkeypatch.setenv("REBOM_PHOTO_HISTORY_TABLE", name)
    monkeypatch.setenv("REBOM_PHOTO_HISTORY_LOCATION", location)

    with pytest.raises(
        RuntimeError, match="사진 이력 테이블 설정이 불완전합니다"
    ):
        validate_config(get_config())


def test_photo_auto_config_rejects_other_table(monkeypatch):
    clear_history_env(monkeypatch)

    monkeypatch.setenv(
        "REBOM_PHOTO_HISTORY_TABLE",
        "rebom_silver.silver_animal_image_version_history",
    )
    monkeypatch.setenv(
        "REBOM_PHOTO_HISTORY_LOCATION",
        HISTORY_LOCATION,
    )

    with pytest.raises(
        RuntimeError, match="허용되지 않은 사진 이력 테이블"
    ):
        validate_config(get_config())


def test_photo_auto_config_rejects_production_location(monkeypatch):
    clear_history_env(monkeypatch)

    monkeypatch.setenv("REBOM_PHOTO_HISTORY_TABLE", HISTORY_TABLE)
    monkeypatch.setenv(
        "REBOM_PHOTO_HISTORY_LOCATION",
        "s3://rebom-data-559987919299/"
        "iceberg/silver_animal_image_version_history",
    )

    with pytest.raises(
        RuntimeError, match="사진 이력 테스트 저장 위치 불일치"
    ):
        validate_config(get_config())

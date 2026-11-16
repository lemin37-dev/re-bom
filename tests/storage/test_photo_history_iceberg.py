import pyarrow as pa
import pytest
from pyiceberg.catalog import load_catalog

from rebom.storage.silver.photo_version_history import (
    apply_photo_version_history,
    plan_photo_history_inserts,
)


KEYS = ("desertion_no", "image_slot", "version")


def make_row(version, status, sha256=None):
    return {
        "desertion_no": "TEST-001",
        "image_slot": 1,
        "version": version,
        "replaces": version - 1 if version > 1 else None,
        "first_seen_at": f"2026-11-{13 + version:02d}T01:00:00Z",
        "source_url": f"https://example.com/v{version}.jpg",
        "original_s3_key": (
            f"bronze/rescue/images/TEST-001/popfile1_v{version:03d}.jpg"
            if status == "stored" else None
        ),
        "serving_s3_key": (
            f"images/TEST-001/1_v{version:03d}.webp"
            if status == "stored" else None
        ),
        "thumb_s3_key": (
            f"images/TEST-001/1_v{version:03d}_thumb.webp"
            if status == "stored" else None
        ),
        "sha256": sha256,
        "status": status,
        "failure_reason": (
            "replaced_not_found" if status == "failed" else None
        ),
        "run_id": f"RUN-{version:03d}",
    }


def test_photo_history_local_iceberg_insert_and_rerun(tmp_path):
    warehouse = tmp_path / "warehouse"
    warehouse.mkdir()

    catalog = load_catalog(
        "photo_history_test",
        type="sql",
        uri=f"sqlite:///{(tmp_path / 'catalog.db').as_posix()}",
        warehouse=str(warehouse),
    )

    catalog.create_namespace("rebom_test")

    fields = [
        pa.field("desertion_no", pa.string(), nullable=False),
        pa.field("image_slot", pa.int32(), nullable=False),
        pa.field("version", pa.int32(), nullable=False),
        pa.field("replaces", pa.int32()),
        pa.field("first_seen_at", pa.string()),
        pa.field("source_url", pa.string()),
        pa.field("original_s3_key", pa.string()),
        pa.field("serving_s3_key", pa.string()),
        pa.field("thumb_s3_key", pa.string()),
        pa.field("sha256", pa.string()),
        pa.field("status", pa.string()),
        pa.field("failure_reason", pa.string()),
        pa.field("run_id", pa.string()),
    ]

    name = "rebom_test.photo_history"
    table = catalog.create_table(name, schema=pa.schema(fields))

    incoming = [
        make_row(1, "stored", "a" * 64),
        make_row(2, "failed"),
        make_row(3, "stored", "c" * 64),
    ]

    first = plan_photo_history_inserts(incoming, [])
    assert first["insert_count"] == 3

    write = apply_photo_version_history(
        catalog,
        name,
        incoming,
        table.location(),
        apply=True,
    )

    assert write["rows_inserted"] == 3
    assert write["rows_updated"] == 0
    assert write["result"] == "APPLIED_AND_VERIFIED"

    stored = catalog.load_table(name).scan().to_arrow().to_pylist()
    assert len(stored) == 3

    rerun = apply_photo_version_history(
        catalog,
        name,
        incoming,
        table.location(),
        apply=True,
    )
    assert rerun["rows_inserted"] == 0
    assert rerun["skip_count"] == 3

    conflict = incoming[2].copy()
    conflict["sha256"] = "d" * 64

    with pytest.raises(ValueError, match="사진 이력 데이터 충돌"):
        apply_photo_version_history(
            catalog,
            name,
            [conflict],
            table.location(),
            apply=True,
        )

def test_photo_current_local_iceberg_replacement(tmp_path):
    from rebom.storage.silver.photo_version_history import (
        apply_photo_current_updates,
    )

    warehouse = tmp_path / "warehouse_current"
    warehouse.mkdir()

    catalog = load_catalog(
        "photo_current_test",
        type="sql",
        uri=f"sqlite:///{(tmp_path / 'current_catalog.db').as_posix()}",
        warehouse=str(warehouse),
    )

    catalog.create_namespace("rebom_test")

    schema = pa.schema([
        pa.field("desertion_no", pa.string(), nullable=False),
        pa.field("image_slot", pa.int32(), nullable=False),
        pa.field("original_s3_key", pa.string()),
        pa.field("serving_s3_key", pa.string()),
        pa.field("thumb_s3_key", pa.string()),
        pa.field("sha256", pa.string()),
    ])

    name = "rebom_test.photo_current"
    table = catalog.create_table(name, schema=schema)

    v1 = make_row(1, "stored", "a" * 64)
    v2 = make_row(2, "failed")
    v3 = make_row(3, "stored", "c" * 64)

    fields = [
        "desertion_no",
        "image_slot",
        "original_s3_key",
        "serving_s3_key",
        "thumb_s3_key",
        "sha256",
    ]

    current = {field: v1[field] for field in fields}

    other = current.copy()
    other["desertion_no"] = "TEST-OTHER"
    other["sha256"] = "f" * 64

    table.append(pa.Table.from_pylist([current, other], schema=schema))

    history = [v1, v2, v3]

    first = apply_photo_current_updates(
        catalog,
        name,
        history,
        table.location(),
        apply=True,
    )

    assert first["changed_count"] == 1
    assert first["verified_rows"] == 2
    assert first["result"] == "APPLIED_AND_VERIFIED"

    stored = (
        catalog.load_table(name)
        .scan()
        .to_arrow()
        .to_pylist()
    )

    by_animal = {row["desertion_no"]: row for row in stored}

    assert by_animal["TEST-001"]["sha256"] == "c" * 64
    assert by_animal["TEST-OTHER"]["sha256"] == "f" * 64

    rerun = apply_photo_current_updates(
        catalog,
        name,
        history,
        table.location(),
        apply=True,
    )

    assert rerun["changed_count"] == 0
    assert rerun["verified_rows"] == 2

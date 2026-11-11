"""서울 AWS Silver 이미지 테스트 적재."""

import os

import pyarrow as pa

from rebom.storage.silver.aws_catalog import get_aws_catalog
from rebom.storage.silver.load_bronze import load_bronze_run
from rebom.storage.silver.transform_image import transform_images

TABLE_NAME = "rebom_silver.silver_animal_image_b_test"
EXPECTED_LOCATION = (
    "s3://rebom-data-559987919299/"
    "bench_tmp/b-storage-test/iceberg/silver_animal_image_b_test"
)

bronze = load_bronze_run(os.environ["REBOM_BRONZE_MANIFEST"])

images = {}

for kind in ("updated", "list_notice", "list_protect"):
    for raw in bronze["rows_by_kind"][kind]:
        for item in transform_images(raw):
            key = (item["desertion_no"], item["image_slot"])

            if key in images and images[key] != item:
                raise RuntimeError(f"IMAGE_CONFLICT: {key}")

            images[key] = item

catalog = get_aws_catalog()
table = catalog.load_table(TABLE_NAME)

if table.location().rstrip("/") != EXPECTED_LOCATION:
    raise RuntimeError("Unexpected Iceberg location")

existing = table.scan().to_arrow().num_rows

print("BEFORE:", existing)
print("PREPARED_IMAGES:", len(images))

if existing != 0:
    raise RuntimeError("Image test table is not empty")

arrow = pa.Table.from_pylist(
    list(images.values()),
    schema=table.schema().as_arrow(),
)

table.append(arrow)

stored = catalog.load_table(TABLE_NAME).scan().to_arrow().to_pylist()

stored_keys = {
    (row["desertion_no"], row["image_slot"])
    for row in stored
}

if len(stored) != len(images):
    raise RuntimeError("IMAGE_COUNT_MISMATCH")

if stored_keys != set(images):
    raise RuntimeError("IMAGE_KEY_MISMATCH")

print("IMAGE_TOTAL:", len(stored))
print("IMAGE_UNIQUE_KEYS:", len(stored_keys))
print("IMAGE_ICEBERG_WRITE: PASS")

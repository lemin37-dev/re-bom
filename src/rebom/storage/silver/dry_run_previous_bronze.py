"""Load the unique previous-day Bronze run for a Silver dry run."""

import re
from datetime import datetime, timedelta
from pathlib import Path

from rebom.storage.silver.load_bronze import load_bronze_run


RUN_ID_PATTERN = re.compile(r"\d{8}T\d{6}-[0-9a-f]+")
BRONZE_FILES = (
    "manifest.json",
    "updated.jsonl.gz",
    "list_notice.jsonl.gz",
    "list_protect.jsonl.gz",
)


def fetch_previous_day_bronze(s3, bucket, current_run_id, destination):
    if not RUN_ID_PATTERN.fullmatch(current_run_id):
        raise ValueError("INVALID_CURRENT_RUN_ID")

    previous_date = (
        datetime.strptime(current_run_id[:8], "%Y%m%d")
        - timedelta(days=1)
    ).strftime("%Y-%m-%d")

    prefix = f"bronze/rescue/raw/dt={previous_date}/"
    keys = []

    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item["Key"]
            if key.endswith("/manifest.json"):
                keys.append(key)

    if len(keys) != 1:
        raise RuntimeError(
            f"PREVIOUS_BRONZE_MANIFEST_COUNT: {len(keys)}"
        )

    manifest_key = keys[0]
    match = re.fullmatch(
        re.escape(prefix)
        + r"run_id=(\d{8}T\d{6}-[0-9a-f]+)/manifest\.json",
        manifest_key,
    )

    if match is None:
        raise RuntimeError("INVALID_PREVIOUS_BRONZE_PATH")

    previous_run_id = match.group(1)
    if previous_run_id[:8] != previous_date.replace("-", ""):
        raise RuntimeError("PREVIOUS_BRONZE_RUN_DATE_MISMATCH")
    source_prefix = manifest_key.rsplit("/", 1)[0]

    directory = Path(destination)
    directory.mkdir(parents=True, exist_ok=True)

    for filename in BRONZE_FILES:
        s3.download_file(
            bucket,
            f"{source_prefix}/{filename}",
            str(directory / filename),
        )

    bronze = load_bronze_run(directory / "manifest.json")

    if bronze["run_id"] != previous_run_id:
        raise RuntimeError("PREVIOUS_BRONZE_RUN_ID_MISMATCH")

    print("PREVIOUS_BRONZE_VERIFIED:", previous_run_id)
    return bronze

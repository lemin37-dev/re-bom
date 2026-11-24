"""Consume an explicit Silver READY publication without selecting newer snapshots."""
import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PublishedInput:
    publish_id: str
    notice_table: str
    photo_table: str
    notice_snapshot_id: int
    photo_snapshot_id: int

    def validate_snapshots(self, catalog):
        for name, snapshot in ((self.notice_table, self.notice_snapshot_id),
                               (self.photo_table, self.photo_snapshot_id)):
            if catalog.load_table(name).snapshot_by_id(snapshot) is None:
                raise ValueError(f"Published Silver snapshot is unavailable: {name}/{snapshot}")


def load_ready(s3, bucket: str, prefix: str, publish_id: str) -> PublishedInput:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", publish_id):
        raise ValueError("INVALID_PUBLISH_ID")
    if not prefix or prefix.startswith("/"):
        raise ValueError("INVALID_PUBLISH_PREFIX")
    record = json.loads(s3.get_object(
        Bucket=bucket, Key=f"{prefix.rstrip('/')}/{publish_id}.json",
    )["Body"].read())
    if record.get("publish_id") != publish_id or record.get("status") != "READY":
        raise ValueError("Silver publication must match the requested READY publish_id")
    for field in ("notice_table", "photo_table", "bronze_run_id", "notice_run_id", "photo_run_id"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise ValueError(f"Invalid Silver publication field: {field}")
    for field in ("notice_snapshot_id", "photo_snapshot_id"):
        if type(record.get(field)) is not int or record[field] <= 0:
            raise ValueError(f"Invalid Silver publication field: {field}")
    return PublishedInput(publish_id, record["notice_table"], record["photo_table"],
                          record["notice_snapshot_id"], record["photo_snapshot_id"])

import json

from botocore.exceptions import ClientError

from rebom.storage.silver.s3_run_journal import stage_key as run_stage_key
from rebom.storage.silver.run_journal import journal_path

IMAGE_STAGES = (
    "PREPARED",
    "IMAGE_APPLIED",
    "COMPLETED",
)


def image_stage_key(prefix, run_id, stage):
    journal_path(run_id, ".")

    if stage not in IMAGE_STAGES:
        raise ValueError("INVALID_IMAGE_STAGE")

    if not prefix or prefix.startswith("/"):
        raise ValueError("INVALID_S3_PREFIX")

    return (
        f"{prefix.rstrip('/')}/images/run_id={run_id}/"
        f"stages/{IMAGE_STAGES.index(stage):02d}_{stage}.json"
    )


def read_image_stage(s3, bucket, prefix, run_id, stage):
    key = image_stage_key(prefix, run_id, stage)

    try:
        response = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"NoSuchKey", "404"}:
            return None
        raise

    record = json.loads(response["Body"].read())

    if record != {"run_id": run_id, "stage": stage}:
        raise RuntimeError("INVALID_IMAGE_CHECKPOINT")

    return record


def current_image_stage(s3, bucket, prefix, run_id):
    latest = None
    missing_seen = False

    for stage in IMAGE_STAGES:
        record = read_image_stage(
            s3, bucket, prefix, run_id, stage
        )

        if record is None:
            missing_seen = True
        else:
            if missing_seen:
                raise RuntimeError("IMAGE_CHECKPOINT_GAP")
            latest = stage

    return latest


def write_image_stage(s3, bucket, prefix, run_id, stage):
    key = image_stage_key(prefix, run_id, stage)
    index = IMAGE_STAGES.index(stage)
    latest = current_image_stage(s3, bucket, prefix, run_id)

    if latest is not None:
        latest_index = IMAGE_STAGES.index(latest)

        if index <= latest_index:
            return "ALREADY_EXISTS"

        if index != latest_index + 1:
            raise ValueError("IMAGE_STAGE_SKIP")
    elif stage != IMAGE_STAGES[0]:
        raise ValueError("IMAGE_FIRST_STAGE_MUST_BE_PREPARED")

    payload = json.dumps({
        "run_id": run_id,
        "stage": stage,
    }).encode("utf-8")

    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=payload,
            ContentType="application/json",
            IfNoneMatch="*",
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")

        if code not in {"PreconditionFailed", "412"}:
            raise

        existing = read_image_stage(
            s3, bucket, prefix, run_id, stage
        )

        if existing != {"run_id": run_id, "stage": stage}:
            raise RuntimeError("IMAGE_CHECKPOINT_CONFLICT")

        return "ALREADY_EXISTS"

    return "CREATED"

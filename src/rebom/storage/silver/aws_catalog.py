"""AWS Glue Catalog와 S3 Iceberg 연결."""

import os

import boto3
from pyiceberg.catalog import load_catalog


def get_aws_catalog(profile=None, region=None):
    profile = profile if profile is not None else os.getenv("AWS_PROFILE")
    region = region or os.getenv("AWS_DEFAULT_REGION", "ap-northeast-2")

    session = boto3.Session(
        profile_name=profile,
        region_name=region,
    )

    credentials = session.get_credentials()

    if credentials is None:
        raise RuntimeError("AWS credentials not found")

    frozen = credentials.get_frozen_credentials()

    properties = {
        "type": "glue",
        "glue.region": region,
        "s3.region": region,
        "s3.access-key-id": frozen.access_key,
        "s3.secret-access-key": frozen.secret_key,
    }

    if frozen.token:
        properties["s3.session-token"] = frozen.token

    if profile:
        properties["glue.profile-name"] = profile

    return load_catalog("rebom_aws", **properties)

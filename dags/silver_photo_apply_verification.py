"""Manual-only Silver photo APPLY verification."""

from datetime import timedelta

import pendulum
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from airflow.sdk import DAG
from kubernetes.client import models as k8s


BUCKET = "rebom-data-559987919299"

with DAG(
    dag_id="silver_photo_apply_verification",
    description="Manual-only Storage photo APPLY verification",
    schedule=None,
    start_date=pendulum.datetime(2026, 11, 23, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 0, "execution_timeout": timedelta(minutes=10)},
    tags=["storage", "test"],
) as dag:
    photo_apply_verification = KubernetesPodOperator(
        task_id="photo_apply_verification",
        name="silver-photo-apply-verify",
        namespace="rebom-storage",
        service_account_name="rebom-storage",
        image=(
            "559987919299.dkr.ecr.ap-northeast-2.amazonaws.com/"
            "rebom/storage:develop"
        ),
        image_pull_policy="Always",
        cmds=[
            "python",
            "/app/scripts/storage/rebom_auto_process_photo_manifests.py",
        ],
        arguments=["--bronze-run-id", "20261123T030014-aebb34", "--apply"],
        env_vars={
            "REBOM_S3_BUCKET": BUCKET,
            "AWS_DEFAULT_REGION": "ap-northeast-2",
            "REBOM_PHOTO_ICEBERG_TABLE":
                "rebom_silver.silver_animal_image_auto_b_test",
            "REBOM_PHOTO_ICEBERG_LOCATION":
                f"s3://{BUCKET}/bench_tmp/b-storage-test/"
                "iceberg/silver_animal_image_auto_b_test",
            "REBOM_PHOTO_HISTORY_TABLE":
                "rebom_silver.silver_animal_image_version_history_auto_b_test",
            "REBOM_PHOTO_HISTORY_LOCATION":
                f"s3://{BUCKET}/bench_tmp/b-storage-test/"
                "iceberg/silver_animal_image_version_history_auto_b_test",
            "REBOM_PHOTO_JOURNAL_PREFIX":
                "bench_tmp/b-storage-test/journals/photo_auto_runs",
        },
        node_selector={"rebom/pool": "main"},
        container_resources=k8s.V1ResourceRequirements(
            requests={"cpu": "100m", "memory": "256Mi"},
            limits={"memory": "1Gi"},
        ),
        get_logs=True,
        on_finish_action="delete_pod",
        in_cluster=True,
    )

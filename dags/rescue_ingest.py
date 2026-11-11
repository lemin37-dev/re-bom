"""구조동물 증분 수집 DAG. 매일 03:00 (Asia/Seoul).

실제 처리는 수집 이미지(ECR rebom/ingest, infra/docker/ingest.Dockerfile)가 한다.
이 파일에는 순서와 호출만 둔다 (AGENTS.md 3번).

  rescue_incremental     → python -m rebom.ingestion.rescue_incremental                      Bronze 수집
  generate_ingest_events → python -m rebom.storage.silver.generate_ingest_events             사라짐·복귀 이벤트

- 이벤트는 수집이 성공해야(상태 파일의 last_run_id 가 가리키는 Bronze 실행이 ok) 돈다.
- 이미지의 ENTRYPOINT 는 수집 모듈로 고정이라, 이벤트 task 는 cmds 로 실행할 모듈을 바꾼다.
- 사진 수집(rescue_photos)은 이 DAG 에 없다. 첫 전체 수집(백필)은 --limit 로 나눠 수동으로 하고(docs/data_contract.md 6절),
  끝난 뒤 새 사진만 받는 task 를 별도 PR 로 추가한다.
- 밀린 날은 상태 파일(state/rescue_state.json)의 마지막 성공일부터 한 번에 따라잡으므로 catchup 을 켜지 않는다.
- 동시에 두 개가 돌면 상태 파일이 꼬이므로 max_active_runs=1.
- API 키는 K8s Secret rebom-ingest(네임스페이스 rebom-ingest)의 DATA_GO_KR_SERVICE_KEY 를 환경변수로 받는다.
- S3 권한은 노드 역할(인스턴스 프로파일)로 받는다. AWS 키 없음.
"""
from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from airflow.sdk import DAG
from kubernetes.client import models as k8s

INGEST_IMAGE = "559987919299.dkr.ecr.ap-northeast-2.amazonaws.com/rebom/ingest:develop"
DATA_BUCKET = "rebom-data-559987919299"


def pod_task(task_id: str, name: str, cmds: list[str] | None = None, arguments: list[str] | None = None, **kwargs):
    """수집 이미지를 띄우는 task. cmds 를 비우면 이미지의 ENTRYPOINT(rescue_incremental)를 쓴다."""
    return KubernetesPodOperator(
        task_id=task_id,
        name=name,
        namespace="rebom-ingest",
        service_account_name="rebom-ingest",
        image=INGEST_IMAGE,
        image_pull_policy="Always",  # develop 태그를 덮어쓰므로 매번 새로 받는다
        cmds=cmds,
        arguments=arguments or [],
        env_vars={"REBOM_S3_BUCKET": DATA_BUCKET},
        env_from=[k8s.V1EnvFromSource(secret_ref=k8s.V1SecretEnvSource(name="rebom-ingest"))],
        node_selector={"rebom/pool": "main"},
        container_resources=k8s.V1ResourceRequirements(
            requests={"cpu": "100m", "memory": "256Mi"},
            limits={"memory": "1Gi"},
        ),
        get_logs=True,
        on_finish_action="delete_pod",
        in_cluster=True,
        **kwargs,
    )


with DAG(
    dag_id="rescue_ingest",
    description="구조동물 공고 증분 수집 → S3 bronze, 사라짐·복귀 이벤트",
    schedule="0 3 * * *",
    start_date=pendulum.datetime(2026, 11, 1, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "ingest",
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
        "execution_timeout": timedelta(hours=1),
    },
    tags=["ingest", "rescue"],
) as dag:
    rescue_incremental = pod_task(
        task_id="rescue_incremental",
        name="rescue-incremental",
        arguments=[],  # 기본 실행. 시험할 때는 ["--dry-run"]
    )

    generate_ingest_events = pod_task(
        task_id="generate_ingest_events",
        name="generate-ingest-events",
        cmds=["python", "-m", "rebom.storage.silver.generate_ingest_events"],
        arguments=[],  # 시험할 때는 ["--dry-run"]
    )

    rescue_incremental >> generate_ingest_events

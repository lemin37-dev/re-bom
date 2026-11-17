"""구조동물 증분 수집 DAG. 매일 03:00 (Asia/Seoul).

실제 처리는 수집 이미지(ECR rebom/ingest, infra/docker/ingest.Dockerfile)가 한다.
이 파일에는 순서와 호출만 둔다 (AGENTS.md 3번).

  rescue_incremental     → python -m rebom.ingestion.rescue_incremental                      Bronze 수집
  generate_ingest_events → python -m rebom.storage.silver.generate_ingest_events             사라짐·복귀 이벤트
  rescue_photos          → python -m rebom.ingestion.rescue_photos --limit 2000               새 사진·재시도 칸

- run_id 는 XCom 으로 넘긴다. 수집이 성공하면 /airflow/xcom/return.json 에 {"run_id", "dt", "manifest"} 를 쓰고
  (do_xcom_push=True, 사이드카가 읽음), 이벤트·사진 task 는 그 run_id 를 --run-id 로 받는다. 상태 파일의 last_run_id 를
  읽지 않으므로, 그 사이 수집을 손으로 한 번 더 돌려도 이 DAG 실행이 만든 Bronze 실행만 처리한다.
  손으로 실행할 때는 --run-id 를 빼면 지금처럼 last_run_id 를 읽는다.
- 수집이 실패하거나 중단(aborted)되면 XCom 을 쓰지 않아 task 가 실패하고, 이벤트·사진은 돌지 않는다.
- 이미지의 ENTRYPOINT 는 수집 모듈로 고정이라, 이벤트·사진 task 는 cmds 로 실행할 모듈을 바꾼다.
- 사진은 수집 뒤에 이벤트와 나란히 돈다. 같은 run_id 를 받고, 상태 파일이 서로 다르다
  (state/rescue_events_state.json, state/rescue_photos_state.json).
- 사진의 첫 전체 수집(백필)은 11/11~11/12 에 수동 파드로 끝냈다(docs/data_contract.md 6절). 이 task 는 그 뒤에 생긴
  새 칸과 실패(failed) 칸 재시도만 받는다. 이미 받은 칸은 사진 주소가 바뀌어도 다시 받지 않는다(같은 문서, 별도 작업).
- --limit 2000 은 하루 처리량이 아니라 상한이다. 하루 새 칸은 약 120~550칸(11/3~11/12 측정)이라 평소에는 그만큼만 하고 끝나고,
  며칠 밀려도 하루 이틀에 따라잡는다. 2000칸 모두 받아도 약 30분(칸당 약 0.9초, 백필 측정).
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
# 수집 task 가 XCom 으로 넘긴 Bronze run_id. KubernetesPodOperator 의 arguments 는 템플릿이라 실행할 때 값으로 바뀐다.
BRONZE_RUN_ID = "{{ ti.xcom_pull(task_ids='rescue_incremental')['run_id'] }}"


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
    description="구조동물 공고 증분 수집 → S3 bronze, 사라짐·복귀 이벤트, 새 사진",
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
        arguments=[],  # 기본 실행. --dry-run 은 XCom 을 쓰지 않아 이 task 가 실패한다(시험은 수동 파드로)
        do_xcom_push=True,  # /airflow/xcom/return.json 의 run_id 를 다음 task 에 넘긴다
    )

    generate_ingest_events = pod_task(
        task_id="generate_ingest_events",
        name="generate-ingest-events",
        cmds=["python", "-m", "rebom.storage.silver.generate_ingest_events"],
        arguments=["--run-id", BRONZE_RUN_ID],  # 시험할 때는 "--dry-run" 추가
    )

    rescue_photos = pod_task(
        task_id="rescue_photos",
        name="rescue-photos",
        cmds=["python", "-m", "rebom.ingestion.rescue_photos"],
        arguments=["--run-id", BRONZE_RUN_ID, "--limit", "2000"],  # 상한. 시험할 때는 "--dry-run" 추가
    )

    rescue_incremental >> [generate_ingest_events, rescue_photos]

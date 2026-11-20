"""사진 태깅 DAG. 지금은 손으로만 실행한다(schedule=None). Silver 가 운영(prod)으로 넘어가면 수집 DAG 뒤에 매일 돌린다.

실제 처리는 사진 태깅 이미지(ECR rebom/tag-img, infra/docker/tag-img.Dockerfile)가 한다.
이 파일에는 순서와 호출만 둔다 (AGENTS.md 3번).

  select_targets → python -m rebom.tagging.run_photo_tags select   Silver 에서 보호중 개의 대표 사진을 골라 대상 목록을 S3 에 쓴다
  tag_photos     → python -m rebom.tagging.run_photo_tags run      대상 목록을 읽어 OpenRouter 로 태깅하고 Gold 에 쓴다

- 두 task 는 같은 --run-id(Airflow run_id)를 받는다. 대상 목록 위치는 그 값으로 정해진다
  (s3://rebom-data-559987919299/gold/_work_test/photo_tags/<run_id>/targets.json).
- 이미 같은 사진(sha256)·같은 질문지 버전으로 끝난 공고는 다시 부르지 않는다. 실패는 3번까지 다시 시도한다(docs/data_contract.md 13절).
- max_photos 는 이번 실행의 사진 상한이다. 일일 증분은 1000(기본), 백필은 실행할 때 params 로 0(상한 없음)을 준다.
  1000장은 약 30분, 백필 약 1만4천 장은 약 6~7시간(사진당 약 1.75초 × 4요청, 동시 4).
- tag_photos 는 유료 호출이라 Airflow 재시도를 끈다(retries=0). 다시 돌리면 끝난 사진은 건너뛰므로 손으로 다시 실행하면 된다.
- 해상도 감시(질문 하나의 prompt_tokens 1,500 초과)나 키·계정 오류(키 무효·크레딧 부족·키 한도)에 걸리면
  run 이 2 로 끝나 task 가 실패한다. 그 전까지 쓴 결과는 남는다.
- 동시에 두 개가 돌면 같은 공고를 두 번 부르므로 max_active_runs=1.
- OpenRouter 키는 K8s Secret rebom-tag-img(네임스페이스 rebom-tag-img)의 OPENROUTER_KEY_TAG_IMG 를 환경변수로 받는다.
- S3·Glue 권한은 노드 역할(인스턴스 프로파일)로 받는다. AWS 키 없음.
"""
from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from airflow.sdk import DAG, Param
from kubernetes.client import models as k8s

TAG_IMAGE = "559987919299.dkr.ecr.ap-northeast-2.amazonaws.com/rebom/tag-img:develop"
DATA_BUCKET = "rebom-data-559987919299"
RUN_ID = "{{ run_id }}"
COMMON_ARGS = ["--run-id", RUN_ID, "--bucket", DATA_BUCKET, "--silver-env", "test", "--gold-env", "test"]


def tag_task(task_id: str, name: str, arguments: list[str], **kwargs):
    """사진 태깅 이미지를 띄우는 task. ENTRYPOINT 가 run_photo_tags 라 arguments 만 준다."""
    return KubernetesPodOperator(
        task_id=task_id,
        name=name,
        namespace="rebom-tag-img",
        service_account_name="rebom-tag-img",
        image=TAG_IMAGE,
        image_pull_policy="Always",  # develop 태그를 덮어쓰므로 매번 새로 받는다
        arguments=arguments,
        env_from=[k8s.V1EnvFromSource(secret_ref=k8s.V1SecretEnvSource(name="rebom-tag-img"))],
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
    dag_id="photo_tags",
    description="보호중 개 대표 사진 → 털·귀·눈매·유사 품종 태그 (Gold)",
    schedule=None,
    start_date=pendulum.datetime(2026, 11, 20, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,
    params={"max_photos": Param(1000, type="integer", minimum=0, description="이번 실행 사진 상한. 0 이면 상한 없음(백필)")},
    default_args={
        "owner": "tag",
        "retries": 0,
    },
    tags=["tag", "photo"],
) as dag:
    select_targets = tag_task(
        task_id="select_targets",
        name="photo-tags-select",
        arguments=["select", *COMMON_ARGS],
        execution_timeout=timedelta(minutes=30),
    )

    tag_photos = tag_task(
        task_id="tag_photos",
        name="photo-tags-run",
        arguments=["run", *COMMON_ARGS, "--max-photos", "{{ params.max_photos }}", "--concurrency", "4"],
        execution_timeout=timedelta(hours=12),
    )

    select_targets >> tag_photos

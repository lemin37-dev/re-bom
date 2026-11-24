"""사진 태깅 DAG. 아직은 손으로만 실행한다(schedule=None).

연계 기준(2026-11-20 결정): 이 DAG 는 rescue_ingest 의 Silver task 뒤에 돈다. 태깅은 Silver 의 보호중 여부와
칸별 현재 사진(키·sha256)을 읽으므로 Bronze 사진 task(photos_done) 뒤가 아니라 Silver 뒤가 맞다.
다만 지금의 Silver task(silver_dry_run)는 dry run 이라 테이블을 갱신하지 않으므로, 연계 실행은 실제로 이루어지지 않는다.
Silver 가 매일 apply 로 바뀌고 200마리 시험이 끝나면 Silver task 에 outlets=[Asset(...)] 를 달고
이 DAG 를 schedule=[그 Asset] 으로 바꾼다(그 전에 연결하면 첫 실행부터 하루 1000장씩 유료 태깅이 돈다).

실제 처리는 사진 태깅 이미지(ECR rebom/tag-img, infra/docker/tag-img.Dockerfile)가 한다.
이 파일에는 순서와 호출만 둔다 (AGENTS.md 3번).

  select_targets → python -m rebom.tagging.run_photo_tags select   Silver 에서 보호중 개의 대표 사진을 골라 대상 목록을 S3 에 쓴다
  tag_photos     → python -m rebom.tagging.run_photo_tags run      대상 목록을 읽어 OpenRouter 로 태깅하고 Gold 에 쓴다

- 두 task 는 같은 --run-id(Airflow run_id)를 받는다. 대상 목록 위치는 그 값으로 정해진다
  (s3://rebom-data-559987919299/gold/_work_test/photo_tags/<run_id>/targets.json).
- select 에 등록된 작업 버전(VERSION_ARGS, gold_version_registry)을 넘긴다. run 은 대상 목록에 적힌 버전을 그대로 쓰고,
  Gold 에 쓰기 전에 지금 코드의 설정이 등록된 config_hash 와 같은지 확인한다(다르면 실패).
  버전을 올릴 때는 새 버전을 먼저 등록한 뒤 이 상수를 바꾼다.
- 이미 같은 사진(sha256)·같은 버전으로 끝난 공고는 다시 부르지 않는다. 실패는 3번까지 다시 시도한다(docs/data_contract.md 13절).
- silver_as_of 를 주면 select 가 그 날짜(그날 끝, KST) 기준 Silver 스냅샷으로 대상을 고른다. 비우면 현재 Silver.
  MVP v0.1 백필은 2026-11-23 으로 준다(11/24 이후 데이터는 v0.1 에서 다루지 않음, 2026-11-24 결정).
- max_photos 는 이번 실행의 사진 상한이다. 일일 증분은 1000(기본), 백필은 실행할 때 params 로 0(상한 없음)을 준다.
  1000장은 약 30분, 백필 약 1만4천 장은 약 6~7시간(사진당 약 1.75초 × 4요청, 동시 4).
- concurrency 는 동시에 태깅하는 사진 수다(기본 4). 백필을 빨리 끝낼 때 8 로 준다. 늘려도 비용은 같고,
  파드 메모리(한도 1Gi)와 OpenRouter·AI Studio 요청 한도(429)만 본다.
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
# 등록된 작업 버전(2026-11-24 등록). 버전을 올릴 때는 등록 먼저, 그다음 이 값을 바꾼다.
VERSION_ARGS = ["--version", "photo_trait@1", "--version", "photo_breed@1"]
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
    params={
        "max_photos": Param(1000, type="integer", minimum=0, description="이번 실행 사진 상한. 0 이면 상한 없음(백필)"),
        "concurrency": Param(4, type="integer", minimum=1, maximum=16, description="동시에 태깅하는 사진 수. 일일 증분 4, 백필은 8"),
        "silver_as_of": Param("", type="string", description="이 날짜(YYYY-MM-DD, 그날 끝 KST) 기준 Silver 스냅샷. 비우면 현재 Silver. v0.1 백필은 2026-11-23"),
    },
    default_args={
        "owner": "tag",
        "retries": 0,
    },
    tags=["tag", "photo"],
) as dag:
    select_targets = tag_task(
        task_id="select_targets",
        name="photo-tags-select",
        arguments=["select", *COMMON_ARGS, *VERSION_ARGS, "--silver-as-of", "{{ params.silver_as_of }}"],
        execution_timeout=timedelta(minutes=30),
    )

    tag_photos = tag_task(
        task_id="tag_photos",
        name="photo-tags-run",
        arguments=["run", *COMMON_ARGS, "--max-photos", "{{ params.max_photos }}", "--concurrency", "{{ params.concurrency }}"],
        execution_timeout=timedelta(hours=12),
    )

    select_targets >> tag_photos

"""구조동물 증분 수집 DAG. 매일 03:00 (Asia/Seoul).

실제 처리는 수집 이미지(ECR rebom/ingest, infra/docker/ingest.Dockerfile)가 한다.
이 파일에는 순서와 호출만 둔다 (AGENTS.md 3번).

  rescue_incremental     → python -m rebom.ingestion.rescue_incremental                      Bronze 수집
  generate_ingest_events → python -m rebom.storage.silver.generate_ingest_events             사라짐·복귀 이벤트
  rescue_photos          → python -m rebom.ingestion.rescue_photos --limit 2000               새 사진·재시도 칸
  embed_photos           → python -m rebom.embedding.cli embed --limit 5000                   새·바뀐 사진 → S3 parquet
  load_photo_embeddings  → python -m rebom.embedding.cli load --max-write 5000                변경분만 RDS pgvector

- run_id 는 XCom 으로 넘긴다. 수집이 성공하면 /airflow/xcom/return.json 에 {"run_id", "dt", "manifest"} 를 쓰고
  (do_xcom_push=True, 사이드카가 읽음), 이벤트·사진 task 는 그 run_id 를 --run-id 로 받는다. 상태 파일의 last_run_id 를
  읽지 않으므로, 그 사이 수집을 손으로 한 번 더 돌려도 이 DAG 실행이 만든 Bronze 실행만 처리한다.
  손으로 실행할 때는 --run-id 를 빼면 지금처럼 last_run_id 를 읽는다.
- 수집이 실패하거나 중단(aborted)되면 XCom 을 쓰지 않아 task 가 실패하고, 이벤트·사진은 돌지 않는다.
- 이미지의 ENTRYPOINT 는 수집 모듈로 고정이라, 이벤트·사진 task 는 cmds 로 실행할 모듈을 바꾼다.
- 사진은 수집 뒤에 이벤트와 나란히 돈다. 같은 run_id 를 받고, 상태 파일이 서로 다르다
  (state/rescue_events_state.json, state/rescue_photos_state.json).
- Silver(silver_dry_run)는 이벤트와 사진이 모두 끝난 뒤에 돈다(11/17 이광훈 님과 합의). Silver 가 사진 manifest 를 읽을 때
  그날 사진이 아직 받는 중이면 안 되기 때문이다. 이벤트는 반드시 성공해야 하고, 사진은 실패해도 Silver 공고 처리를 막지 않는다
  (photos_done, trigger_rule all_done). 그래서 사진 task 가 실패해도 DAG 실행 결과에는 잘 드러나지 않으니 로그로 따로 확인한다.
- Silver 는 --check-photo-manifests 로 같은 run_id 의 사진 manifest 도 DRY_RUN 검사한다(PR #36). 이 옵션은 #36 이 들어간
  storage 이미지에만 있으므로, 이 DAG 는 #36 머지와 이미지 빌드가 끝난 뒤에 배포한다. 옛 이미지면 silver_dry_run 이 인자 오류로 실패한다.
- 사진 임베딩(embed_photos → load_photo_embeddings)은 사진 task 가 끝나면(photos_done) 바로 돈다. Silver 와는 따로 가서
  서로 막지 않는다. 임베딩 이미지(ECR rebom/embed, infra/docker/embed.Dockerfile)를 쓴다.
  - embed 는 run_id 를 받지 않는다. 모든 사진 manifest 의 현재 사진과 S3 결과(gold/image_embedding/)를 sha256 으로 비교해
    새·바뀐 사진만 Gemini 로 보낸다(docs/image_embedding_pipeline.md). 그래서 사진 task 가 일부 실패해도 받은 만큼만 하고,
    빠진 칸은 다음 날 따라잡는다. --limit 5000 은 상한(약 $0.6). 평소 하루 수백 장, 1분 안팎.
  - load 는 RDS 의 sha256 과 비교해 바뀐 칸만 쓴다. --max-write 5000 을 넘거나 차원·정규화가 틀리면 쓰지 않고 실패한다
    (사람이 staged 로 확인한 뒤 손으로 실행). embed 가 실패하면 load 는 돌지 않는다.
  - Gemini 키(rebom/dev/gemini_api_key)와 DB 접속(rebom/dev/rebom-db)은 노드 역할로 Secrets Manager 에서 읽는다.
    수집 Secret(rebom-ingest)은 붙이지 않는다.
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
from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG
from kubernetes.client import models as k8s

INGEST_IMAGE = "559987919299.dkr.ecr.ap-northeast-2.amazonaws.com/rebom/ingest:develop"
EMBED_IMAGE = "559987919299.dkr.ecr.ap-northeast-2.amazonaws.com/rebom/embed:develop"
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

    silver_dry_run = KubernetesPodOperator(
        task_id="silver_dry_run",
        name="silver-dry-run",
        namespace="rebom-storage",
        service_account_name="rebom-storage",
        image="559987919299.dkr.ecr.ap-northeast-2.amazonaws.com/rebom/storage:develop",
        image_pull_policy="Always",
        cmds=["python", "-m", "rebom.storage.silver.run_silver_once"],
        arguments=[
            "--run-id", BRONZE_RUN_ID,
            "--bucket", DATA_BUCKET,
            "--environment", "test",
            "--check-photo-manifests",
        ],
        env_vars={"REBOM_S3_BUCKET": DATA_BUCKET},
        node_selector={"rebom/pool": "main"},
        container_resources=k8s.V1ResourceRequirements(
            requests={"cpu": "100m", "memory": "256Mi"},
            limits={"memory": "1Gi"},
        ),
        get_logs=True,
        on_finish_action="delete_pod",
        in_cluster=True,
    )

    # 사진이 끝난 표시. 사진 task 가 실패해도 끝나기만 하면 성공으로 넘어간다(all_done).
    # Silver 는 기본 규칙(모두 성공)을 그대로 둬서, 이벤트가 실패하면 돌지 않고 사진 실패로는 막히지 않는다.
    photos_done = EmptyOperator(task_id="photos_done", trigger_rule="all_done")

    def embed_task(task_id: str, name: str, arguments: list[str]):
        """임베딩 이미지를 띄우는 task. ENTRYPOINT 가 rebom.embedding.cli 라 arguments 가 하위 명령이다."""
        return KubernetesPodOperator(
            task_id=task_id,
            name=name,
            namespace="rebom-ingest",  # Airflow 가 파드를 띄울 권한이 있는 곳. 클러스터 API 는 쓰지 않는다
            service_account_name="rebom-ingest",
            image=EMBED_IMAGE,
            image_pull_policy="Always",
            arguments=arguments,
            env_vars={"REBOM_S3_BUCKET": DATA_BUCKET, "AWS_DEFAULT_REGION": "ap-northeast-2"},
            node_selector={"rebom/pool": "main"},
            # 전체 S3 결과(4만+ 행 × 768, float32)와 manifest 를 메모리에 읽는다. 실측 뒤 조정
            container_resources=k8s.V1ResourceRequirements(
                requests={"cpu": "500m", "memory": "512Mi"},
                limits={"memory": "2Gi"},
            ),
            get_logs=True,
            on_finish_action="delete_pod",
            in_cluster=True,
        )

    embed_photos = embed_task(
        task_id="embed_photos",
        name="embed-photos",
        arguments=["embed", "--limit", "5000"],  # 상한. 시험할 때는 "--dry-run" 추가
    )
    load_photo_embeddings = embed_task(
        task_id="load_photo_embeddings",
        name="load-photo-embeddings",
        arguments=["load", "--max-write", "5000"],  # 시험할 때는 "--dry-run" 추가
    )

    rescue_incremental >> [generate_ingest_events, rescue_photos]
    rescue_photos >> photos_done
    [generate_ingest_events, photos_done] >> silver_dry_run
    photos_done >> embed_photos >> load_photo_embeddings

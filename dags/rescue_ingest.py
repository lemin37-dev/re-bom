"""구조동물 증분 수집 DAG. 매일 03:00 (Asia/Seoul).

실제 처리는 수집 이미지(ECR rebom/ingest, infra/docker/ingest.Dockerfile)가 한다.
이 파일에는 순서와 호출만 둔다 (AGENTS.md 3번).

  rescue_incremental     → python -m rebom.ingestion.rescue_incremental                      Bronze 수집
  generate_ingest_events → python -m rebom.storage.silver.generate_ingest_events             사라짐·복귀 이벤트
  rescue_photos          → python -m rebom.ingestion.rescue_photos --limit 2000               새 사진·재시도 칸
  check_embedding_drift  → python -m rebom.embedding.cli canary --gold-env prod              모델 변화 감시
  embed_photos           → python -m rebom.embedding.cli embed --limit 5000 --image-batch 1 --gold-env prod  새·바뀐 사진 → Gold prod 표
  sync_notice_status     → python -m rebom.serving.sync notice_status --run-id <run_id>        Silver 공고 → RDS 공고
  sync_photo_vector      → python -m rebom.serving.sync photo_vector --publish-id silver_<run_id> 사진 칸·사진 벡터 → RDS
  sync_tag / sync_text_vector → python -m rebom.serving.sync tag | text_vector --publish-id ...  태그·글 벡터 → RDS

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
- 사진 임베딩(check_embedding_drift → embed_photos)은 사진 task 가 끝나면(photos_done) 바로 돈다. Gold 까지만 쓰고,
  RDS 반영은 아래 RDS 동기화(sync_*)가 한다(11/25, 예전 load_photo_embeddings 는 v0.1 스키마와 맞지 않아 뺌).
  Silver 와는 따로 가서 서로 막지 않는다. 임베딩 이미지(ECR rebom/embed, infra/docker/embed.Dockerfile)를 쓴다.
  - check_embedding_drift: 모델 이름이 같아도 내부가 바뀌면 벡터가 섞이므로, 고정 사진 6장을 다시 임베딩해 기준 벡터와
    코사인 0.99 이상인지 본다. 미만이면 실패(embed·동기화 벡터 갱신 안 함)하고, 이번 벡터를 모델 이름·날짜 경로에 저장하고,
    로그에 대응 방법을 남긴다(src/rebom/embedding/canary.py). 확인 전에는 이 task 를 '성공'으로 표시하지 않는다.
  - 결과 원본은 Gold Iceberg prod 임베딩 묶음 표다(--gold-env prod, docs/data_contract.md 13.4). DAG 는 test 표에 쓰지 않는다.
    embed 는 결과·처리 기록·요청 기록 표에 쓰고 S3 parquet 도 같이 써 둔다(되돌릴 때용, 읽지 않음).
    canary 는 결과를 canary 표와 요청 기록 표에 남긴다. 파드는 노드 역할의 Glue·S3 권한으로 표에 쓴다.
    요청 기록은 요청 1번 = 1행(태깅과 같은 뜻)이라, embed·canary 는 사진을 1장씩 보낸다(--image-batch 1, 11-23 결정).
    비용은 묶어 보낼 때와 같고, 하루 수백 장이라 시간 차이도 몇 분 안이다.
  - embed 는 run_id 를 받지 않는다. 모든 사진 manifest 의 현재 사진과 Gold 결과 표를 sha256 으로 비교해
    새·바뀐 사진만 Gemini 로 보낸다(docs/image_embedding_pipeline.md). 그래서 사진 task 가 일부 실패해도 받은 만큼만 하고,
    빠진 칸은 다음 날 따라잡는다. --limit 5000 은 상한(약 $0.6). 평소 하루 수백 장, 1분 안팎.
  - Gemini 키(서버 운영 전용 rebom/embed/prod/gemini_api_key)와 DB 접속(rebom/dev/rebom-db)은 노드 역할로 Secrets Manager 에서 읽는다.
    수집 Secret(rebom-ingest)은 붙이지 않는다.
- RDS 동기화(sync_*, docs/data_contract.md 16절): Variable rebom_rds_sync_enabled 가 "true" 일 때만 돈다(기본 꺼짐, RDS 쓰기).
  - sync_notice_status: Silver 공고 적재(silver_notice_apply)가 끝나면 이 실행의 공고 완료 기록으로 공고를 반영한다. 사진을 기다리지 않는다.
  - sync_photo_vector: 발행(silver_gold_publish)·임베딩(embed_photos)·공고 반영이 끝나면(all_done) 이 실행의 READY 발행으로
    사진 칸과 사진 벡터를 반영한다. 임베딩이 실패해도 사진은 반영하고, 벡터가 없는 칸은 다음 날 채운다.
    발행이 실패해 이 실행의 READY 기록이 없으면 실패한다(직전 완료본이 RDS 에 남아 있음).
  - sync_tag·sync_text_vector: 사진 반영 뒤. 활성 버전은 RDS active_version 에서 읽고, 없는 작업은 건너뛴다.
  - Gold 는 RDS_SYNC_GOLD_ENV(지금 test, 11/25 결정)를 읽는다. embed_photos 는 prod 에 쓰므로, 사진 벡터를 test 로 옮기기 전에는
    image_embedding 을 active_version 에 넣지 않는다.
  - 경로마다 RDS sync_state 의 순번보다 오래된 입력은 쓰지 않고 실패한다(종료 코드 4). 접속은 노드 역할로 rebom/dev/rebom-db.
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
from airflow.providers.standard.operators.python import ShortCircuitOperator
from airflow.sdk import DAG, Variable
from kubernetes.client import models as k8s

INGEST_IMAGE = "559987919299.dkr.ecr.ap-northeast-2.amazonaws.com/rebom/ingest:develop"
GEMINI_SECRET_ID = "rebom/embed/prod/gemini_api_key"  # 임베딩 전용 키(비용 실측용으로 분리)
EMBED_IMAGE = "559987919299.dkr.ecr.ap-northeast-2.amazonaws.com/rebom/embed:develop"
DATA_BUCKET = "rebom-data-559987919299"
# 수집 task 가 XCom 으로 넘긴 Bronze run_id. KubernetesPodOperator 의 arguments 는 템플릿이라 실행할 때 값으로 바뀐다.
BRONZE_RUN_ID = "{{ ti.xcom_pull(task_ids='rescue_incremental')['run_id'] }}"
# silver_gold_publish 가 쓰는 발행 id (rebom_publish_silver_gold.py 기본값 silver_<bronze_run_id>)
PUBLISH_ID = "silver_{{ ti.xcom_pull(task_ids='rescue_incremental')['run_id'] }}"
RDS_SYNC_GOLD_ENV = "test"  # 동기화가 읽는 Gold 환경 (11/25 결정, docs/data_contract.md 16절)


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


def is_rds_sync_enabled():
    return str(Variable.get("rebom_rds_sync_enabled", default="false")).strip().lower() == "true"


def is_silver_photo_apply_enabled():
    value = Variable.get(
        "rebom_silver_photo_apply_enabled",
        default="false",
    )
    return str(value).strip().lower() == "true"


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

    silver_shelter_apply = KubernetesPodOperator(
        task_id="silver_shelter_apply",
        name="silver-shelter-apply",
        namespace="rebom-storage",
        service_account_name="rebom-storage",
        image="559987919299.dkr.ecr.ap-northeast-2.amazonaws.com/rebom/storage:develop",
        image_pull_policy="Always",
        cmds=["python", "scripts/storage/rebom_run_silver_shelter.py"],
        arguments=[
            "--run-id", BRONZE_RUN_ID,
            "--apply",
        ],
        env_vars={
            "REBOM_S3_BUCKET": DATA_BUCKET,
            "AWS_DEFAULT_REGION": "ap-northeast-2",
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

    silver_notice_apply = KubernetesPodOperator(
        task_id="silver_notice_apply",
        name="silver-notice-apply",
        namespace="rebom-storage",
        service_account_name="rebom-storage",
        image="559987919299.dkr.ecr.ap-northeast-2.amazonaws.com/rebom/storage:develop",
        image_pull_policy="Always",
        cmds=["python", "-m", "rebom.storage.silver.run_silver_once"],
        arguments=[
            "--run-id", BRONZE_RUN_ID,
            "--bucket", DATA_BUCKET,
            "--environment", "test",
            "--apply",
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
    silver_photo_apply_enabled = ShortCircuitOperator(
        task_id="silver_photo_apply_enabled",
        python_callable=is_silver_photo_apply_enabled,
    )

    silver_photo_apply = KubernetesPodOperator(
        task_id="silver_photo_apply",
        name="silver-photo-apply",
        namespace="rebom-storage",
        service_account_name="rebom-storage",
        image="559987919299.dkr.ecr.ap-northeast-2.amazonaws.com/rebom/storage:develop",
        image_pull_policy="Always",
        cmds=["python", "scripts/storage/rebom_auto_process_photo_manifests.py"],
        arguments=[
            "--bronze-run-id", BRONZE_RUN_ID,
            "--apply",
        ],
        env_vars={
            "REBOM_S3_BUCKET": DATA_BUCKET,
            "AWS_DEFAULT_REGION": "ap-northeast-2",
            "REBOM_PHOTO_ICEBERG_TABLE": "rebom_silver.silver_animal_image_auto_b_test",
            "REBOM_PHOTO_ICEBERG_LOCATION": (
                f"s3://{DATA_BUCKET}/bench_tmp/b-storage-test/"
                "iceberg/silver_animal_image_auto_b_test"
            ),
            "REBOM_PHOTO_HISTORY_TABLE": (
                "rebom_silver.silver_animal_image_version_history_auto_b_test"
            ),
            "REBOM_PHOTO_HISTORY_LOCATION": (
                f"s3://{DATA_BUCKET}/bench_tmp/b-storage-test/"
                "iceberg/silver_animal_image_version_history_auto_b_test"
            ),
            "REBOM_PHOTO_JOURNAL_PREFIX": (
                "bench_tmp/b-storage-test/journals/photo_auto_runs"
            ),
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

    silver_gold_publish = KubernetesPodOperator(
        task_id="silver_gold_publish",
        name="silver-gold-publish",
        namespace="rebom-storage",
        service_account_name="rebom-storage",
        image="559987919299.dkr.ecr.ap-northeast-2.amazonaws.com/rebom/storage:develop",
        image_pull_policy="Always",
        cmds=["python", "scripts/storage/rebom_publish_silver_gold.py"],
        arguments=[
            "--bronze-run-id", BRONZE_RUN_ID,
            "--bucket", DATA_BUCKET,
            "--notice-table",
            "rebom_silver.silver_animal_notice_real_20261111_test",
            "--photo-table",
            "rebom_silver.silver_animal_image_auto_b_test",
            "--notice-prefix",
            "bench_tmp/b-storage-test/journal-recovery-20261111",
            "--photo-prefix",
            "bench_tmp/b-storage-test/journals/photo_auto_runs",
            "--publish-prefix",
            "bench_tmp/b-storage-test/publish",
            "--pod-auth",
            "--publish",
        ],
        env_vars={
            "REBOM_S3_BUCKET": DATA_BUCKET,
            "AWS_DEFAULT_REGION": "ap-northeast-2",
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
            env_vars={
                "REBOM_S3_BUCKET": DATA_BUCKET,
                "AWS_DEFAULT_REGION": "ap-northeast-2",
                "REBOM_GEMINI_SECRET_ID": GEMINI_SECRET_ID,  # 시크릿 이름만. 키 값은 Pod 안에서 읽는다
                # OpenRouter 비용 구분: 생성 기록의 external_user 로 남는다 (누가·어디서·무엇을)
                "REBOM_USAGE_TAG": f"rebom-embed:airflow:{task_id}",
            },
            node_selector={"rebom/pool": "main"},
            # 전체 Gold 결과(사진 4만 장 × 768, float32 약 120MB)와 manifest 를 메모리에 읽는다. 실측 뒤 조정
            container_resources=k8s.V1ResourceRequirements(
                requests={"cpu": "500m", "memory": "512Mi"},
                limits={"memory": "2Gi"},
            ),
            get_logs=True,
            on_finish_action="delete_pod",
            in_cluster=True,
        )

    # 모델 변화 감시: 고정 사진 6장을 다시 임베딩해 기준과 코사인 0.99 이상이어야 embed 로 간다(1장씩 요청 6번, 약 $0.0007).
    # 미만이면 종료 코드 3 으로 실패하고, 로그에 대응 방법을 남긴다. 확인 전에는 '성공'으로 표시하지 않는다.
    check_embedding_drift = embed_task(
        task_id="check_embedding_drift",
        name="check-embedding-drift",
        arguments=["canary", "--gold-env", "prod"],
    )
    embed_photos = embed_task(
        task_id="embed_photos",
        name="embed-photos",
        arguments=["embed", "--limit", "5000", "--image-batch", "1", "--gold-env", "prod"],  # 상한. 시험할 때는 "--dry-run" 추가
    )

    def sync_task(task_id: str, name: str, route: str, ref: list[str], **kwargs):
        """RDS 동기화 task. 임베딩 이미지(psycopg·pyiceberg 가 있음)에서 rebom.serving.sync 를 부른다."""
        return KubernetesPodOperator(
            task_id=task_id,
            name=name,
            namespace="rebom-ingest",
            service_account_name="rebom-ingest",
            image=EMBED_IMAGE,
            image_pull_policy="Always",
            cmds=["python", "-m", "rebom.serving.sync"],
            arguments=[route, *ref, "--gold-env", RDS_SYNC_GOLD_ENV],  # 시험할 때는 "--dry-run" 추가
            env_vars={"REBOM_S3_BUCKET": DATA_BUCKET, "AWS_DEFAULT_REGION": "ap-northeast-2"},
            node_selector={"rebom/pool": "main"},
            # 사진 벡터 전체(4만 장 × 768)를 메모리에 읽는다. 실측 뒤 조정
            container_resources=k8s.V1ResourceRequirements(
                requests={"cpu": "250m", "memory": "512Mi"},
                limits={"memory": "2Gi"},
            ),
            get_logs=True,
            on_finish_action="delete_pod",
            in_cluster=True,
            **kwargs,
        )

    # RDS 쓰기라 Variable 로 켠다. 꺼져 있으면 아래 sync_* 가 모두 건너뜀(skipped)으로 끝난다.
    rds_sync_enabled = ShortCircuitOperator(task_id="rds_sync_enabled", python_callable=is_rds_sync_enabled)
    sync_notice_status = sync_task("sync_notice_status", "sync-notice-status", "notice_status",
                                   ["--run-id", BRONZE_RUN_ID])
    sync_photo_vector = sync_task("sync_photo_vector", "sync-photo-vector", "photo_vector",
                                  ["--publish-id", PUBLISH_ID], trigger_rule="all_done")
    sync_tag = sync_task("sync_tag", "sync-tag", "tag", ["--publish-id", PUBLISH_ID])
    sync_text_vector = sync_task("sync_text_vector", "sync-text-vector", "text_vector", ["--publish-id", PUBLISH_ID])

    rescue_incremental >> [generate_ingest_events, rescue_photos]
    rescue_incremental >> silver_shelter_apply
    rescue_photos >> photos_done
    generate_ingest_events >> silver_notice_apply
    photos_done >> silver_photo_apply_enabled >> silver_photo_apply
    [silver_notice_apply, silver_photo_apply] >> silver_gold_publish
    photos_done >> check_embedding_drift >> embed_photos
    [rds_sync_enabled, silver_notice_apply] >> sync_notice_status
    [silver_gold_publish, embed_photos, sync_notice_status] >> sync_photo_vector >> [sync_tag, sync_text_vector]

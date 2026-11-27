"""dags/rescue_ingest.py 확인. Airflow 가 없으면 import 테스트는 건너뛴다 (로컬·CI 기본 환경에는 없음)."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

DAG_FILE = Path(__file__).resolve().parents[1] / "dags" / "rescue_ingest.py"


def _tree() -> ast.Module:
    return ast.parse(DAG_FILE.read_text(encoding="utf-8"))


def _dag_kwargs() -> dict[str, ast.expr]:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "DAG":
            return {k.arg: k.value for k in node.keywords}
    raise AssertionError("DAG(...) 를 찾지 못했습니다")


def test_dag_schedule_and_guards():
    kw = _dag_kwargs()
    assert ast.literal_eval(kw["dag_id"]) == "rescue_ingest"
    assert ast.literal_eval(kw["schedule"]) == "0 3 * * *"
    assert ast.literal_eval(kw["catchup"]) is False
    assert ast.literal_eval(kw["max_active_runs"]) == 1


def _pod_tasks() -> dict[str, dict[str, ast.expr]]:
    tasks = {}
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "pod_task":
            kw = {k.arg: k.value for k in node.keywords}
            tasks[ast.literal_eval(kw["task_id"])] = kw
    return tasks


def _constants() -> dict[str, str]:
    """DAG 파일 맨 위의 문자열 상수 (예: BRONZE_RUN_ID)."""
    out = {}
    for node in _tree().body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            out.update({t.id: node.value.value for t in node.targets if isinstance(t, ast.Name)})
    return out


def _arguments(kw: dict[str, ast.expr]) -> list[str]:
    """arguments 리스트를 값으로 바꾼다. 상수 이름은 그 값으로 바꾼다."""
    consts = _constants()
    return [consts[e.id] if isinstance(e, ast.Name) else ast.literal_eval(e) for e in kw["arguments"].elts]


def test_photo_task_runs_photos_module_with_limit():
    """사진 task 는 사진 모듈을 상한(--limit)과 함께 부른다. 상한이 없으면 밀린 날 한 번에 오래 돈다."""
    kw = _pod_tasks()["rescue_photos"]
    assert ast.literal_eval(kw["cmds"]) == ["python", "-m", "rebom.ingestion.rescue_photos"]
    args = _arguments(kw)
    assert "--limit" in args
    assert int(args[args.index("--limit") + 1]) > 0


def test_incremental_pushes_run_id_to_xcom():
    """수집 task 는 XCom 을 켠다. 수집 모듈이 /airflow/xcom/return.json 에 run_id 를 쓴다."""
    kw = _pod_tasks()["rescue_incremental"]
    assert ast.literal_eval(kw["do_xcom_push"]) is True


def test_events_and_photos_receive_run_id_from_xcom():
    """이벤트·사진은 상태 파일의 last_run_id 가 아니라 이 DAG 실행의 수집 run_id(XCom)를 받는다."""
    template = _constants()["BRONZE_RUN_ID"]
    assert "xcom_pull(task_ids='rescue_incremental')" in template and "['run_id']" in template
    for task_id in ("generate_ingest_events", "rescue_photos"):
        args = _arguments(_pod_tasks()[task_id])
        assert args[args.index("--run-id") + 1] == template, task_id


def test_photo_task_runs_after_incremental():
    """사진은 Bronze 수집(last_run_id 갱신) 뒤에 돌아야 한다. 이벤트와는 나란히."""
    src = DAG_FILE.read_text(encoding="utf-8")
    assert "rescue_incremental >> [generate_ingest_events, rescue_photos]" in src


def test_dag_only_calls_no_rebom_logic():
    """DAG 는 호출만 둔다 (AGENTS.md 3번): rebom 패키지를 import 하지 않는다."""
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("rebom")
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("rebom") for a in node.names)


def test_dag_has_no_secret_values():
    """API 키는 Secret 에서만 받는다. DAG 에 키 이름을 값으로 넣지 않는다."""
    text = DAG_FILE.read_text(encoding="utf-8")
    assert "serviceKey" not in text
    env_keys = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.keyword) and node.arg == "env_vars" and isinstance(node.value, ast.Dict):
            env_keys |= {ast.literal_eval(k) for k in node.value.keys}
    assert "DATA_GO_KR_SERVICE_KEY" not in env_keys


def test_dag_imports_with_airflow():
    pytest.importorskip("airflow")
    pytest.importorskip("airflow.providers.cncf.kubernetes")
    from airflow.models import DagBag

    bag = DagBag(dag_folder=str(DAG_FILE.parent), include_examples=False)
    assert not bag.import_errors, bag.import_errors
    dag = bag.get_dag("rescue_ingest")
    assert dag is not None
    assert dag.max_active_runs == 1
    assert dag.catchup is False


def _silver_task_kwargs(task_id="silver_notice_apply"):
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "KubernetesPodOperator":
            continue
        kw = {k.arg: k.value for k in node.keywords}
        if "task_id" in kw and isinstance(kw["task_id"], ast.Constant):
            if ast.literal_eval(kw["task_id"]) == task_id:
                return kw
    raise AssertionError(f"Task not found: {task_id}")


def test_silver_receives_bronze_run_id():
    args = _arguments(_silver_task_kwargs())
    assert args[args.index("--run-id") + 1] == _constants()["BRONZE_RUN_ID"]


def test_silver_uses_storage_image_and_apply():
    kw = _silver_task_kwargs()
    args = _arguments(kw)
    assert ast.literal_eval(kw["image"]).endswith("/rebom/storage:develop")
    assert ast.literal_eval(kw["namespace"]) == "rebom-storage"
    assert ast.literal_eval(kw["service_account_name"]) == "rebom-storage"
    assert ast.literal_eval(kw["cmds"]) == [
        "python", "-m", "rebom.storage.silver.run_silver_once"
    ]
    assert args[args.index("--environment") + 1] == "test"
    assert "--apply" in args
    assert "--apply-image" not in args
    assert "--check-image" not in args
    assert "--check-photo-manifests" not in args


def test_silver_photo_uses_independent_runner():
    kw = _silver_task_kwargs("silver_photo_apply")
    args = _arguments(kw)
    assert ast.literal_eval(kw["cmds"]) == [
        "python", "scripts/storage/rebom_auto_process_photo_manifests.py"
    ]
    assert args[args.index("--bronze-run-id") + 1] == _constants()["BRONZE_RUN_ID"]
    assert "--apply" in args


def test_silver_runs_with_independent_dependencies():
    src = DAG_FILE.read_text(encoding="utf-8")
    assert "rescue_photos >> photos_done" in src
    assert "generate_ingest_events >> silver_notice_apply" in src
    assert "photos_done >> silver_photo_apply_enabled >> silver_photo_apply" in src
    assert "[silver_notice_apply, silver_photo_apply] >> silver_gold_publish" in src

    rules = {}
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            kw = {k.arg: k.value for k in node.keywords}
            if "task_id" in kw and isinstance(kw["task_id"], ast.Constant):
                rules[kw["task_id"].value] = (
                    ast.literal_eval(kw["trigger_rule"])
                    if "trigger_rule" in kw else None
                )

    assert rules["photos_done"] == "all_done"
    assert rules["silver_notice_apply"] in (None, "all_success")
    assert rules["silver_photo_apply"] in (None, "all_success")
    assert rules["silver_gold_publish"] in (None, "all_success")
    assert "rescue_incremental >> [generate_ingest_events, rescue_photos]" in src


def test_silver_shelter_uses_storage_runner():
    kw = _silver_task_kwargs("silver_shelter_apply")
    args = _arguments(kw)

    assert ast.literal_eval(kw["image"]).endswith("/rebom/storage:develop")
    assert ast.literal_eval(kw["namespace"]) == "rebom-storage"
    assert ast.literal_eval(kw["service_account_name"]) == "rebom-storage"
    assert ast.literal_eval(kw["cmds"]) == [
        "python", "scripts/storage/rebom_run_silver_shelter.py"
    ]
    assert args[args.index("--run-id") + 1] == _constants()["BRONZE_RUN_ID"]
    assert "--apply" in args
    assert "--profile" not in args
    assert "trigger_rule" not in kw


def test_silver_shelter_runs_independently():
    src = DAG_FILE.read_text(encoding="utf-8")
    assert "rescue_incremental >> silver_shelter_apply" in src
    assert "silver_shelter_apply >> silver_notice_apply" not in src
    assert "silver_shelter_apply >> silver_photo_apply" not in src
    assert "silver_shelter_apply >> silver_gold_publish" not in src
    assert "[silver_notice_apply, silver_photo_apply] >> silver_gold_publish" in src


def _embed_tasks() -> dict[str, dict[str, ast.expr]]:
    tasks = {}
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "embed_task":
            kw = {k.arg: k.value for k in node.keywords}
            tasks[ast.literal_eval(kw["task_id"])] = kw
    return tasks


def test_photo_embedding_runs_after_photos_not_blocking_silver():
    """사진 임베딩은 사진 task 가 끝나면 바로 돌고, Silver 와는 따로 간다(서로 막지 않음)."""
    src = DAG_FILE.read_text(encoding="utf-8")
    assert "photos_done >> check_embedding_drift >> embed_photos\n" in src
    assert "load_photo_embeddings =" not in src  # RDS 반영은 sync_* 가 한다 (11/25)
    for task in ("check_embedding_drift", "embed_photos"):
        for silver_task in (
            "silver_notice_apply",
            "silver_photo_apply",
            "silver_shelter_apply",
            "silver_gold_publish",
        ):
            assert f"{task} >> {silver_task}" not in src
    assert _constants()["EMBED_IMAGE"].endswith("/rebom/embed:develop")


def test_photo_embedding_has_caps():
    """embed 는 상한(--limit)을 둔다. 0 이면 상한이 없어 막아야 한다."""
    tasks = _embed_tasks()
    embed = ast.literal_eval(tasks["embed_photos"]["arguments"])
    assert embed[0] == "embed" and int(embed[embed.index("--limit") + 1]) > 0 and "--dry-run" not in embed



def test_photo_embedding_uses_prod_embed_key_name():
    """임베딩 파드는 서버 운영 전용 Gemini 키 시크릿 이름만 받는다(값은 Pod 안에서 Secrets Manager 로 읽음)."""
    assert _constants()["GEMINI_SECRET_ID"] == "rebom/embed/prod/gemini_api_key"
    assert "GEMINI_API_KEY" not in DAG_FILE.read_text(encoding="utf-8")


def test_drift_check_runs_canary_before_embed():
    """임베딩 전 모델 변화 감시: canary 가 실패하면(기본 all_success) embed·load 는 돌지 않는다."""
    tasks = _embed_tasks()
    kw = tasks["check_embedding_drift"]
    assert ast.literal_eval(kw["arguments"]) == ["canary", "--gold-env", "prod"]
    assert "trigger_rule" not in tasks["embed_photos"] and "trigger_rule" not in kw


def test_photo_embedding_tasks_use_gold_prod_only():
    """사진 임베딩은 Gold prod를 사용한다."""
    tasks = _embed_tasks()
    for task in ("check_embedding_drift", "embed_photos"):  # load_photo_embeddings 는 develop에서 뺌 (11/25)
        args = ast.literal_eval(tasks[task]["arguments"])
        assert args[args.index("--gold-env") + 1] == "prod", task
        assert "--profile" not in args, task  # 파드는 노드 역할로 접속
    embed = ast.literal_eval(_embed_tasks()["embed_photos"]["arguments"])
    assert embed[embed.index("--image-batch") + 1] == "1"  # 요청 기록 1행 = 사진 1장 (11-23 결정)


def test_text_embedding_plan_uses_same_run_ready_publication_without_writing():
    source = DAG_FILE.read_text(encoding="utf-8")
    publisher = next(
        {kw.arg: kw.value for kw in node.keywords}
        for node in ast.walk(_tree())
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "KubernetesPodOperator"
        and any(kw.arg == "task_id" and isinstance(kw.value, ast.Constant)
                and kw.value.value == "silver_gold_publish" for kw in node.keywords)
    )
    constants = _constants()

    def args(node):
        return [constants[value.id] if isinstance(value, ast.Name) else ast.literal_eval(value)
                for value in node.elts]

    publish_args = args(publisher["arguments"])
    plan_args = args(_embed_tasks()["embed_text"]["arguments"])
    assert ast.literal_eval(publisher["do_xcom_push"]) is True
    assert publish_args[publish_args.index("--publish-output") + 1] == "/airflow/xcom/return.json"
    assert "silver_gold_publish >> embed_text" in source
    assert plan_args[0] == "embed-text" and "--write" not in plan_args
    assert plan_args[plan_args.index("--publish-id") + 1] == (
        "{{ ti.xcom_pull(task_ids='silver_gold_publish')['publish_id'] }}"
    )
    for option in ("--gold-env", "--silver-env"):
        assert plan_args[plan_args.index(option) + 1] == "test"
    assert plan_args[plan_args.index("--publish-prefix") + 1] == (
        publish_args[publish_args.index("--publish-prefix") + 1]
    )
    assert plan_args[plan_args.index("--publish-bucket") + 1] == publish_args[publish_args.index("--bucket") + 1]


def test_embedding_tasks_label_openrouter_usage():
    """OpenRouter 비용 구분: 임베딩 파드는 task 이름이 든 REBOM_USAGE_TAG 를 받는다(키 값은 아님)."""
    src = DAG_FILE.read_text(encoding="utf-8")
    assert '"REBOM_USAGE_TAG": f"rebom-embed:airflow:{task_id}"' in src


def _sync_tasks() -> dict[str, list[ast.expr]]:
    tasks = {}
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "sync_task":
            tasks[ast.literal_eval(node.args[0])] = node.args
    return tasks


def test_rds_sync_tasks_routes_and_order():
    """RDS 동기화: 경로 4개, 공고는 사진을 기다리지 않고, 사진·벡터는 발행·임베딩 뒤, 태그·글 벡터는 사진 뒤 (11/23 분리안)."""
    tasks = _sync_tasks()
    assert {t: ast.literal_eval(a[2]) for t, a in tasks.items()} == {
        "sync_notice_status": "notice_status", "sync_photo_vector": "photo_vector",
        "sync_tag": "tag", "sync_text_vector": "text_vector"}
    consts = _constants()
    refs = {t: [consts[e.id] if isinstance(e, ast.Name) else ast.literal_eval(e) for e in a[3].elts]
            for t, a in tasks.items()}
    assert refs["sync_notice_status"] == ["--run-id", consts["BRONZE_RUN_ID"]]
    for t in ("sync_photo_vector", "sync_tag", "sync_text_vector"):
        assert refs[t] == ["--publish-id", consts["PUBLISH_ID"]], t
    assert consts["PUBLISH_ID"] == "silver_" + consts["BRONZE_RUN_ID"]
    assert consts["RDS_SYNC_GOLD_ENV"] == "test"
    src = DAG_FILE.read_text(encoding="utf-8")
    assert "[rds_sync_enabled, silver_notice_apply] >> sync_notice_status" in src
    assert "[silver_gold_publish, embed_photos, sync_notice_status] >> sync_photo_vector >> [sync_tag, sync_text_vector]" in src
    assert "silver_photo_apply >> sync_notice_status" not in src


def test_rds_sync_off_by_default():
    """RDS 쓰기는 Variable rebom_rds_sync_enabled 가 true 일 때만."""
    src = DAG_FILE.read_text(encoding="utf-8")
    assert 'Variable.get("rebom_rds_sync_enabled", default="false")' in src
    assert 'ShortCircuitOperator(task_id="rds_sync_enabled"' in src
    assert '"-m", "rebom.serving.sync"' in src and "--profile" not in src.split("def sync_task")[1].split("rds_sync_enabled =")[0]

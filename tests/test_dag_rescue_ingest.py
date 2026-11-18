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


def _silver_task_kwargs():
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "KubernetesPodOperator":
            continue
        kw = {k.arg: k.value for k in node.keywords}
        if "task_id" in kw and isinstance(kw["task_id"], ast.Constant) and ast.literal_eval(kw["task_id"]) == "silver_dry_run":
            return kw
    raise AssertionError("silver_dry_run 태스크를 찾지 못했습니다")


def test_silver_receives_bronze_run_id():
    kw = _silver_task_kwargs()
    args = _arguments(kw)
    assert args[args.index("--run-id") + 1] == _constants()["BRONZE_RUN_ID"]


def test_silver_uses_storage_image_and_dry_run():
    kw = _silver_task_kwargs()
    image = ast.literal_eval(kw["image"])
    args = _arguments(kw)

    assert image.endswith("/rebom/storage:develop")
    assert ast.literal_eval(kw["namespace"]) == "rebom-storage"
    assert ast.literal_eval(kw["service_account_name"]) == "rebom-storage"
    assert ast.literal_eval(kw["cmds"]) == [
        "python", "-m", "rebom.storage.silver.run_silver_once"
    ]
    assert args[args.index("--environment") + 1] == "test"
    assert "--apply" not in args
    assert "--apply-image" not in args


def test_silver_runs_after_events_and_photos():
    """Silver 는 이벤트(필수)와 사진(실패 허용)이 끝난 뒤에 돈다. all_done 은 photos_done 에만 건다."""
    src = DAG_FILE.read_text(encoding="utf-8")
    assert "rescue_photos >> photos_done" in src
    assert "[generate_ingest_events, photos_done] >> silver_dry_run" in src
    rules = {}
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            kw = {k.arg: k.value for k in node.keywords}
            if "task_id" in kw and isinstance(kw["task_id"], ast.Constant):
                rules[kw["task_id"].value] = ast.literal_eval(kw["trigger_rule"]) if "trigger_rule" in kw else None
    assert rules["photos_done"] == "all_done"
    assert rules["silver_dry_run"] is None  # 기본(all_success): 이벤트가 실패하면 Silver 는 돌지 않는다
    assert "rescue_incremental >> [generate_ingest_events, rescue_photos]" in src
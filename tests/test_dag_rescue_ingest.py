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

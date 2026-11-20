"""dags/photo_tags.py 확인. Airflow 가 없으면 import 테스트는 건너뛴다."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

DAG_FILE = Path(__file__).resolve().parents[1] / "dags" / "photo_tags.py"


def _tree() -> ast.Module:
    return ast.parse(DAG_FILE.read_text(encoding="utf-8"))


def _dag_kwargs() -> dict[str, ast.expr]:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "DAG":
            return {k.arg: k.value for k in node.keywords}
    raise AssertionError("DAG(...) 를 찾지 못했습니다")


def _constants() -> dict:
    """DAG 파일 맨 위의 상수. 리스트 안의 상수 이름(RUN_ID 등)은 값으로 바꾼다."""
    out: dict = {}

    def value(e):
        if isinstance(e, ast.Name):
            return out[e.id]
        if isinstance(e, ast.Starred):
            return out[e.value.id]
        if isinstance(e, ast.List):
            items = []
            for x in e.elts:
                items += value(x) if isinstance(x, ast.Starred) else [value(x)]
            return items
        return ast.literal_eval(e)

    for node in _tree().body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = value(node.value)
            except (ValueError, KeyError):
                pass
    out["_value"] = value
    return out


def _tasks() -> dict[str, list[str]]:
    consts = _constants()
    tasks = {}
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "tag_task":
            kw = {k.arg: k.value for k in node.keywords}
            tasks[ast.literal_eval(kw["task_id"])] = consts["_value"](kw["arguments"])
    return tasks


def test_manual_only_and_single_run():
    kw = _dag_kwargs()
    assert ast.literal_eval(kw["dag_id"]) == "photo_tags"
    assert ast.literal_eval(kw["schedule"]) is None  # Silver 운영 전까지 손으로만
    assert ast.literal_eval(kw["catchup"]) is False
    assert ast.literal_eval(kw["max_active_runs"]) == 1


def test_paid_task_is_not_retried():
    kw = _dag_kwargs()
    default_args = {ast.literal_eval(k): v for k, v in zip(kw["default_args"].keys, kw["default_args"].values)}
    assert ast.literal_eval(default_args["retries"]) == 0


def test_select_then_run_with_same_run_id():
    tasks = _tasks()
    assert tasks["select_targets"][0] == "select" and tasks["tag_photos"][0] == "run"
    for args in tasks.values():
        assert args[args.index("--run-id") + 1] == "{{ run_id }}"
        assert args[args.index("--gold-env") + 1] == "test"
    run = tasks["tag_photos"]
    assert run[run.index("--max-photos") + 1] == "{{ params.max_photos }}"
    assert "select_targets >> tag_photos" in DAG_FILE.read_text(encoding="utf-8")


def test_image_and_secret():
    consts = _constants()
    assert consts["TAG_IMAGE"].endswith("/rebom/tag-img:develop")
    text = DAG_FILE.read_text(encoding="utf-8")
    assert 'name="rebom-tag-img"' in text and 'namespace="rebom-tag-img"' in text
    assert "sk-or-" not in text


def test_dag_only_calls_no_rebom_logic():
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("rebom")
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("rebom") for a in node.names)


def test_dag_imports_with_airflow():
    pytest.importorskip("airflow")
    pytest.importorskip("airflow.providers.cncf.kubernetes")
    from airflow.models import DagBag

    bag = DagBag(dag_folder=str(DAG_FILE), include_examples=False)
    assert not bag.import_errors, bag.import_errors
    dag = bag.get_dag("photo_tags")
    assert dag is not None and dag.max_active_runs == 1

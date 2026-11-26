"""옛 임베딩 로그 ↔ 공용 로그 복사 (2026-11-24 로그 통합, docs/gold_log_unification_20261124.md).

    python -m rebom.embedding.log_copy --gold-env test|prod [--write] [--reverse] [--profile rebom]

- 기본은 확인만 한다. --write 여야 쓴다(사람이 실행). 03:00 DAG 와 겹치지 않을 때 실행한다.
- 정방향: gold_embedding_inference_log → gold_inference_log, gold_embedding_request_log → gold_model_request_log.
- --reverse: 되돌릴 때. 공용 로그의 임베딩 행(task)만 옛 표로 복사한다.
- 원본의 행은 지우거나 바꾸지 않는다. 대상에는 없는 행만 더하고, 처리 기록은 같은 키에서 원본이 더 최신
  (gold_updated_at)일 때만 바꾼다. 대상이 더 최신이면 그대로 둔다. 다시 실행해도 결과가 같다.
- 임베딩이 아닌 task, 키 중복, 대상 표에 없는 칸의 값이 있으면 아무것도 쓰지 않고 멈춘다.
- 쓴 뒤에는 다시 읽어 원본의 모든 행이 대상에 같은 값으로 있는지 본다.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from rebom.common import gold_catalog as gc
from rebom.embedding.gold_store import IMAGE_TASK, table_arrow

EMBEDDING_TASKS = (IMAGE_TASK, "text_embedding")
PAIRS = (  # (옛 표, 공용 표)
    (gc.LEGACY_EMBEDDING_INFERENCE_LOG_TABLE, gc.INFERENCE_LOG_TABLE),
    (gc.LEGACY_EMBEDDING_REQUEST_LOG_TABLE, gc.REQUEST_LOG_TABLE),
)
UPDATE_CHUNK = 100  # upsert 조건이 행 수만큼 길어져 나눠서 쓴다


class CopyError(RuntimeError):
    """복사하면 값이 잘못되거나 사라진다. 쓰지 않고 멈춘다."""


@dataclass
class TablePlan:
    source: Any
    target: Any
    inference: bool
    columns: list[str]  # 원본과 대상에 모두 있는 칸
    rows: list[dict]
    add: list[dict] = field(default_factory=list)
    update: list[dict] = field(default_factory=list)  # 처리 기록만: 원본이 더 최신
    keep_newer: int = 0  # 대상이 더 최신이라 그대로 둔 행

    def stats(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "source": ".".join(self.source.name()), "target": ".".join(self.target.name()),
            "source_rows": len(self.rows), "by_task": dict(Counter(r["task"] for r in self.rows)),
            "add": len(self.add), "update": len(self.update), "keep_newer": self.keep_newer,
        }
        if self.inference:
            out["by_status"] = dict(Counter(r["status"] for r in self.rows))
        else:
            out["cost_usd"] = round(sum(r["cost_usd"] or 0 for r in self.rows), 6)
        return out


def _read(table) -> list[dict]:
    from pyiceberg.expressions import In

    return table.scan(row_filter=In("task", list(EMBEDDING_TASKS))).to_arrow().to_pylist()


def _check_tasks(table) -> None:
    """옛 표에는 임베딩 행만 있어야 한다 (모르는 task 를 빠뜨리고 복사하지 않게)."""
    tasks = set(table.scan(selected_fields=("task",)).to_arrow().column("task").to_pylist())
    unknown = tasks - set(EMBEDDING_TASKS)
    if unknown:
        raise CopyError(f"{'.'.join(table.name())}: 임베딩이 아닌 task {sorted(map(str, unknown))}")


def _inference_key(row: dict) -> tuple:
    return tuple(row[k] for k in gc.INFERENCE_LOG_KEYS)


def _request_key(row: dict) -> tuple:
    """request_id 가 빈 행도 있을 수 있어 요청 시각·대상까지 본다."""
    return tuple(row[k] for k in ("request_id", "gold_run_id", "task", "subject_type", "subject_key", "requested_at"))


def _index(rows: list[dict], key, name: str) -> dict[tuple, dict]:
    index = {key(r): r for r in rows}
    if len(index) != len(rows):
        raise CopyError(f"{name}: 같은 키의 행이 있음 ({len(rows) - len(index)}개 중복)")
    return index


def _newer(a: dict, b: dict) -> bool:
    x, y = a["gold_updated_at"], b["gold_updated_at"]
    return x is not None and (y is None or x > y)


def plan(catalog, env, *, reverse: bool = False) -> list[TablePlan]:
    plans = []
    for legacy, shared in PAIRS:
        legacy_t, shared_t = catalog.load_table(env.table(legacy)), catalog.load_table(env.table(shared))
        _check_tasks(legacy_t)
        source, target = (shared_t, legacy_t) if reverse else (legacy_t, shared_t)
        have = {f.name for f in target.schema().fields}
        names = [f.name for f in source.schema().fields]
        rows = _read(source)
        lost = [n for n in names if n not in have and any(r[n] is not None for r in rows)]
        if lost:
            raise CopyError(f"{'.'.join(target.name())}: 칸이 없어 값이 사라짐 {lost}. 칸을 먼저 더해 주세요.")
        inference = shared == gc.INFERENCE_LOG_TABLE
        key = _inference_key if inference else _request_key
        src = _index(rows, key, ".".join(source.name()))
        dst = _index(_read(target), key, ".".join(target.name()))
        p = TablePlan(source, target, inference, [n for n in names if n in have], rows)
        for k, row in src.items():
            old = dst.get(k)
            if old is None:
                p.add.append(row)
            elif any(old[c] != row[c] for c in p.columns):
                if not inference:  # 요청 기록은 append 만이라 같은 요청의 값이 다르면 사람이 봐야 한다
                    raise CopyError(f"{'.'.join(target.name())}: 같은 요청의 값이 다름 {k}")
                if _newer(row, old):
                    p.update.append(row)
                elif _newer(old, row):
                    p.keep_newer += 1
                else:
                    raise CopyError(f"{'.'.join(target.name())}: 같은 시각인데 값이 다름 {k}")
        plans.append(p)
    return plans


def write(plans: list[TablePlan]) -> None:
    for p in plans:
        if p.add:
            p.target.append(table_arrow(p.target, p.add))
        for i in range(0, len(p.update), UPDATE_CHUNK):
            chunk = p.update[i:i + UPDATE_CHUNK]
            p.target.upsert(table_arrow(p.target, chunk), join_cols=list(gc.INFERENCE_LOG_KEYS))


def verify(catalog, env, *, reverse: bool = False) -> bool:
    """다시 읽어 더할 행도 바꿀 행도 없으면 통과 (대상이 더 최신인 행은 정상)."""
    return not any(p.add or p.update for p in plan(catalog, env, reverse=reverse))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m rebom.embedding.log_copy",
                                 description="옛 임베딩 로그 ↔ 공용 로그 복사 (기본은 확인만)")
    ap.add_argument("--gold-env", choices=sorted(gc.ENVIRONMENTS), required=True)
    ap.add_argument("--reverse", action="store_true", help="되돌리기: 공용 로그의 임베딩 행 → 옛 표")
    ap.add_argument("--write", action="store_true", help="실제로 쓴다 (사람이 실행, 03:00 DAG 와 겹치지 않게)")
    ap.add_argument("--profile", default=None, help="AWS 프로필 (PC)")
    args = ap.parse_args(argv)

    from pyiceberg.exceptions import NoSuchTableError

    from rebom.storage.silver.aws_catalog import get_aws_catalog

    catalog, env = get_aws_catalog(profile=args.profile), gc.get_gold_environment(args.gold_env)
    try:
        plans = plan(catalog, env, reverse=args.reverse)
        print(json.dumps([p.stats() for p in plans], ensure_ascii=False, indent=2))
        if not args.write:
            print("확인만 했습니다. 쓰려면 --write")
            return 0
        write(plans)
        ok = verify(catalog, env, reverse=args.reverse)
    except (CopyError, NoSuchTableError) as exc:  # 표가 없으면 gold_catalog --create --group log 를 먼저
        print(f"복사 중단: {exc}", file=sys.stderr)
        return 2
    print("검증", "통과" if ok else "실패")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())

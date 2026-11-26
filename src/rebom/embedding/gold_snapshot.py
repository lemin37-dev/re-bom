"""prod 임베딩 결과·로그를 기준 시각까지 _test 로 복사한다 (v0.1 검증용 고정 시점, 2026-11-24).

    python -m rebom.embedding.gold_snapshot --until 2026-11-24T00:00:00+09:00 [--write] [--profile rebom]

- 방향은 prod → test 하나다. prod 는 읽기만 하고, 매일 적재는 그대로 이어진다.
- 기준 시각 전의 행만 복사한다. 사진 벡터·처리 기록은 gold_updated_at, 요청 기록은 requested_at, canary 는 checked_at.
- 처리·요청 기록은 prod 의 옛 임베딩 로그에서 읽어 test 의 공용 로그에 쓴다.
- 기본은 확인만 한다. --write 여야 쓴다(사람이 실행).
- test 에 없는 행만 더한다. 같은 키의 값이 다르면 쓰지 않고 멈추고, test 쪽이 더 최신이면(test 에서 다시 실행한 것)
  그대로 둔다. 다시 실행해도 결과가 같다.
- 기준 시각 전의 OK 처리 기록과 사진 벡터의 사진이 서로 다르면 멈춘다 (한쪽만 복사되지 않게).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from rebom.common import gold_catalog as gc
from rebom.embedding.gold_store import OK, load_logs, table_arrow, vectors_of
from rebom.embedding.log_copy import EMBEDDING_TASKS, CopyError, _index, _inference_key, _request_key


@dataclass(frozen=True)
class Spec:
    name: str
    source: str  # prod 표
    target: str  # test 표
    time: str  # 기준 시각과 비교하는 칸
    key: Callable[[dict], tuple]
    vector: bool = False


def _image_key(row: dict) -> tuple:
    return tuple(row[k] for k in gc.IMAGE_EMBEDDING_KEYS) + (row.get("version"),)


SPECS = (
    Spec("image", gc.IMAGE_EMBEDDING_TABLE, gc.IMAGE_EMBEDDING_TABLE, "gold_updated_at", _image_key, vector=True),
    Spec("inference", gc.LEGACY_EMBEDDING_INFERENCE_LOG_TABLE, gc.INFERENCE_LOG_TABLE, "gold_updated_at", _inference_key),
    Spec("request", gc.LEGACY_EMBEDDING_REQUEST_LOG_TABLE, gc.REQUEST_LOG_TABLE, "requested_at", _request_key),
    Spec("canary", gc.EMBEDDING_CANARY_TABLE, gc.EMBEDDING_CANARY_TABLE, "checked_at", lambda r: (r["gold_run_id"],)),
)


@dataclass
class Part:
    spec: Spec
    target: Any
    source_rows: int  # 기준 시각 전
    after: int  # prod 에 있지만 기준 시각 뒤라 복사하지 않는 행
    add: list[dict] = field(default_factory=list)
    keep_newer: int = 0
    only_target: int = 0  # test 에만 있는 임베딩 행 (test 에서 직접 실행한 것)

    def stats(self) -> dict[str, Any]:
        return {"table": ".".join(self.target.name()), "source_rows": self.source_rows, "after_until": self.after,
                "add": len(self.add), "keep_newer": self.keep_newer, "only_in_test": self.only_target}


def _rows(table, vector: bool) -> list[dict]:
    from pyiceberg.expressions import AlwaysTrue, In

    flt = In("task", list(EMBEDDING_TASKS)) if any(f.name == "task" for f in table.schema().fields) else AlwaysTrue()
    arrow = table.scan(row_filter=flt).to_arrow()
    if not vector:
        return arrow.to_pylist()
    # 벡터는 파이썬 float 로 풀지 않는다 (3.6만 × 768)
    vectors = vectors_of(arrow)
    rows = arrow.drop_columns(["embedding"]).to_pylist()
    for row, vec in zip(rows, vectors, strict=True):
        row["embedding"] = vec
    return rows


def plan(catalog, source_env, target_env, until: datetime) -> list[Part]:
    if until.tzinfo is None:
        raise CopyError("기준 시각에 시간대가 없습니다 (예: 2026-11-24T00:00:00+09:00)")
    if target_env.name == "prod" or source_env.table("x") == target_env.table("x"):
        raise CopyError("prod 에는 쓰지 않습니다 (prod → test 만)")
    load_logs(catalog, target_env)  # 공용 로그에 임베딩 칸이 있는지
    parts, taken = [], {}
    for spec in SPECS:
        source = catalog.load_table(source_env.table(spec.source))
        target = catalog.load_table(target_env.table(spec.target))
        everything = _rows(source, spec.vector)
        if any(r[spec.time] is None for r in everything):
            raise CopyError(f"{'.'.join(source.name())}: {spec.time} 이 빈 행이 있어 시점을 정할 수 없음")
        rows = [r for r in everything if r[spec.time] < until]
        taken[spec.name] = rows
        have = {f.name for f in target.schema().fields}
        names = [f.name for f in source.schema().fields]
        lost = [n for n in names if n not in have and any(r[n] is not None for r in rows)]
        if lost:
            raise CopyError(f"{'.'.join(target.name())}: 칸이 없어 값이 사라짐 {lost}")
        common = [n for n in names if n in have]
        src = _index(rows, spec.key, ".".join(source.name()))
        dst = _index(_rows(target, spec.vector), spec.key, ".".join(target.name()))
        part = Part(spec, target, len(rows), len(everything) - len(rows), only_target=len(set(dst) - set(src)))
        for key, row in src.items():
            old = dst.get(key)
            if old is None:
                part.add.append(row)
            elif any(old[c] != row[c] for c in common):
                if old[spec.time] is not None and old[spec.time] > row[spec.time]:
                    part.keep_newer += 1
                else:
                    raise CopyError(f"{'.'.join(target.name())}: 같은 키의 값이 다름 {key}")
        parts.append(part)
    logged = {(r["subject_key"], r["version"]) for r in taken["inference"] if r["status"] == OK and r["task"] == EMBEDDING_TASKS[0]}
    images = {r["subject_key"] for r in taken["image"]}
    if {sha for sha, _ in logged} != images:
        raise CopyError(f"기준 시각 전 OK 처리 기록 {len({s for s, _ in logged})}장과 사진 벡터 {len(images)}장의 사진이 다름")
    return parts


def write(parts: list[Part]) -> None:
    for p in parts:
        if p.add:
            p.target.append(table_arrow(p.target, p.add, vector=p.spec.vector))


def verify(catalog, source_env, target_env, until: datetime) -> bool:
    return not any(p.add for p in plan(catalog, source_env, target_env, until))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m rebom.embedding.gold_snapshot",
                                 description="prod 임베딩 결과·로그를 기준 시각까지 _test 로 복사 (기본은 확인만)")
    ap.add_argument("--until", required=True, type=datetime.fromisoformat,
                    help="이 시각 전의 행만 (시간대 포함, 예 2026-11-24T00:00:00+09:00)")
    ap.add_argument("--write", action="store_true", help="test 표에 실제로 쓴다 (사람이 실행)")
    ap.add_argument("--profile", default=None, help="AWS 프로필 (PC)")
    args = ap.parse_args(argv)

    from pyiceberg.exceptions import NoSuchTableError

    from rebom.embedding.gold_store import LogSchemaError
    from rebom.storage.silver.aws_catalog import get_aws_catalog

    catalog = get_aws_catalog(profile=args.profile)
    envs = gc.get_gold_environment("prod"), gc.get_gold_environment("test")
    try:
        parts = plan(catalog, *envs, args.until)
        print(json.dumps([p.stats() for p in parts], ensure_ascii=False, indent=2))
        if not args.write:
            print("확인만 했습니다. 쓰려면 --write")
            return 0
        write(parts)
        ok = verify(catalog, *envs, args.until)
    except (CopyError, LogSchemaError, NoSuchTableError) as exc:
        print(f"복사 중단: {exc}", file=sys.stderr)
        return 2
    print("검증", "통과" if ok else "실패")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())

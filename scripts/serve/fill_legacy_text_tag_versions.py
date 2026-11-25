"""Gold 글 태그(text_personality·text_health)의 version 을 task@1 로 채운다. 기본은 dry-run(건수만), --apply 로 쓴다.

두 표를 함께 바꾼다 (동기화는 태그 행과, 처리 기록의 '최신 입력 해시'를 함께 읽는다).
- gold_animal_tag:   source=text, version 빈 행 중 레지스트리 @1 의 prompt_version·model_id 와 같은 행 → version = task@1
- gold_inference_log: version 칸에 질문지 버전 문자열(예: text-personality-v1)이 든 행 → version = task@1
이미 @1 행이 있으면(겹침) 아무것도 쓰지 않고 멈춘다. 작업·표마다 overwrite 한 번(커밋 하나).

⚠ 채운 뒤에는 run_text_tags 가 VERSIONS(질문지 버전 문자열)로 처리 기록을 찾으므로 지난 결과를 못 보고 전부 다시 부른다.
  DAG 로 만들 때 run_text_tags 의 버전을 레지스트리 task@1 로 바꾼 뒤에 돌린다.

    python scripts/serve/fill_legacy_text_tag_versions.py --profile rebom            # 건수만
    python scripts/serve/fill_legacy_text_tag_versions.py --profile rebom --apply    # 실제로 씀
"""
from __future__ import annotations

import argparse

TASKS = ("text_personality", "text_health")


def with_version(rows, version: str) -> list[dict]:
    return [{**r, "version": version} for r in rows]


def overlaps(old_rows, new_rows, key_cols) -> int:
    """채운 뒤 키가 이미 있는 @1 행과 겹치는 수."""
    have = {tuple(r[c] for c in key_cols) for r in new_rows}
    return sum(1 for r in old_rows if tuple(r[c] for c in key_cols) in have)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", default="test")
    ap.add_argument("--profile")
    ap.add_argument("--task", choices=TASKS, action="append", help="기본: 둘 다")
    ap.add_argument("--apply", action="store_true", help="Gold 에 실제로 쓴다 (없으면 건수만)")
    args = ap.parse_args(argv)

    import pyarrow as pa
    from pyiceberg.expressions import And, EqualTo, IsNull

    from rebom.common import gold_catalog as gc
    from rebom.common.gold_version_registry import resolve_version
    from rebom.storage.silver.aws_catalog import get_aws_catalog

    catalog = get_aws_catalog(profile=args.profile)
    env = gc.get_gold_environment(args.env)
    tag_t = catalog.load_table(env.table(gc.TAG_TABLE))
    log_t = catalog.load_table(env.table(gc.INFERENCE_LOG_TABLE))
    tag_key = ("subject_type", "subject_key", "task", "tag_code", "source_hash")
    plans = []
    for task in args.task or TASKS:
        version = f"{task}@1"
        config = resolve_version(catalog, env, task, version)
        prompt, model = config["prompt_version"], config["model_id"]
        base = And(And(EqualTo("task", task), EqualTo("source", "text")), EqualTo("subject_type", "animal"))
        tag_f = And(And(base, IsNull("version")), And(EqualTo("prompt_version", prompt), EqualTo("model", model)))
        tags = tag_t.scan(row_filter=tag_f).to_arrow().to_pylist()
        tags_v1 = tag_t.scan(row_filter=And(base, EqualTo("version", version))).to_arrow().to_pylist()
        log_base = And(EqualTo("task", task), EqualTo("subject_type", "animal"))
        log_f = And(log_base, EqualTo("version", prompt))
        logs = log_t.scan(row_filter=log_f).to_arrow().to_pylist()
        logs_v1 = log_t.scan(row_filter=And(log_base, EqualTo("version", version))).to_arrow().to_pylist()
        tag_clash = overlaps(tags, tags_v1, tag_key)
        log_clash = overlaps(with_version(logs, version), logs_v1, gc.INFERENCE_LOG_KEYS)
        print(f"{task} ({prompt}, {model}): 태그 {len(tags)}행 (공고 {len({r['subject_key'] for r in tags})}), "
              f"처리 기록 {len(logs)}행 → version {version}. 이미 있는 @1: 태그 {len(tags_v1)}, 기록 {len(logs_v1)}")
        if tag_clash or log_clash:
            print(f"  겹침 태그 {tag_clash}, 기록 {log_clash}: 쓰지 않고 멈춥니다")
            return 3
        plans.append((task, tags, tag_f, logs, log_f, version))
    if not args.apply:
        print("dry-run: 아무것도 쓰지 않았습니다. 쓰려면 --apply")
        return 0
    for task, tags, tag_f, logs, log_f, version in plans:
        if logs:  # 기록 먼저: 태그만 바뀐 채 멈추면 동기화가 그 작업의 태그를 '현재 아님'으로 빼 버린다
            log_t.overwrite(pa.Table.from_pylist(with_version(logs, version), schema=log_t.schema().as_arrow()),
                            overwrite_filter=log_f)
            log_t = catalog.load_table(env.table(gc.INFERENCE_LOG_TABLE))
        if tags:
            tag_t.overwrite(pa.Table.from_pylist(with_version(tags, version), schema=tag_t.schema().as_arrow()),
                            overwrite_filter=tag_f)
            tag_t = catalog.load_table(env.table(gc.TAG_TABLE))
        left_tags = len(tag_t.scan(row_filter=tag_f, selected_fields=("subject_key",)).to_arrow())
        left_logs = len(log_t.scan(row_filter=log_f, selected_fields=("subject_key",)).to_arrow())
        print(f"{task}: 반영함. 남은 옛 태그 {left_tags}, 남은 옛 기록 {left_logs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

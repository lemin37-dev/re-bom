"""Gold 사진 태그(photo_trait·photo_breed)의 version 빈 옛 행을 정리한다. 기본은 dry-run(건수만), --apply 로 쓴다.

같은 질문지·모델(레지스트리 task@1 설정의 prompt_version·model_id)로 만든 옛 행만 다룬다.
- 같은 사진·작업에 이미 버전 행이 있으면: 옛 행을 지운다 (동기화는 이미 버전 행만 쓰고 있어 RDS 결과가 바뀌지 않음)
- 버전 행이 없으면: 옛 행의 version 을 task@1 로 채운다 (같은 사진에 @1 결과가 두 벌 생기지 않게 이 경우만 채움)
작업마다 overwrite 한 번(커밋 하나)이라 중간에 실패해도 반쯤 바뀐 상태로 남지 않는다. 사진 태깅이 도는 중에는 돌리지 않는다.

글 태그(text_personality·text_health)는 다루지 않는다: scripts/serve/fill_legacy_text_tag_versions.py 가 맡는다
(run_text_tags 는 등록 버전 task@1 로 태그·처리 기록을 쓴다).

    python scripts/serve/fill_legacy_photo_tag_versions.py --profile rebom            # 건수만
    python scripts/serve/fill_legacy_photo_tag_versions.py --profile rebom --apply    # 실제로 씀
"""
from __future__ import annotations

import argparse

TASKS = ("photo_trait", "photo_breed")


def split_legacy(legacy_rows, versioned_subjects: set[str], version: str):
    """(채울 행, 지울 행 수). 채울 행은 version 을 넣은 사본."""
    fill = [{**r, "version": version} for r in legacy_rows if r["subject_key"] not in versioned_subjects]
    return fill, len(legacy_rows) - len(fill)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", default="test")
    ap.add_argument("--profile")
    ap.add_argument("--task", choices=TASKS, action="append", help="기본: 둘 다")
    ap.add_argument("--apply", action="store_true", help="Gold 에 실제로 쓴다 (없으면 건수만)")
    args = ap.parse_args(argv)

    import pyarrow as pa
    from pyiceberg.expressions import And, EqualTo, IsNull

    from rebom.common.gold_catalog import TAG_TABLE, get_gold_environment
    from rebom.common.gold_version_registry import resolve_version
    from rebom.storage.silver.aws_catalog import get_aws_catalog

    catalog = get_aws_catalog(profile=args.profile)
    env = get_gold_environment(args.env)
    table = catalog.load_table(env.table(TAG_TABLE))
    for task in args.task or TASKS:
        version = f"{task}@1"
        config = resolve_version(catalog, env, task, version)
        base = And(And(EqualTo("task", task), EqualTo("source", "photo")), EqualTo("subject_type", "photo"))
        legacy_f = And(And(base, IsNull("version")),
                       And(EqualTo("prompt_version", config["prompt_version"]), EqualTo("model", config["model_id"])))
        legacy = table.scan(row_filter=legacy_f).to_arrow().to_pylist()
        versioned = {r["subject_key"] for r in table.scan(row_filter=And(base, EqualTo("version", version)),
                                                           selected_fields=("subject_key",)).to_arrow().to_pylist()}
        fill, dropped = split_legacy(legacy, versioned, version)
        print(f"{task}: 옛 행 {len(legacy)} (사진 {len({r['subject_key'] for r in legacy})}장) → "
              f"version 채움 {len(fill)}행 (사진 {len({r['subject_key'] for r in fill})}장), 지움 {dropped}행. "
              f"질문지 {config['prompt_version']}, 모델 {config['model_id']}")
        if args.apply and legacy:
            table.overwrite(pa.Table.from_pylist(fill, schema=table.schema().as_arrow()), overwrite_filter=legacy_f)
            table = catalog.load_table(env.table(TAG_TABLE))
            left = len(table.scan(row_filter=legacy_f, selected_fields=("subject_key",)).to_arrow())
            print(f"  반영함. 남은 옛 행 {left}")
    if not args.apply:
        print("dry-run: 아무것도 쓰지 않았습니다. 쓰려면 --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

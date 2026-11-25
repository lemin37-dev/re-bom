"""Gold 태그 표에서 version 빈 옛 행과 버전 행이 겹친 대상을 비교한다. 읽기만 하고 아무것도 지우지 않는다.

같은 (대상, 작업)에 옛 행과 버전 행이 둘 다 있을 때, 태그 목록(tag_code·rank·similarity·searchable·displayable)이
같으면 same, 다르면 diff 로 센다. same 인 옛 행은 지워도 동기화 결과가 바뀌지 않는다(동기화는 이미 버전 행만 쓴다).
지우는 일은 Gold 담당자가 정한다.

    python scripts/serve/compare_legacy_tags.py --profile rebom                 # 작업별 요약
    python scripts/serve/compare_legacy_tags.py --profile rebom --show-diff 5    # 다른 대상 몇 개 보기
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict

COMPARE = ("tag_code", "rank", "similarity", "searchable", "displayable")


def compare(rows):
    """rows: Gold 태그 행. 돌려주는 값: {작업: Counter(same/diff/legacy_rows)}, diff 대상 목록."""
    groups: dict[tuple, dict[str, set]] = defaultdict(lambda: {"legacy": set(), "versioned": set()})
    legacy_rows: Counter = Counter()
    for r in rows:
        key = (r["subject_type"], r["subject_key"], r["task"])
        side = "versioned" if r.get("version") else "legacy"
        groups[key][side].add(tuple(r.get(c) for c in COMPARE))
        if side == "legacy":
            legacy_rows[key] += 1
    out: dict[str, Counter] = defaultdict(Counter)
    diffs = []
    for r in rows:  # 행 수: 옛 행·버전 행
        out[r["task"]]["rows_" + ("versioned" if r.get("version") else "legacy")] += 1
    for key, g in groups.items():
        if not g["legacy"] or not g["versioned"]:
            out[key[2]]["subjects_legacy_only" if g["legacy"] else "subjects_versioned_only"] += 1
            continue
        same = g["legacy"] == g["versioned"]
        out[key[2]]["same" if same else "diff"] += 1
        out[key[2]]["legacy_rows_" + ("same" if same else "diff")] += legacy_rows[key]
        if not same:
            diffs.append((key, sorted(g["legacy"], key=str), sorted(g["versioned"], key=str)))
    return out, diffs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", default="test")
    ap.add_argument("--profile")
    ap.add_argument("--show-diff", type=int, default=0)
    args = ap.parse_args(argv)

    from rebom.common.gold_catalog import TAG_TABLE, get_gold_environment
    from rebom.storage.silver.aws_catalog import get_aws_catalog

    table = get_aws_catalog(profile=args.profile).load_table(get_gold_environment(args.env).table(TAG_TABLE))
    rows = table.scan(selected_fields=("subject_type", "subject_key", "task", "version", *COMPARE)).to_arrow().to_pylist()
    out, diffs = compare(rows)
    for task in sorted(out):
        print(task, dict(out[task]))
    for key, old, new in diffs[: args.show_diff]:
        print("\n", key, "\n  옛:", old, "\n  새:", new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

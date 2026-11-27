"""층별 DBML 을 하나로 합친다 (dbdiagram.io 에 붙여 넣기·운영 화면 탭2 입력).

원본: docs/schema/bronze.dbml · silver.dbml · gold.dbml, docs/schema/rds.dbml(운영 기록 ops_* 포함), 층 사이 연결 docs/schema/links.dbml.
합본은 만들 때마다 같은 내용이 나오므로 커밋하지 않는다(data/ 아래나 임시 경로에 둔다).

  python scripts/schema/merge_pipeline_dbml.py --out data/pipeline_schema.dbml
  python scripts/schema/merge_pipeline_dbml.py            # 표준 출력
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = (
    ROOT / "docs" / "schema" / "bronze.dbml",
    ROOT / "docs" / "schema" / "silver.dbml",
    ROOT / "docs" / "schema" / "gold.dbml",
    ROOT / "docs" / "schema" / "rds.dbml",
    ROOT / "docs" / "schema" / "links.dbml",  # Ref 만. 표가 모두 나온 뒤에 와야 한다
)


def merge(sources: tuple[Path, ...] = SOURCES) -> str:
    parts = ["// Re:봄 전체 파이프라인 스키마 합본 (자동 생성, 고치지 말 것). 원본은 아래 각 파일"]
    for path in sources:
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n").rstrip("\n")
        parts.append(f"\n// ════════ {path.relative_to(ROOT).as_posix()} ════════\n{text}")
    return "\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, help="쓸 파일. 비우면 표준 출력")
    args = ap.parse_args(argv)
    text = merge()
    if args.out is None:
        sys.stdout.write(text)
        return
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"합본 {args.out} ({len(text.splitlines())}줄)")


if __name__ == "__main__":
    main()

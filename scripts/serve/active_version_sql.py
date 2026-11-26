"""Gold 레지스트리(기본 test)의 버전을 읽어 RDS active_version 에 넣을 SQL 을 출력한다. 읽기만 하고 아무것도 쓰지 않는다.

    python scripts/serve/active_version_sql.py                      # 등록된 버전 목록
    python scripts/serve/active_version_sql.py image_embedding@1 photo_trait@1 --by lemin   # INSERT 문

출력한 SQL 은 사람이 RDS 에 직접 실행한다. 이미 활성인 작업이면 직전 버전을 previous_version 에 남기고 바꾼다.
"""
from __future__ import annotations

import argparse
import json


def sql_literal(value: str | None) -> str:
    return "NULL" if value is None else "'" + value.replace("'", "''") + "'"


def query_config(config: dict) -> str | None:
    keys = {k: config[k] for k in ("model_id", "dim") if k in config}
    return json.dumps(keys, ensure_ascii=False, sort_keys=True) if keys else None


def insert_sql(task: str, version: str, config_hash: str, config: dict, by: str) -> str:
    qc = query_config(config)
    return (
        "INSERT INTO active_version (task, version, config_hash, query_config, activated_by)\n"
        f"VALUES ({sql_literal(task)}, {sql_literal(version)}, {sql_literal(config_hash)}, "
        f"{sql_literal(qc)}{'::jsonb' if qc else ''}, {sql_literal(by)})\n"
        "ON CONFLICT (task) DO UPDATE SET version = EXCLUDED.version, config_hash = EXCLUDED.config_hash,\n"
        "    query_config = EXCLUDED.query_config, activated_by = EXCLUDED.activated_by, activated_at = now(),\n"
        "    previous_version = CASE WHEN active_version.version = EXCLUDED.version\n"
        "                            THEN active_version.previous_version ELSE active_version.version END;"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("versions", nargs="*", help="task@N. 비우면 등록된 버전 목록만 보여 준다")
    ap.add_argument("--env", default="test")
    ap.add_argument("--profile")
    ap.add_argument("--by", default="lemin", help="activated_by")
    args = ap.parse_args(argv)

    from rebom.common.gold_catalog import VERSION_REGISTRY_TABLE, get_gold_environment
    from rebom.common.gold_version_registry import canonical_config, resolve_version
    from rebom.storage.silver.aws_catalog import get_aws_catalog

    catalog = get_aws_catalog(profile=args.profile)
    env = get_gold_environment(args.env)
    if not args.versions:
        rows = catalog.load_table(env.table(VERSION_REGISTRY_TABLE)).scan(
            selected_fields=("task", "version", "config_hash", "registered_at")).to_arrow().to_pylist()
        for r in sorted(rows, key=lambda r: (r["task"], r["version"])):
            print(f"{r['version']:<28} {r['config_hash']}  {r['registered_at']:%Y-%m-%d %H:%M}")
        return 0
    print("BEGIN;")
    for version in args.versions:
        task = version.split("@", 1)[0]
        config = resolve_version(catalog, env, task, version)  # 해시 검증 포함
        print(insert_sql(task, version, canonical_config(config)[1], config, args.by))
    print("COMMIT;")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""active_version.query_config 를 그 버전의 Gold 레지스트리 설정으로 채우는 UPDATE 문을 출력한다(쓰지 않음).

    python -m rebom.serving.sync.query_config --task text_embedding --version text_embedding@3 --profile rebom

검색 API 는 사용자 입력을 이 값(model_id·dim)으로 임베딩해 같은 버전의 저장 벡터와 비교한다(serving/db_repository.py).
값을 손으로 적으면 저장 벡터와 다른 모델·차원으로 질의할 수 있어서, 레지스트리에서 읽어 만든다.
config_hash 도 조건에 넣어, 활성 행이 이 레지스트리 설정이 아니면 0행이 바뀐다. 적용은 사람이 한다(AGENTS.md).
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from rebom.serving.sync import inputs

TASKS = ("text_embedding", "image_embedding")
QUERY_KEYS = ("model_id", "dim")


def query_config(config: dict[str, Any]) -> dict[str, Any]:
    missing = [k for k in QUERY_KEYS if not config.get(k)]
    if missing:
        raise ValueError(f"레지스트리 설정에 {', '.join(missing)} 가 없습니다")
    return {"model_id": str(config["model_id"]), "dim": int(config["dim"])}


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def update_sql(task: str, version: str, config_hash: str, qc: dict[str, Any]) -> str:
    body = json.dumps(qc, ensure_ascii=False, sort_keys=True)
    return (f"UPDATE active_version SET query_config = {_quote(body)}::jsonb "
            f"WHERE task = {_quote(task)} AND version = {_quote(version)} AND config_hash = {_quote(config_hash)};")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m rebom.serving.sync.query_config", description=__doc__.splitlines()[0])
    ap.add_argument("--task", required=True, choices=TASKS)
    ap.add_argument("--version", required=True, help="예: text_embedding@3 (active_version.version 과 같게)")
    ap.add_argument("--gold-env", default="test", choices=("test", "prod"))
    ap.add_argument("--profile", default=None, help="AWS 프로필 (PC)")
    args = ap.parse_args(argv)

    from rebom.common.gold_catalog import get_gold_environment
    from rebom.storage.silver.aws_catalog import get_aws_catalog

    config, config_hash = inputs.registry_config(get_aws_catalog(profile=args.profile),
                                                 get_gold_environment(args.gold_env), args.task, args.version)
    print(update_sql(args.task, args.version, config_hash, query_config(config)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

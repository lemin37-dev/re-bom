"""version 칸 없이 쌓인 Gold 행(이미지 임베딩·Jev 글 태그)을 레지스트리 버전 하나로 묶을 설정 JSON 을 출력한다.

동기화는 레지스트리 설정의 (model, prompt_version) 으로 version 이 빈 옛 Gold 행을 읽는다(serving/sync/inputs.legacy_key).
그래서 설정 값은 지금 코드가 Gold 에 쓰는 값과 같아야 한다.
- image_embedding: embedding/cli.py 가 --version 실행 때 맞춰 보는 설정과 같은 dict (다르면 다음 임베딩 실행이 거부됨)
- text_personality·text_health: Jev 모델과 질문지 버전 (run_text_tags 는 아직 레지스트리를 보지 않음)

    python scripts/serve/legacy_version_config.py --task image_embedding > image_embedding@1.json
    python -m rebom.common.gold_version_registry --env test --task image_embedding --version image_embedding@1 \
        --config image_embedding@1.json --profile rebom
"""
from __future__ import annotations

import argparse
import json

TASKS = ("image_embedding", "text_personality", "text_health")


def config_for(task: str, dim: int = 768) -> dict:
    if task == "image_embedding":
        from rebom.embedding import gemini, prep

        return {"model_id": gemini.MODEL, "dim": dim, "preprocess_version": prep.PREP_VERSION,
                "blank_std": prep.BLANK_STD}
    if task in ("text_personality", "text_health"):
        from rebom.tagging import text_tags
        from rebom.tagging.jev_client import MODEL

        return {"model_id": MODEL, "prompt_version": text_tags.VERSIONS[task]}
    raise ValueError(f"지원하지 않는 작업: {task}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", required=True, choices=TASKS)
    ap.add_argument("--dim", type=int, default=768)
    args = ap.parse_args(argv)
    print(json.dumps(config_for(args.task, args.dim), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

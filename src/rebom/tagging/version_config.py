"""사진 태깅 작업의 버전 설정 (gold_version_registry 에 등록하고 실행 전에 맞춰 보는 값).

등록과 검증이 같은 함수(runtime_config)를 쓴다. 질문지 문장·응답 형식·품종 이름표·후처리 기준이 바뀌면
해시가 달라져, 같은 버전 이름으로는 실행이 거부된다. 그때는 새 버전(task@N+1)을 등록한다.

등록용 JSON 만들기 (PC):
    python -m rebom.tagging.version_config --task photo_trait > photo_trait@1.json
    python -m rebom.common.gold_version_registry --env test --task photo_trait --version photo_trait@1 \
        --config photo_trait@1.json --profile rebom
"""
from __future__ import annotations

import argparse
import hashlib
import json

from rebom.tagging import breed_tags, image_input, trait_tags

MODEL = "google/gemini-3.1-flash-lite"
PROVIDER = "google-ai-studio"
REASONING = "low"
TEMPERATURE = 1.0
MEDIA_RESOLUTION = "MEDIA_RESOLUTION_MEDIUM"
REPEATS = 2
# 사진 준비(image_input.prepare_image): EXIF 회전만 바로잡아 JPEG 92 로 다시 쓰고, 크기는 줄이지 않는다
PREPROCESS_VERSION = "exif-jpeg92-v1"


def _sha256(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _prompt(task: str) -> dict:
    if task == trait_tags.TASK:
        return {"version": trait_tags.PROMPT_VERSION, "system": trait_tags.SYSTEM_PROMPT,
                "user": trait_tags.USER_PROMPT, "schema": trait_tags.response_schema()}
    if task == breed_tags.TASK:
        return {"version": breed_tags.PROMPT_VERSION, "system": breed_tags.SYSTEM_PROMPT,
                "user": breed_tags.build_prompt(), "schema": breed_tags.response_schema()}
    raise ValueError(f"사진 태깅 작업이 아님: {task}")


def _postprocess(task: str) -> dict:
    if task == trait_tags.TASK:
        return {"rule": "two-run-agree", "repeats": REPEATS}
    return {"rule": "two-run-top3-mean", "repeats": REPEATS, "strong_similarity": breed_tags.STRONG_SIMILARITY,
            "alias_sha256": _sha256({"alias": breed_tags.ALIAS, "candidates": list(breed_tags.load_candidates())})}


def runtime_config(task: str) -> dict:
    """지금 코드가 이 작업을 부르는 설정. 레지스트리의 config_json 과 해시까지 같아야 실행한다."""
    prompt = _prompt(task)
    return {
        "model_id": MODEL,
        "provider": PROVIDER,
        "prompt_version": prompt["version"],
        "prompt_sha256": _sha256({k: prompt[k] for k in ("system", "user", "schema")}),
        "preprocess_version": PREPROCESS_VERSION,
        "max_image_bytes": image_input.MAX_IMAGE_BYTES,
        "media_resolution": MEDIA_RESOLUTION,
        "reasoning_effort": REASONING,
        "temperature": TEMPERATURE,
        "postprocess": _postprocess(task),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="사진 태깅 버전 설정 JSON 출력 (레지스트리 등록용)")
    ap.add_argument("--task", required=True, choices=(trait_tags.TASK, breed_tags.TASK))
    args = ap.parse_args(argv)
    print(json.dumps(runtime_config(args.task), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

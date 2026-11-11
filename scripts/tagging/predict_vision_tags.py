"""비전 모델(Gemini)로 귀 태그를 고르게 하고 모델별 JSONL 로 쓴다 (B 방식).

설치: pip install -U google-genai python-dotenv pillow
키: 레포 루트 .env 의 GEMINI_API_KEY (유료 등급)
예)
  python scripts/tagging/predict_vision_tags.py --limit 3                       # 동작 확인
  python scripts/tagging/predict_vision_tags.py --out-dir data/eval/photo_tags/vision_v4   # 기본 2개 모델, body_eval_set.csv
  python scripts/tagging/score_vision.py                                         # 채점·보고서
출력: data/eval/photo_tags/vision/<모델>.jsonl  (한 줄 = 사진 1장, 태그·근거·토큰·지연)

사진은 평가 라벨과 같은 1번 사진만 넣는다. 공고 품종(kindNm)은 넣지 않는다. 배경 제거 없이 EXIF 회전만 바로잡는다.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from rebom.embedding.gemini import RETRY_CODES, _error_code, load_api_key  # noqa: E402
from rebom.tagging.vision_tags import (  # noqa: E402
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    USER_PROMPT,
    parse_answer,
    prepare_image,
    response_schema,
)

OUT_DIR = ROOT / "data" / "eval" / "photo_tags"
DEFAULT_MODELS = ["gemini-3.1-flash-lite", "gemini-3.5-flash"]


def make_config(media_resolution: str, thinking_level: str):
    from google.genai import types

    res = {"low": types.MediaResolution.MEDIA_RESOLUTION_LOW, "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
           "high": types.MediaResolution.MEDIA_RESOLUTION_HIGH}[media_resolution]
    return types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_json_schema=response_schema(),
        media_resolution=res,
        # Gemini 2.5 는 thinking_level 을 받지 않아 'none' 이면 thinking 설정을 넣지 않는다.
        thinking_config=None if thinking_level == "none" else types.ThinkingConfig(thinking_level=thinking_level),
    )


def tag_one(client, model: str, config, row: dict, max_retries: int = 5) -> dict:
    from google.genai import types

    rec = {"desertionNo": row["desertionNo"], "photo": 1, "model": model, "prompt_version": PROMPT_VERSION}
    path = ROOT / row["photo_path"].replace("\\", "/")
    try:
        data, mime = prepare_image(path.read_bytes())
    except (OSError, ValueError) as exc:
        return {**rec, "error": f"사진 읽기 실패: {exc}"}
    contents = [types.Part.from_bytes(data=data, mime_type=mime), USER_PROMPT]
    for attempt in range(max_retries + 1):
        t0 = time.perf_counter()
        try:
            resp = client.models.generate_content(model=model, contents=contents, config=config)
            latency = time.perf_counter() - t0
            answers = parse_answer(resp.text)
        except Exception as exc:  # noqa: BLE001 - SDK 예외 종류가 버전마다 다름
            if _error_code(exc) in RETRY_CODES and attempt < max_retries:
                time.sleep(min(2 ** attempt, 30))
                continue
            return {**rec, "error": f"{type(exc).__name__}: {exc}"[:500]}
        u = resp.usage_metadata
        return {
            **rec,
            "tags": {k: {"value": a.value, "term": a.term, "evidence": a.evidence} for k, a in answers.items()},
            "usage": {"prompt": u.prompt_token_count or 0, "output": u.candidates_token_count or 0,
                      "thoughts": u.thoughts_token_count or 0},
            "latency_sec": round(latency, 3),
        }
    return {**rec, "error": "재시도 초과"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eval-set", type=Path, default=OUT_DIR / "body_eval_set.csv")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR / "vision")
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--media-resolution", choices=["low", "medium", "high"], default="medium")
    ap.add_argument("--thinking-level", default="low", help="low·minimal·high, 또는 none(설정 안 함, Gemini 2.5용)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    from google import genai

    with args.eval_set.open(encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if r["photo_path"]]
    if args.limit:
        rows = rows[: args.limit]
    client = genai.Client(api_key=load_api_key())
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for model in args.models:
        config = make_config(args.media_resolution, args.thinking_level)
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            recs = list(pool.map(lambda r: tag_one(client, model, config, r), rows))
        meta = {"media_resolution": args.media_resolution, "thinking_level": args.thinking_level}
        out = args.out_dir / f"{model}.jsonl"
        with out.open("w", encoding="utf-8", newline="\n") as fo:
            for rec in recs:
                fo.write(json.dumps({**rec, **meta}, ensure_ascii=False) + "\n")
        errors = [r for r in recs if "error" in r]
        print(f"{model}: {len(recs) - len(errors)}/{len(recs)}장 성공, {time.time() - t0:.0f}초 → {out}")
        for r in errors[:3]:
            print(f"  오류 {r['desertionNo']}: {r['error']}")


if __name__ == "__main__":
    main()

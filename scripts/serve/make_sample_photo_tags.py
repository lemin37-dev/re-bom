"""data/sample/rescue_sample.json 의 보호중 개에 실제 사진 태거를 돌려 data/sample/photo_tags.json 을 만든다.

검색 API sample 모드의 태그 검색용이다. 운영에서는 Gold gold_animal_tag 를 서비스 DB 에 적재해 쓴다(별도 작업).
- 대표 사진: popfile1~8 중 실제로 받아지는 가장 작은 칸(docs/data_contract.md 13.1, 결정 #47 과 같은 기준).
- 특징 질문지(photo_trait)만 부른다. 품종 태그는 화면에 보이지 않는 태그라 검색 화면 태그에 쓰지 않는다.
- 판정·호출 설정은 rebom.tagging.run_photo_tags 를 그대로 쓴다(2회 일치, medium 해상도). X-Title 은 rebom-tag-photo.
- 유료 호출이다. 보호중 개 약 50마리 × 2번, 약 $0.08 (2026-11 가격).

실행 (레포 루트, .env 에 OPENROUTER_KEY_TAG_IMG):
    python scripts/serve/make_sample_photo_tags.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import requests

from rebom.common.openrouter import OpenRouterClient
from rebom.serving.schemas import PHOTO_KEYS
from rebom.tagging import run_photo_tags as rpt
from rebom.tagging import tag_codes, trait_tags

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "data" / "sample" / "rescue_sample.json"
OUT = REPO / "data" / "sample" / "photo_tags.json"


def representative(raw: dict) -> tuple[int, bytes] | None:
    for slot, key in enumerate(PHOTO_KEYS, start=1):
        url = raw.get(key)
        if not url:
            continue
        try:
            resp = requests.get(url, timeout=30)
        except requests.RequestException:
            continue
        if resp.status_code == 200 and resp.content:
            return slot, resp.content
    return None


def main() -> int:
    rows = json.loads(SAMPLE.read_text(encoding="utf-8"))
    dogs = [r for r in rows if r.get("upKindCd") == rpt.DOG_SPECIES_CODE and r.get("processState") == "보호중"]
    photos: dict[str, dict] = {}  # sha256 -> 사진·공고 (같은 사진은 한 번만 부른다)
    animals: list[tuple[str, int, str]] = []  # (공고, 대표 칸, sha256)
    no_photo: list[str] = []
    for raw in dogs:
        got = representative(raw)
        if got is None:
            no_photo.append(raw["desertionNo"])
            continue
        slot, data = got
        sha = hashlib.sha256(data).hexdigest()
        photo = photos.setdefault(sha, {"url": raw[PHOTO_KEYS[slot - 1]], "data": data, "nos": []})
        photo["nos"].append(raw["desertionNo"])
        animals.append((raw["desertionNo"], slot, sha))
    items = [(rpt.Target(sha, p["url"], (trait_tags.TASK,), tuple(p["nos"])), p["data"]) for sha, p in photos.items()]
    print(f"보호중 개 {len(dogs)}마리, 대표 사진 {len(items)}장, 사진 없음 {len(no_photo)}")

    client = OpenRouterClient(rpt.KEY_ENV, title="rebom-tag-photo")
    cfg = rpt.TaggerConfig(run_id=f"sample-{datetime.now(UTC):%Y%m%dT%H%M%SZ}", concurrency=4)
    outcomes, stop = rpt.tag_photos(items, client, cfg)
    if stop is not None:
        print(f"중단: {stop}", file=sys.stderr)
        return 2

    by_sha: dict[str, list[str]] = {}
    failed, cost = 0, 0.0
    for o in outcomes:
        cost += sum(r["cost_usd"] or 0 for r in o.requests)
        task = o.tasks[trait_tags.TASK]
        if task.status == rpt.FAILED:
            failed += 1
            continue
        by_sha[o.target.sha256] = [r["tag_code"] for r in task.tag_rows if tag_codes.get(r["tag_code"]).displayable]
    tagged = sorted(
        ({"desertionNo": no, "imageSlot": slot, "sourceHash": sha, "tags": by_sha[sha]}
         for no, slot, sha in animals if sha in by_sha),
        key=lambda t: t["desertionNo"],
    )
    OUT.write_text(json.dumps({
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": rpt.MODEL,
        "promptVersion": trait_tags.PROMPT_VERSION,
        "note": "scripts/serve/make_sample_photo_tags.py 로 만든 sample 모드용 사진 태그 (보호중 개 대표 사진)",
        "items": tagged,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"태그 {len(tagged)}마리 저장, 실패한 사진 {failed}장, 비용 ${cost:.4f} → {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

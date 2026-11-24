"""Build text cards from Silver and Gold tags, then save new card vectors."""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from rebom.common import gold_catalog as gc
from rebom.common.gold_version_registry import require_version_column, resolve_version
from rebom.embedding import gemini, prep, text_card
from rebom.embedding.gold_store import table_arrow
from rebom.tagging.run_photo_tags import representative_photos
from rebom.tagging.tag_codes import TAGS

TASK = "text_embedding"
PHOTO_LABELS = {"fur": "털", "ear": "귀", "eye": "눈매"}
PERSONALITY = {
    "gentle": "순함", "friendly": "친화적", "timid": "겁많음", "wary": "경계",
    "active": "활발", "aggressive": "사나움", "sensitive": "예민", "curious": "호기심",
}
HEALTH = {"health.info": "있음", "health.none": "없음"}
# 작업 → (받는 source 값, subject_type). source 는 출처(photo 사진 · text 공고 글 · rule 공고 칸)이고 모델 이름은
# 넣지 않는다(11/24 결정). 글 태그는 지금 적재 코드가 jev 로 쓰고 있어, 바뀔 때까지 jev 도 받는다.
TAG_SOURCES = {
    "photo_trait": (("photo",), "photo"),
    "text_personality": (("text", "jev"), "animal"),
    "text_health": (("text", "jev"), "animal"),
}


@dataclass(frozen=True)
class Card:
    notice: str
    text: str
    source_hash: str
    source_updated: str | None
    tag_versions: dict[str, str]


def _tag_index(tags, versions: dict[str, str]):
    index = defaultdict(set)
    for row in tags:
        task = row["task"]
        version = versions.get(task)
        if version is None or not row.get("searchable"):
            continue
        sources, subject_type = TAG_SOURCES[task]
        if row["source"] not in sources or row["subject_type"] != subject_type:
            continue
        if (row.get("version") or row["prompt_version"]) != version:
            continue
        index[(task, row["subject_key"], row["source_hash"])].add(row["tag_code"])
    return index


def build_cards(notices, photos, tags, template: str, versions: dict[str, str]) -> list[Card]:
    """Choose the current representative photo and Jev's current input per notice."""
    current_photos = representative_photos(photos)
    index = _tag_index(tags, versions)
    cards = []
    for row in sorted(notices, key=lambda row: str(row["desertion_no"])):
        if row.get("status_group") != "PROTECTING":
            continue
        notice = str(row["desertion_no"])
        photo = current_photos.get(notice)
        photo_hash = str(photo["sha256"]).lower() if photo else ""
        raw = row.get("info_text")
        raw = raw if raw and raw.strip() else row.get("special_mark")
        # Matches the Jev v1 input cleaning contract.
        jev_hash = text_card.text_sha256(text_card.persona({"specialMark": raw}))
        values = []
        used_versions = {}
        if template == "A2":
            traits = sorted(index.get(("photo_trait", photo_hash, photo_hash), ()))
            for code in traits:
                if code in TAGS:
                    values.append((PHOTO_LABELS[code.partition(".")[0]], TAGS[code].label))
                    used_versions["photo_trait"] = versions["photo_trait"]
            personality = index.get(("text_personality", notice, jev_hash), ())
            labels = [label for code, label in PERSONALITY.items() if f"personality.{code}" in personality]
            if labels:
                values.append(("성격", "·".join(labels)))
                used_versions["text_personality"] = versions["text_personality"]
            health = index.get(("text_health", notice, jev_hash), ())
            if len(health) == 1:
                label = HEALTH.get(next(iter(health)))
                if label:
                    values.append(("건강정보", label))
                    used_versions["text_health"] = versions["text_health"]
        card, source_hash = text_card.card_from_silver(
            row | {"special_mark": raw} if template == "A2" else row, template, values,
        )
        if card:
            cards.append(Card(notice, card, source_hash, row.get("source_updated_at_raw"), used_versions))
    return cards


def write_cards(catalog, env, table, cards: list[Card], *, template: str, version: str, run_id: str, embedder) -> None:
    """Save request, then results, then per-card success status."""
    from pyiceberg.expressions import And, EqualTo, Or

    requests = catalog.load_table(env.table(gc.EMBEDDING_REQUEST_LOG_TABLE))
    logs = catalog.load_table(env.table(gc.EMBEDDING_INFERENCE_LOG_TABLE))
    vectors, meta = embedder.embed_texts_recorded([text_card.doc_input(card.text) for card in cards])
    valid = len(vectors) == len(cards) and all(
        len(vector) == gemini.DEFAULT_DIM and all(map(math.isfinite, vector)) and any(vector)
        for vector in vectors
    )
    requested_at = meta["requested_at"]
    requests.append(table_arrow(requests, [{
        "request_id": meta.get("request_id"), "provider": meta.get("provider"),
        "requested_model": meta.get("requested_model"), "response_model": meta.get("response_model"),
        "prompt_tokens": meta.get("prompt_tokens"), "cost_usd": meta.get("cost_usd"),
        "latency_ms": meta.get("latency_ms"), "attempts": meta.get("attempts"), "batch_size": len(cards),
        "task": TASK, "subject_type": "animal", "subject_key": cards[0].notice, "version": version,
        "prompt_version": template, "status": "OK" if valid else "ERROR", "gold_run_id": run_id,
        "requested_at": requested_at, "dt": requested_at.astimezone(UTC).date().isoformat(),
        "error_code": None if valid else "INVALID_VECTORS",
    }]))
    if not valid:
        raise ValueError("Text embedding returned an unexpected count, dimension or vector value")

    now = datetime.now(UTC)
    outcomes = [{
        "subject_type": "animal", "subject_key": card.notice, "task": TASK, "version": version,
        "input_hash": card.source_hash, "status": "OK", "attempts": 1, "gold_run_id": run_id,
        "gold_updated_at": now, "detail": json.dumps({"request_id": meta.get("request_id"), "batch_size": len(cards)}),
    } for card in cards]
    results = [{
        "subject_type": "animal", "subject_key": card.notice, "task": TASK, "version": version,
        "model": text_card.MODEL_KEY, "prompt_version": template, "source_hash": card.source_hash,
        "input_text": card.text, "source_update_dt": card.source_updated,
        "tag_versions": json.dumps(card.tag_versions, sort_keys=True),
        "gold_run_id": run_id, "gold_updated_at": now, "embedding": prep.l2_normalize(vector),
    } for card, vector in zip(cards, vectors, strict=True)]
    terms = [And(EqualTo("subject_key", card.notice), EqualTo("source_hash", card.source_hash)) for card in cards]
    keys = Or(*terms) if len(terms) > 1 else terms[0]
    table.overwrite(table_arrow(table, results, vector=True), overwrite_filter=And(
        EqualTo("subject_type", "animal"), EqualTo("task", TASK), EqualTo("version", version), keys,
    ))
    logs.upsert(table_arrow(logs, outcomes), join_cols=list(gc.INFERENCE_LOG_KEYS))


def run(catalog, gold, silver, version: str, *, limit: int, write: bool,
        published=None, embedder_factory=gemini.GeminiEmbedder) -> dict:
    from pyiceberg.expressions import And, EqualTo, In

    config = resolve_version(catalog, gold, TASK, version)
    template = config.get("template_version")
    versions = config.get("tag_versions", {})
    if config["model_id"] != gemini.MODEL or config.get("dim") != gemini.DEFAULT_DIM:
        raise ValueError("Text embedding version does not match the configured model and dimension")
    if template not in text_card.TEMPLATE_VERSIONS:
        raise ValueError(f"Unsupported text card template: {template}")
    if (
        not isinstance(versions, dict) or set(versions) - TAG_SOURCES.keys()
        or any(not isinstance(value, str) or not value for value in versions.values())
    ):
        raise ValueError("tag_versions must map supported tag tasks to version strings")
    if template == "A2" and set(versions) != TAG_SOURCES.keys():
        raise ValueError("A2 requires versions for photo_trait, text_personality and text_health")
    if limit < 1:
        raise ValueError("limit must be positive")

    table = catalog.load_table(gold.table(gc.TEXT_EMBEDDING_TABLE))
    require_version_column(table)
    if not any(field.name == "tag_versions" for field in table.schema().fields):
        raise ValueError("Apply the Gold embedding schema before running text embedding")
    if write:
        require_version_column(catalog.load_table(gold.table(gc.EMBEDDING_REQUEST_LOG_TABLE)))

    notice_scan, photo_scan = {}, {}
    if published is not None:
        published.validate_snapshots(catalog)
        notice_scan["snapshot_id"] = published.notice_snapshot_id
        photo_scan["snapshot_id"] = published.photo_snapshot_id
    notices = catalog.load_table(published.notice_table if published else silver.notice_table)
    # Select only public card fields and the identifiers needed for selection.
    notice_fields = {"desertion_no", "status_group", "source_updated_at_raw", "info_text", *text_card.SILVER_TO_API}
    available = {field.name for field in notices.schema().fields}
    notice_rows = notices.scan(
        row_filter=EqualTo("status_group", "PROTECTING"), selected_fields=tuple(sorted(notice_fields & available)),
        **notice_scan,
    ).to_arrow().to_pylist()
    photos, tags = [], []
    if template == "A2":
        photos = catalog.load_table(published.photo_table if published else silver.image_table).scan(
            selected_fields=("desertion_no", "image_slot", "sha256", "original_s3_key"),
            **photo_scan,
        ).to_arrow().to_pylist()
        tag_table = catalog.load_table(gold.table(gc.TAG_TABLE))
        tag_fields = ("subject_type", "subject_key", "source_hash", "task", "source", "tag_code",
                      "searchable", "prompt_version")
        if any(field.name == "version" for field in tag_table.schema().fields):
            tag_fields += ("version",)
        tags = tag_table.scan(row_filter=In("task", sorted(versions)), selected_fields=tag_fields).to_arrow().to_pylist()
    cards = build_cards(notice_rows, photos, tags, template, versions)
    existing = table.scan(
        row_filter=And(EqualTo("subject_type", "animal"), EqualTo("task", TASK), EqualTo("version", version)),
        selected_fields=("subject_key", "source_hash"),
    ).to_arrow().to_pylist()
    keys = {(row["subject_key"], row["source_hash"]) for row in existing}
    to_write = [card for card in cards if (card.notice, card.source_hash) not in keys][:limit]
    run_id = f"text-{uuid4().hex}"
    if write and published is not None:
        inputs = catalog.load_table(gold.table(gc.RUN_INPUT_TABLE))
        inputs.append(table_arrow(inputs, [{
            "gold_run_id": run_id, "publish_id": published.publish_id, "task": TASK,
            "version": version, "started_at": datetime.now(UTC),
        }]))
    if write and to_write:
        embedder = embedder_factory(dim=gemini.DEFAULT_DIM)
        for start in range(0, len(to_write), gemini.TEXT_BATCH):
            write_cards(catalog, gold, table, to_write[start:start + gemini.TEXT_BATCH],
                        template=template, version=version, run_id=run_id, embedder=embedder)
    result = {"cards": len(cards), "planned": len(to_write), "written": len(to_write) if write else 0}
    if published is not None:
        result.update(publish_id=published.publish_id, gold_run_id=run_id)
    return result


def command(args: argparse.Namespace) -> int:
    from rebom.storage.silver.aws_catalog import get_aws_catalog
    from rebom.storage.silver.environment_config import get_silver_environment

    if args.silver_env != args.gold_env:
        raise ValueError("Silver and Gold environments must match")
    published = None
    if args.publish_id:
        import boto3
        from rebom.common.silver_input import load_ready

        if not args.publish_bucket or not args.publish_prefix:
            raise ValueError("--publish-id requires --publish-bucket and --publish-prefix")
        s3 = boto3.Session(profile_name=args.profile).client("s3")
        published = load_ready(s3, args.publish_bucket, args.publish_prefix, args.publish_id)
    elif args.publish_bucket or args.publish_prefix:
        raise ValueError("Publication location requires --publish-id")
    silver = None if published else get_silver_environment(args.silver_env)
    result = run(get_aws_catalog(profile=args.profile), gc.get_gold_environment(args.gold_env),
                 silver, args.version, limit=args.limit, write=args.write, published=published)
    print(json.dumps(result, ensure_ascii=False))
    return 0

"""Build text cards from Silver and Gold tags, then save new card vectors."""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from uuid import uuid4

from rebom.common import gold_catalog as gc
from rebom.common.gold_version_registry import require_version_column, resolve_version
from rebom.embedding import card_template, gemini, prep, text_card
from rebom.embedding.gold_store import load_logs, table_arrow
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


KST = timezone(timedelta(hours=9))


def silver_cutoff(value: str | None) -> tuple[datetime, bool] | None:
    """--silver-as-of 값 → (기준 시각, 그 시각 포함 여부). 사진 태깅의 select --silver-as-of(#78)와 같은 규칙이다.

    날짜만 주면 그날 끝: "2026-11-23" → 2026-11-24 00:00 KST 보다 앞선 마지막 스냅샷. 시각을 주면 그 시각까지(포함).
    """
    if not value:
        return None
    if len(value) == 10:
        return datetime.combine(date.fromisoformat(value) + timedelta(days=1), time(), KST), False
    at = datetime.fromisoformat(value)
    return (at if at.tzinfo else at.replace(tzinfo=KST)), True


def _snapshot(table, name: str, cutoff: tuple[datetime, bool] | None):
    """읽을 Silver 스냅샷. 기준 시각 전 스냅샷이 없으면 멈춘다."""
    if cutoff is None:
        return table.current_snapshot()
    at, inclusive = cutoff
    snapshot = table.snapshot_as_of_timestamp(int(at.timestamp() * 1000), inclusive=inclusive)
    if snapshot is None:
        raise ValueError(f"{name}: no snapshot before {at.isoformat()}")
    return snapshot


def _check_tag_sources(tags, versions: dict[str, str]) -> None:
    """이 버전의 태그인데 모르는 source 면 멈춘다. 그대로 두면 태그가 카드에서 오류 없이 빠진다."""
    unknown = sorted({
        (row["task"], str(row["source"])) for row in tags
        if (row.get("version") or row["prompt_version"]) == versions.get(row["task"])
        and row["source"] not in TAG_SOURCES[row["task"]][0]
    })
    if unknown:
        raise ValueError(f"Unexpected tag source {unknown}; expected one of TAG_SOURCES")


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


def _note_text(row):
    """특징 글. info_text 가 비면 special_mark."""
    raw = row.get("info_text")
    return raw if raw and raw.strip() else row.get("special_mark")


def text_tag_hash(row) -> str:
    """글 태그의 source_hash 와 같은 값 (Jev v1 입력 정리 규칙)."""
    return text_card.text_sha256(text_card.persona({"specialMark": _note_text(row)}))


def build_cards(notices, photos, tags, template: str, versions: dict[str, str]) -> list[Card]:
    """Choose the current representative photo and Jev's current input per notice."""
    spec = card_template.load(template)
    current_photos = representative_photos(photos)
    index = _tag_index(tags, versions)
    cards = []
    for row in sorted(notices, key=lambda row: str(row["desertion_no"])):
        if row.get("status_group") != "PROTECTING":
            continue
        notice = str(row["desertion_no"])
        photo = current_photos.get(notice)
        photo_hash = str(photo["sha256"]).lower() if photo else ""
        raw = _note_text(row)
        jev_hash = text_tag_hash(row)
        values = []
        used_versions = {}
        if spec.uses_tags:
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
            row | {"special_mark": raw} if spec.note_from_info_text else row, template, values,
        )
        if card:
            cards.append(Card(notice, card, source_hash, row.get("source_updated_at_raw"), used_versions))
    return cards


def write_cards(catalog, env, table, cards: list[Card], *, template: str, version: str, run_id: str, embedder,
                silver_snapshots: dict[str, int] | None = None) -> None:
    """Save request, then results, then per-card success status."""
    from pyiceberg.expressions import And, EqualTo, Or

    logs, requests = load_logs(catalog, env)
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
        "gold_updated_at": now, "detail": json.dumps({
            "request_id": meta.get("request_id"), "batch_size": len(cards),
            **({"silver_snapshots": silver_snapshots} if silver_snapshots else {}),
        }),
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


def verify_template(catalog, gold, config: dict, *, write: bool) -> bool:
    """등록된 템플릿 내용·지문과 지금 코드가 같은지 (text_version.py). 다르면 모델을 부르기 전에 멈춘다."""
    from rebom.embedding import text_version

    return text_version.verify(catalog, gold, config, write=write)


def run(catalog, gold, silver, version: str, *, limit: int, write: bool, embedder_factory=gemini.GeminiEmbedder,
        silver_as_of: str | None = None, require_tags: tuple[str, ...] = (), show: int = 0, published=None) -> dict:
    from pyiceberg.expressions import And, EqualTo, In

    if published is not None and silver_as_of is not None:
        raise ValueError("--publish-id and --silver-as-of are mutually exclusive")

    config = resolve_version(catalog, gold, TASK, version)
    template = config.get("template_version")
    versions = config.get("tag_versions", {})
    if config["model_id"] != gemini.MODEL or config.get("dim") != gemini.DEFAULT_DIM:
        raise ValueError("Text embedding version does not match the configured model and dimension")
    spec = card_template.load(template)
    if (
        not isinstance(versions, dict) or set(versions) - TAG_SOURCES.keys()
        or any(not isinstance(value, str) or not value for value in versions.values())
    ):
        raise ValueError("tag_versions must map supported tag tasks to version strings")
    if spec.uses_tags and set(versions) != TAG_SOURCES.keys():
        raise ValueError(f"{template} requires versions for photo_trait, text_personality and text_health")
    fingerprint = verify_template(catalog, gold, config, write=write)
    if limit < 1:
        raise ValueError("limit must be positive")
    if set(require_tags) - set(versions):
        raise ValueError(f"require_tags must be tasks of this version: {sorted(versions)}")
    cutoff = silver_cutoff(silver_as_of)

    table = catalog.load_table(gold.table(gc.TEXT_EMBEDDING_TABLE))
    require_version_column(table)
    if not any(field.name == "tag_versions" for field in table.schema().fields):
        raise ValueError("Apply the Gold embedding schema before running text embedding")
    if write:
        require_version_column(load_logs(catalog, gold)[1])

    notice_table = published.notice_table if published else silver.notice_table
    notices = catalog.load_table(notice_table)
    snapshots = {}
    if published is not None:
        published.validate_snapshots(catalog)
        snapshots.update({published.notice_table: published.notice_snapshot_id,
                          published.photo_table: published.photo_snapshot_id})

    def snapshot_id(silver_table, name: str) -> int | None:
        if published is not None:
            return snapshots[name]
        snapshot = _snapshot(silver_table, name, cutoff)
        if snapshot is not None:
            snapshots[name] = snapshot.snapshot_id
        return snapshot.snapshot_id if cutoff is not None else None

    # Select only public card fields and the identifiers needed for selection.
    notice_fields = {"desertion_no", "status_group", "source_updated_at_raw", "info_text", *text_card.SILVER_TO_API}
    available = {field.name for field in notices.schema().fields}
    notice_rows = notices.scan(
        row_filter=EqualTo("status_group", "PROTECTING"), selected_fields=tuple(sorted(notice_fields & available)),
        snapshot_id=snapshot_id(notices, notice_table),
    ).to_arrow().to_pylist()
    photos, tags = [], []
    if spec.uses_tags:
        photo_table = published.photo_table if published else silver.image_table
        images = catalog.load_table(photo_table)
        photos = images.scan(
            selected_fields=("desertion_no", "image_slot", "sha256", "original_s3_key"),
            snapshot_id=snapshot_id(images, photo_table),
        ).to_arrow().to_pylist()
        tag_table = catalog.load_table(gold.table(gc.TAG_TABLE))
        tag_fields = ("subject_type", "subject_key", "source_hash", "task", "source", "tag_code",
                      "searchable", "prompt_version")
        if any(field.name == "version" for field in tag_table.schema().fields):
            tag_fields += ("version",)
        tags = tag_table.scan(row_filter=In("task", sorted(versions)), selected_fields=tag_fields).to_arrow().to_pylist()
        _check_tag_sources(tags, versions)
    cards = build_cards(notice_rows, photos, tags, template, versions)
    with_tags = {task: sum(task in card.tag_versions for card in cards) for task in sorted(versions)}
    selected = [card for card in cards if set(require_tags) <= set(card.tag_versions)]
    existing = table.scan(
        row_filter=And(EqualTo("subject_type", "animal"), EqualTo("task", TASK), EqualTo("version", version)),
        selected_fields=("subject_key", "source_hash"),
    ).to_arrow().to_pylist()
    keys = {(row["subject_key"], row["source_hash"]) for row in existing}
    to_write = [card for card in selected if (card.notice, card.source_hash) not in keys][:limit]
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
                        template=template, version=version, run_id=run_id, embedder=embedder,
                        silver_snapshots=snapshots)
    result = {"template": template, "template_sha256": spec.sha256, "template_recorded": fingerprint,
            "cards": len(cards), "with_tags": with_tags, "selected": len(selected), "planned": len(to_write),
            "written": len(to_write) if write else 0, "silver_as_of": cutoff[0].isoformat() if cutoff else None,
            "silver_snapshots": snapshots,
            "show": [{"notice": c.notice, "text": c.text, "tag_versions": c.tag_versions} for c in to_write[:show]]}
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
                 silver, args.version, limit=args.limit, write=args.write, silver_as_of=args.silver_as_of,
                 require_tags=tuple(args.require_tags), show=args.show, published=published)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0

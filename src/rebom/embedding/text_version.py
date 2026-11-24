"""글 임베딩(text_embedding@N)의 실행 설정: 레지스트리에 등록할 값을 만들고, 실행 전에 지금 코드와 같은지 본다.

두 버전을 구분한다.
- 템플릿 버전 A1, A2 …   글의 형식 (card_templates/<버전>.json). Gold 의 prompt_version
- 실행 버전 text_embedding@N  벡터를 만든 조건 전체 (템플릿 내용·지문 + 태그 버전 + 모델). Gold 의 version, 등록 뒤 바꿀 수 없음

등록값에는 템플릿의 **내용 전체**와 지문 두 개가 들어간다.
- template_sha256  템플릿 파일 내용. 파일을 고치면 달라진다
- golden_sha256    고정 예시(golden.json)로 만든 카드들. 체중·털색·가리기·태그 이름표 같은 코드를 고쳐도 달라진다
지금 코드로 만든 값이 등록값과 다르면 모델을 부르기 전에 멈춘다. 같은 템플릿 이름인데 내용이 다른 등록이 있어도 멈춘다.

    # 등록용 JSON 만들기 (PC)
    python -m rebom.embedding.text_version config --template A2 --tag photo_trait=photo_trait@1 \\
        --tag text_personality=text-personality-v1 --tag text_health=text-health-v1 > text_embedding@2.json
    # 등록 (사람이 실행). 템플릿 이름이 같은데 내용이 다른 등록이 있으면 거부한다
    python -m rebom.embedding.text_version register --env test --version text_embedding@2 --config text_embedding@2.json --profile rebom
    # 등록된 버전별 템플릿 이력 (읽기만)
    python -m rebom.embedding.text_version history --env test --profile rebom
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from rebom.common import gold_catalog as gc
from rebom.common.gold_version_registry import canonical_config, register_version
from rebom.embedding import card_template, gemini, text_batch, text_card

TASK = text_batch.TASK
FINGERPRINTS = ("template", "template_sha256", "golden_sha256", "doc_prefix")


def golden_cards(template: str) -> list[str]:
    """고정 예시로 만든 카드. build_cards 를 그대로 써서 태그 고르기·이름표까지 포함한다."""
    data = card_template.golden(template)
    versions = {task: f"golden-{task}" for task in text_batch.TAG_SOURCES}
    tags = [{"task": "photo_trait", "source": "photo", "subject_type": "photo", "subject_key": sha, "source_hash": sha,
             "tag_code": code, "searchable": True, "version": versions["photo_trait"], "prompt_version": "-"}
            for sha, codes in data["photo_tags"].items() for code in codes]
    texts = {n["desertion_no"]: n for n in data["notices"]}
    for notice, by_task in data["text_tags"].items():
        source_hash = text_batch.text_tag_hash(texts[notice])
        tags += [{"task": task, "source": "text", "subject_type": "animal", "subject_key": notice, "source_hash": source_hash,
                  "tag_code": code, "searchable": True, "version": None, "prompt_version": versions[task]}
                 for task, codes in by_task.items() for code in codes]
    return [card.text for card in text_batch.build_cards(data["notices"], data["photos"], tags, template, versions)]


def runtime_config(template: str, tag_versions: dict[str, str]) -> dict[str, Any]:
    """지금 코드가 이 템플릿·태그 버전으로 글 벡터를 만드는 조건. 레지스트리의 config_json 과 같아야 실행한다."""
    loaded = card_template.load(template)
    return {
        "model_id": gemini.MODEL,
        "dim": gemini.DEFAULT_DIM,
        "template_version": template,
        "template": dict(loaded.content),
        "template_sha256": loaded.sha256,
        "golden_sha256": card_template.content_sha256(golden_cards(template)),
        "doc_prefix": text_card.doc_input(""),
        "tag_versions": dict(tag_versions),
    }


def has_fingerprint(config: dict[str, Any]) -> bool:
    return all(key in config for key in FINGERPRINTS)


def check_config(config: dict[str, Any]) -> None:
    """등록값과 지금 코드가 다르면 다른 항목을 알려 주고 멈춘다."""
    runtime = runtime_config(config["template_version"], config.get("tag_versions", {}))
    if canonical_config(runtime)[1] == canonical_config(config)[1]:
        return
    different = sorted(k for k in runtime.keys() | config.keys() if runtime.get(k) != config.get(k))
    raise card_template.TemplateError(
        f"지금 코드가 등록된 설정과 다름 {different}. 템플릿이나 카드 생성 코드가 바뀌었으면 새 text_embedding@N 을 등록해 주세요.")


def registered(catalog, env) -> list[dict[str, Any]]:
    """등록된 글 임베딩 버전 (번호 순). config 는 JSON 을 푼 값."""
    from pyiceberg.expressions import EqualTo

    rows = catalog.load_table(env.table(gc.VERSION_REGISTRY_TABLE)).scan(row_filter=EqualTo("task", TASK)).to_arrow().to_pylist()
    rows = [{"version": r["version"], "registered_at": r["registered_at"], "config": json.loads(r["config_json"])} for r in rows]
    return sorted(rows, key=lambda r: int(r["version"].rpartition("@")[2]))


def check_template_history(catalog, env, config: dict[str, Any]) -> None:
    """같은 템플릿 이름인데 내용이 다른 등록이 있으면 멈춘다 (이름만 같고 글이 다른 벡터가 생기지 않게)."""
    name, sha = config["template_version"], config["template_sha256"]
    other = [r["version"] for r in registered(catalog, env)
             if r["config"].get("template_version") == name and r["config"].get("template_sha256") not in (None, sha)]
    if other:
        raise card_template.TemplateError(f"템플릿 {name} 의 내용이 이미 등록된 {other} 와 다름. 새 템플릿 버전 이름을 써 주세요.")


def verify(catalog, env, config: dict[str, Any], *, write: bool) -> bool:
    """실행 전 검사. 지문이 없는 옛 등록(text_embedding@1)은 계획만 볼 수 있고 쓰지 못한다. 반환: 지문이 있는지."""
    if not has_fingerprint(config):
        if write:
            raise card_template.TemplateError(
                "이 버전에는 템플릿 내용과 지문이 없어 쓸 수 없음. text_version config 로 만든 설정을 새 버전으로 등록해 주세요.")
        return False
    check_config(config)
    check_template_history(catalog, env, config)
    return True


def register(catalog, env, version: str, config: dict[str, Any]) -> bool:
    if not has_fingerprint(config):
        raise card_template.TemplateError("등록할 설정에 템플릿 내용과 지문이 없음. text_version config 로 만들어 주세요.")
    check_config(config)
    check_template_history(catalog, env, config)
    return register_version(catalog, env, TASK, version, config)


def _tag_versions(values: list[str]) -> dict[str, str]:
    pairs = [v.partition("=") for v in values]
    tags = {task: version for task, _, version in pairs}
    if len(tags) != len(pairs) or any(task not in text_batch.TAG_SOURCES or not version for task, _, version in pairs):
        raise ValueError(f"--tag 는 작업=버전 (작업 {sorted(text_batch.TAG_SOURCES)}, 작업마다 하나)")
    return tags


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m rebom.embedding.text_version", description="글 임베딩 실행 버전")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("config", help="등록용 JSON 출력")
    c.add_argument("--template", required=True, choices=card_template.available())
    c.add_argument("--tag", action="append", default=[], metavar="작업=버전")
    c.add_argument("--out", type=Path, default=None, help="파일로 쓰기 (UTF-8). 비우면 화면에 출력")
    r = sub.add_parser("register", help="레지스트리에 등록 (사람이 실행)")
    r.add_argument("--version", required=True)
    r.add_argument("--config", type=Path, required=True)
    h = sub.add_parser("history", help="등록된 버전별 템플릿 이력 (읽기만)")
    h.add_argument("--full", action="store_true", help="템플릿 내용까지 출력")
    for p in (r, h):
        p.add_argument("--env", choices=sorted(gc.ENVIRONMENTS), default="test")
        p.add_argument("--profile", default=None)
    args = ap.parse_args(argv)

    if args.cmd == "config":
        text = json.dumps(runtime_config(args.template, _tag_versions(args.tag)), ensure_ascii=False, indent=2, sort_keys=True)
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8", newline="\n")
        else:
            print(text)
        return 0

    from rebom.storage.silver.aws_catalog import get_aws_catalog

    catalog, env = get_aws_catalog(profile=args.profile), gc.get_gold_environment(args.env)
    try:
        if args.cmd == "register":
            config = json.loads(args.config.read_text(encoding="utf-8-sig"))
            created = register(catalog, env, args.version, config)
            print(f"{args.version}: {'등록함' if created else '이미 같은 설정으로 등록됨'}")
        else:
            for row in registered(catalog, env):
                cfg = row["config"]
                print(json.dumps({
                    "version": row["version"], "registered_at": row["registered_at"].isoformat(),
                    "template_version": cfg.get("template_version"), "template_sha256": cfg.get("template_sha256"),
                    "golden_sha256": cfg.get("golden_sha256"), "tag_versions": cfg.get("tag_versions"),
                    **({"template": cfg.get("template")} if args.full else {}),
                }, ensure_ascii=False, indent=2 if args.full else None))
    except ValueError as exc:
        print(f"멈춤: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

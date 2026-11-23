"""Register and resolve immutable Gold model configurations."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from rebom.common.gold_catalog import ENVIRONMENTS, VERSION_REGISTRY_TABLE, get_gold_environment


def canonical_config(config: dict) -> tuple[str, str]:
    """Return canonical JSON and its SHA-256 fingerprint."""
    model = config.get("model_id") if isinstance(config, dict) else None
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Gold version config requires a nonempty model_id")
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _find(table, task: str, version: str) -> dict | None:
    from pyiceberg.expressions import And, EqualTo

    rows = table.scan(
        row_filter=And(EqualTo("task", task), EqualTo("version", version)),
        limit=2,
    ).to_arrow().to_pylist()
    if len(rows) > 1:
        raise ValueError(f"Duplicate Gold version: {task}/{version}")
    return rows[0] if rows else None


def _validate_key(task: str, version: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", task) or not re.fullmatch(
        rf"{re.escape(task)}@[1-9][0-9]*", version
    ):
        raise ValueError("Gold version must use task@N with a positive integer N")


def resolve_version(catalog, env, task: str, version: str) -> dict:
    """Load a version and verify the registered settings before use."""
    _validate_key(task, version)
    row = _find(catalog.load_table(env.table(VERSION_REGISTRY_TABLE)), task, version)
    if row is None:
        raise ValueError(f"Unregistered Gold version: {version}")
    config = json.loads(row["config_json"])
    if canonical_config(config)[1] != row["config_hash"]:
        raise ValueError(f"Gold version config hash mismatch: {version}")
    return config


def verify_runtime_config(catalog, env, task: str, version: str, runtime_config: dict) -> None:
    """Reject a candidate if the running code cannot reproduce its registered settings."""
    registered = resolve_version(catalog, env, task, version)
    if canonical_config(registered)[1] != canonical_config(runtime_config)[1]:
        raise ValueError(f"Gold version does not match the runtime config: {version}")


def require_version_column(table) -> None:
    if not any(field.name == "version" for field in table.schema().fields):
        raise ValueError("Gold version column is missing. Apply the Gold catalog schema before running a candidate.")


def register_version(catalog, env, task: str, version: str, config: dict) -> bool:
    """Append a version. Repeating the same registration is a no-op."""
    import pyarrow as pa
    from pyiceberg.exceptions import CommitFailedException

    _validate_key(task, version)
    canonical, config_hash = canonical_config(config)
    name = env.table(VERSION_REGISTRY_TABLE)
    table = catalog.load_table(name)

    row = {
        "task": task,
        "version": version,
        "config_json": canonical,
        "config_hash": config_hash,
        "registered_at": datetime.now(UTC),
    }
    for attempt in range(2):
        existing = _find(table, task, version)
        if existing is not None:
            if (existing["config_json"], existing["config_hash"]) != (canonical, config_hash):
                raise ValueError(f"Gold version already has a different config: {version}")
            return False
        try:
            table.append(pa.Table.from_pylist([row], schema=table.schema().as_arrow()))
            return True
        except CommitFailedException:
            if attempt:
                raise
            table = catalog.load_table(name)
    raise AssertionError("Unreachable Gold registry registration state")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=sorted(ENVIRONMENTS), default="test")
    parser.add_argument("--profile")
    parser.add_argument("--task", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--config", type=Path, help="Register settings from this JSON file; omit to resolve")
    args = parser.parse_args()

    from rebom.storage.silver.aws_catalog import get_aws_catalog

    catalog = get_aws_catalog(profile=args.profile)
    env = get_gold_environment(args.env)
    if args.config is not None:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        created = register_version(catalog, env, args.task, args.version, config)
        print(f"{args.version}: {'registered' if created else 'already registered'}")
    else:
        config = resolve_version(catalog, env, args.task, args.version)
        print(json.dumps(config, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

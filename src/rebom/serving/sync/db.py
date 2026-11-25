"""RDS 읽기·쓰기 (psycopg). 경로 하나의 반영 = 트랜잭션 하나: sync_state 행을 잠그고 순번을 확인한 뒤 행을 바꾸고
sync_state 를 고친다. 같은 경로가 동시에 돌면 뒤의 것은 잠금을 기다렸다가 순번 확인에서 멈춘다.

접속은 rebom.common.migrate.db_params (Secrets Manager rebom/dev/rebom-db, REBOM_DB_HOST·PORT·SSLMODE).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

ROUTES = ("notice_status", "photo_vector", "tag", "text_vector")
_RUN_ID = re.compile(r"\d{8}T\d{6}")


class StaleInput(RuntimeError):
    """RDS 에 이미 반영한 완료본보다 오래된 입력. 종료 코드 4."""


def connect():
    import psycopg

    from rebom.common.migrate import db_params

    return psycopg.connect(**db_params(), autocommit=True)  # 트랜잭션은 경로마다 conn.transaction() 으로 연다


def to_vector_literal(values: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


# ---- sync_state --------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Position:
    """이번 입력의 위치. notice_status 는 Bronze run_id(시각으로 시작), 나머지는 Silver 발행 순번."""

    publish_seq: int | None = None
    silver_run_id: str | None = None
    notice_snapshot_id: int | None = None
    image_snapshot_id: int | None = None


def lock_route(conn, route: str) -> dict[str, Any] | None:
    if route not in ROUTES:
        raise ValueError(f"경로 오류: {route}")
    conn.execute("INSERT INTO sync_state (route) VALUES (%s) ON CONFLICT (route) DO NOTHING", (route,))
    row = conn.execute(
        "SELECT last_publish_seq, last_silver_run_id, notice_snapshot_id, image_snapshot_id "
        "FROM sync_state WHERE route = %s FOR UPDATE", (route,)).fetchone()
    keys = ("last_publish_seq", "last_silver_run_id", "notice_snapshot_id", "image_snapshot_id")
    return dict(zip(keys, row))


def check_order(route: str, state: Mapping[str, Any], pos: Position) -> bool:
    """새 입력이면 True, 같은 입력의 재실행이면 False(멱등으로 다시 적용), 오래된 입력이면 StaleInput."""
    if route == "notice_status":
        if not pos.silver_run_id or not _RUN_ID.match(pos.silver_run_id):
            raise ValueError(f"Silver run_id 는 시각(YYYYMMDDTHHMMSS)으로 시작해야 순서를 비교할 수 있습니다: {pos.silver_run_id!r}")
        last = state.get("last_silver_run_id")
        if last is None or pos.silver_run_id > last:
            return True
        if pos.silver_run_id == last:
            return False
        raise StaleInput(f"{route}: 이미 {last} 를 반영했습니다. {pos.silver_run_id} 는 더 오래된 실행입니다")
    if pos.publish_seq is None or pos.publish_seq <= 0:
        raise ValueError("publish_seq 가 필요합니다")
    last = state.get("last_publish_seq")
    if last is None or pos.publish_seq > last:
        return True
    if pos.publish_seq == last:
        return False
    raise StaleInput(f"{route}: 이미 발행 순번 {last} 를 반영했습니다. {pos.publish_seq} 는 더 오래된 발행입니다")


def save_route(conn, route: str, pos: Position) -> None:
    conn.execute(
        "UPDATE sync_state SET last_publish_seq = %s, last_silver_run_id = %s, notice_snapshot_id = %s, "
        "image_snapshot_id = %s, applied_at = now() WHERE route = %s",
        (pos.publish_seq, pos.silver_run_id, pos.notice_snapshot_id, pos.image_snapshot_id, route))


# ---- active_version ----------------------------------------------------------------------------------------------

def active_versions(conn) -> dict[str, dict[str, Any]]:
    rows = conn.execute("SELECT task, version, config_hash FROM active_version").fetchall()
    return {t: {"version": v, "config_hash": h} for t, v, h in rows}


# ---- 공고 --------------------------------------------------------------------------------------------------------

def existing_notices(conn) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT desertion_no, content_hash, status_group, closed_transition_at, visible_until FROM service_notice")
    return {r[0]: {"content_hash": r[1], "status_group": r[2], "closed_transition_at": r[3], "visible_until": r[4]}
            for r in rows}


def notice_numbers(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT desertion_no FROM service_notice")}


def _upsert(conn, table: str, columns: Sequence[str], key: Sequence[str], rows: Iterable[Mapping[str, Any]],
            casts: Mapping[str, str] | None = None, extra_set: str = "synced_at = now()") -> int:
    rows = list(rows)
    if not rows:
        return 0
    casts = casts or {}
    values = ", ".join(f"%s{casts.get(c, '')}" for c in columns)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c not in key)
    sql = (f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({values}) "
           f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {updates}{', ' + extra_set if extra_set else ''}")
    with conn.cursor() as cur:
        cur.executemany(sql, [tuple(r[c] for c in columns) for r in rows])
    return len(rows)


def _delete(conn, table: str, key: Sequence[str], keys: Iterable[tuple], where: str = "", params: tuple = ()) -> int:
    keys = list(keys)
    if not keys:
        return 0
    cond = " AND ".join(f"{c} = %s" for c in key) + (f" AND {where}" if where else "")
    with conn.cursor() as cur:
        cur.executemany(f"DELETE FROM {table} WHERE {cond}", [tuple(k) + params for k in keys])
    return len(keys)


def upsert_notices(conn, rows: Sequence[Mapping[str, Any]]) -> int:
    from rebom.serving.sync.plan import NOTICE_COLUMNS

    cols = (*NOTICE_COLUMNS, "content_hash", "closed_transition_at", "visible_until")
    return _upsert(conn, "service_notice", cols, ("desertion_no",), rows)


# ---- 사진 --------------------------------------------------------------------------------------------------------

def existing_photos(conn) -> dict[tuple[str, int], dict[str, Any]]:
    rows = conn.execute(
        "SELECT desertion_no, image_slot, sha256, photo_version, serving_s3_key, thumb_s3_key FROM service_notice_photo")
    return {(r[0], r[1]): {"sha256": r[2], "photo_version": r[3], "serving_s3_key": r[4], "thumb_s3_key": r[5]}
            for r in rows}


def upsert_photos(conn, rows: Sequence[Mapping[str, Any]], publish_seq: int) -> int:
    rows = [{**r, "silver_publish_seq": publish_seq} for r in rows]
    cols = ("desertion_no", "image_slot", "sha256", "photo_version", "serving_s3_key", "thumb_s3_key",
            "silver_publish_seq")
    return _upsert(conn, "service_notice_photo", cols, ("desertion_no", "image_slot"), rows)


def delete_photos(conn, keys: Iterable[tuple[str, int]]) -> int:
    return _delete(conn, "service_notice_photo", ("desertion_no", "image_slot"), keys)


def existing_image_vectors(conn, version: str) -> dict[tuple[str, int], str]:
    rows = conn.execute("SELECT desertion_no, image_slot, sha256 FROM image_embedding WHERE version = %s", (version,))
    return {(r[0], r[1]): r[2] for r in rows}


def upsert_image_vectors(conn, rows: Sequence[Mapping[str, Any]]) -> int:
    rows = [{**r, "embedding": to_vector_literal(r["embedding"])} for r in rows]
    cols = ("desertion_no", "image_slot", "version", "photo_version", "sha256", "s3_key", "photo_run_id",
            "gold_run_id", "embedding")
    return _upsert(conn, "image_embedding", cols, ("desertion_no", "image_slot", "version"), rows,
                   casts={"embedding": "::vector"}, extra_set="embedded_at = now()")


def delete_image_vectors(conn, keys: Iterable[tuple[str, int]], version: str) -> int:
    return _delete(conn, "image_embedding", ("desertion_no", "image_slot"), keys, "version = %s", (version,))


# ---- 태그 --------------------------------------------------------------------------------------------------------

def existing_tags(conn) -> dict[tuple, dict[str, Any]]:
    """모든 태그 행. 활성에서 빠진 작업의 행은 계획에서 지울 행이 된다."""
    from rebom.serving.sync.plan import TAG_COMPARE, TAG_KEY

    rows = conn.execute(f"SELECT {', '.join(TAG_KEY + TAG_COMPARE)} FROM service_notice_tag")
    n = len(TAG_KEY)
    return {tuple(r[:n]): dict(zip(TAG_COMPARE, r[n:])) for r in rows}


def upsert_tags(conn, rows: Sequence[Mapping[str, Any]]) -> int:
    from rebom.serving.sync.plan import TAG_COMPARE, TAG_KEY

    return _upsert(conn, "service_notice_tag", TAG_KEY + TAG_COMPARE, TAG_KEY, rows)


def delete_tags(conn, keys: Iterable[tuple]) -> int:
    from rebom.serving.sync.plan import TAG_KEY

    return _delete(conn, "service_notice_tag", TAG_KEY, keys)


# ---- 글 벡터 -----------------------------------------------------------------------------------------------------

def existing_text_vectors(conn, version: str) -> dict[str, str]:
    rows = conn.execute("SELECT desertion_no, text_sha256 FROM text_embedding WHERE version = %s AND text_kind = 'card'",
                        (version,))
    return {r[0]: r[1] for r in rows}


def upsert_text_vectors(conn, rows: Sequence[Mapping[str, Any]]) -> int:
    rows = [{**r, "embedding": to_vector_literal(r["embedding"])} for r in rows]
    cols = ("desertion_no", "text_kind", "version", "text_sha256", "gold_run_id", "embedding")
    return _upsert(conn, "text_embedding", cols, ("desertion_no", "text_kind", "version"), rows,
                   casts={"embedding": "::vector"}, extra_set="embedded_at = now()")


def delete_text_vectors(conn, keys: Iterable[tuple[str]], version: str) -> int:
    return _delete(conn, "text_embedding", ("desertion_no",), keys, "version = %s AND text_kind = 'card'", (version,))

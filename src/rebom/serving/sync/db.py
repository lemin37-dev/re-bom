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
    # Gold 와 pgvector 둘 다 float32 라 유효숫자 9자리면 값이 그대로다 (repr(float) 의 17자리보다 COPY 양이 절반쯤)
    return "[" + ",".join(format(v, ".9g") for v in values) + "]"


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


def set_query_config(conn, task: str, version: str, config_hash: str, qc: Mapping[str, Any]) -> bool:
    """활성 행이 이 버전·설정 해시일 때만 query_config 를 바꾼다. 바뀌었으면 True (같은 값이면 False)."""
    import json

    body = json.dumps(dict(qc), sort_keys=True)
    cur = conn.execute(
        "UPDATE active_version SET query_config = %s::jsonb WHERE task = %s AND version = %s AND config_hash = %s "
        "AND query_config IS DISTINCT FROM %s::jsonb", (body, task, version, config_hash, body))
    return cur.rowcount == 1


# ---- 공고 --------------------------------------------------------------------------------------------------------

def existing_notices(conn) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT desertion_no, content_hash, status_group, closed_transition_at, visible_until FROM service_notice")
    return {r[0]: {"content_hash": r[1], "status_group": r[2], "closed_transition_at": r[3], "visible_until": r[4]}
            for r in rows}


def notice_numbers(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT desertion_no FROM service_notice")}


_STAGE = "_sync_stage"


def _stage(conn, table: str, columns: Sequence[str], rows: Sequence[tuple]) -> None:
    """행을 COPY 로 임시 표에 한 번에 올린다(행마다 왕복하지 않게). 임시 표는 트랜잭션이 끝나면 사라진다."""
    conn.execute(f"DROP TABLE IF EXISTS {_STAGE}")
    conn.execute(f"CREATE TEMP TABLE {_STAGE} ON COMMIT DROP AS SELECT {', '.join(columns)} FROM {table} WITH NO DATA")
    with conn.cursor() as cur, cur.copy(f"COPY {_STAGE} ({', '.join(columns)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)


def _dedupe(rows: Iterable[tuple], key_idx: Sequence[int]) -> list[tuple]:
    """같은 키가 두 번 오면 뒤의 것(행마다 보내던 때와 같은 결과). ON CONFLICT 는 한 문장에서 같은 행을 두 번 못 바꾼다."""
    out: dict[tuple, tuple] = {}
    for r in rows:
        out[tuple(r[i] for i in key_idx)] = r
    return list(out.values())


def _upsert(conn, table: str, columns: Sequence[str], key: Sequence[str], rows: Iterable[Mapping[str, Any]],
            casts: Mapping[str, str] | None = None, extra_set: str = "synced_at = now()") -> int:
    """casts 는 예전 호환용(임시 표가 대상 표와 같은 형식이라 COPY 가 바로 변환한다)."""
    data = _dedupe((tuple(r[c] for c in columns) for r in rows), [columns.index(k) for k in key])
    if not data:
        return 0
    _stage(conn, table, columns, data)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c not in key)
    cols = ", ".join(columns)
    conn.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM {_STAGE} "
                 f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {updates}{', ' + extra_set if extra_set else ''}")
    conn.execute(f"DROP TABLE {_STAGE}")
    return len(data)


def _delete(conn, table: str, key: Sequence[str], keys: Iterable[tuple], where: str = "", params: tuple = ()) -> int:
    data = _dedupe((tuple(k) for k in keys), range(len(key)))
    if not data:
        return 0
    _stage(conn, table, key, data)
    cond = " AND ".join(f"t.{c} = k.{c}" for c in key)
    if where:  # where 는 대상 표를 t 로 부른다 (예: t.version = %s)
        cond += f" AND {where}"
    conn.execute(f"DELETE FROM {table} t USING {_STAGE} k WHERE {cond}", params)
    conn.execute(f"DROP TABLE {_STAGE}")
    return len(data)


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
    cols = ("desertion_no", "image_slot", "version", "photo_version", "sha256", "s3_key", "gold_run_id", "embedding")
    return _upsert(conn, "image_embedding", cols, ("desertion_no", "image_slot", "version"), rows,
                   casts={"embedding": "::vector"}, extra_set="embedded_at = now()")


def delete_image_vectors(conn, keys: Iterable[tuple[str, int]], version: str) -> int:
    return _delete(conn, "image_embedding", ("desertion_no", "image_slot"), keys, "t.version = %s", (version,))


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
    return _delete(conn, "text_embedding", ("desertion_no",), keys, "t.version = %s AND t.text_kind = 'card'", (version,))

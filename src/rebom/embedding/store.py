"""pgvector 테이블(image_embedding, db/migrations/001_init.sql) 읽기·쓰기.

접속 정보는 Secrets Manager rebom/dev/rebom-db (JSON: host, port, dbname, username, password).
내 PC 에서 SSM 포트포워딩으로 붙을 때는 REBOM_DB_HOST=127.0.0.1, REBOM_DB_PORT=<로컬 포트> 로 주소만 바꾼다.
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterable, Sequence

from rebom.embedding.manifest import Photo

DEFAULT_DB_SECRET_ID = "rebom/dev/rebom-db"
TABLE = "image_embedding"


def db_params() -> dict[str, Any]:
    import boto3

    secret_id = os.environ.get("REBOM_DB_SECRET_ID", DEFAULT_DB_SECRET_ID)
    region = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
    s = json.loads(boto3.client("secretsmanager", region_name=region).get_secret_value(SecretId=secret_id)["SecretString"])
    return {
        "host": os.environ.get("REBOM_DB_HOST") or s["host"],
        "port": int(os.environ.get("REBOM_DB_PORT") or s.get("port", 5432)),
        "dbname": s["dbname"],
        "user": s["username"],
        "password": s["password"],
        "sslmode": "require",  # RDS rds.force_ssl=1
        "connect_timeout": 10,
    }


def connect():
    import psycopg

    return psycopg.connect(**db_params())


def to_vector_literal(values: Sequence[float]) -> str:
    """pgvector 텍스트 형식. pgvector 파이썬 패키지 없이 ::vector 로 넣는다."""
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def parse_vector(text: str) -> list[float]:
    """pgvector 텍스트 '[0.1,0.2,...]' → float 목록."""
    return [float(x) for x in text.strip("[]").split(",")] if text.strip("[]") else []


def stored_rows(conn, model: str, limit: int | None = None) -> list[tuple[str, int, str, str, list[float]]]:
    """(desertion_no, image_slot, sha256, s3_key, embedding). limit 이 있으면 무작위로 그만큼."""
    sql = f"SELECT desertion_no, image_slot, sha256, s3_key, embedding::text FROM {TABLE} WHERE model = %s"
    params: tuple = (model,)
    if limit is not None:
        sql += " ORDER BY random() LIMIT %s"
        params += (limit,)
    return [(no, slot, sha, key, parse_vector(vec)) for no, slot, sha, key, vec in conn.execute(sql, params)]


def nearest(conn, model: str, vector: Sequence[float], k: int = 1) -> list[tuple[str, int, str, float]]:
    """코사인 거리 순 이웃 (desertion_no, image_slot, sha256, 코사인 유사도)."""
    lit = to_vector_literal(vector)
    rows = conn.execute(
        f"SELECT desertion_no, image_slot, sha256, 1 - (embedding <=> %s::vector) FROM {TABLE} "
        f"WHERE model = %s ORDER BY embedding <=> %s::vector LIMIT %s",
        (lit, model, lit, k),
    )
    return [(no, slot, sha, float(sim)) for no, slot, sha, sim in rows]


def table_exists(conn) -> bool:
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{TABLE}",)).fetchone()[0]


def existing_hashes(conn, model: str) -> dict[tuple[str, int], str]:
    rows = conn.execute(f"SELECT desertion_no, image_slot, sha256 FROM {TABLE} WHERE model = %s", (model,))
    return {(no, slot): sha for no, slot, sha in rows}


def pending(photos: Iterable[Photo], done: dict[tuple[str, int], str]) -> list[Photo]:
    """아직 없거나 sha256 이 바뀐 사진만. 공고·슬롯 순으로 정렬해 재실행 결과가 같게 한다."""
    return sorted((p for p in photos if done.get(p.key) != p.sha256), key=lambda p: p.key)


_UPSERT = f"""
INSERT INTO {TABLE} (desertion_no, image_slot, model, photo_version, sha256, s3_key, photo_run_id, embedding)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)
ON CONFLICT (desertion_no, image_slot, model) DO UPDATE SET
    photo_version = EXCLUDED.photo_version,
    sha256        = EXCLUDED.sha256,
    s3_key        = EXCLUDED.s3_key,
    photo_run_id  = EXCLUDED.photo_run_id,
    embedding     = EXCLUDED.embedding,
    embedded_at   = now()
"""


def upsert(conn, model: str, rows: Sequence[tuple[Photo, Sequence[float]]]) -> int:
    with conn.cursor() as cur:
        cur.executemany(
            _UPSERT,
            [(p.desertion_no, p.slot, model, p.version, p.sha256, p.s3_key, p.photo_run_id, to_vector_literal(v))
             for p, v in rows],
        )
    conn.commit()
    return len(rows)

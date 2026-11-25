"""REBOM_DATA_MODE=db: RDS 서비스 표(service_notice·service_notice_photo·service_notice_tag)를 읽는 저장소.

표는 S3 → RDS 동기화(rebom.serving.sync)가 채운다. API 는 읽기만 한다.
- 보이는 공고: 보호중, 그리고 종료 뒤 visible_until 이 안 지난 공고(상세 링크 유지용, 30일)
- 태그: 동기화가 활성 버전 태그만 넣으므로 버전을 다시 고르지 않는다. 검색·칩에는 searchable 태그만 쓴다
- 응답 모양은 sample 모드와 같다. 행을 공공 API 이름(camelCase)으로 바꿔 schemas.to_summary 에 넘긴다.
  나이·체중은 정제된 칸(birth_year·weight_kg)을 쓰고 원문 파싱(derive)을 하지 않는다.
보호자 실명은 RDS 에 없다(동기화가 옮기지 않음).
"""
from __future__ import annotations

import threading
from collections import defaultdict
from datetime import date
from typing import Any, Callable, Sequence

from rebom.serving.repository import MATCH_RANK, SPECIES_CODES, AnimalQuery, SearchPage, _words
from rebom.serving.tags import tag_match

VISIBLE = "(n.status_group = 'PROTECTING' OR (n.visible_until IS NOT NULL AND n.visible_until > now()))"
NOTICE_FIELDS = (
    "desertion_no", "notice_no", "status_group", "process_state_raw", "closure_reason_code", "species_code",
    "species_name", "breed_name_raw", "breed_full_name_raw", "sex_code", "neuter_code", "birth_year", "weight_raw",
    "weight_kg", "color_raw", "special_mark", "sfe_soci", "sfe_health", "health_chk", "vaccination_chk", "happen_date",
    "happen_place", "org_name", "notice_start_date", "notice_end_date", "care_reg_no", "care_name", "care_tel",
    "care_address", "synced_at",
)
_SELECT = ", ".join(f"n.{c}" for c in NOTICE_FIELDS)
# 검색어를 찾는 칸 (sample 모드: specialMark·kindFullNm·colorCd·happenPlace·sfeSoci)
_TEXT = "concat_ws(' ', n.special_mark, n.breed_full_name_raw, n.color_raw, n.happen_place, n.sfe_soci)"


def _ymd(d: date | None) -> str | None:
    return d.strftime("%Y%m%d") if d else None


def age_of(birth_year: int | None, today: date) -> int | None:
    return None if birth_year is None else max(0, today.year - int(birth_year))


def to_raw(row: dict[str, Any], photos: Sequence[dict[str, Any]], image_base: str, today: date) -> dict[str, Any]:
    """RDS 행 → sample 모드와 같은 공공 API 이름의 dict. 앞에 _ 가 붙은 키는 schemas 가 원문 파싱 대신 쓰는 값."""
    def url(key: str | None) -> str | None:
        return f"{image_base.rstrip('/')}/{key.lstrip('/')}" if key else None

    years = age_of(row.get("birth_year"), today)
    raw = {
        "desertionNo": row["desertion_no"], "noticeNo": row.get("notice_no"),
        "upKindCd": row.get("species_code"), "upKindNm": row.get("species_name"),
        "kindNm": row.get("breed_name_raw"), "kindFullNm": row.get("breed_full_name_raw"),
        "colorCd": row.get("color_raw"), "sexCd": row.get("sex_code"), "neuterYn": row.get("neuter_code"),
        "age": f"{row['birth_year']}(년생)" if row.get("birth_year") else None, "weight": row.get("weight_raw"),
        "processState": row.get("process_state_raw"), "specialMark": row.get("special_mark"),
        "happenDt": _ymd(row.get("happen_date")), "happenPlace": row.get("happen_place"),
        "noticeSdt": _ymd(row.get("notice_start_date")), "noticeEdt": _ymd(row.get("notice_end_date")),
        "orgNm": row.get("org_name"), "careRegNo": row.get("care_reg_no"), "careNm": row.get("care_name"),
        "careTel": row.get("care_tel"), "careAddr": row.get("care_address"),
        "updTm": row["synced_at"].isoformat() if row.get("synced_at") else None,
        "endReason": row.get("closure_reason_code"), "healthChk": row.get("health_chk"),
        "vaccinationChk": row.get("vaccination_chk"), "sfeSoci": row.get("sfe_soci"), "sfeHealth": row.get("sfe_health"),
        "_ageYears": years,
        "_ageLabel": None if years is None else ("1살 미만" if years == 0 else f"{years}살"),
        "_weightKg": float(row["weight_kg"]) if row.get("weight_kg") is not None else None,
        "_isProtected": row.get("status_group") == "PROTECTING",
        "_photos": [url(p["serving_s3_key"]) for p in photos],
        "_thumbnail": url(photos[0].get("thumb_s3_key") or photos[0]["serving_s3_key"]) if photos else None,
    }
    return raw


def where(query: AnimalQuery, today: date) -> tuple[str, list[Any]]:
    """AnimalQuery → WHERE 절과 값. sample 모드의 _matches 와 같은 뜻."""
    cond, args = [VISIBLE], []
    if query.species:
        cond.append("n.species_code = %s")
        args.append(SPECIES_CODES[query.species])
    if query.state == "protect":
        cond.append("n.status_group = 'PROTECTING'")
    elif query.state == "ended":
        cond.append("n.status_group = 'CLOSED'")
    if query.sex:
        cond.append("n.sex_code = %s")
        args.append(query.sex)
    if query.shelter:
        cond.append("n.care_reg_no = %s")
        args.append(query.shelter)
    if query.regions:
        ors = []
        for token in query.regions:
            ors.append("(strpos(split_part(coalesce(n.care_address, ''), ' ', 1), %s) > 0 "
                       "OR strpos(split_part(coalesce(n.org_name, ''), ' ', 1), %s) > 0)")
            args += [token, token]
        cond.append("(" + " OR ".join(ors) + ")")
    if query.age_min is not None or query.age_max is not None:
        age = "greatest(0, %s - n.birth_year)"
        cond.append("n.birth_year IS NOT NULL")
        if query.age_min is not None:
            cond.append(f"{age} >= %s")
            args += [today.year, query.age_min]
        if query.age_max is not None:
            cond.append(f"{age} <= %s")
            args += [today.year, query.age_max]
    if query.weight_min is not None:
        cond.append("n.weight_kg >= %s")
        args.append(query.weight_min)
    if query.weight_lt is not None:
        cond.append("n.weight_kg < %s")
        args.append(query.weight_lt)
    words = _words(query.q)
    if words:
        cond.append("(" + " OR ".join(f"strpos({_TEXT}, %s) > 0" for _ in words) + ")")
        args += words
    return " AND ".join(cond), args


def order_by(query: AnimalQuery, today: date) -> tuple[str, list[Any]]:
    if query.sort == "notice_end":  # 공고 중인 아이를 종료일 가까운 순으로 먼저, 끝난 공고는 뒤
        return ("(n.notice_end_date IS NOT NULL AND n.notice_end_date < %s), n.notice_end_date ASC NULLS LAST, "
                "n.desertion_no", [today])
    return "n.happen_date DESC NULLS LAST, n.desertion_no DESC", []


class DbRepository:
    """연결 하나를 스레드끼리 나눠 쓴다(psycopg 연결은 스레드 안전, 문장은 차례로 돈다). 끊기면 다시 붙는다."""

    def __init__(self, connect: Callable[[], Any], image_base_url: str = "", today: Callable[[], date] = date.today):
        self._connect = connect
        self._conn = None
        self._lock = threading.Lock()
        self._image_base = image_base_url
        self._today = today

    def _rows(self, sql: str, args: Sequence[Any] = ()) -> list[dict[str, Any]]:
        import psycopg
        from psycopg.rows import dict_row

        for attempt in (1, 2):
            with self._lock:
                if self._conn is None or self._conn.closed:
                    self._conn = self._connect()
                conn = self._conn
            try:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(sql, list(args))
                    return cur.fetchall()
            except psycopg.OperationalError:
                with self._lock:
                    self._conn = None
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    # ---- 읽기 도우미 ----

    def _photos(self, nos: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = defaultdict(list)
        if nos:
            for r in self._rows("SELECT desertion_no, serving_s3_key, thumb_s3_key FROM service_notice_photo "
                                "WHERE desertion_no = ANY(%s) ORDER BY desertion_no, image_slot", [list(nos)]):
                out[r["desertion_no"]].append(r)
        return out

    def _tags(self, nos: Sequence[str]) -> dict[str, frozenset[str]]:
        found: dict[str, set[str]] = defaultdict(set)
        if nos:
            for r in self._rows("SELECT desertion_no, tag_code FROM service_notice_tag "
                                "WHERE searchable AND desertion_no = ANY(%s)", [list(nos)]):
                found[r["desertion_no"]].add(r["tag_code"])
        return {no: frozenset(v) for no, v in found.items()}

    def _notices(self, nos: Sequence[str]) -> list[dict[str, Any]]:
        """nos 순서대로 raw dict (보이는 공고만)."""
        if not nos:
            return []
        today = self._today()
        rows = {r["desertion_no"]: r for r in self._rows(
            f"SELECT {_SELECT} FROM service_notice n WHERE n.desertion_no = ANY(%s) AND {VISIBLE}", [list(nos)])}
        photos = self._photos(list(rows))
        return [to_raw(rows[no], photos.get(no, []), self._image_base, today) for no in nos if no in rows]

    # ---- AnimalRepository ----

    def search(self, query: AnimalQuery) -> SearchPage:
        today = self._today()
        cond, args = where(query, today)
        order, order_args = order_by(query, today)
        start = (query.page - 1) * query.size
        if not query.tags:
            total = self._rows(f"SELECT count(*) AS n FROM service_notice n WHERE {cond}", args)[0]["n"]
            ids = [r["desertion_no"] for r in self._rows(
                f"SELECT n.desertion_no FROM service_notice n WHERE {cond} ORDER BY {order} LIMIT %s OFFSET %s",
                [*args, *order_args, query.size, start])]
            return SearchPage(total, self._with_tags(ids))
        # 태그는 거르지 않고 순서를 바꾼다(serving/tags.py). 조건에 맞는 번호를 모두 읽어 단계별로 줄 세운다
        ids = [r["desertion_no"] for r in self._rows(
            f"SELECT n.desertion_no FROM service_notice n WHERE {cond} ORDER BY {order}", [*args, *order_args])]
        tags = self._tags(ids)
        ranked = [(no, tag_match(tags.get(no, frozenset()), query.tags)) for no in ids]
        ranked = [(no, m) for no, m in ranked if m is not None]
        ranked.sort(key=lambda x: MATCH_RANK[x[1]])
        matched = sum(1 for _, m in ranked if m == "all")
        page = [no for no, _ in ranked[start:start + query.size]]
        return SearchPage(len(ranked), self._with_tags(page, tags), matched)

    def _with_tags(self, nos: Sequence[str], tags: dict[str, frozenset[str]] | None = None) -> list[dict[str, Any]]:
        tags = tags if tags is not None else self._tags(nos)
        rows = self._notices(nos)
        for r in rows:
            r["_tags"] = tags.get(r["desertionNo"], frozenset())
        return rows

    def get(self, desertion_no: str) -> dict[str, Any] | None:
        rows = self._with_tags([desertion_no])
        return rows[0] if rows else None

    def tags_of(self, desertion_no: str) -> frozenset[str]:
        return self._tags([desertion_no]).get(desertion_no, frozenset())

    def shelters(self) -> list[tuple[dict[str, Any], int]]:
        """보호소별 대표 행과 보호중 동물 수. 보호중 많은 순 (sample 모드와 같음)."""
        rows = self._rows(
            "SELECT n.care_reg_no, min(n.care_name) AS care_name, min(n.care_tel) AS care_tel, "
            "min(n.care_address) AS care_address, count(*) FILTER (WHERE n.status_group = 'PROTECTING') AS protected "
            f"FROM service_notice n WHERE {VISIBLE} AND n.care_reg_no IS NOT NULL GROUP BY n.care_reg_no "
            "ORDER BY protected DESC, care_name")
        return [({"careRegNo": r["care_reg_no"], "careNm": r["care_name"], "careTel": r["care_tel"],
                  "careAddr": r["care_address"]}, r["protected"]) for r in rows]

    def count(self) -> int:
        return self._rows(f"SELECT count(*) AS n FROM service_notice n WHERE {VISIBLE}")[0]["n"]

    def tag_counts(self) -> dict[str, int]:
        """태그별 보호중 동물 수."""
        rows = self._rows("SELECT t.tag_code, count(DISTINCT t.desertion_no) AS n FROM service_notice_tag t "
                          "JOIN service_notice n USING (desertion_no) "
                          "WHERE t.searchable AND n.status_group = 'PROTECTING' GROUP BY t.tag_code")
        return {r["tag_code"]: r["n"] for r in rows}


def connect_from_settings(dsn: str | None) -> Callable[[], Any]:
    """REBOM_DB_DSN 이 있으면 그것으로, 없으면 Secrets Manager(rebom.common.migrate.db_params)로 붙는다."""
    import psycopg

    if dsn:
        return lambda: psycopg.connect(dsn, autocommit=True)

    def _connect():
        from rebom.common.migrate import db_params

        return psycopg.connect(**db_params(), autocommit=True)

    return _connect


"""입양 절차 안내 화면용 '우리 지역 입양 지원'·'반려동물 축제' 스냅샷을 만든다 (일회성, 로컬 실행).

    python scripts/guide/build_adoption_support.py            # frontend/src/data/adoption-support.json 갱신

자료 (모두 읽기만 함):
- 행정안전부_대한민국 공공서비스(혜택) 정보 (data.go.kr 15113968): 신청할 수 있는 입양비·펫보험 지원
- 국가법령정보센터 자치법규 Open API: 동물 조례 가운데 '입양' 조문이 있는 지자체와 지원 종류 (자동 분류, 검토 전)
- 전국문화축제 표준데이터 (data.go.kr 15013104): 반려동물 축제

키는 .env 에서만 읽고(DATA_GO_KR_SERVICE_KEY 또는 DATA_API, Law_open_data) 출력·파일에 남기지 않는다.
외부 사이트 링크(정부24 상세 주소, 축제 홈페이지)는 화면 규칙상 쓰지 않으므로 저장하지 않는다.
"""

from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "frontend" / "src" / "data" / "adoption-support.json"

GOV24 = "https://api.odcloud.kr/api/gov24/v3/"
LAW_SEARCH = "https://www.law.go.kr/DRF/lawSearch.do"
LAW_SERVICE = "https://www.law.go.kr/DRF/lawService.do"
FESTIVAL = "http://api.data.go.kr/openapi/tn_pubr_public_cltur_fstvl_api"

GOV24_WORDS = ("유기동물", "유실", "입양비", "펫보험", "동물보험", "반려동물", "반려견", "입양동물")
GOV24_FIELDS = ("서비스명", "서비스목적요약", "지원내용")

# 조례 '입양' 조문의 지원 종류. 화면 표시 이름은 프론트(lib/adoption-support.ts)에 둔다.
ORDINANCE_KINDS: list[tuple[str, str]] = [
    ("cost", r"(입양비|비용|진료비|치료비|예방접종|중성화|의료비|미용비).{0,60}(지원|보조|지급)|(지원|보조|지급).{0,60}(입양비|진료비|치료비)"),
    ("insurance", r"보험"),
    ("education", r"교육"),
    ("center", r"입양\s*(센터|카페|시설|공간)|(센터|시설).{0,20}입양"),
    ("event", r"행사|홍보|캠페인|축제|입양의 날"),
    ("aftercare", r"사후\s*관리"),
    ("goods", r"물품|용품"),
]
SKIP_ORDINANCE_NAMES = re.compile(r"야생동물|축산|가축|실험동물")
PET_FESTIVAL = re.compile(r"반려|펫|애견|댕댕")


# ---------- 순수 함수 (tests/test_guide_adoption_support.py) ----------


def split_region(org: str | None) -> tuple[str | None, str | None]:
    """'서울특별시 영등포구' → ('서울특별시', '영등포구'), '경상남도' → ('경상남도', None). '(구)…'(옛 기관)는 (None, None)."""
    org = (org or "").strip()
    if not org or org.startswith("(구)"):
        return None, None
    parts = org.split()
    return parts[0], (" ".join(parts[1:]) or None)


def program_kinds(row: dict) -> list[str]:
    """공공서비스 한 건이 유기동물 입양 지원이면 ['cost'] / ['insurance'] / 둘 다, 아니면 [].

    아이 입양 지원(입양축하금 등)이 '입양'에 함께 걸리므로 동물 관련 말이 같이 있어야 한다.
    보호센터 운영 지원처럼 입양자에게 주는 비용·보험이 아닌 것은 뺀다.
    """
    text = " ".join(str(row.get(k) or "") for k in ("서비스명", "서비스목적요약", "지원내용", "지원대상"))
    if "입양" not in text or not re.search(r"유기|유실|반려|입양동물|보호동물", text):
        return []
    head = " ".join(str(row.get(k) or "") for k in ("서비스명", "서비스목적요약"))
    kinds = []
    if re.search(r"입양비|입양장려금|입양.{0,10}비용|부담\s*비용", head + " " + str(row.get("지원내용") or "")):
        kinds.append("cost")
    if re.search(r"보험|보헙", head):
        kinds.append("insurance")
    return kinds


def _article_title(text: str) -> tuple[str, str]:
    m = re.search(r"(제\d+조(?:의\d+)?)\(([^)]*)\)", text)
    return (m.group(1), m.group(2)) if m else ("", "")


def _pieces(text: str) -> list[str]:
    parts = re.split(r"(?<=[.다])\s+|(?=\d+\.\s?[가-힣「])|(?=[①-⑳])", text)
    return [p for p in parts if "입양" in p]


def classify_articles(articles: list[str]) -> dict[str, list[str]]:
    """'입양'이 들어간 조문들 → {지원 종류: [조문 번호...]}. 아무 종류도 없으면 {'basic': [...]}.

    조 제목에 '입양'이 있으면 조 전체를, 아니면 '입양'이 들어간 문장만 본다.
    등록 수수료 조(제목에 '수수료')에서 입양 동물을 감면·면제하면 'fee'.
    """
    kinds: dict[str, list[str]] = {}
    for raw in articles:
        text = html.unescape(raw)
        no, title = _article_title(text)
        texts = [text] if "입양" in title else _pieces(text)
        for piece in texts:
            for kind, pat in ORDINANCE_KINDS:
                if re.search(pat, piece):
                    kinds.setdefault(kind, [])
                    if no and no not in kinds[kind]:
                        kinds[kind].append(no)
            if re.search(r"수수료", title) and re.search(r"면제|감면", piece):
                kinds.setdefault("fee", [])
                if no and no not in kinds["fee"]:
                    kinds["fee"].append(no)
    if not kinds:
        nos = [_article_title(html.unescape(a))[0] for a in articles]
        kinds["basic"] = [n for n in dict.fromkeys(nos) if n]
    return kinds


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def clean(text: str | None) -> str:
    """줄바꿈을 하나로 맞추고, 줄마다 연속 공백과 빈 줄을 정리. 담당자 이메일 주소는 남기지 않는다(공개 저장소)."""
    text = _EMAIL.sub("담당 부서에 주소 문의", text or "")
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.split("\n")]
    return "\n".join(line for line in lines if line)


COVERED_ITEMS: list[tuple[str, str]] = [
    ("질병 진단", r"질병\s*진단|질병질단|진단비"),
    ("치료", r"치료비"),
    ("예방접종", r"예방\s*접종"),
    ("중성화 수술", r"중성화"),
    ("동물등록(내장칩)", r"내장형\s*동물\s*등록비|등록비"),
    ("미용", r"미용"),
    ("펫보험 가입", r"펫\s*보[험헙]|보험\s*가입비"),
    ("교육·훈련", r"사회화|훈련|교육비"),
]


def benefit_facts(row: dict, kinds: list[str]) -> dict:
    """공공서비스 원문 → 화면에 쓸 쉬운 요약: 얼마를, 무엇에, 누가(조건), 언제까지.

    원문을 고치지 않고 글자에서 찾기만 한다. 못 찾은 칸은 비워 두고 화면은 원문을 보여 준다.
    """
    summary = str(row.get("summary") or row.get("서비스목적요약") or "")
    content = str(row.get("content") or row.get("지원내용") or "")
    target = str(row.get("target") or row.get("지원대상") or "")
    deadline = str(row.get("deadline") or row.get("신청기한") or "")
    method = str(row.get("method") or row.get("신청방법") or "")
    text = f"{summary} {content}"

    amount = None
    for pat in (r"최대\s*(\d+)\s*만\s*원", r"(\d+)\s*만\s*원\s*(?:이내\s*)?지원", r"(\d+)\s*만\s*원"):
        m = re.search(pat, text)
        if m:
            amount = int(m.group(1))
            break
    pm = re.search(r"(\d+)\s*%", text)
    percent = int(pm.group(1)) if pm else None

    if "insurance" in kinds and "cost" not in kinds:
        items = ["펫보험 가입"]
        title = "펫보험 가입비 지원"
    else:
        items = [name for name, pat in COVERED_ITEMS if re.search(pat, content)]
        title = "입양장려금" if "장려금" in summary + str(row.get("name") or row.get("서비스명") or "") else "입양 비용 지원"
    headline = f"{title} {'최대 ' if '최대' in text or percent else ''}{amount}만원" if amount else title

    all_text = f"{summary} {target} {content}"
    conditions = []
    if re.search(r"보호센터|보호소|동물가족센터", all_text):
        conditions.append("이 지역 보호센터에서 입양")
    if "내장형" in all_text:
        conditions.append("내장형 동물등록 완료")
    elif "동물등록" in all_text:
        conditions.append("동물등록 완료")
    if re.search(r"입양\s*예정자\s*교육|동물사랑배움터", all_text):
        conditions.append("입양 전 교육 이수")
    if re.search(r"도민|구민|시민|군민|주민등록|거주", target):
        conditions.append("이 지역 주민")
    if "insurance" in kinds and re.search(r"보[험헙]에?\s*가입한", all_text):
        conditions.append("펫보험 가입 후 신청")

    w = re.search(r"입양\s*(?:후|일로부터)\s*(\d+)\s*(개월|년)", f"{deadline} {target} {content}")
    apply_within = f"입양 후 {w.group(1)}{w.group(2)} 안에 신청" if w else ("언제든 신청" if "상시" in deadline else "")
    channels = [label for label, pat in (("방문", r"방문"), ("정부24 온라인", r"정부24"), ("이메일", r"이메일|@"), ("팩스", r"팩스"), ("우편", r"우편")) if re.search(pat, method)]
    return {
        "headline": headline,
        "amountMan": amount,
        "percent": percent,
        "items": items,
        "conditions": conditions,
        "applyWithin": apply_within,
        "channels": channels,
        "budgetLimited": bool(re.search(r"소진|사업량", f"{deadline} {content} {method}")),
    }


def is_pet_festival(name: str | None) -> bool:
    return bool(PET_FESTIVAL.search(name or ""))


def region_of_address(addr: str | None) -> tuple[str | None, str | None]:
    parts = (addr or "").split()
    if not parts:
        return None, None
    sigungu = parts[1] if len(parts) > 1 and re.search(r"(시|군|구)$", parts[1]) else None
    return parts[0], sigungu


def _num(v) -> float | None:
    try:
        return round(float(v), 5)
    except (TypeError, ValueError):
        return None


# 지금 행정구역 이름이 지도 검색에 아직 없을 때 찾아볼 옛 이름
GEO_ALIAS = {"전남광주통합특별시": ("광주광역시", "전라남도")}


def geocode_queries(sido: str, sigungu: str | None) -> list[str]:
    """지도 검색어 후보. 시·도만이면 시·도 이름, 통합된 시·도는 옛 이름으로도 찾는다."""
    names = (sido, *GEO_ALIAS.get(sido, ()))
    return [f"{n} {sigungu}" if sigungu else n for n in names]


# ---------- 호출 ----------


def _env() -> dict[str, str]:
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _get_json(url: str, params: dict, secret: str, timeout: int = 60):
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={"User-Agent": "rebom-guide-snapshot"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError) as exc:  # 키가 담긴 주소를 남기지 않도록 종류만
            if attempt == 2:
                raise RuntimeError(f"호출 실패: {url.split('?')[0]} ({type(exc).__name__})") from None
            time.sleep(2)
    raise AssertionError  # pragma: no cover


def fetch_programs(key: str) -> list[dict]:
    rows: dict[str, dict] = {}
    for field in GOV24_FIELDS:
        for word in GOV24_WORDS:
            d = _get_json(GOV24 + "serviceList", {"serviceKey": key, "returnType": "JSON", "page": 1, "perPage": 500, f"cond[{field}::LIKE]": word}, key)
            for x in d.get("data", []):
                rows[x["서비스ID"]] = x
    out = []
    for x in rows.values():
        kinds = program_kinds(x)
        if not kinds:
            continue
        det = _get_json(GOV24 + "serviceDetail", {"serviceKey": key, "returnType": "JSON", "page": 1, "perPage": 1, "cond[서비스ID::EQ]": x["서비스ID"]}, key)
        d = (det.get("data") or [{}])[0]
        sido, sigungu = split_region(x.get("소관기관명"))
        out.append({
            "id": x["서비스ID"],
            "sido": sido,
            "sigungu": sigungu,
            "kinds": kinds,
            "name": x.get("서비스명"),
            "summary": clean(x.get("서비스목적요약")),
            "target": clean(x.get("지원대상")),
            "content": clean(x.get("지원내용")),
            "deadline": clean(x.get("신청기한")),
            "method": clean(d.get("신청방법") or x.get("신청방법")),
            "documents": clean(d.get("구비서류")),
            "contact": clean(d.get("문의처") or x.get("전화문의")),
            "law": clean(d.get("법령")),
            "ordinance": clean(d.get("자치법규")),
            "updatedAt": (x.get("수정일시") or "")[:8],
        })
        out[-1]["benefit"] = benefit_facts(out[-1], kinds)
    return sorted(out, key=lambda p: (p["sido"] or "", p["sigungu"] or "", p["name"] or ""))


def fetch_ordinances(oc: str) -> list[dict]:
    def search(query: str, mode: int) -> list[dict]:
        found, page = [], 1
        while True:
            d = _get_json(LAW_SEARCH, {"OC": oc, "target": "ordin", "type": "JSON", "query": query, "display": 100, "page": page, "search": mode}, oc)["OrdinSearch"]
            laws = d.get("law") or []
            laws = [laws] if isinstance(laws, dict) else laws
            found += laws
            if len(laws) < 100 or page >= 40:
                return found
            page += 1
            time.sleep(0.3)

    named = {x["자치법규일련번호"]: x for x in search("동물", 1)}
    adopt = {x["자치법규일련번호"] for x in search("입양", 2)}
    out = []
    for mst, x in named.items():
        if mst not in adopt or SKIP_ORDINANCE_NAMES.search(x["자치법규명"]):
            continue
        sido, sigungu = split_region(x.get("지자체기관명"))
        if not sido:
            continue
        body = _get_json(LAW_SERVICE, {"OC": oc, "target": "ordin", "MST": mst, "type": "JSON"}, oc)
        articles: list[str] = []

        def walk(o, acc):
            if isinstance(o, dict):
                for v in o.values():
                    walk(v, acc)
            elif isinstance(o, list):
                for v in o:
                    walk(v, acc)
            elif isinstance(o, str):
                acc.append(o)

        def find(o):
            if isinstance(o, dict):
                if "조내용" in o or "조문내용" in o:
                    acc: list[str] = []
                    walk(o, acc)
                    articles.append(re.sub(r"\s+", " ", " ".join(a for a in acc if a.strip())))
                else:
                    for v in o.values():
                        find(v)
            elif isinstance(o, list):
                for v in o:
                    find(v)

        find(body)
        hits = [a for a in articles if "입양" in a]
        if not hits:
            continue
        out.append({
            "sido": sido,
            "sigungu": sigungu,
            "name": x["자치법규명"],
            "kinds": classify_articles(hits),
            "enforcedAt": x.get("시행일자"),
            "reviewed": False,
        })
        time.sleep(0.2)
    return sorted(out, key=lambda o: (o["sido"], o["sigungu"] or "", o["name"]))


def fetch_festivals(key: str) -> tuple[list[dict], str | None]:
    items, page = [], 1
    while True:
        d = _get_json(FESTIVAL, {"serviceKey": key, "pageNo": page, "numOfRows": 1000, "type": "json"}, key)
        body = d.get("response", d)["body"]
        it = body["items"]["item"] if isinstance(body["items"], dict) else body["items"]
        items += it
        if not it or len(items) >= int(body.get("totalCount", 0)):
            break
        page += 1
    out, ref = [], None
    for i in items:
        if not is_pet_festival(i.get("fstvlNm")):
            continue
        addr = i.get("rdnmadr") or i.get("lnmadr")
        sido, sigungu = region_of_address(addr)
        ref = max(ref or "", i.get("referenceDate") or "") or ref
        out.append({
            "name": i.get("fstvlNm"),
            "start": i.get("fstvlStartDate"),
            "end": i.get("fstvlEndDate"),
            "place": i.get("opar"),
            "address": addr,
            "sido": sido,
            "sigungu": sigungu,
            "host": i.get("mnnstNm") or i.get("auspcInsttNm"),
            "lat": _num(i.get("latitude")),
            "lng": _num(i.get("longitude")),
        })
    return sorted(out, key=lambda f: f["start"] or ""), ref


NOMINATIM = "https://nominatim.openstreetmap.org/search"
GEO_CACHE = Path(__file__).with_name("region_coords.json")


def fetch_coords(regions: set[tuple[str, str | None]]) -> list[dict]:
    """시·도/시·군·구 대표 좌표. OpenStreetMap Nominatim(초당 1건 이하) 결과를 region_coords.json 에 캐시한다.
    못 찾으면 시·도 좌표를 쓰고 approx=True."""
    cache = json.loads(GEO_CACHE.read_text(encoding="utf-8")) if GEO_CACHE.exists() else {}
    def look(q: str):
        if q in cache:
            return cache[q]
        req = urllib.request.Request(NOMINATIM + "?" + urllib.parse.urlencode({"q": q, "format": "json", "limit": 1, "countrycodes": "kr"}), headers={"User-Agent": "rebom-guide-snapshot/1.0 (adoption guide, one-off)", "Accept-Language": "ko"})
        with urllib.request.urlopen(req, timeout=30) as r:
            hit = json.loads(r.read())
        time.sleep(1.1)
        cache[q] = [round(float(hit[0]["lat"]), 5), round(float(hit[0]["lon"]), 5)] if hit else None
        return cache[q]
    out = []
    sidos = sorted({s for s, _ in regions})
    sido_xy = {}
    for sido in sidos:
        sido_xy[sido] = next((xy for q in geocode_queries(sido, None) if (xy := look(q))), None)
    for sido, sigungu in sorted(regions, key=lambda r: (r[0], r[1] or "")):
        xy = sido_xy.get(sido) if sigungu is None else next((xy for q in geocode_queries(sido, sigungu) if (xy := look(q))), None)
        approx = xy is None
        xy = xy or sido_xy.get(sido)
        if xy:
            out.append({"sido": sido, "sigungu": sigungu, "lat": xy[0], "lng": xy[1], "approx": approx})
    GEO_CACHE.write_bytes((json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return out


def main() -> int:
    env = _env()
    data_key = urllib.parse.unquote(env.get("DATA_GO_KR_SERVICE_KEY") or env.get("DATA_API") or "")
    oc = env.get("Law_open_data", "")
    if not data_key or not oc:
        print(".env 에 DATA_GO_KR_SERVICE_KEY(또는 DATA_API)와 Law_open_data 가 필요합니다.")
        return 1
    programs = fetch_programs(data_key)
    print("신청할 수 있는 지원", len(programs))
    ordinances = fetch_ordinances(oc)
    print("입양 조문이 있는 동물 조례", len(ordinances), "지자체", len({(o["sido"], o["sigungu"]) for o in ordinances}))
    festivals, ref = fetch_festivals(data_key)
    print("반려동물 축제", len(festivals))
    regions = {(x["sido"], x["sigungu"]) for x in programs + ordinances if x["sido"]}
    regions |= {(x["sido"], None) for x in programs + ordinances if x["sido"]}
    coords = fetch_coords(regions)
    print("지역 좌표", len(coords), "대략(시·도 좌표로 대신)", sum(c["approx"] for c in coords))
    snapshot = {
        "generatedAt": date.today().isoformat(),
        "sources": {
            "programs": "행정안전부 대한민국 공공서비스(혜택) 정보",
            "ordinances": "국가법령정보센터 자치법규 (동물 조례의 '입양' 조문 자동 분류, 운영팀 검토 전)",
            "festivals": f"전국문화축제 표준데이터 (데이터 기준일 {ref or '미상'})",
            "coords": "지역 위치: OpenStreetMap Nominatim (© OpenStreetMap contributors)",
        },
        "regions": coords,
        "programs": programs,
        "ordinances": ordinances,
        "festivals": festivals,
    }
    OUT.write_bytes((json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    print("저장", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())

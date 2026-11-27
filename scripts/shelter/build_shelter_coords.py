"""보호소 주소 → 좌표 스냅샷 (frontend/src/data/shelter-coords.json). 지도로 보기·보호센터 지도용.

re-bom 폴더에서 실행: python scripts/shelter/build_shelter_coords.py [--table rebom_silver.silver_shelter_b_test]

- 입력: Silver 보호소 표(care_reg_no, care_name, care_address). 읽기만 한다.
- 좌표: 카카오 로컬 API. ① 주소 검색 → ② 같은 주소로 장소 검색 → ③ '시·군·구 + 보호소 이름' 장소 검색
  (②③은 같은 시·도 결과만, precision='address'·'place') → ④ 시·군·구 중심(카카오, 없으면
  scripts/guide/region_coords.json)을 쓰고 precision='region'(대략 위치).
- 키: 환경변수 KAKAO_REST_API_KEY, 없으면 .env 의 kakao_rest_api. 키·주소가 담긴 요청 주소는 출력하지 않는다.
- 결과에는 보호소 번호와 좌표·정확도만 남긴다(이름·주소·전화는 화면이 검색 API 에서 받는다).
나중에(B) 같은 값을 RDS 보호소 칼럼으로 옮긴다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "frontend" / "src" / "data" / "shelter-coords.json"
REGION_COORDS = ROOT / "scripts" / "guide" / "region_coords.json"
KAKAO = "https://dapi.kakao.com/v2/local/search/"
KST = timezone(timedelta(hours=9))
KOREA = (33.0, 38.7, 124.5, 132.0)  # 위도·경도 범위 (독도 포함)
SIDO_SHORT = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "광주광역시": "광주",
    "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종", "경기도": "경기", "강원특별자치도": "강원",
    "강원도": "강원", "충청북도": "충북", "충청남도": "충남", "전북특별자치도": "전북", "전라북도": "전북",
    "전라남도": "전남", "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주",
}


def clean_address(addr: str | None) -> str:
    """'경상남도 창원시 성산구 공단로474번길 117 (상복동) 창원동물보호센터' → '경상남도 창원시 성산구 공단로474번길 117'.
    두 칸 이상 띄우고 덧붙인 말('… 201  828번지')도 뗀다."""
    text = re.split(r"[(（]|\s{2,}", (addr or "").strip(), maxsplit=1)[0]
    return re.sub(r"\s+", " ", text).strip().rstrip(",")


def sido_sigungu(addr: str | None) -> tuple[str | None, str | None]:
    parts = clean_address(addr).split()
    sido = parts[0] if parts else None
    sigungu = parts[1] if len(parts) > 1 and re.search(r"(시|군|구)$", parts[1]) else None
    return sido, sigungu


def same_sido(a: str | None, b: str | None) -> bool:
    """'경상북도' 와 '경북' 처럼 이름이 달라도 같은 시·도인지."""
    if not a or not b:
        return False
    return SIDO_SHORT.get(a, a[:2]) == SIDO_SHORT.get(b, b[:2])


def in_korea(lat: float, lng: float) -> bool:
    return KOREA[0] <= lat <= KOREA[1] and KOREA[2] <= lng <= KOREA[3]


def first_point(docs: list[dict], sido: str | None = None) -> tuple[float, float] | None:
    """카카오 결과 중 첫 좌표 (sido 를 주면 같은 시·도 결과만)."""
    for d in docs:
        addr = d.get("road_address_name") or d.get("address_name") or ""
        if sido and not same_sido(sido, addr.split(" ")[0] if addr else None):
            continue
        try:
            lat, lng = round(float(d["y"]), 6), round(float(d["x"]), 6)
        except (KeyError, TypeError, ValueError):
            continue
        if in_korea(lat, lng):
            return lat, lng
    return None


def region_point(regions: dict[str, list[float]], sido: str | None, sigungu: str | None) -> tuple[float, float] | None:
    for key in (f"{sido} {sigungu}" if sigungu else None, sido):
        if key and regions.get(key):
            return tuple(regions[key])  # type: ignore[return-value]
    return None


def locate(row: dict, search, regions: dict[str, list[float]]) -> tuple[float, float, str] | None:
    """보호소 한 곳의 (위도, 경도, 정확도). search(kind, query) → 카카오 documents."""
    addr = clean_address(row.get("care_address"))
    sido, sigungu = sido_sigungu(addr)
    if addr and (p := first_point(search("address", addr))):
        return (*p, "address")
    if addr and (p := first_point(search("keyword", addr), sido)):  # 장소 검색이 도로명을 더 넓게 찾는다
        return (*p, "address")
    name = (row.get("care_name") or "").strip()
    if name and (p := first_point(search("keyword", " ".join(x for x in (sigungu, name) if x)), sido)):
        return (*p, "place")
    if sigungu and (p := first_point(search("address", f"{sido} {sigungu}"))):
        return (*p, "region")
    if p := region_point(regions, sido, sigungu):
        return (*p, "region")
    return None


def kakao_search(key: str):
    def search(kind: str, query: str) -> list[dict]:
        url = KAKAO + f"{kind}.json?" + urllib.parse.urlencode({"query": query, "size": 5})
        req = urllib.request.Request(url, headers={"Authorization": f"KakaoAK {key}"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    time.sleep(0.05)
                    return json.loads(r.read()).get("documents", [])
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    sys.exit(f"카카오가 키를 거절함 (HTTP {exc.code}). 키 또는 허용 IP 설정을 확인해 주세요")
                if attempt == 2:
                    raise RuntimeError(f"카카오 {kind} 검색 실패 (HTTP {exc.code})") from None
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == 2:
                    raise RuntimeError(f"카카오 {kind} 검색 실패 ({type(exc).__name__})") from None
            time.sleep(1 + attempt)
        return []
    return search


def kakao_key() -> str:
    if os.environ.get("KAKAO_REST_API_KEY"):
        return os.environ["KAKAO_REST_API_KEY"]
    env = ROOT / ".env"
    for line in env.read_text(encoding="utf-8").splitlines() if env.exists() else []:
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            if k.strip() == "kakao_rest_api":
                return v.strip().strip('"').strip("'")
    sys.exit("KAKAO_REST_API_KEY(또는 .env 의 kakao_rest_api)가 없습니다")


def load_shelters(table: str) -> tuple[list[dict], int]:
    sys.path.insert(0, str(ROOT / "src"))
    from rebom.storage.silver.aws_catalog import get_aws_catalog

    got = get_aws_catalog(profile=os.environ.get("AWS_PROFILE", "rebom"))
    catalog = got[0] if isinstance(got, tuple) else got
    t = catalog.load_table(table)
    rows = t.scan(selected_fields=("care_reg_no", "care_name", "care_address")).to_arrow().to_pylist()
    return rows, t.current_snapshot().snapshot_id


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--table", default="rebom_silver.silver_shelter_b_test")
    args = ap.parse_args()
    rows, snapshot = load_shelters(args.table)
    regions = json.loads(REGION_COORDS.read_text(encoding="utf-8"))
    search = kakao_search(kakao_key())
    shelters, missing = {}, []
    for i, row in enumerate(sorted(rows, key=lambda r: r["care_reg_no"]), 1):
        hit = locate(row, search, regions)
        if hit:
            shelters[row["care_reg_no"]] = list(hit)
        else:
            missing.append(row["care_reg_no"])
        if i % 50 == 0:
            print(f"{i}/{len(rows)}")
    counts = {k: sum(1 for v in shelters.values() if v[2] == k) for k in ("address", "place", "region")}
    out = {
        "generatedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "source": {"table": args.table, "snapshotId": str(snapshot), "rows": len(rows)},
        "method": "카카오 로컬 API: 주소 → (없으면) 보호소 이름 장소 → (없으면) 시·군·구 중심",
        "counts": {**counts, "missing": len(missing)},
        "fields": ["lat", "lng", "precision"],
        "shelters": dict(sorted(shelters.items())),
    }
    OUT.write_bytes((json.dumps(out, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    print(f"보호소 {len(rows)}곳 · 주소 {counts['address']} · 장소 {counts['place']} · 대략(시·군·구) {counts['region']} · 없음 {len(missing)}")
    if missing:
        print("좌표 없음:", ", ".join(missing))
    print(f"저장 {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

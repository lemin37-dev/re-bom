"""scripts/shelter/build_shelter_coords.py 의 주소 정리·좌표 고르기 규칙. 카카오는 부르지 않는다."""

import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_shelter_coords", ROOT / "scripts" / "shelter" / "build_shelter_coords.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_clean_address_drops_notes():
    assert mod.clean_address("경상남도 창원시 성산구 공단로474번길 117 (상복동) 창원동물보호센터") == "경상남도 창원시 성산구 공단로474번길 117"
    assert mod.clean_address("충청북도 단양군 매포읍 단양산업단지1로 201  828번지") == "충청북도 단양군 매포읍 단양산업단지1로 201"
    assert mod.clean_address(None) == ""


def test_sido_sigungu_and_same_sido():
    assert mod.sido_sigungu("경상북도 안동시 서후면 풍산태사로 3252-29") == ("경상북도", "안동시")
    assert mod.sido_sigungu("세종특별자치시 전동면 미륵당1길 188") == ("세종특별자치시", None)
    assert mod.same_sido("경상북도", "경북") and mod.same_sido("전북특별자치도", "전라북도")
    assert not mod.same_sido("경상북도", "경남")


def test_first_point_filters_sido_and_korea():
    docs = [
        {"address_name": "경남 창원시", "x": "128.6", "y": "35.2"},
        {"address_name": "경북 안동시", "x": "128.7", "y": "36.6"},
    ]
    assert mod.first_point(docs) == (35.2, 128.6)
    assert mod.first_point(docs, "경상북도") == (36.6, 128.7)
    assert mod.first_point([{"address_name": "서울 중구", "x": "0", "y": "0"}]) is None


def test_locate_falls_back_in_order():
    calls = []

    def search(kind, query):
        calls.append((kind, query))
        if kind == "keyword" and "보호센터" in query:
            return [{"address_name": "전남 장성군 삼계면", "x": "126.7", "y": "35.2"}]
        return []

    row = {"care_name": "장성군동물보호센터", "care_address": "전라남도 장성군 삼계면 능성로 382-31  0"}
    assert mod.locate(row, search, {}) == (35.2, 126.7, "place")
    assert calls[0] == ("address", "전라남도 장성군 삼계면 능성로 382-31")
    # 아무것도 못 찾으면 시·군·구 중심, 그것도 없으면 None
    assert mod.locate({"care_name": "x", "care_address": "전라남도 장성군 어딘가"}, lambda k, q: [], {"전라남도 장성군": [35.3, 126.78]}) == (35.3, 126.78, "region")
    assert mod.locate({"care_name": "x", "care_address": "없는도 없는군"}, lambda k, q: [], {}) is None


def test_snapshot_has_only_coords():
    snap = json.loads((ROOT / "frontend" / "src" / "data" / "shelter-coords.json").read_text(encoding="utf-8"))
    assert snap["fields"] == ["lat", "lng", "precision"]
    for reg_no, (lat, lng, precision) in snap["shelters"].items():
        assert reg_no.isdigit() and mod.in_korea(lat, lng) and precision in {"address", "place", "region"}

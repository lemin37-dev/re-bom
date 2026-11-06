"""사진 태그 평가 도구 테스트. 실제 API·사진 다운로드 없이 샘플 JSON과 합성 이미지로 확인한다."""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from rebom.tagging import photo_eval as ev
from rebom.tagging.color_ratio import color_ratio, pixel_color
from rebom.tagging.jev_client import parse_answers, question_batches
from rebom.tagging.photo_tags import UNKNOWN, colors_in_color_cd, gold_breed, parse_ratio_label

ROOT = Path(__file__).resolve().parents[1]


# --- 태그 사전 ---

@pytest.mark.parametrize(
    "color_cd, expected",
    [
        ("흰색", {"흰색"}),
        ("갈색&검정&흰색", {"갈색", "검정", "흰색"}),
        ("기타(고등어태비)", {"회색", "검정"}),
        ("기타(삼색)", {"흰색", "검정", "주황"}),
        ("레몬색&흰색", {"크림·황토", "흰색"}),
        ("기타(치즈)", {"주황"}),
        ("청회색", {"회색"}),
        ("기타(스탠다드)", set()),
        (None, set()),
    ],
)
def test_colors_in_color_cd(color_cd, expected):
    assert colors_in_color_cd(color_cd) == expected


@pytest.mark.parametrize(
    "kind, expected",
    [("포메라니안", "포메라니안"), ("미디엄 푸들", "푸들"), ("믹스견", None),
     ("핏불테리어 믹스견", None), ("한국 고양이", None), ("기타", None), (None, None)],
)
def test_gold_breed(kind, expected):
    assert gold_breed(kind) == expected


def test_parse_ratio_label():
    assert parse_ratio_label("흰색:60, 갈색:40") == {"흰색": 0.6, "갈색": 0.4}
    assert parse_ratio_label("흰색:3/검정:1") == {"흰색": 0.75, "검정": 0.25}
    assert parse_ratio_label("") == {}
    with pytest.raises(ValueError):
        parse_ratio_label("보라:100")


# --- 채점 ---

def test_threshold_table_counts_unknown_as_not_answered():
    pairs = [
        ("짧음", {"짧음": 0.9, "김": 0.1}),
        ("김", {"짧음": 0.65, "김": 0.35}),  # 0.6 기준이면 채택·오답, 0.7이면 미채택
        ("김", {UNKNOWN: 0.8, "김": 0.2}),  # 알 수 없음 → 미채택
        (UNKNOWN, {"짧음": 0.9}),  # 정답이 알 수 없음 → 채점 제외
        ("짧음", None),  # 예측 없음 → 미채택
    ]
    rows = {r.threshold: r for r in ev.threshold_table(pairs, thresholds=(0.6, 0.7))}
    assert (rows[0.6].n, rows[0.6].answered, rows[0.6].correct) == (4, 2, 1)
    assert (rows[0.7].answered, rows[0.7].correct) == (1, 1)
    assert rows[0.7].coverage == 0.25 and rows[0.7].accuracy == 1.0


def test_macro_f1_perfect_and_abstain():
    perfect = [("짧음", {"짧음": 0.9}), ("김", {"김": 0.9})]
    assert ev.macro_f1(perfect, 0.6) == 1.0
    abstain = [("짧음", {"짧음": 0.9}), ("김", {"김": 0.5})]
    assert ev.macro_f1(abstain, 0.6) == pytest.approx(0.5)  # 짧음 F1 1.0, 김 F1 0


def test_calibration_bins():
    pairs = [("a", {"a": 0.95}), ("a", {"b": 0.92}), ("a", {"a": 0.55})]
    cal = {name: (n, acc) for name, n, acc in ev.calibration(pairs)}
    assert cal["0.9~1.0"] == (2, 0.5)
    assert cal["0.5~0.6"] == (1, 1.0)


def test_cohen_kappa():
    assert ev.cohen_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == 1.0
    assert ev.cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == pytest.approx(0.0)
    assert ev.cohen_kappa([], []) is None


def test_ratio_error_and_summary():
    gold = {"흰색": 0.6, "갈색": 0.4}
    assert ev.ratio_error(gold, gold) == 0
    assert ev.ratio_error(gold, {"흰색": 1.0}) == pytest.approx(40)
    s = ev.ratio_summary([(gold, {"흰색": 0.5, "갈색": 0.5}), (gold, {"갈색": 0.7, "흰색": 0.3})])
    assert s.n == 2 and s.main_match == 0.5 and s.mean_error_pp == pytest.approx(20)


def test_breed_summary():
    pairs = [
        ("푸들", {"푸들": 0.6, "말티즈": 0.3}),
        ("말티즈", {"푸들": 0.5, "비숑 프리제": 0.3, "말티즈": 0.2}),
        ("진도견", {"시바": 0.9}),  # 후보 목록에 정답 없음
        (None, {"푸들": 0.9}),  # 믹스견 → 채점 제외
    ]
    s = ev.breed_summary(pairs)
    assert s["n"] == 3
    assert s["top1"] == pytest.approx(1 / 3)
    assert s["top3"] == pytest.approx(2 / 3)
    assert s["gold_not_in_candidates"] == 1


# --- 털색 비율 (픽셀) ---

@pytest.mark.parametrize(
    "rgb, name",
    [((250, 250, 250), "흰색"), ((10, 10, 10), "검정"), ((128, 128, 128), "회색"),
     ((110, 70, 40), "갈색"), ((235, 140, 50), "주황"), ((230, 200, 150), "크림·황토"), ((40, 160, 60), None)],
)
def test_pixel_color(rgb, name):
    assert pixel_color(*rgb) == name


def test_color_ratio_half_white_half_black():
    img = Image.new("RGB", (100, 100), (250, 250, 250))
    img.paste((10, 10, 10), (50, 0, 100, 100))
    ratios = color_ratio(img, center=1.0)
    assert set(ratios) == {"흰색", "검정"}
    assert ratios["흰색"] == pytest.approx(0.5, abs=0.05)


def test_color_ratio_ignores_green_background_and_small_noise():
    img = Image.new("RGB", (100, 100), (40, 160, 60))  # 잔디 배경
    img.paste((235, 140, 50), (20, 20, 80, 80))  # 치즈 고양이
    img.paste((10, 10, 10), (20, 20, 22, 22))  # 1% 남짓 잡음
    assert color_ratio(img, center=1.0) == {"주황": 1.0}


def test_grabcut_mask_drops_background():
    pytest.importorskip("cv2")
    from rebom.tagging.background import grabcut_mask

    # 회색 바닥 위 가운데 흰 개. 가운데 70% 자르기만 하면 회색이 섞인다.
    img = Image.new("RGB", (200, 200), (128, 128, 128))
    img.paste((250, 250, 250), (60, 50, 140, 150))
    mask = grabcut_mask(img)
    assert mask is not None and mask.size == img.size
    masked, cropped = color_ratio(img, mask=mask), color_ratio(img)
    assert masked["흰색"] >= 0.9  # 경계 몇 픽셀은 섞일 수 있다
    assert cropped["회색"] > masked.get("회색", 0) + 0.2


def test_grabcut_mask_none_on_blank_image():
    pytest.importorskip("cv2")
    from rebom.tagging.background import grabcut_mask

    assert grabcut_mask(Image.new("RGB", (100, 100), (128, 128, 128))) is None


# --- Jev 질문지·응답 ---

def test_question_batches_split_by_five():
    batches = question_batches(with_breed=True)
    assert all(len(b) <= 5 for b in batches)
    assert sum(len(b) for b in batches) == 6
    assert [q["id"] for q in batches[1]] == ["q1"]
    assert all(q["choices"][-1] == UNKNOWN for b in batches for q in b)


def test_parse_answers_playground_shape():
    batch = [{"id": "q1", "tag": "fur_length"}, {"id": "q2", "tag": "injury"}, {"id": "q3", "tag": "species"}]
    response = {  # 플레이그라운드 View details 와 같은 모양
        "answers": {
            "q1": {"type": "choice", "choice": "짧음", "probabilities": {"짧음": 0.8, "김": 0.19, UNKNOWN: 0.01}},
            "q2": {"type": "boolean", "probability": 0.97},
        },
        "confidence": {"q1": 0.76},
    }
    out = parse_answers(response, batch)
    assert out["fur_length"]["짧음"] == 0.8
    assert out["injury"] == {"예": 0.97, "아니오": pytest.approx(0.03)}
    assert "species" not in out


# --- 스크립트 (샘플 JSON, 다운로드 없음) ---

def test_build_eval_set_and_score_end_to_end(tmp_path):
    sheet = tmp_path / "eval_set.csv"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/tagging/build_eval_set.py"), "--out", str(sheet), "--n-dog", "10", "--n-cat", "5"],
        check=True, capture_output=True,
    )
    with sheet.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 15
    assert "careOwnerNm" not in rows[0] and "careTel" not in rows[0]
    dogs = [r for r in rows if r["upKindNm"] == "개"]
    assert len(dogs) == 10 and all(r["gold_breed"] for r in dogs[:5])  # 품종견을 먼저 채움

    # 라벨 두 건을 채우고 가짜 예측으로 채점
    rows[0]["label_fur_length"] = "짧음"
    rows[0]["label_color_ratio"] = "흰색:70, 갈색:30"
    rows[1]["label_fur_length"] = "김"
    with sheet.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    pred = tmp_path / "pred.jsonl"
    recs = [
        {"desertionNo": rows[0]["desertionNo"], "method": "fake", "tag": "fur_length", "probabilities": {"짧음": 0.9, "김": 0.1}},
        {"desertionNo": rows[1]["desertionNo"], "method": "fake", "tag": "fur_length", "probabilities": {"짧음": 0.7, "김": 0.3}},
        {"desertionNo": rows[0]["desertionNo"], "method": "fake", "tag": "color_ratio", "ratios": {"흰색": 0.5, "갈색": 0.5}},
        {"desertionNo": rows[0]["desertionNo"], "method": "fake", "tag": "breed",
         "probabilities": {rows[0]["gold_breed"]: 0.8, "말티즈": 0.2}},
    ]
    pred.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")
    report = tmp_path / "report.md"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/tagging/score.py"), "--eval-set", str(sheet), "--pred", str(pred), "--out", str(report)],
        check=True, capture_output=True,
    )
    text = report.read_text(encoding="utf-8")
    assert "| 0.60 | 100% | 50% |" in text  # 2건 모두 채택, 1건 정답
    assert "주색 일치 100% · 비율 평균 오차 20.0%p" in text
    assert "상위 1개 적중 100%" in text


# --- 임베딩 제로샷 (모델 없이 계산 부분만) ---

def test_zero_shot_picks_closest_option_and_sums_to_one():
    from rebom.tagging.photo_tags import PALETTE
    from rebom.tagging.zero_shot import PROMPTS, zero_shot

    assert set(PROMPTS["color_main"]) == set(PALETTE)
    probs = zero_shot([1.0, 0.0], {"흰색": [0.9, 0.1], "검정": [0.0, 1.0]}, temperature=0.05)
    assert max(probs, key=probs.get) == "흰색"
    assert sum(probs.values()) == pytest.approx(1.0)


def test_zero_shot_temperature_changes_confidence_not_ranking():
    from rebom.tagging.zero_shot import zero_shot

    opts = {"a": [1.0, 0.2], "b": [0.2, 1.0]}
    sharp = zero_shot([1.0, 0.0], opts, temperature=0.01)
    flat = zero_shot([1.0, 0.0], opts, temperature=1.0)
    assert max(sharp, key=sharp.get) == max(flat, key=flat.get) == "a"
    assert sharp["a"] > flat["a"]


# --- 품종견 추가 수집 ---

def test_fetch_breed_dogs_choose_caps_each_breed():
    import importlib
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "tagging"))
    choose = importlib.import_module("fetch_breed_dogs").choose
    recs = [{"desertionNo": str(i), "kindNm": "말티즈", "popfile1": "u"} for i in range(10)]
    recs += [{"desertionNo": "p1", "kindNm": "푸들", "popfile1": "u"},
             {"desertionNo": "p2", "kindNm": "푸들"},  # 사진 없음
             {"desertionNo": "m1", "kindNm": "믹스견", "popfile1": "u"},  # 품종 정답 없음
             {"desertionNo": "p3", "kindNm": "푸들", "popfile1": "u"}]
    got = choose(recs, exclude={"p3"}, target=10, per_breed=3, seed=1)
    nos = {r["desertionNo"] for r in got}
    assert len(got) == 4 and "p1" in nos and not nos & {"p2", "m1", "p3"}

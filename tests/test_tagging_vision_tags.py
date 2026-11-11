"""비전 모델 귀 태깅 (v1 귀 부분만): 스키마, 응답 해석, 사진 회전, 채점, 스크립트 흐름. 실제 API 는 부르지 않는다."""
from __future__ import annotations

import csv
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rebom.tagging import vision_tags as vt

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "tagging" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def answer(value="선 귀") -> str:
    return json.dumps({"ear": {"evidence": "귀뿌리가 서 있고 끝이 뾰족함", "value": value}}, ensure_ascii=False)


def test_prompt_is_v1_ear_part():
    v1_ear = """[귀]
- 선 귀: 귀가 위로 곧게 서 있음 (끝이 뾰족하든 둥글든)
- 접힌 귀: 귀 아래쪽은 서 있고 끝부분이 앞이나 옆으로 꺾임, 또는 귀가 뒤로 접혀 붙음
- 늘어진 귀: 귀 전체가 머리 옆으로 처져 내려옴
- 안 보임: 귀가 털에 묻히거나 사진에서 잘려 판단할 수 없음"""
    assert v1_ear in vt.USER_PROMPT
    assert "다리" not in vt.USER_PROMPT and "꼬리" not in vt.USER_PROMPT
    assert "prick" not in vt.USER_PROMPT  # 표준 용어는 프롬프트에 넣지 않는다
    assert "품종" in vt.SYSTEM_PROMPT


def test_schema_has_not_visible_and_evidence_first():
    prop = vt.response_schema()["properties"]["ear"]
    assert prop["properties"]["value"]["enum"] == list(vt.OPTIONS["ear"])
    assert prop["properties"]["value"]["enum"][-1] == vt.NOT_VISIBLE
    assert prop["propertyOrdering"] == ["evidence", "value"]
    assert set(vt.OPTIONS) == {"ear"}  # 다리·꼬리는 다루지 않는다


def test_terms_for_tag():
    assert vt.terms_for("선 귀") == ["쫑긋 선 귀(prick)", "박쥐 귀(bat)"]
    assert len(vt.terms_for("접힌 귀")) == 3
    assert vt.terms_for(vt.NOT_VISIBLE) == []


@pytest.mark.parametrize("tag", ["선 귀", "접힌 귀", "늘어진 귀", "안 보임"])
def test_parse_answer(tag):
    out = vt.parse_answer(answer(tag))
    assert (out["ear"].value, out["ear"].term) == (tag, "")
    assert out["ear"].evidence == "귀뿌리가 서 있고 끝이 뾰족함"


def test_parse_answer_rejects_unknown_option():
    with pytest.raises(ValueError):
        vt.parse_answer(answer("쫑긋 선 귀(prick)"))


def jpeg_bytes(orientation: int | None) -> bytes:
    from PIL import Image

    im = Image.new("RGB", (40, 20), "white")
    buf = io.BytesIO()
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation
        im.save(buf, format="JPEG", exif=exif)
    else:
        im.save(buf, format="JPEG")
    return buf.getvalue()


def test_prepare_image_keeps_upright_original():
    data = jpeg_bytes(None)
    assert vt.prepare_image(data) == (data, "image/jpeg")


def test_prepare_image_applies_exif_rotation():
    from PIL import Image

    out, mime = vt.prepare_image(jpeg_bytes(6))  # 6 = 시계 방향 90도
    assert mime == "image/jpeg"
    assert Image.open(io.BytesIO(out)).size == (20, 40)


def test_tag_stats():
    s = vt.TagStats()
    for gold, pred in [("선 귀", "선 귀"), ("선 귀", "늘어진 귀"), ("선 귀", "안 보임"), ("안 보임", "선 귀"), ("안 보임", "안 보임")]:
        s.add(gold, pred)
    assert (s.n_visible, s.answered, s.correct, s.n_hidden, s.hallucinated) == (3, 2, 1, 2, 1)
    assert s.accuracy == pytest.approx(1 / 3)
    assert s.precision == pytest.approx(0.5)
    assert s.hallucination == pytest.approx(0.5)
    assert s.shown_precision == pytest.approx(1 / 3)


class FakeModels:
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append((model, contents, config))
        text = self.texts.pop(0)
        if isinstance(text, Exception):
            raise text
        usage = SimpleNamespace(prompt_token_count=900, candidates_token_count=80, thoughts_token_count=20)
        return SimpleNamespace(text=text, usage_metadata=usage)


def test_tag_one_and_score(tmp_path, monkeypatch):
    pytest.importorskip("google.genai")
    predict = load_script("predict_vision_tags")
    photo = tmp_path / "a.jpg"
    photo.write_bytes(jpeg_bytes(None))
    monkeypatch.setattr(predict, "ROOT", tmp_path)
    busy = RuntimeError("busy")
    busy.code = 429
    monkeypatch.setattr(predict.time, "sleep", lambda s: None)
    client = SimpleNamespace(models=FakeModels([busy, answer(), "not json"]))
    row = {"desertionNo": "1", "photo_path": "a.jpg", "kindNm": "진도견"}
    rec = predict.tag_one(client, "gemini-3.1-flash-lite", None, row)
    assert rec["tags"]["ear"] == {"value": "선 귀", "term": "", "evidence": "귀뿌리가 서 있고 끝이 뾰족함"}
    assert rec["usage"] == {"prompt": 900, "output": 80, "thoughts": 20}
    sent = client.models.calls[-1][1]
    assert "진도견" not in json.dumps([c for c in sent if isinstance(c, str)], ensure_ascii=False)  # 품종은 안 보냄
    bad = predict.tag_one(client, "gemini-3.1-flash-lite", None, row)
    assert "error" in bad

    # 채점 스크립트까지
    score = load_script("score_vision")
    gold = tmp_path / "gold.csv"
    with gold.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["desertionNo", "kindNm", "photo_path", "label_ear"])
        w.writerow(["1", "진도견", "a.jpg", "선 귀"])
        w.writerow(["2", "믹스견", "b.jpg", "안 보임"])
    v2 = tmp_path / "v2"
    v2.mkdir()
    pred = v2 / "gemini-3.1-flash-lite.jsonl"
    # 표준 용어(term)가 있는 v3 결과를 흉내 낸다
    with_term = {**rec, "prompt_version": "ear-vision-v3",
                 "tags": {"ear": {**rec["tags"]["ear"], "term": "쫑긋 선 귀(prick)"}}}
    hallucinated = {**with_term, "desertionNo": "2"}
    pred.write_text("".join(json.dumps({**r, "media_resolution": "medium", "thinking_level": "low"}, ensure_ascii=False) + "\n"
                            for r in (with_term, hallucinated)), encoding="utf-8")
    # v1 결과(귀·다리·꼬리, term 없음)도 같은 이름으로 함께 채점할 수 있다
    v1 = tmp_path / "v1"
    v1.mkdir()
    old = {**rec, "prompt_version": "body-vision-v1", "media_resolution": "medium", "thinking_level": "low",
           "tags": {"ear": {"value": "늘어진 귀", "evidence": "옛 근거"}, "leg": {"value": "안 보임", "evidence": ""},
                    "tail": {"value": "말린 꼬리", "evidence": ""}}}
    pred_v1 = v1 / "gemini-3.1-flash-lite.jsonl"
    pred_v1.write_text(json.dumps(old, ensure_ascii=False) + "\n", encoding="utf-8")
    out, out_csv = tmp_path / "r.md", tmp_path / "e.csv"
    monkeypatch.setattr("sys.argv", ["x", "--eval-set", str(gold), "--pred", str(pred_v1), str(pred), "--out", str(out), "--csv", str(out_csv)])
    score.main()
    text = out.read_text(encoding="utf-8")
    v2_name = "gemini-3.1-flash-lite · ear-vision-v3"
    assert f"| {v2_name} | 귀 | 1 | 100% | 100% | 100% | 1 | 100% |" in text  # 2번은 안 보이는데 답함 = 환각
    assert "| gemini-3.1-flash-lite · body-vision-v1 | 귀 | 1 | 0% |" in text
    assert "다리" not in text and "꼬리" not in text
    assert f"- {v2_name}: 쫑긋 선 귀(prick) 2장 (정답과 같음 1)" in text
    assert "| 2 | 믹스견 | 귀 | 안 보임 | 선 귀 (쫑긋 선 귀(prick)) |" in text
    assert vt.USER_PROMPT.splitlines()[0] in text
    rows = list(csv.reader(out_csv.open(encoding="utf-8-sig")))
    assert rows[0][-3:] == [f"{v2_name}:value", f"{v2_name}:term", f"{v2_name}:evidence"]
    assert rows[1][4:] == ["늘어진 귀", "", "옛 근거", "선 귀", "쫑긋 선 귀(prick)", "귀뿌리가 서 있고 끝이 뾰족함"]
    assert len(rows) == 1 + 2


def test_score_skips_unsure_gold():
    score = load_script("score_vision")
    gold = {"1": {"label_ear": vt.UNSURE}, "2": {"label_ear": "선 귀"}}
    preds = [{"desertionNo": n, "tags": {"ear": {"value": "선 귀", "evidence": ""}}} for n in ("1", "2")]
    s, conf = score.score(preds, gold, "ear")
    assert (s.n_visible, s.n_hidden, s.correct) == (1, 0, 1)
    assert sum(conf.values()) == 1

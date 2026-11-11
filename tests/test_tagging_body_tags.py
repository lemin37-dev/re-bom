"""귀·다리·꼬리 몸 특징 태그: 보기 문장과 채점 규칙."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from rebom.tagging.zero_shot import BODY_PROMPTS, PROMPT_SETS

ROOT = Path(__file__).resolve().parents[1]


def load_score():
    spec = importlib.util.spec_from_file_location("score", ROOT / "scripts" / "tagging" / "score.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_body_prompt_set():
    assert PROMPT_SETS["body"] is BODY_PROMPTS
    assert set(BODY_PROMPTS) == {"ear", "leg", "tail"}
    for options in BODY_PROMPTS.values():
        assert len(options) >= 3
        assert "안 보임" not in options
        assert all(texts for texts in options.values())


def test_not_visible_is_excluded_from_scoring():
    score = load_score()
    row = {"label_ear": "선 귀", "label_leg": "안 보임", "label_tail": ""}
    assert score.gold_for("ear", row) == "선 귀"
    assert score.gold_for("leg", row) is None
    assert score.gold_for("tail", row) is None

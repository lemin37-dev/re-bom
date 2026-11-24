"""사진 태깅 버전 설정: 질문지·이름표·후처리 기준이 바뀌면 해시가 달라져 같은 버전으로 실행할 수 없다."""
import json

import pytest

from rebom.common.gold_version_registry import canonical_config
from rebom.tagging import breed_tags, trait_tags, version_config as vc


def test_config_is_stable_and_registrable():
    for task in (trait_tags.TASK, breed_tags.TASK):
        a, b = vc.runtime_config(task), vc.runtime_config(task)
        assert canonical_config(a) == canonical_config(b)
        assert a["model_id"] == "google/gemini-3.1-flash-lite" and len(a["prompt_sha256"]) == 64
    assert "alias_sha256" in vc.runtime_config(breed_tags.TASK)["postprocess"]
    assert "alias_sha256" not in vc.runtime_config(trait_tags.TASK)["postprocess"]


def test_prompt_text_change_changes_hash_even_with_same_prompt_version(monkeypatch):
    before = canonical_config(vc.runtime_config(trait_tags.TASK))[1]
    monkeypatch.setattr(trait_tags, "USER_PROMPT", trait_tags.USER_PROMPT + " ")
    assert canonical_config(vc.runtime_config(trait_tags.TASK))[1] != before


def test_alias_or_threshold_change_changes_breed_hash(monkeypatch):
    before = canonical_config(vc.runtime_config(breed_tags.TASK))[1]
    monkeypatch.setitem(breed_tags.ALIAS, "테스트견", "진도견")
    after_alias = canonical_config(vc.runtime_config(breed_tags.TASK))[1]
    monkeypatch.setattr(breed_tags, "STRONG_SIMILARITY", 80)
    assert len({before, after_alias, canonical_config(vc.runtime_config(breed_tags.TASK))[1]}) == 3


def test_cli_prints_registration_json(capsys):
    assert vc.main(["--task", trait_tags.TASK]) == 0
    assert json.loads(capsys.readouterr().out) == vc.runtime_config(trait_tags.TASK)


def test_unknown_task_rejected():
    with pytest.raises(ValueError):
        vc.runtime_config("photo_ear")

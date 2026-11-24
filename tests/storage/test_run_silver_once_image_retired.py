"""run_silver_once 의 옛 이미지 실험 옵션은 아무것도 실행하기 전에 멈춘다 (image_table 은 사진 처리 표)."""
import subprocess

import pytest

from rebom.storage.silver import run_silver_once


@pytest.mark.parametrize("flags", [["--apply", "--apply-image"], ["--check-image"]])
def test_image_experiment_flags_stop_before_any_work(monkeypatch, capsys, flags):
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(run_silver_once, "fetch_run_inputs", lambda *a, **k: ran.append("fetch"))
    monkeypatch.setattr("sys.argv", ["run_silver_once", "--run-id", "20261124T000000-abc123", "--bucket", "b", *flags])
    with pytest.raises(SystemExit):
        run_silver_once.main()
    assert ran == []
    assert "IMAGE_EXPERIMENT_RETIRED" in capsys.readouterr().err

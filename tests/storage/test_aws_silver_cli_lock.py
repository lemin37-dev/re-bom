"""No-AWS tests for the AWS Silver command's writer lock."""
import argparse
import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "storage" / "rebom_run_aws_silver.py"
spec = importlib.util.spec_from_file_location("rebom_run_aws_silver_cli", MODULE_PATH)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def _args(tmp_path, apply):
    return argparse.Namespace(apply=apply, journal_dir=tmp_path)


def test_apply_holds_global_lock_during_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "parse_args", lambda: _args(tmp_path, True))
    called = []

    def execute(args):
        called.append(True)
        assert (tmp_path / "aws-silver-tables.lock").exists()
        with pytest.raises(RuntimeError, match="locked"):
            cli.main()

    monkeypatch.setattr(cli, "execute", execute)
    cli.main()
    assert called == [True]
    assert not (tmp_path / "aws-silver-tables.lock").exists()


def test_lock_removed_after_failed_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "parse_args", lambda: _args(tmp_path, True))

    def fail(args):
        raise ValueError("simulated failure")

    monkeypatch.setattr(cli, "execute", fail)
    with pytest.raises(ValueError, match="simulated failure"):
        cli.main()
    assert not (tmp_path / "aws-silver-tables.lock").exists()


def test_dry_run_does_not_create_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "parse_args", lambda: _args(tmp_path, False))
    monkeypatch.setattr(cli, "execute", lambda args: None)
    cli.main()
    assert not (tmp_path / "aws-silver-tables.lock").exists()

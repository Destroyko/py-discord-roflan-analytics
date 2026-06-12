"""The CLI must translate failures into stable exit codes for schedulers."""

from __future__ import annotations

import pytest

from bot import cli
from bot.services.scanner import ScanStats


def _scan_failed():
    stats = ScanStats(
        run_id="r", channels_total=1, channels_failed=1, failed_channel_ids=[111]
    )
    from bot.pipeline import ScanFailedError

    return ScanFailedError(stats)


def test_run_returns_2_on_scan_failed(env_settings, monkeypatch):
    async def fake_run_pipeline(*args, **kwargs):
        raise _scan_failed()

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    assert cli.main(["run", "--year", "2026", "--month", "3"]) == 2


def test_run_returns_1_on_value_error(env_settings, monkeypatch):
    async def fake_run_pipeline(*args, **kwargs):
        raise ValueError("bad period")

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    assert cli.main(["run", "--year", "2026", "--month", "3"]) == 1


def test_run_returns_1_on_unexpected_error(env_settings, monkeypatch):
    async def fake_run_pipeline(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    assert cli.main(["run", "--year", "2026", "--month", "3"]) == 1


def test_run_returns_0_on_success(env_settings, monkeypatch):
    async def fake_run_pipeline(*args, **kwargs):
        return None

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    assert cli.main(["run", "--year", "2026", "--month", "3"]) == 0

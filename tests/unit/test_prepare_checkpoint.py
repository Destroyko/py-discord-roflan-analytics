"""Unit tests for the run-lock / resume logic in ``_prepare_checkpoint``."""

from __future__ import annotations

import pytest

from bot.pipeline import _prepare_checkpoint
from bot.services.scan_checkpoint import new_checkpoint, save_checkpoint


def _save(settings, run_id: str, phase: str):
    cp = new_checkpoint(
        run_id=run_id, guild_id=settings.guild_id, year=2026, month=1,
        channel_ids=settings.stats_channel_ids,
    )
    cp.phase = phase
    save_checkpoint(settings, cp)
    return cp


def test_resume_without_checkpoint_raises(settings):
    with pytest.raises(ValueError, match="Nothing to resume"):
        _prepare_checkpoint(settings, year=2026, month=1, resume=True)


def test_resume_committed_raises(settings):
    _save(settings, "r1", "committed")
    with pytest.raises(ValueError, match="already committed"):
        _prepare_checkpoint(settings, year=2026, month=1, resume=True)


def test_fresh_run_while_scanning_is_locked(settings):
    _save(settings, "r1", "scanning")
    with pytest.raises(RuntimeError, match="already in progress"):
        _prepare_checkpoint(settings, year=2026, month=1, resume=False)


def test_fresh_run_with_stale_checkpoint_returns_stale_id(settings):
    _save(settings, "old-run", "committed")
    checkpoint, run_id, stale_run_id = _prepare_checkpoint(
        settings, year=2026, month=1, resume=False
    )
    assert stale_run_id == "old-run"
    assert run_id != "old-run"
    assert checkpoint.phase == "scanning"


def test_fresh_run_without_checkpoint_has_no_stale(settings):
    checkpoint, run_id, stale_run_id = _prepare_checkpoint(
        settings, year=2026, month=1, resume=False
    )
    assert stale_run_id is None
    assert checkpoint.run_id == run_id


def test_resume_scanning_reuses_run(settings):
    _save(settings, "r1", "scanning")
    checkpoint, run_id, stale_run_id = _prepare_checkpoint(
        settings, year=2026, month=1, resume=True
    )
    assert run_id == "r1"
    assert stale_run_id is None
    assert checkpoint.phase == "scanning"

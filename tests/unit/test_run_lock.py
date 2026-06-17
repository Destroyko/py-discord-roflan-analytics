"""Tests for in-process pipeline run locking."""

from __future__ import annotations

import pytest

from bot.pipeline import PipelineBusyError, run_pipeline
from bot.services.run_lock import (
    release_memory_run,
    try_acquire_memory_run,
)


def test_memory_run_lock_rejects_duplicate():
    assert try_acquire_memory_run(1000, 2026, 5) is True
    assert try_acquire_memory_run(1000, 2026, 5) is False
    release_memory_run(1000, 2026, 5)
    assert try_acquire_memory_run(1000, 2026, 5) is True
    release_memory_run(1000, 2026, 5)


def test_memory_run_lock_allows_different_months():
    assert try_acquire_memory_run(1000, 2026, 5) is True
    assert try_acquire_memory_run(1000, 2026, 6) is True
    release_memory_run(1000, 2026, 5)
    release_memory_run(1000, 2026, 6)


@pytest.mark.asyncio
async def test_run_pipeline_raises_busy_when_period_locked(env_settings, monkeypatch):
    settings = env_settings
    assert try_acquire_memory_run(settings.guild_id, 2026, 4) is True
    try:
        with pytest.raises(PipelineBusyError, match="уже выполняется"):
            await run_pipeline(2026, 4, print_top=False)
    finally:
        release_memory_run(settings.guild_id, 2026, 4)

"""Monthly finalization scheduling and DB markers."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bot.database.db import Database
from bot.services.monthly_finalization import (
    mark_period_attempted,
    mark_period_finalized,
    pending_finalization_period,
)
from bot.utils.dates import finalization_deadline


@pytest.mark.asyncio
async def test_pending_none_before_deadline(env_settings):
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 7, 1, 9, 30, tzinfo=tz)
    async with Database(env_settings.database_path) as db:
        await db.init_db()
        assert await pending_finalization_period(db, env_settings, now=now) is None


@pytest.mark.asyncio
async def test_pending_returns_previous_month_after_deadline(env_settings):
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 7, 1, 11, 0, tzinfo=tz)
    async with Database(env_settings.database_path) as db:
        await db.init_db()
        assert await pending_finalization_period(db, env_settings, now=now) == (
            2026,
            6,
        )


@pytest.mark.asyncio
async def test_pending_none_after_attempted(env_settings):
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 7, 15, 12, 0, tzinfo=tz)
    async with Database(env_settings.database_path) as db:
        await db.init_db()
        await mark_period_attempted(
            db, env_settings, 2026, 6, "run-1", embed_posted=False
        )
        assert await pending_finalization_period(db, env_settings, now=now) is None


@pytest.mark.asyncio
async def test_mark_finalized_implies_finalized(env_settings):
    async with Database(env_settings.database_path) as db:
        await db.init_db()
        await mark_period_finalized(db, env_settings, 2026, 6, "run-1")
        guild = str(env_settings.guild_id)
        assert await db.is_month_finalized(guild, 2026, 6)
        assert await db.is_month_attempted(guild, 2026, 6)


def test_finalization_deadline_first_of_month_at_configured_time(env_settings):
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 7, 15, 12, 0, tzinfo=tz)
    deadline = finalization_deadline(now)
    assert deadline == datetime(2026, 7, 1, 10, 0, tzinfo=tz)

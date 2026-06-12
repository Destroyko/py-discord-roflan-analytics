"""The scheduler cog must survive pipeline failures and report them.

A crash in the monthly job must not propagate out of the ``tasks.loop`` callback
(which would otherwise stop the loop); instead it notifies the leaderboard
channel. The slash command must tell the invoker how to resume.

The cog module evaluates ``get_tz()`` at import time, so it is imported lazily
via fixtures that depend on ``env_settings`` (which populates the environment).
"""

from __future__ import annotations

import datetime as _dt
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.pipeline import ScanFailedError
from bot.services.scanner import ScanStats


class _Day1Datetime:
    @staticmethod
    def now(tz=None):
        return _dt.datetime(2026, 2, 1, 0, 5, tzinfo=tz)


class _Day2Datetime:
    @staticmethod
    def now(tz=None):
        return _dt.datetime(2026, 2, 2, 0, 5, tzinfo=tz)


def _scan_failed_stats() -> ScanStats:
    return ScanStats(
        run_id="r", channels_total=1, channels_failed=1, failed_channel_ids=[111]
    )


@pytest.fixture
def cogmod(env_settings):
    from bot.cogs import leaderboard

    return leaderboard


@pytest.fixture
def cog(cogmod):
    return cogmod.LeaderboardCog(bot=MagicMock())


async def test_monthly_notifies_on_scan_failed(cogmod, cog, monkeypatch):
    monkeypatch.setattr(cogmod, "datetime", _Day1Datetime)
    monkeypatch.setattr(
        cogmod, "run_pipeline",
        AsyncMock(side_effect=ScanFailedError(_scan_failed_stats())),
    )
    notify = AsyncMock()
    monkeypatch.setattr(cog, "_notify_failure", notify)

    await cogmod.LeaderboardCog.monthly_leaderboard.coro(cog)

    notify.assert_awaited_once()


async def test_monthly_survives_generic_error(cogmod, cog, monkeypatch):
    monkeypatch.setattr(cogmod, "datetime", _Day1Datetime)
    monkeypatch.setattr(
        cogmod, "run_pipeline", AsyncMock(side_effect=RuntimeError("boom"))
    )
    notify = AsyncMock()
    monkeypatch.setattr(cog, "_notify_failure", notify)

    # Must not raise: the loop has to stay alive for next month.
    await cogmod.LeaderboardCog.monthly_leaderboard.coro(cog)

    notify.assert_awaited_once()


async def test_monthly_skips_when_not_first_of_month(cogmod, cog, monkeypatch):
    monkeypatch.setattr(cogmod, "datetime", _Day2Datetime)
    run = AsyncMock()
    monkeypatch.setattr(cogmod, "run_pipeline", run)

    await cogmod.LeaderboardCog.monthly_leaderboard.coro(cog)

    run.assert_not_awaited()


async def test_recalculate_reports_resume_hint_on_scan_failed(cogmod, cog, monkeypatch):
    monkeypatch.setattr(cogmod, "_can_recalculate", lambda _interaction: True)
    monkeypatch.setattr(
        cogmod, "run_pipeline",
        AsyncMock(side_effect=ScanFailedError(_scan_failed_stats())),
    )

    interaction = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    interaction.followup.send = AsyncMock()

    await cogmod.LeaderboardCog.recalculate_leaderboard.callback(
        cog, interaction, 2026, 1
    )

    interaction.edit_original_response.assert_awaited()
    content = interaction.edit_original_response.call_args.kwargs["content"]
    assert "resume" in content.lower()

"""The scheduler cog must survive pipeline failures and report them.

A crash in the monthly job must not propagate out of the ``tasks.loop`` callback
(which would otherwise stop the loop); instead it notifies the leaderboard
channel.

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


def _slash_interaction():
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def test_recalculate_reports_resume_hint_on_scan_failed(cogmod, cog, monkeypatch):
    interaction = _slash_interaction()
    monkeypatch.setattr(cogmod, "_can_recalculate", lambda _i: True)
    monkeypatch.setattr(
        cogmod, "run_pipeline",
        AsyncMock(side_effect=ScanFailedError(_scan_failed_stats())),
    )
    monkeypatch.setattr(cogmod, "BotChannelReader", MagicMock())

    await cog.recalculate_leaderboard.callback(cog, interaction, 2026, 3)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.edit_original_response.assert_awaited_once()
    call = interaction.edit_original_response.await_args
    content = call.kwargs.get("content") or call.args[0]
    assert "resume" in content.lower()


async def test_recalculate_denies_without_role(cogmod, cog, monkeypatch):
    interaction = _slash_interaction()
    monkeypatch.setattr(cogmod, "_can_recalculate", lambda _i: False)

    await cog.recalculate_leaderboard.callback(cog, interaction, 2026, 3)

    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    interaction.response.defer.assert_not_awaited()


def test_can_recalculate_with_matching_role(cogmod, make_settings, monkeypatch):
    role = MagicMock()
    role.id = 42
    member = MagicMock(spec=["roles", "guild_permissions"])
    member.guild_permissions.administrator = False
    member.roles = [role]
    interaction = MagicMock(user=member)

    monkeypatch.setattr(
        cogmod,
        "get_settings",
        lambda: make_settings(manual_recalc_role_ids=frozenset({42})),
    )
    monkeypatch.setattr(cogmod.discord, "Member", type(member))

    assert cogmod._can_recalculate(interaction) is True


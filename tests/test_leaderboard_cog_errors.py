"""The scheduler cog must survive pipeline failures and report them."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from bot.database.db import Database
from bot.pipeline import PipelineResult, ScanFailedError
from bot.services.leaderboard_service import LeaderboardEntry
from bot.services.monthly_finalization import mark_period_attempted
from bot.services.scanner import ScanStats

_MOSCOW = ZoneInfo("Europe/Moscow")


def _scan_failed_stats() -> ScanStats:
    return ScanStats(
        run_id="r", channels_total=1, channels_failed=1, failed_channel_ids=[111]
    )


@pytest.fixture
def cogmod(env_settings):
    from bot.cogs import leaderboard

    return leaderboard


@pytest.fixture
def cog(cogmod, env_settings):
    object.__setattr__(env_settings, "role_durkichi_channel_id", 111)
    object.__setattr__(env_settings, "role_roflinkichi_channel_id", 222)
    return cogmod.LeaderboardCog(bot=MagicMock())


async def test_monthly_notifies_on_scan_failed(cogmod, cog, monkeypatch):
    monkeypatch.setattr(
        cogmod,
        "run_pipeline",
        AsyncMock(side_effect=ScanFailedError(_scan_failed_stats())),
    )
    monkeypatch.setattr(cogmod, "BotChannelReader", MagicMock())
    mark = AsyncMock()
    monkeypatch.setattr(cogmod, "mark_period_attempted", mark)
    notify = AsyncMock()
    monkeypatch.setattr(cog, "_notify_failure", notify)

    await cog._run_monthly_finalization(2026, 6)

    notify.assert_awaited_once()
    mark.assert_awaited_once()


async def test_monthly_survives_generic_error(cogmod, cog, monkeypatch):
    monkeypatch.setattr(
        cogmod, "run_pipeline", AsyncMock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(cogmod, "BotChannelReader", MagicMock())
    monkeypatch.setattr(cogmod, "should_resume_period", lambda *_a, **_k: False)
    mark = AsyncMock()
    monkeypatch.setattr(cogmod, "mark_period_attempted", mark)
    notify = AsyncMock()
    monkeypatch.setattr(cog, "_notify_failure", notify)

    await cog._run_monthly_finalization(2026, 6)

    notify.assert_awaited_once()
    mark.assert_awaited_once()


async def _pending_after_deadline(db, settings, *, now=None):
    from bot.services.monthly_finalization import pending_finalization_period

    fixed = datetime(2026, 7, 1, 11, 0, tzinfo=_MOSCOW)
    return await pending_finalization_period(db, settings, now=fixed)


async def test_startup_catchup_delegates_to_maybe_run(cogmod, cog, monkeypatch):
    cog.bot.wait_until_ready = AsyncMock()
    maybe = AsyncMock()
    monkeypatch.setattr(cog, "_maybe_run_monthly_finalization", maybe)

    await cog._startup_monthly_catchup()

    cog.bot.wait_until_ready.assert_awaited_once()
    maybe.assert_awaited_once_with(reason="catch-up")


async def test_catchup_runs_pending_period_after_deadline(cogmod, cog, monkeypatch):
    monkeypatch.setattr(cogmod, "pending_finalization_period", _pending_after_deadline)
    run = AsyncMock()
    monkeypatch.setattr(cog, "_run_monthly_finalization", run)

    await cog._maybe_run_monthly_finalization(reason="catch-up")

    run.assert_awaited_once_with(2026, 6)


async def test_watchdog_skips_when_period_already_attempted(
    cogmod, cog, env_settings, monkeypatch
):
    async with Database(env_settings.database_path) as db:
        await db.init_db()
        await mark_period_attempted(
            db, env_settings, 2026, 6, "run-1", embed_posted=False
        )

    monkeypatch.setattr(cogmod, "pending_finalization_period", _pending_after_deadline)
    run = AsyncMock()
    monkeypatch.setattr(cog, "_run_monthly_finalization", run)

    await cog._maybe_run_monthly_finalization(reason="watchdog")

    run.assert_not_awaited()


async def test_monthly_generic_error_skips_attempt_when_resume_possible(
    cogmod, cog, monkeypatch
):
    monkeypatch.setattr(
        cogmod, "run_pipeline", AsyncMock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(cogmod, "BotChannelReader", MagicMock())
    monkeypatch.setattr(cogmod, "should_resume_period", lambda *_a, **_k: True)
    mark = AsyncMock()
    monkeypatch.setattr(cogmod, "mark_period_attempted", mark)
    notify = AsyncMock()
    monkeypatch.setattr(cog, "_notify_failure", notify)

    await cog._run_monthly_finalization(2026, 6)

    notify.assert_not_awaited()
    mark.assert_not_awaited()


async def test_monthly_invalid_embed_config_marks_attempted_without_scan(
    cogmod, env_settings, monkeypatch
):
    object.__setattr__(env_settings, "role_durkichi_channel_id", None)
    object.__setattr__(env_settings, "role_roflinkichi_channel_id", None)
    cog = cogmod.LeaderboardCog(bot=MagicMock())

    pipeline = AsyncMock()
    monkeypatch.setattr(cogmod, "run_pipeline", pipeline)
    mark = AsyncMock()
    monkeypatch.setattr(cogmod, "mark_period_attempted", mark)

    await cog._run_monthly_finalization(2026, 6)

    pipeline.assert_not_awaited()
    mark.assert_awaited_once()


async def test_monthly_embed_fail_logs_attempt_without_discord_notify(
    cogmod, cog, monkeypatch
):
    result = PipelineResult(
        success=True,
        run_id="r",
        messages_matched=1,
        channels_completed=1,
        channels_skipped=0,
        channels_failed=0,
        top_entries=[LeaderboardEntry(rank=1, author_id="1", total_reactions=1)],
        embed_posted=False,
    )
    monkeypatch.setattr(cogmod, "run_pipeline", AsyncMock(return_value=result))
    monkeypatch.setattr(cogmod, "BotChannelReader", MagicMock())
    mark_attempt = AsyncMock()
    monkeypatch.setattr(cogmod, "mark_period_attempted", mark_attempt)
    mark_final = AsyncMock()
    monkeypatch.setattr(cogmod, "mark_period_finalized", mark_final)
    notify = AsyncMock()
    monkeypatch.setattr(cog, "_notify_failure", notify)

    await cog._run_monthly_finalization(2026, 6)

    notify.assert_not_awaited()
    mark_final.assert_not_awaited()
    mark_attempt.assert_awaited_once()


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
    assert "resume: да" in content.lower()
    assert "/recalculate_leaderboard" in content


async def test_recalculate_checkpoint_error_shown(cogmod, cog, monkeypatch):
    from bot.pipeline import CheckpointError

    interaction = _slash_interaction()
    monkeypatch.setattr(cogmod, "_can_recalculate", lambda _i: True)
    monkeypatch.setattr(
        cogmod,
        "run_pipeline",
        AsyncMock(
            side_effect=CheckpointError(
                "Нечего продолжать за **2026-05**: незавершённого скана нет."
            )
        ),
    )
    monkeypatch.setattr(cogmod, "BotChannelReader", MagicMock())

    await cog.recalculate_leaderboard.callback(
        cog, interaction, 2026, 5, False, False, True
    )

    content = interaction.edit_original_response.await_args.kwargs.get("content") or (
        interaction.edit_original_response.await_args.args[0]
    )
    assert "Нечего продолжать" in content


async def test_recalculate_busy_rejected(cogmod, cog, monkeypatch):
    from bot.pipeline import PipelineBusyError

    interaction = _slash_interaction()
    monkeypatch.setattr(cogmod, "_can_recalculate", lambda _i: True)
    monkeypatch.setattr(
        cogmod,
        "run_pipeline",
        AsyncMock(
            side_effect=PipelineBusyError(2026, 5),
        ),
    )
    monkeypatch.setattr(cogmod, "BotChannelReader", MagicMock())

    await cog.recalculate_leaderboard.callback(cog, interaction, 2026, 5)

    content = interaction.edit_original_response.await_args.kwargs.get("content") or (
        interaction.edit_original_response.await_args.args[0]
    )
    assert "уже выполняется" in content


async def test_recalculate_success_shows_embed_warning(cogmod, cog, monkeypatch):
    from bot.pipeline import PipelineResult
    from bot.services.leaderboard_service import LeaderboardEntry

    interaction = _slash_interaction()
    monkeypatch.setattr(cogmod, "_can_recalculate", lambda _i: True)
    result = PipelineResult(
        success=True,
        run_id="r",
        messages_matched=10,
        channels_completed=2,
        channels_skipped=0,
        channels_failed=0,
        top_entries=[LeaderboardEntry(rank=1, author_id="1", total_reactions=5)],
        warnings=["Не удалось опубликовать TOP в канал `123`: нет доступа."],
    )
    monkeypatch.setattr(cogmod, "run_pipeline", AsyncMock(return_value=result))
    monkeypatch.setattr(cogmod, "BotChannelReader", MagicMock())

    await cog.recalculate_leaderboard.callback(cog, interaction, 2026, 5)

    content = interaction.edit_original_response.await_args.kwargs.get("content") or (
        interaction.edit_original_response.await_args.args[0]
    )
    assert "Готово" in content
    assert "Внимание" in content
    assert "нет доступа" in content


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


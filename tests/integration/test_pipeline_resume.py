"""Integration tests for ``run_pipeline``: commit, fail-closed, and resume.

These drive the full orchestration with a ``FakeChannelReader`` and a real temp
database. They confirm the contract that matters operationally: a partial scan
never touches prod, and an interrupted run resumes to a clean commit.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.database.db import Database
from bot.pipeline import ScanFailedError, run_pipeline
from bot.services import discord_retry
from bot.services.scan_checkpoint import (
    checkpoint_path,
    load_checkpoint,
    new_checkpoint,
    save_checkpoint,
)
from bot.utils.dates import month_bounds_utc, to_db_timestamp
from tests.fakes.channel_reader import (
    FakeChannel,
    FakeChannelReader,
    FakeMessage,
    FakeReaction,
    make_http_exception,
)

YEAR, MONTH = 2026, 1


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    async def fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(discord_retry.asyncio, "sleep", fake_sleep)


def _msg(message_id: int, author_id: int) -> FakeMessage:
    return FakeMessage(
        message_id,
        author_id,
        reactions=[FakeReaction("EBALO", 3)],
        created_at=datetime(2026, 1, 15, 12, tzinfo=timezone.utc),
    )


def _healthy_reader() -> FakeChannelReader:
    return FakeChannelReader({
        111: FakeChannel(111, "a", messages=[_msg(1, 10)]),
        222: FakeChannel(222, "b", messages=[_msg(2, 11)]),
    })


async def _prod_count(settings) -> int:
    after, before = month_bounds_utc(YEAR, MONTH)
    async with Database(settings.database_path) as db:
        await db.init_db()
        rows = await db.get_leaderboard(
            str(settings.guild_id), to_db_timestamp(after), to_db_timestamp(before)
        )
    return sum(count for _, count in rows)


async def test_full_success_commits_and_clears_checkpoint(env_settings):
    settings = env_settings
    result = await run_pipeline(
        YEAR, MONTH, reader=_healthy_reader(), print_top=False
    )

    assert result.success is True
    assert result.channels_completed == 2
    assert result.messages_matched == 2
    assert not checkpoint_path(settings, YEAR, MONTH).exists()
    assert await _prod_count(settings) == 6  # two messages, 3 reactions each


async def test_partial_scan_leaves_prod_untouched(env_settings):
    settings = env_settings
    reader = FakeChannelReader({
        111: FakeChannel(111, "a", messages=[_msg(1, 10)]),
        222: FakeChannel(222, "broken", raise_exc=make_http_exception(503)),
    })

    with pytest.raises(ScanFailedError):
        await run_pipeline(YEAR, MONTH, reader=reader, print_top=False)

    # Nothing committed; checkpoint and staging kept for --resume.
    assert await _prod_count(settings) == 0
    assert checkpoint_path(settings, YEAR, MONTH).exists()
    checkpoint = load_checkpoint(settings, YEAR, MONTH)
    assert checkpoint is not None
    assert checkpoint.channel(222).status == "failed"
    async with Database(settings.database_path) as db:
        assert await db.count_staging_run(checkpoint.run_id) == 1


async def test_resume_after_failure_commits(env_settings):
    settings = env_settings
    failing = FakeChannelReader({
        111: FakeChannel(111, "a", messages=[_msg(1, 10)]),
        222: FakeChannel(222, "broken", raise_exc=make_http_exception(503)),
    })
    with pytest.raises(ScanFailedError):
        await run_pipeline(YEAR, MONTH, reader=failing, print_top=False)

    fixed = _healthy_reader()
    result = await run_pipeline(
        YEAR, MONTH, reader=fixed, print_top=False, resume=True
    )

    assert result.success is True
    assert 111 not in fixed.fetched  # already completed before the crash
    assert not checkpoint_path(settings, YEAR, MONTH).exists()
    assert await _prod_count(settings) == 6


async def test_ready_to_commit_with_empty_staging_fails(env_settings):
    settings = env_settings
    checkpoint = new_checkpoint(
        run_id="orphan", guild_id=settings.guild_id, year=YEAR, month=MONTH,
        channel_ids=settings.stats_channel_ids,
    )
    for cid in settings.stats_channel_ids:
        checkpoint.channel(cid).status = "completed"
    checkpoint.phase = "ready_to_commit"
    save_checkpoint(settings, checkpoint)

    # No staging rows exist for "orphan", so the resumed commit must refuse.
    with pytest.raises(ScanFailedError):
        await run_pipeline(YEAR, MONTH, print_top=False, resume=True)

"""Integration tests: per-channel isolation, strict/non-strict, resume, caps.

Uses a real (temp-file) SQLite database plus ``FakeChannelReader`` so the scan
loop, checkpoint writes and staging inserts run end to end without Discord.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.services import discord_retry
from bot.services.scan_checkpoint import new_checkpoint
from bot.services.scanner import scan_channels
from tests.fakes.channel_reader import (
    FakeChannel,
    FakeChannelReader,
    FakeMessage,
    FakeReaction,
)

AFTER = datetime(2026, 1, 1, tzinfo=timezone.utc)
BEFORE = datetime(2026, 2, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    async def fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(discord_retry.asyncio, "sleep", fake_sleep)


def _msg(message_id: int, author_id: int, count: int = 3) -> FakeMessage:
    return FakeMessage(
        message_id,
        author_id,
        reactions=[FakeReaction("EBALO", count)],
        created_at=datetime(2026, 1, 15, 12, tzinfo=timezone.utc),
    )


async def _run(reader, db, settings, *, checkpoint=None):
    if checkpoint is None:
        checkpoint = new_checkpoint(
            run_id="run-1", guild_id=settings.guild_id, year=2026, month=1,
            channel_ids=settings.stats_channel_ids,
        )
    stats = await scan_channels(
        reader,
        db,
        run_id=checkpoint.run_id,
        guild_id=settings.guild_id,
        channel_ids=settings.stats_channel_ids,
        after_utc=AFTER,
        before_utc=BEFORE,
        emoji_names=settings.emoji_names,
        settings=settings,
        checkpoint=checkpoint,
    )
    return stats, checkpoint


async def test_one_channel_fails_others_continue(db, make_settings):
    from tests.fakes.channel_reader import make_http_exception

    settings = make_settings()
    reader = FakeChannelReader({
        111: FakeChannel(111, "good", messages=[_msg(1, 10)]),
        222: FakeChannel(222, "broken", raise_exc=make_http_exception(503)),
    })

    stats, checkpoint = await _run(reader, db, settings)

    assert stats.success is False
    assert stats.channels_failed == 1
    assert stats.failed_channel_ids == [222]
    assert stats.channels_completed == 1
    # Only the healthy channel staged rows; the failed one blocks the commit.
    assert await db.count_staging_run("run-1") == 1
    assert checkpoint.channel(111).status == "completed"
    assert checkpoint.channel(222).status == "failed"


async def test_strict_unavailable_channel_marked_failed(db, make_settings):
    settings = make_settings(scan_strict_channels=True)
    reader = FakeChannelReader({
        111: FakeChannel(111, "good", messages=[_msg(1, 10)]),
        222: None,  # unavailable (403/404 resolved to None)
    })

    stats, checkpoint = await _run(reader, db, settings)

    assert stats.success is False
    assert checkpoint.channel(222).status == "failed"


async def test_nonstrict_unavailable_channel_skipped(db, make_settings):
    settings = make_settings(scan_strict_channels=False)
    reader = FakeChannelReader({
        111: FakeChannel(111, "good", messages=[_msg(1, 10)]),
        222: None,
    })

    stats, checkpoint = await _run(reader, db, settings)

    assert stats.success is True
    assert stats.channels_skipped == 1
    assert checkpoint.channel(222).status == "skipped"


async def test_incomplete_when_message_cap_hit(db, make_settings):
    settings = make_settings(scan_max_messages_per_channel=1, stats_channel_ids=[111])
    reader = FakeChannelReader({
        111: FakeChannel(111, "busy", messages=[_msg(1, 10), _msg(2, 11)]),
    })

    stats, checkpoint = await _run(reader, db, settings)

    assert stats.success is False
    assert stats.channels_incomplete == 1
    assert checkpoint.channel(111).status == "incomplete"


async def test_resume_skips_completed_channel(db, make_settings):
    settings = make_settings()
    checkpoint = new_checkpoint(
        run_id="run-1", guild_id=settings.guild_id, year=2026, month=1,
        channel_ids=settings.stats_channel_ids,
    )
    checkpoint.channel(111).status = "completed"
    checkpoint.channel(111).matched = 4

    reader = FakeChannelReader({
        111: FakeChannel(111, "already-done", messages=[_msg(1, 10)]),
        222: FakeChannel(222, "todo", messages=[_msg(2, 11)]),
    })

    stats, checkpoint = await _run(reader, db, settings, checkpoint=checkpoint)

    assert 111 not in reader.fetched  # completed channel never re-fetched
    assert 222 in reader.fetched
    assert stats.success is True
    assert checkpoint.channel(222).status == "completed"

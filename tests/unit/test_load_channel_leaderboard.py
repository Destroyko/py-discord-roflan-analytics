"""Per-channel leaderboard loader for /show_leaderboard."""

from __future__ import annotations

import pytest

from bot.database.db import Database, MessageRow
from bot.services.channel_top_service import (
    format_last_sync_footer,
    load_channel_leaderboard_for_period,
    load_channel_last_scanned_for_period,
)
from bot.services.leaderboard_service import format_embed_description


async def test_load_channel_leaderboard_top5(env_settings):
    settings = env_settings
    async with Database(settings.database_path) as db:
        await db.init_db()
        await db.upsert_messages(
            [
                MessageRow(
                    message_id="m1",
                    author_id="u1",
                    channel_id="111",
                    guild_id="1000",
                    created_at="2026-03-10 12:00:00",
                    reaction_count=10,
                    last_scanned_at="2026-03-10 12:00:00",
                ),
                MessageRow(
                    message_id="m2",
                    author_id="u2",
                    channel_id="111",
                    guild_id="1000",
                    created_at="2026-03-11 12:00:00",
                    reaction_count=20,
                    last_scanned_at="2026-03-11 12:00:00",
                ),
                MessageRow(
                    message_id="m3",
                    author_id="u3",
                    channel_id="222",
                    guild_id="1000",
                    created_at="2026-03-12 12:00:00",
                    reaction_count=99,
                    last_scanned_at="2026-03-12 12:00:00",
                ),
            ]
        )

    entries = await load_channel_leaderboard_for_period(2026, 3, 111, limit=5)

    assert len(entries) == 2
    assert entries[0].author_id == "u2"
    assert entries[0].total_reactions == 20
    assert entries[1].author_id == "u1"


async def test_load_channel_last_scanned_returns_max(env_settings):
    settings = env_settings
    async with Database(settings.database_path) as db:
        await db.init_db()
        await db.upsert_messages(
            [
                MessageRow(
                    message_id="m1",
                    author_id="u1",
                    channel_id="111",
                    guild_id="1000",
                    created_at="2026-03-10 12:00:00",
                    reaction_count=10,
                    last_scanned_at="2026-03-10 08:00:00",
                ),
                MessageRow(
                    message_id="m2",
                    author_id="u2",
                    channel_id="111",
                    guild_id="1000",
                    created_at="2026-03-11 12:00:00",
                    reaction_count=20,
                    last_scanned_at="2026-03-15 14:30:00",
                ),
            ]
        )

    last = await load_channel_last_scanned_for_period(2026, 3, 111)
    assert last == "2026-03-15 14:30:00"


def test_format_last_sync_footer_msk(env_settings):
    _ = env_settings
    text = format_last_sync_footer("2026-06-17 10:09:23")
    assert "17.06.2026" in text
    assert "МСК" in text
    assert "синхронизация:" in text


def test_format_last_sync_footer_missing(env_settings):
    _ = env_settings
    assert "не выполнялась" in format_last_sync_footer(None)


async def test_load_channel_leaderboard_rejects_unknown_channel(env_settings):
    with pytest.raises(ValueError, match="STATS_CHANNEL_IDS"):
        await load_channel_leaderboard_for_period(2026, 3, 999999)


def test_format_embed_includes_channel_label():
    from bot.services.leaderboard_service import LeaderboardEntry

    text = format_embed_description(
        [LeaderboardEntry(rank=1, author_id="1", total_reactions=3)],
        year=2026,
        month=3,
        tz_label="Europe/Moscow",
        emoji_names=frozenset({"EBALO"}),
        top_n=5,
        channel_label="#general",
    )
    assert "#general" in text
    assert "топ 5" in text


def test_format_embed_omits_header_when_requested():
    from bot.services.leaderboard_service import LeaderboardEntry

    text = format_embed_description(
        [LeaderboardEntry(rank=1, author_id="1", total_reactions=3)],
        year=2026,
        month=3,
        tz_label="Europe/Moscow",
        emoji_names=frozenset({"EBALO"}),
        top_n=5,
        channel_label="#general",
        include_header=False,
    )
    assert "Рейтинг" not in text
    assert "EBALO" not in text
    assert "<@1>" in text

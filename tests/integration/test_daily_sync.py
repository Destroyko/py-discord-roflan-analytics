"""Incremental daily sync updates SQLite without a full history re-scan."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.database.db import Database, MessageRow
from bot.services.daily_sync import run_daily_sync
from bot.utils.dates import month_bounds_utc, to_db_timestamp
from tests.fakes.channel_reader import FakeChannel, FakeChannelReader, FakeMessage, FakeReaction


@pytest.mark.asyncio
async def test_daily_sync_refreshes_updates_and_deletes(env_settings, monkeypatch):
    channel_id = 111
    monkeypatch.setenv("STATS_CHANNEL_IDS", str(channel_id))
    from bot.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    year, month = 2026, 1
    after_utc, before_utc = month_bounds_utc(year, month)
    after_db = to_db_timestamp(after_utc)
    before_db = to_db_timestamp(before_utc)
    guild_id_str = str(settings.guild_id)

    kept = FakeMessage(
        1001,
        42,
        channel_id=channel_id,
        reactions=[FakeReaction("EBALO", 3)],
        created_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
    )
    updated = FakeMessage(
        1002,
        43,
        channel_id=channel_id,
        reactions=[FakeReaction("EBALO", 9)],
        created_at=datetime(2026, 1, 11, tzinfo=timezone.utc),
    )
    new_msg = FakeMessage(
        1003,
        44,
        channel_id=channel_id,
        reactions=[FakeReaction("EBALO", 2)],
        created_at=datetime(2026, 1, 12, tzinfo=timezone.utc),
    )

    channel = FakeChannel(channel_id, "stats", messages=[kept, updated, new_msg])
    reader = FakeChannelReader({channel_id: channel})

    async with Database(settings.database_path) as db:
        await db.init_db()
        scanned_at = to_db_timestamp(datetime(2026, 1, 1, tzinfo=timezone.utc))
        await db.upsert_messages(
            [
                MessageRow(
                    message_id="1001",
                    author_id="42",
                    channel_id=str(channel_id),
                    guild_id=guild_id_str,
                    created_at=to_db_timestamp(kept.created_at),
                    reaction_count=3,
                    last_scanned_at=scanned_at,
                ),
                MessageRow(
                    message_id="1002",
                    author_id="43",
                    channel_id=str(channel_id),
                    guild_id=guild_id_str,
                    created_at=to_db_timestamp(updated.created_at),
                    reaction_count=1,
                    last_scanned_at=scanned_at,
                ),
                MessageRow(
                    message_id="9999",
                    author_id="99",
                    channel_id=str(channel_id),
                    guild_id=guild_id_str,
                    created_at=to_db_timestamp(updated.created_at),
                    reaction_count=5,
                    last_scanned_at=scanned_at,
                ),
            ]
        )

    stats = await run_daily_sync(year, month, reader=reader)

    assert stats.channels_synced == 1
    assert stats.deleted == 1
    assert stats.refreshed == 2
    assert set(channel.fetch_calls) == {1001, 1002, 9999}

    async with Database(settings.database_path) as db:
        rows = await db.list_message_ids_for_channel(
            guild_id_str, str(channel_id), after_db, before_db
        )

    assert set(rows) == {"1001", "1002", "1003"}

    async with Database(settings.database_path) as db:
        cursor = await db.connection.execute(
            "SELECT reaction_count FROM messages WHERE message_id = ?",
            ("1002",),
        )
        count = (await cursor.fetchone())[0]
        await cursor.close()

    assert int(count) == 9

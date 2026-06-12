"""Integration tests for ``commit_scan_run`` atomicity (the prod-data guard)."""

from __future__ import annotations

import pytest

from bot.database.db import MessageRow

GUILD = "1000"
AFTER = "2026-01-01 00:00:00"
BEFORE = "2026-02-01 00:00:00"
IN_PERIOD = "2026-01-15 12:00:00"
OUT_OF_PERIOD = "2025-12-15 12:00:00"


def _row(message_id: str, *, created_at: str, run_scope: str = "prod") -> MessageRow:
    return MessageRow(
        message_id=message_id,
        author_id="42",
        channel_id="111",
        guild_id=GUILD,
        created_at=created_at,
        reaction_count=5,
        last_scanned_at="2026-01-31 00:00:00",
    )


async def _messages_count(db) -> int:
    cursor = await db.connection.execute("SELECT COUNT(*) FROM messages")
    (count,) = await cursor.fetchone()
    await cursor.close()
    return count


async def test_commit_replaces_period_and_clears_staging(db):
    await db.upsert_messages([_row("old", created_at=IN_PERIOD)])
    await db.upsert_messages_staging(
        [_row("new", created_at=IN_PERIOD)], run_id="r1"
    )

    committed = await db.commit_scan_run(GUILD, AFTER, BEFORE, "r1")

    assert committed == 1
    leaderboard = await db.get_leaderboard(GUILD, AFTER, BEFORE)
    assert await _messages_count(db) == 1
    assert leaderboard == [("42", 5)]
    assert await db.count_staging_run("r1") == 0


async def test_commit_rolls_back_on_error(db):
    # A prod row outside the period with the same id a staged row will reuse.
    await db.upsert_messages([_row("dup", created_at=OUT_OF_PERIOD)])
    # A prod row inside the period that the commit will try to delete.
    await db.upsert_messages([_row("inperiod", created_at=IN_PERIOD)])
    # Staged row collides on PRIMARY KEY(message_id) with the out-of-period row.
    await db.upsert_messages_staging([_row("dup", created_at=IN_PERIOD)], run_id="r1")

    with pytest.raises(Exception):
        await db.commit_scan_run(GUILD, AFTER, BEFORE, "r1")

    # Rollback: the in-period row must survive and staging must be intact.
    cursor = await db.connection.execute(
        "SELECT message_id FROM messages ORDER BY message_id"
    )
    ids = [row[0] for row in await cursor.fetchall()]
    await cursor.close()
    assert ids == ["dup", "inperiod"]
    assert await db.count_staging_run("r1") == 1

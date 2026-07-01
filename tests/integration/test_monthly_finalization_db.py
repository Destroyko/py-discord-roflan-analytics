"""Integration tests for monthly_finalizations SQLite CRUD."""

from __future__ import annotations

from bot.services.monthly_finalization import mark_period_attempted, mark_period_finalized

GUILD = "1000"


async def test_attempted_without_embed_is_not_finalized(db, settings):
    await mark_period_attempted(db, settings, 2026, 6, "run-1", embed_posted=False)

    assert await db.is_month_attempted(GUILD, 2026, 6)
    assert not await db.is_month_finalized(GUILD, 2026, 6)


async def test_finalized_implies_attempted(db, settings):
    await mark_period_finalized(db, settings, 2026, 6, "run-2")

    assert await db.is_month_attempted(GUILD, 2026, 6)
    assert await db.is_month_finalized(GUILD, 2026, 6)


async def test_attempt_then_finalize_upgrades_row(db, settings):
    await mark_period_attempted(db, settings, 2026, 6, "run-a", embed_posted=False)
    await mark_period_finalized(db, settings, 2026, 6, "run-b")

    assert await db.is_month_finalized(GUILD, 2026, 6)

    cursor = await db.connection.execute(
        """
        SELECT run_id, embed_posted, finalized_at
        FROM monthly_finalizations
        WHERE guild_id = ? AND year = ? AND month = ?
        """,
        (GUILD, 2026, 6),
    )
    row = await cursor.fetchone()
    await cursor.close()

    assert row is not None
    assert row[0] == "run-b"
    assert row[1] == 1
    assert row[2] is not None


async def test_mark_attempted_is_idempotent_per_period(db, settings):
    await mark_period_attempted(db, settings, 2026, 6, "run-1", embed_posted=False)
    await mark_period_attempted(db, settings, 2026, 6, "run-2", embed_posted=False)

    cursor = await db.connection.execute(
        "SELECT COUNT(*) FROM monthly_finalizations WHERE guild_id = ?",
        (GUILD,),
    )
    (count,) = await cursor.fetchone()
    await cursor.close()

    assert count == 1

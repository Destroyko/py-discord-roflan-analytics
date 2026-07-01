"""Monthly auto-finalization: pending period, resume, DB markers."""

from __future__ import annotations

from datetime import datetime, timezone

from bot.config import Settings
from bot.database.db import Database
from bot.services.scan_checkpoint import load_checkpoint
from bot.utils.dates import finalization_deadline, previous_calendar_month, to_db_timestamp

_SCAN_RESUMABLE_PHASES = frozenset({"scanning", "ready_to_commit"})


def should_resume_period(settings: Settings, year: int, month: int) -> bool:
    """True when an in-progress checkpoint can be resumed for this period."""
    checkpoint = load_checkpoint(settings, year, month)
    if checkpoint is None:
        return False
    return checkpoint.phase in _SCAN_RESUMABLE_PHASES


async def pending_finalization_period(
    db: Database,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> tuple[int, int] | None:
    """Return (year, month) to auto-finalize, or None if nothing is due."""
    from bot.utils.dates import get_tz

    tz = get_tz()
    if now is None:
        now = datetime.now(tz=tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    if now < finalization_deadline(now):
        return None

    year, month = previous_calendar_month(now)
    guild_id = str(settings.guild_id)
    if await db.is_month_attempted(guild_id, year, month):
        return None
    return year, month


def utc_now_db() -> str:
    return to_db_timestamp(datetime.now(tz=timezone.utc))


async def mark_period_attempted(
    db: Database,
    settings: Settings,
    year: int,
    month: int,
    run_id: str | None,
    *,
    embed_posted: bool,
) -> None:
    ts = utc_now_db()
    await db.mark_month_attempted(
        str(settings.guild_id),
        year,
        month,
        run_id,
        embed_posted=embed_posted,
        attempted_at=ts,
    )


async def mark_period_finalized(
    db: Database,
    settings: Settings,
    year: int,
    month: int,
    run_id: str,
) -> None:
    ts = utc_now_db()
    await db.mark_month_finalized(
        str(settings.guild_id),
        year,
        month,
        run_id,
        attempted_at=ts,
        finalized_at=ts,
    )

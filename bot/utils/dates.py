"""Month boundary helpers.

Boundaries are computed in the configured leaderboard timezone (Europe/Moscow by
default) and exposed both as timezone-aware datetimes and as UTC, so the scanner
and the database agree on the half-open interval ``[after, before)``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bot.config import get_settings

# Discord launched in 2015; reject obviously bogus years.
MIN_YEAR = 2015

_DB_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_tz() -> ZoneInfo:
    """Return the configured leaderboard timezone."""
    return ZoneInfo(get_settings().timezone)


def validate_period(year: int, month: int) -> None:
    """Raise ``ValueError`` if the (year, month) pair is out of range."""
    if not 1 <= month <= 12:
        raise ValueError(f"month must be between 1 and 12, got {month}")
    current_year = datetime.now(tz=timezone.utc).year
    if not MIN_YEAR <= year <= current_year + 1:
        raise ValueError(
            f"year must be between {MIN_YEAR} and {current_year + 1}, got {year}"
        )


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """Return the half-open month interval in the configured timezone."""
    tz = get_tz()
    start = datetime(year, month, 1, tzinfo=tz)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=tz)
    else:
        end = datetime(year, month + 1, 1, tzinfo=tz)
    return start, end


def month_bounds_utc(year: int, month: int) -> tuple[datetime, datetime]:
    """Return the same month interval converted to UTC."""
    start, end = month_bounds(year, month)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def previous_calendar_month(
    now: datetime | None = None,
) -> tuple[int, int]:
    """Return (year, month) for the calendar month before ``now`` in leaderboard TZ."""
    if now is None:
        now = datetime.now(tz=get_tz())
    elif now.tzinfo is None:
        now = now.replace(tzinfo=get_tz())
    else:
        now = now.astimezone(get_tz())

    first_of_current = datetime(now.year, now.month, 1, tzinfo=now.tzinfo)
    last_of_previous = first_of_current - timedelta(days=1)
    return last_of_previous.year, last_of_previous.month


def next_monthly_run_at(
    *,
    day: int = 1,
    hour: int = 0,
    minute: int = 5,
    now: datetime | None = None,
) -> datetime:
    """Next scheduled run at ``day`` 00:05 (configurable) in leaderboard timezone."""
    tz = get_tz()
    if now is None:
        now = datetime.now(tz=tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    candidate = datetime(now.year, now.month, day, hour, minute, tzinfo=tz)
    if now >= candidate:
        if now.month == 12:
            candidate = datetime(now.year + 1, 1, day, hour, minute, tzinfo=tz)
        else:
            candidate = datetime(
                now.year, now.month + 1, day, hour, minute, tzinfo=tz
            )
    return candidate


def to_db_timestamp(dt: datetime) -> str:
    """Serialize a datetime to the UTC string format used in SQLite.

    The fixed-width format keeps lexicographic ordering aligned with chronological
    ordering, so range comparisons in SQL work on plain TEXT columns.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(_DB_TIMESTAMP_FORMAT)

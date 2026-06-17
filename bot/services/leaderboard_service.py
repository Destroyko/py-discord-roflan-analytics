"""Build leaderboard entries from stored messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from bot.config import get_settings
from bot.database.db import Database
from bot.utils.dates import month_bounds_utc, to_db_timestamp, validate_period


def format_emoji_label(emoji_names: frozenset[str]) -> str:
    """Display configured emojis, e.g. ``:EBALO:, :ROFL:``."""
    return ", ".join(f":{name}:" for name in sorted(emoji_names))


@dataclass(frozen=True)
class LeaderboardEntry:
    """A single ranked row of the leaderboard."""

    rank: int
    author_id: str
    total_reactions: int


async def build_leaderboard(
    db: Database,
    *,
    guild_id: int,
    after: str,
    before: str,
    limit: int | None = None,
    excluded_user_ids: frozenset[str] | None = None,
) -> list[LeaderboardEntry]:
    """Return ranked leaderboard entries for the period."""
    if excluded_user_ids is None:
        excluded_user_ids = get_settings().excluded_user_ids
    rows = await db.get_leaderboard(
        str(guild_id),
        after,
        before,
        limit=limit,
        excluded_user_ids=excluded_user_ids,
    )
    return [
        LeaderboardEntry(rank=index, author_id=author_id, total_reactions=total)
        for index, (author_id, total) in enumerate(rows, start=1)
    ]


async def load_leaderboard_for_period(
    year: int,
    month: int,
    *,
    limit: int | None = None,
) -> list[LeaderboardEntry]:
    """Load leaderboard rows from SQLite for a calendar month (no Discord scan)."""
    validate_period(year, month)
    settings = get_settings()
    after_utc, before_utc = month_bounds_utc(year, month)
    after_db = to_db_timestamp(after_utc)
    before_db = to_db_timestamp(before_utc)

    async with Database(settings.database_path) as db:
        return await build_leaderboard(
            db,
            guild_id=settings.guild_id,
            after=after_db,
            before=before_db,
            limit=limit,
        )


def format_console_top(
    entries: list[LeaderboardEntry],
    *,
    year: int,
    month: int,
    tz_label: str,
    emoji_names: frozenset[str],
    top_n: int = 10,
) -> str:
    """Render a short TOP-N summary for the terminal."""
    header = (
        f"Рейтинг {year}-{month:02d} ({tz_label}), "
        f"эмодзи {format_emoji_label(emoji_names)}, топ {top_n}"
    )
    if not entries:
        return f"{header}\n  (за этот период реакций нет)"

    lines = [header]
    for entry in entries[:top_n]:
        lines.append(
            f"  {entry.rank}. пользователь {entry.author_id} — "
            f"{entry.total_reactions} реакций"
        )
    return "\n".join(lines)


def period_label(after: datetime, before: datetime) -> str:
    """Human-readable period bounds, e.g. for report metadata."""
    return f"{after.isoformat()} .. {before.isoformat()}"


def format_embed_description(
    entries: list[LeaderboardEntry],
    *,
    year: int,
    month: int,
    tz_label: str,
    emoji_names: frozenset[str],
    top_n: int,
    channel_label: str | None = None,
    include_header: bool = True,
) -> str:
    """Render TOP-N lines for a Discord embed description (max 4096 chars)."""
    empty = "_За этот период реакций не найдено._"
    if not entries:
        if not include_header:
            return empty
        scope = f" · {channel_label}" if channel_label else ""
        header = (
            f"**Рейтинг {year}-{month:02d}** ({tz_label}){scope}\n"
            f"Эмодзи {format_emoji_label(emoji_names)} · топ {top_n}\n\n"
        )
        return header + empty

    lines: list[str] = []
    if include_header:
        scope = f" · {channel_label}" if channel_label else ""
        lines.append(
            f"**Рейтинг {year}-{month:02d}** ({tz_label}){scope}\n"
            f"Эмодзи {format_emoji_label(emoji_names)} · топ {top_n}\n"
        )
    for entry in entries[:top_n]:
        lines.append(
            f"**{entry.rank}.** <@{entry.author_id}> — "
            f"{entry.total_reactions} реакций"
        )
    text = "\n".join(lines)
    if len(text) > 4000:
        return text[:3997] + "..."
    return text

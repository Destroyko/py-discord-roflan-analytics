"""Per-channel leaderboard from stored messages."""

from __future__ import annotations

from dataclasses import dataclass

from bot.config import get_settings
from bot.database.db import Database
from bot.services.leaderboard_service import LeaderboardEntry, format_emoji_label
from bot.utils.dates import month_bounds_utc, to_db_timestamp, validate_period
from bot.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ChannelTop:
    """TOP-N for a single stats channel."""

    channel_id: int
    entries: list[LeaderboardEntry]


async def build_channel_tops(
    db: Database,
    *,
    guild_id: int,
    channel_ids: list[int],
    after: str,
    before: str,
    top_n: int,
    excluded_user_ids: frozenset[str] | None = None,
) -> list[ChannelTop]:
    if excluded_user_ids is None:
        excluded_user_ids = get_settings().excluded_user_ids
    guild_id_str = str(guild_id)
    result: list[ChannelTop] = []
    for channel_id in channel_ids:
        rows = await db.get_leaderboard_for_channel(
            guild_id_str,
            str(channel_id),
            after,
            before,
            limit=top_n,
            excluded_user_ids=excluded_user_ids,
        )
        entries = [
            LeaderboardEntry(rank=index, author_id=author_id, total_reactions=total)
            for index, (author_id, total) in enumerate(rows, start=1)
        ]
        result.append(ChannelTop(channel_id=channel_id, entries=entries))
    return result


async def load_channel_leaderboard_for_period(
    year: int,
    month: int,
    channel_id: int,
    *,
    limit: int | None = 5,
    excluded_user_ids: frozenset[str] | None = None,
) -> list[LeaderboardEntry]:
    """Load TOP-N for one stats channel and calendar month (no Discord scan).

    ``limit=None`` returns the full channel ranking (for skip-ahead winner picks).
    """
    validate_period(year, month)
    settings = get_settings()
    if excluded_user_ids is None:
        excluded_user_ids = settings.excluded_user_ids
    if channel_id not in settings.stats_channel_ids:
        raise ValueError(
            f"Channel {channel_id} is not in STATS_CHANNEL_IDS; "
            "pick a configured stats channel."
        )
    after_utc, before_utc = month_bounds_utc(year, month)
    after_db = to_db_timestamp(after_utc)
    before_db = to_db_timestamp(before_utc)

    async with Database(settings.database_path) as db:
        rows = await db.get_leaderboard_for_channel(
            str(settings.guild_id),
            str(channel_id),
            after_db,
            before_db,
            limit=limit,
            excluded_user_ids=excluded_user_ids,
        )
    return [
        LeaderboardEntry(rank=index, author_id=author_id, total_reactions=total)
        for index, (author_id, total) in enumerate(rows, start=1)
    ]


def format_console_channel_tops(
    channel_tops: list[ChannelTop],
    *,
    year: int,
    month: int,
    tz_label: str,
    emoji_names: frozenset[str],
    top_n: int,
) -> str:
    header = (
        f"Per-channel TOP {top_n} for {year}-{month:02d} ({tz_label}), "
        f"emoji {format_emoji_label(emoji_names)}"
    )
    lines = [header]
    for block in channel_tops:
        lines.append("")
        lines.append(f"--- Channel {block.channel_id} ---")
        if not block.entries:
            lines.append("  (no data for this channel in the selected period)")
            continue
        for entry in block.entries:
            lines.append(
                f"  {entry.rank}. user {entry.author_id} - "
                f"{entry.total_reactions} reactions"
            )
    return "\n".join(lines)

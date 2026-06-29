"""Per-channel leaderboard from stored messages."""

from __future__ import annotations

from dataclasses import dataclass

from bot.config import Settings, get_settings
from bot.database.db import Database
from bot.services.leaderboard_service import LeaderboardEntry, format_emoji_label
from bot.utils.dates import (
    format_db_timestamp_local,
    local_timezone_short_label,
    month_bounds_utc,
    to_db_timestamp,
    validate_period,
)
from bot.utils.logger import get_logger

logger = get_logger(__name__)

LEADERBOARD_POST_SECTION_DURKICHI = "Дуркичи"
LEADERBOARD_POST_SECTION_ROFLINKICHI = "Рофлинкичи"


@dataclass(frozen=True)
class ChannelTop:
    """TOP-N for a single stats channel."""

    channel_id: int
    entries: list[LeaderboardEntry]


@dataclass(frozen=True)
class NamedChannelTop:
    """TOP-N for a stats channel with a display title."""

    title: str
    channel_id: int
    entries: list[LeaderboardEntry]


async def load_leaderboard_post_channel_tops(
    year: int,
    month: int,
    *,
    settings: Settings | None = None,
) -> list[NamedChannelTop]:
    """TOP per durkichi/roflinkichi channel for ``LEADERBOARD_CHANNEL_ID`` posts."""
    cfg = settings or get_settings()
    cfg.validate_leaderboard_post_channel_settings()
    top_n = cfg.leaderboard_channel_top_n
    durkichi_id = cfg.role_durkichi_channel_id
    roflinkichi_id = cfg.role_roflinkichi_channel_id
    assert durkichi_id is not None
    assert roflinkichi_id is not None

    durkichi = await load_channel_leaderboard_for_period(
        year,
        month,
        durkichi_id,
        limit=top_n,
        excluded_user_ids=cfg.excluded_user_ids,
    )
    roflinkichi = await load_channel_leaderboard_for_period(
        year,
        month,
        roflinkichi_id,
        limit=top_n,
        excluded_user_ids=cfg.excluded_user_ids,
    )
    return [
        NamedChannelTop(
            title=LEADERBOARD_POST_SECTION_DURKICHI,
            channel_id=durkichi_id,
            entries=durkichi,
        ),
        NamedChannelTop(
            title=LEADERBOARD_POST_SECTION_ROFLINKICHI,
            channel_id=roflinkichi_id,
            entries=roflinkichi,
        ),
    ]


def format_named_channel_tops_embed(
    channel_tops: list[NamedChannelTop],
    *,
    year: int,
    month: int,
    tz_label: str,
    emoji_names: frozenset[str],
    top_n: int,
) -> str:
    """Render per-channel TOP blocks for a Discord embed description."""
    header = (
        f"**Рейтинг {year}-{month:02d}** ({tz_label})\n"
        f"Эмодзи {format_emoji_label(emoji_names)} · топ {top_n} по каналу"
    )
    blocks = [header]
    empty = "_За этот период реакций не найдено._"
    for section in channel_tops:
        blocks.append("")
        blocks.append(f"**{section.title}** (<#{section.channel_id}>)")
        if not section.entries:
            blocks.append(empty)
            continue
        for entry in section.entries[:top_n]:
            blocks.append(
                f"**{entry.rank}.** <@{entry.author_id}> — "
                f"{entry.total_reactions} реакций"
            )
    text = "\n".join(blocks)
    if len(text) > 4000:
        return text[:3997] + "..."
    return text


def format_named_channel_tops_console(
    channel_tops: list[NamedChannelTop],
    *,
    year: int,
    month: int,
    tz_label: str,
    emoji_names: frozenset[str],
    top_n: int,
) -> str:
    """Plain-text TOP summary for CLI and ephemeral slash responses."""
    header = (
        f"Рейтинг {year}-{month:02d} ({tz_label}), "
        f"эмодзи {format_emoji_label(emoji_names)}, топ {top_n} по каналу"
    )
    lines = [header]
    for section in channel_tops:
        lines.append("")
        lines.append(f"{section.title} (канал {section.channel_id}):")
        if not section.entries:
            lines.append("  (за этот период реакций нет)")
            continue
        for entry in section.entries[:top_n]:
            lines.append(
                f"  {entry.rank}. пользователь {entry.author_id} — "
                f"{entry.total_reactions} реакций"
            )
    return "\n".join(lines)


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


async def load_channel_last_scanned_for_period(
    year: int,
    month: int,
    channel_id: int,
) -> str | None:
    """Return latest ``last_scanned_at`` (UTC DB string) for channel + month."""
    validate_period(year, month)
    settings = get_settings()
    if channel_id not in settings.stats_channel_ids:
        raise ValueError(
            f"Channel {channel_id} is not in STATS_CHANNEL_IDS; "
            "pick a configured stats channel."
        )
    after_utc, before_utc = month_bounds_utc(year, month)
    after_db = to_db_timestamp(after_utc)
    before_db = to_db_timestamp(before_utc)

    async with Database(settings.database_path) as db:
        return await db.get_max_last_scanned_at_for_channel(
            str(settings.guild_id),
            str(channel_id),
            after_db,
            before_db,
        )


def format_last_sync_footer(last_scanned_db: str | None) -> str:
    """Embed footer: SQLite source + last Discord sync time in local TZ."""
    local_time = format_db_timestamp_local(last_scanned_db)
    if local_time is None:
        return "Из SQLite · синхронизация с Discord не выполнялась"
    tz_label = local_timezone_short_label()
    return f"Из SQLite · синхронизация: {local_time} ({tz_label})"


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

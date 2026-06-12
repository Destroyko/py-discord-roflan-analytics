"""Print TOP-N leaderboard per stats channel."""

from __future__ import annotations

from bot.config import get_settings
from bot.database.db import Database
from bot.services.channel_top_service import (
    build_channel_tops,
    format_console_channel_tops,
)
from bot.utils.dates import month_bounds_utc, to_db_timestamp, validate_period


async def run_channel_tops(
    year: int,
    month: int,
    *,
    channel_id: int | None = None,
) -> None:
    validate_period(year, month)
    settings = get_settings()

    channel_ids = settings.stats_channel_ids
    if channel_id is not None:
        if channel_id not in channel_ids:
            raise ValueError(
                f"channel-id {channel_id} is not in STATS_CHANNEL_IDS: {channel_ids}"
            )
        channel_ids = [channel_id]

    after_utc, before_utc = month_bounds_utc(year, month)
    after_db = to_db_timestamp(after_utc)
    before_db = to_db_timestamp(before_utc)

    async with Database(settings.database_path) as db:
        await db.init_db()
        channel_tops = await build_channel_tops(
            db,
            guild_id=settings.guild_id,
            channel_ids=channel_ids,
            after=after_db,
            before=before_db,
            top_n=settings.top_n,
        )

    print(
        format_console_channel_tops(
            channel_tops,
            year=year,
            month=month,
            tz_label=settings.timezone,
            emoji_names=settings.emoji_names,
            top_n=settings.top_n,
        )
    )

    if len(channel_ids) > 1:
        print()
        print(
            "(Guild-wide TOP across all scanned channels: `verify` or "
            "/show_leaderboard per channel.)"
        )

"""Offline audit of stored data for a month (no Discord API calls)."""

from __future__ import annotations

from bot.config import get_settings
from bot.database.db import Database
from bot.services.leaderboard_service import (
    build_leaderboard,
    format_console_top,
    format_emoji_label,
)
from bot.services.user_messages_service import (
    build_user_message_rows,
    format_console_user_messages,
)
from bot.utils.dates import month_bounds, month_bounds_utc, to_db_timestamp, validate_period


async def run_verify(year: int, month: int, user_id: str | None = None) -> None:
    """Print period bounds, DB counters and TOP-N from SQLite."""
    validate_period(year, month)
    settings = get_settings()

    after_local, before_local = month_bounds(year, month)
    after_utc, before_utc = month_bounds_utc(year, month)
    after_db = to_db_timestamp(after_utc)
    before_db = to_db_timestamp(before_utc)
    guild_id = str(settings.guild_id)

    print("=== Period (configured timezone) ===")
    print(f"  timezone: {settings.timezone}")
    print(f"  after:    {after_local.isoformat()}")
    print(f"  before:   {before_local.isoformat()}  (exclusive)")
    print(f"  UTC after/before: {after_db} .. {before_db}")
    print(f"  emoji:    {format_emoji_label(settings.emoji_names)}")
    print(f"  channels in config: {settings.stats_channel_ids}")
    print()

    async with Database(settings.database_path) as db:
        await db.init_db()
        audit = await db.get_period_audit(guild_id, after_db, before_db)
        entries = await build_leaderboard(
            db,
            guild_id=settings.guild_id,
            after=after_db,
            before=before_db,
        )

    print("=== Database summary (this period only) ===")
    print(f"  messages stored:     {audit['message_count']}")
    print(f"  unique authors:      {audit['author_count']}")
    print(f"  channels with data:  {audit['channel_count']}")
    print(f"  sum(reaction_count): {audit['total_reactions']}")
    print(f"  created_at range:    {audit['min_created_at']} .. {audit['max_created_at']}")
    print(f"  reaction_count min/max: {audit['min_reaction_count']} / {audit['max_reaction_count']}")
    print()

    print("=== Sanity checks ===")
    ok = True
    if audit["zero_reaction_rows"]:
        print(f"  FAIL: {audit['zero_reaction_rows']} rows with reaction_count <= 0")
        ok = False
    else:
        print("  OK: no rows with zero reactions")

    if audit["rows_outside_period"]:
        print(
            f"  WARN: {audit['rows_outside_period']} rows for this guild "
            "outside the selected month (other runs / old data)"
        )
    else:
        print("  OK: no other rows for this guild outside the month window")

    if audit["message_count"] == 0:
        print("  WARN: no messages in DB for this period (re-run scan?)")
        ok = False

    expected_channels = set(str(c) for c in settings.stats_channel_ids)
    print(
        f"  INFO: config lists {len(expected_channels)} channel(s); "
        f"DB has data in {audit['channel_count']} channel(s)"
    )
    print(f"  Overall: {'looks consistent' if ok else 'review warnings above'}")
    print()

    print(
        format_console_top(
            entries,
            year=year,
            month=month,
            tz_label=settings.timezone,
            emoji_names=settings.emoji_names,
            top_n=settings.top_n,
        )
    )

    spot_author = user_id or (entries[0].author_id if entries else None)
    if spot_author:
        print()
        label = (
            f"Messages for user {spot_author}"
            if user_id
            else f"Spot-check: rank 1 author {spot_author}"
        )
        print(f"=== {label} ===")
        async with Database(settings.database_path) as db:
            await db.init_db()
            raw = await db.get_messages_by_author(
                guild_id,
                after_db,
                before_db,
                str(spot_author),
                limit=None if user_id else 5,
            )
        rows = build_user_message_rows(raw, guild_id=settings.guild_id)
        print(
            format_console_user_messages(
                str(spot_author),
                rows,
                year=year,
                month=month,
                emoji_names=settings.emoji_names,
            )
        )

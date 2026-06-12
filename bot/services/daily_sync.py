"""Incremental daily sync: refresh known messages, discover new, purge deleted."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

import discord

from bot.client import ChannelReader, create_bot_http_reader
from bot.config import Settings, get_settings
from bot.database.db import Database, MessageRow
from bot.services.discord_retry import retry_discord
from bot.services.scanner import message_row_if_tracked
from bot.services.sync_state import load_sync_state, save_sync_state
from bot.utils.dates import month_bounds_utc, to_db_timestamp, validate_period
from bot.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class DailySyncStats:
    """Counters for one incremental sync run."""

    year: int
    month: int
    channels_total: int = 0
    channels_synced: int = 0
    channels_failed: int = 0
    refreshed: int = 0
    deleted: int = 0
    upserted: int = 0
    failed_channel_ids: list[int] = field(default_factory=list)


async def run_daily_sync(
    year: int,
    month: int,
    *,
    reader: ChannelReader | None = None,
) -> DailySyncStats:
    """Refresh the SQLite period for ``year``/``month`` without a full re-scan."""
    validate_period(year, month)
    settings = get_settings()
    after_utc, before_utc = month_bounds_utc(year, month)
    after_db = to_db_timestamp(after_utc)
    before_db = to_db_timestamp(before_utc)
    guild_id_str = str(settings.guild_id)

    stats = DailySyncStats(
        year=year,
        month=month,
        channels_total=len(settings.stats_channel_ids),
    )

    logger.info(
        "Daily sync for %s-%02d (%s channels).",
        year,
        month,
        stats.channels_total,
    )

    sync_state = load_sync_state(settings, year, month)

    async with Database(settings.database_path) as db:
        await db.init_db()

        if reader is not None:
            await _sync_all_channels(
                reader,
                db,
                settings,
                sync_state,
                stats,
                guild_id_str=guild_id_str,
                after_db=after_db,
                before_db=before_db,
                after_utc=after_utc,
                before_utc=before_utc,
            )
        else:
            async with create_bot_http_reader(settings) as http_reader:
                await _sync_all_channels(
                    http_reader,
                    db,
                    settings,
                    sync_state,
                    stats,
                    guild_id_str=guild_id_str,
                    after_db=after_db,
                    before_db=before_db,
                    after_utc=after_utc,
                    before_utc=before_utc,
                )

    save_sync_state(settings, sync_state)

    logger.info(
        "Daily sync done %s-%02d: refreshed=%s deleted=%s upserted=%s "
        "channels=%s/%s failed=%s",
        year,
        month,
        stats.refreshed,
        stats.deleted,
        stats.upserted,
        stats.channels_synced,
        stats.channels_total,
        stats.channels_failed,
    )
    return stats


async def _sync_all_channels(
    reader: ChannelReader,
    db: Database,
    settings: Settings,
    sync_state,
    stats: DailySyncStats,
    *,
    guild_id_str: str,
    after_db: str,
    before_db: str,
    after_utc: datetime,
    before_utc: datetime,
) -> None:
    for channel_id in settings.stats_channel_ids:
        channel = await reader.fetch_text_channel(channel_id)
        if channel is None:
            stats.channels_failed += 1
            stats.failed_channel_ids.append(channel_id)
            logger.warning("Daily sync: channel %s unavailable.", channel_id)
            continue

        try:
            refreshed, deleted, upserted = await _sync_single_channel(
                channel,
                db,
                settings,
                sync_state,
                guild_id_str=guild_id_str,
                after_db=after_db,
                before_db=before_db,
                after_utc=after_utc,
                before_utc=before_utc,
            )
            stats.refreshed += refreshed
            stats.deleted += deleted
            stats.upserted += upserted
            stats.channels_synced += 1
        except (discord.HTTPException, OSError) as exc:
            stats.channels_failed += 1
            stats.failed_channel_ids.append(channel_id)
            logger.error("Daily sync failed for channel %s: %s", channel_id, exc)

        if settings.scan_channel_delay_sec > 0:
            await asyncio.sleep(settings.scan_channel_delay_sec)


async def _sync_single_channel(
    channel: discord.TextChannel,
    db: Database,
    settings: Settings,
    sync_state,
    *,
    guild_id_str: str,
    after_db: str,
    before_db: str,
    after_utc: datetime,
    before_utc: datetime,
) -> tuple[int, int, int]:
    """Return ``(refreshed, deleted, upserted)`` for one channel."""
    channel_id_str = str(channel.id)
    scanned_at = to_db_timestamp(datetime.now(tz=timezone.utc))
    emoji_names = settings.emoji_names
    excluded = settings.excluded_user_ids

    known_ids = await db.list_message_ids_for_channel(
        guild_id_str, channel_id_str, after_db, before_db
    )

    to_delete: list[str] = []
    to_upsert: list[MessageRow] = []
    refreshed = 0

    for index, message_id in enumerate(known_ids, start=1):
        try:
            message = await retry_discord(
                lambda mid=int(message_id): channel.fetch_message(mid),
                max_attempts=settings.scan_retry_max_attempts,
                label=f"fetch:{message_id}",
            )
        except discord.NotFound:
            to_delete.append(message_id)
            continue

        row = message_row_if_tracked(
            message,
            guild_id_str=guild_id_str,
            emoji_names=emoji_names,
            excluded_user_ids=excluded,
            scanned_at=scanned_at,
        )
        if row is None:
            to_delete.append(message_id)
        else:
            to_upsert.append(row)
            refreshed += 1

        if (
            settings.daily_sync_message_delay_sec > 0
            and index % settings.daily_sync_fetch_batch_size == 0
        ):
            await asyncio.sleep(settings.daily_sync_message_delay_sec)

    deleted = await db.delete_messages_by_ids(to_delete)
    await _upsert_batches(db, to_upsert, settings.scan_batch_size)

    cursor = sync_state.channel(channel.id)
    history_after: datetime | discord.Object
    if cursor.last_message_id is not None:
        history_after = discord.Object(id=cursor.last_message_id)
    else:
        history_after = after_utc

    new_rows: list[MessageRow] = []
    new_from_history = 0
    newest_id = cursor.last_message_id

    async for message in channel.history(
        limit=None,
        after=history_after,
        before=before_utc,
        oldest_first=True,
    ):
        row = message_row_if_tracked(
            message,
            guild_id_str=guild_id_str,
            emoji_names=emoji_names,
            excluded_user_ids=excluded,
            scanned_at=scanned_at,
        )
        if row is None:
            continue
        new_rows.append(row)
        new_from_history += 1
        if newest_id is None or message.id > newest_id:
            newest_id = message.id

        if len(new_rows) >= settings.scan_batch_size:
            await _upsert_batches(db, new_rows, settings.scan_batch_size)
            new_rows.clear()

    if new_rows:
        await _upsert_batches(db, new_rows, settings.scan_batch_size)

    if newest_id is not None:
        cursor.last_message_id = newest_id

    logger.info(
        "Daily sync #%s: refreshed=%s deleted=%s new_from_history=%s.",
        channel.name,
        refreshed,
        deleted,
        new_from_history,
    )
    return refreshed, deleted, refreshed + new_from_history


async def _upsert_batches(
    db: Database, rows: list[MessageRow], batch_size: int
) -> None:
    for offset in range(0, len(rows), batch_size):
        await db.upsert_messages(rows[offset : offset + batch_size])

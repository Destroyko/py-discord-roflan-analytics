"""Scan channel history into staging with per-channel isolation (Block B).

Scanned rows are written to ``messages_staging`` under a ``run_id``; the pipeline
commits them into ``messages`` in one transaction only after a fully successful
run. A failure on one channel is isolated (logged, recorded) so the rest of the
run continues, but it blocks the commit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

import discord

from bot.client import ChannelReader
from bot.config import Settings
from bot.database.db import Database, MessageRow
from bot.services.discord_retry import retry_discord
from bot.services.scan_checkpoint import ScanCheckpoint, save_checkpoint
from bot.utils.dates import to_db_timestamp
from bot.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ScanProgressEvent:
    """Progress snapshot emitted while scanning a channel."""

    channel_index: int
    channels_total: int
    channel_name: str
    messages_seen: int
    messages_matched: int


ScanProgressCallback = Callable[[ScanProgressEvent], Awaitable[None]]


@dataclass
class ScanStats:
    """Aggregate counters for a completed scan."""

    run_id: str
    channels_total: int
    channels_completed: int = 0
    channels_skipped: int = 0
    channels_failed: int = 0
    channels_incomplete: int = 0
    messages_matched: int = 0
    failed_channel_ids: list[int] = field(default_factory=list)
    incomplete_channel_ids: list[int] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """True only if every channel is accounted for with no failures.

        Completed channels (plus skipped ones when strict mode is off) must cover
        the whole configured set, and there must be no failed/incomplete channels.
        """
        if self.channels_failed or self.channels_incomplete:
            return False
        return (
            self.channels_completed + self.channels_skipped == self.channels_total
        )


@dataclass
class _ChannelScanResult:
    matched: int
    messages_seen: int
    incomplete: bool


def _reaction_emoji_name(reaction: discord.Reaction) -> str | None:
    emoji = reaction.emoji
    name = getattr(emoji, "name", None)
    if name is None and isinstance(emoji, str):
        return emoji
    return name


def count_emoji_reactions(
    reactions: list[discord.Reaction], emoji_names: frozenset[str]
) -> int:
    """Sum reaction counts for all configured emojis on one message.

    If both :EBALO: and :ROFL: are configured and present, counts are added
    (e.g. 5 + 6 = 11). Other reactions are ignored.
    """
    total = 0
    for reaction in reactions:
        name = _reaction_emoji_name(reaction)
        if name is not None and name in emoji_names:
            total += reaction.count
    return total


async def scan_channels(
    reader: ChannelReader,
    db: Database,
    *,
    run_id: str,
    guild_id: int,
    channel_ids: list[int],
    after_utc: datetime,
    before_utc: datetime,
    emoji_names: frozenset[str],
    settings: Settings,
    checkpoint: ScanCheckpoint,
    on_progress: ScanProgressCallback | None = None,
) -> ScanStats:
    """Scan each channel's history for the period into ``messages_staging``.

    Channels already marked ``completed`` in the checkpoint (a resumed run) are
    skipped. Each channel is wrapped in retry + isolation: a permanent failure is
    recorded and the loop continues, but it leaves the run non-committable.
    """
    import asyncio

    guild_id_str = str(guild_id)
    total = len(channel_ids)

    for index, channel_id in enumerate(channel_ids, start=1):
        state = checkpoint.channel(channel_id)
        if state.status == "completed":
            logger.info("Resume: channel %s already completed; skipping.", channel_id)
            continue

        state.status = "in_progress"
        save_checkpoint(settings, checkpoint)

        channel = await reader.fetch_text_channel(channel_id)
        if channel is None:
            if settings.scan_strict_channels:
                state.status = "failed"
                state.error = "channel unavailable (strict mode)"
                logger.error("Channel %s unavailable; strict mode → failed.", channel_id)
            else:
                state.status = "skipped"
                logger.warning("Channel %s unavailable; skipped (non-strict).", channel_id)
            save_checkpoint(settings, checkpoint)
            continue

        logger.info(
            "Scanning channel %s/%s: #%s (%s)",
            index,
            total,
            channel.name,
            channel_id,
        )

        try:
            result = await retry_discord(
                lambda ch=channel: _scan_single_channel(
                    db,
                    ch,
                    run_id=run_id,
                    guild_id_str=guild_id_str,
                    after_utc=after_utc,
                    before_utc=before_utc,
                    emoji_names=emoji_names,
                    settings=settings,
                    channel_index=index,
                    channels_total=total,
                    on_progress=on_progress,
                ),
                max_attempts=settings.scan_retry_max_attempts,
                label=f"channel:{channel_id}",
            )
        except (discord.HTTPException, OSError) as exc:
            state.status = "failed"
            state.error = str(exc)
            logger.error("Channel %s failed permanently: %s", channel_id, exc)
            save_checkpoint(settings, checkpoint)
            continue

        state.matched = result.matched
        state.messages_seen = result.messages_seen
        if result.incomplete:
            state.status = "incomplete"
            state.error = (
                f"hit SCAN_MAX_MESSAGES_PER_CHANNEL="
                f"{settings.scan_max_messages_per_channel}"
            )
            logger.error(
                "Channel %s incomplete: reached message cap %s.",
                channel_id,
                settings.scan_max_messages_per_channel,
            )
        else:
            state.status = "completed"
            state.error = None
        save_checkpoint(settings, checkpoint)

        if settings.scan_channel_delay_sec > 0:
            await asyncio.sleep(settings.scan_channel_delay_sec)

    return stats_from_checkpoint(run_id, channel_ids, checkpoint)


def stats_from_checkpoint(
    run_id: str, channel_ids: list[int], checkpoint: ScanCheckpoint
) -> ScanStats:
    """Derive aggregate ``ScanStats`` from per-channel checkpoint states."""
    stats = ScanStats(run_id=run_id, channels_total=len(channel_ids))
    for channel_id in channel_ids:
        state = checkpoint.channel(channel_id)
        if state.status == "completed":
            stats.channels_completed += 1
            stats.messages_matched += state.matched
        elif state.status == "skipped":
            stats.channels_skipped += 1
        elif state.status == "incomplete":
            stats.channels_incomplete += 1
            stats.incomplete_channel_ids.append(channel_id)
        else:
            stats.channels_failed += 1
            stats.failed_channel_ids.append(channel_id)
    return stats


async def _scan_single_channel(
    db: Database,
    channel: discord.TextChannel,
    *,
    run_id: str,
    guild_id_str: str,
    after_utc: datetime,
    before_utc: datetime,
    emoji_names: frozenset[str],
    settings: Settings,
    channel_index: int,
    channels_total: int,
    on_progress: ScanProgressCallback | None,
) -> _ChannelScanResult:
    """Scan one channel's history into staging.

    Idempotent: re-running the same channel (resume or retry) overwrites its own
    staged rows by ``(message_id, run_id)``. Returns matched/seen counts and
    whether the message cap was hit (incomplete).
    """
    scanned_at = to_db_timestamp(datetime.now(tz=timezone.utc))
    batch: list[MessageRow] = []
    matched = 0
    messages_seen = 0
    incomplete = False
    cap = settings.scan_max_messages_per_channel

    async for message in channel.history(
        limit=None, after=after_utc, before=before_utc, oldest_first=True
    ):
        messages_seen += 1
        if cap and messages_seen > cap:
            incomplete = True
            break

        if message.author.bot or message.is_system():
            continue

        if str(message.author.id) in settings.excluded_user_ids:
            continue

        reaction_count = count_emoji_reactions(message.reactions, emoji_names)
        if reaction_count == 0:
            continue

        batch.append(
            MessageRow(
                message_id=str(message.id),
                author_id=str(message.author.id),
                channel_id=str(channel.id),
                guild_id=guild_id_str,
                created_at=to_db_timestamp(message.created_at),
                reaction_count=reaction_count,
                last_scanned_at=scanned_at,
            )
        )
        matched += 1

        if len(batch) >= settings.scan_batch_size:
            await db.upsert_messages_staging(batch, run_id)
            batch.clear()

        if on_progress is not None and messages_seen % settings.scan_progress_every == 0:
            await on_progress(
                ScanProgressEvent(
                    channel_index=channel_index,
                    channels_total=channels_total,
                    channel_name=channel.name,
                    messages_seen=messages_seen,
                    messages_matched=matched,
                )
            )

    if batch:
        await db.upsert_messages_staging(batch, run_id)

    logger.info(
        "Channel #%s: %s matching messages (%s seen%s).",
        channel.name,
        matched,
        messages_seen,
        ", INCOMPLETE" if incomplete else "",
    )
    return _ChannelScanResult(
        matched=matched, messages_seen=messages_seen, incomplete=incomplete
    )

"""End-to-end run: scan to staging -> commit -> leaderboard -> optional Rofler roles.

Block B: scanning writes to ``messages_staging`` under a ``run_id``; the period
in prod ``messages`` is replaced in a single transaction (`commit_scan_run`) only
after a strictly successful run. A failed/partial run leaves prod untouched and
keeps a checkpoint so the run can be resumed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import discord
from discord.ext import commands

from bot.client import ChannelReader, create_bot_http_reader
from bot.config import Settings, get_settings
from bot.database.db import Database
from bot.services import role_service
from bot.services.leaderboard_service import (
    LeaderboardEntry,
    build_leaderboard,
    format_console_top,
    format_embed_description,
)
from bot.services.scan_checkpoint import (
    ScanCheckpoint,
    clear_checkpoint,
    load_checkpoint,
    new_checkpoint,
    save_checkpoint,
)
from bot.services.scanner import (
    ScanProgressCallback,
    ScanStats,
    scan_channels,
    stats_from_checkpoint,
)
from bot.utils.dates import month_bounds, month_bounds_utc, to_db_timestamp, validate_period
from bot.utils.logger import get_logger

logger = get_logger(__name__)


class ScanFailedError(RuntimeError):
    """Raised when a scan did not complete every channel, so no commit happened.

    Prod ``messages`` is unchanged; the staging rows and checkpoint are kept so
    the run can be resumed.
    """

    def __init__(self, stats: ScanStats) -> None:
        self.stats = stats
        failed = ", ".join(str(c) for c in stats.failed_channel_ids) or "-"
        incomplete = ", ".join(str(c) for c in stats.incomplete_channel_ids) or "-"
        super().__init__(
            f"Scan incomplete (run {stats.run_id}): "
            f"failed channels [{failed}], incomplete [{incomplete}]. "
            f"Prod data unchanged; resume with --resume."
        )


@dataclass
class PipelineResult:
    """Outcome of a single pipeline run."""

    success: bool
    run_id: str
    messages_matched: int
    channels_completed: int
    channels_skipped: int
    channels_failed: int
    top_entries: list[LeaderboardEntry]
    failed_channel_ids: list[int] = field(default_factory=list)
    incomplete_channel_ids: list[int] = field(default_factory=list)


async def _post_leaderboard_embed(
    bot: commands.Bot,
    settings: Settings,
    *,
    year: int,
    month: int,
    entries: list[LeaderboardEntry],
) -> None:
    channel_id = settings.leaderboard_channel_id
    if channel_id is None:
        logger.warning(
            "post_embed requested but LEADERBOARD_CHANNEL_ID is not set; skipping."
        )
        return

    channel = await bot.fetch_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        logger.warning(
            "LEADERBOARD_CHANNEL_ID %s is not a text channel; skipping embed.",
            channel_id,
        )
        return

    description = format_embed_description(
        entries,
        year=year,
        month=month,
        tz_label=settings.timezone,
        emoji_names=settings.emoji_names,
        top_n=settings.top_n,
    )
    embed = discord.Embed(
        title=f"Leaderboard {year}-{month:02d}",
        description=description,
        colour=discord.Colour.blue(),
    )
    embed.set_footer(text=f"SQLite: {settings.database_path}")
    await channel.send(embed=embed)
    logger.info("Posted leaderboard embed to channel %s.", channel_id)


def _prepare_checkpoint(
    settings: Settings,
    *,
    year: int,
    month: int,
    resume: bool,
) -> tuple[ScanCheckpoint, str, int | None]:
    """Resolve the checkpoint to use, enforcing the single-run lock.

    Returns ``(checkpoint, run_id, stale_run_id_to_discard)``. The third value is
    a staging ``run_id`` that must be discarded before scanning (a stale run that
    we are overwriting); it is ``None`` when there is nothing to clean up.
    """
    existing = load_checkpoint(settings, year, month)

    if resume:
        if existing is None:
            raise ValueError(
                f"Nothing to resume for {year}-{month:02d}: no checkpoint found."
            )
        if existing.phase == "committed":
            raise ValueError(
                f"Run for {year}-{month:02d} already committed; "
                "delete the checkpoint to scan again."
            )
        logger.info(
            "Resuming run %s for %s-%02d (phase=%s).",
            existing.run_id,
            year,
            month,
            existing.phase,
        )
        return existing, existing.run_id, None

    if existing is not None and existing.phase in ("scanning", "ready_to_commit"):
        raise RuntimeError(
            f"A scan for {year}-{month:02d} is already in progress "
            f"(run {existing.run_id}, phase {existing.phase}). "
            "Resume it with --resume, or delete the checkpoint to start over."
        )

    stale_run_id = existing.run_id if existing is not None else None
    checkpoint = new_checkpoint(
        run_id=uuid.uuid4().hex,
        guild_id=settings.guild_id,
        year=year,
        month=month,
        channel_ids=settings.stats_channel_ids,
    )
    return checkpoint, checkpoint.run_id, stale_run_id


async def run_pipeline(
    year: int,
    month: int,
    *,
    reader: ChannelReader | None = None,
    post_embed: bool = False,
    assign_roles: bool = False,
    bot: commands.Bot | None = None,
    print_top: bool = True,
    resume: bool = False,
    on_progress: ScanProgressCallback | None = None,
) -> PipelineResult:
    """Run the full leaderboard pipeline for the given month."""
    validate_period(year, month)
    settings = get_settings()

    after_local, before_local = month_bounds(year, month)
    after_utc, before_utc = month_bounds_utc(year, month)
    after_db = to_db_timestamp(after_utc)
    before_db = to_db_timestamp(before_utc)

    logger.info(
        "Running pipeline for %s-%02d (%s): %s .. %s",
        year,
        month,
        settings.timezone,
        after_local.isoformat(),
        before_local.isoformat(),
    )

    checkpoint, run_id, stale_run_id = _prepare_checkpoint(
        settings, year=year, month=month, resume=resume
    )

    async with Database(settings.database_path) as db:
        await db.init_db()

        if stale_run_id is not None:
            await db.discard_staging_run(stale_run_id)
        save_checkpoint(settings, checkpoint)

        if checkpoint.phase == "ready_to_commit":
            stats = stats_from_checkpoint(
                run_id, settings.stats_channel_ids, checkpoint
            )
            if await db.count_staging_run(run_id) == 0:
                raise ScanFailedError(stats)
        else:
            if reader is not None:
                stats = await _scan(
                    reader, db, settings, run_id, after_utc, before_utc,
                    checkpoint, on_progress,
                )
            else:
                async with create_bot_http_reader(settings) as http_reader:
                    stats = await _scan(
                        http_reader, db, settings, run_id, after_utc, before_utc,
                        checkpoint, on_progress,
                    )

            if not stats.success:
                # Keep staging + checkpoint for --resume; prod is untouched.
                raise ScanFailedError(stats)

            checkpoint.phase = "ready_to_commit"
            save_checkpoint(settings, checkpoint)

        await db.commit_scan_run(str(settings.guild_id), after_db, before_db, run_id)
        clear_checkpoint(settings, year, month)

        entries = await build_leaderboard(
            db,
            guild_id=settings.guild_id,
            after=after_db,
            before=before_db,
        )

    top_entries = entries[: settings.top_n]

    if print_top:
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

    if assign_roles:
        if bot is None:
            raise ValueError("assign_roles=True requires a running bot instance")
        await role_service.run_rofler_role_reassignment(
            bot,
            year=year,
            month=month,
        )

    if post_embed:
        if bot is None:
            raise ValueError("post_embed=True requires a running bot instance")
        await _post_leaderboard_embed(
            bot,
            settings,
            year=year,
            month=month,
            entries=entries,
        )

    report_note = str(settings.database_path)
    logger.info(
        "Done: %s matched messages across %s channels (%s skipped). Stored: %s",
        stats.messages_matched,
        stats.channels_completed,
        stats.channels_skipped,
        report_note,
    )

    return PipelineResult(
        success=True,
        run_id=run_id,
        messages_matched=stats.messages_matched,
        channels_completed=stats.channels_completed,
        channels_skipped=stats.channels_skipped,
        channels_failed=stats.channels_failed,
        top_entries=top_entries,
        failed_channel_ids=list(stats.failed_channel_ids),
        incomplete_channel_ids=list(stats.incomplete_channel_ids),
    )


async def _scan(
    reader: ChannelReader,
    db: Database,
    settings: Settings,
    run_id: str,
    after_utc,
    before_utc,
    checkpoint: ScanCheckpoint,
    on_progress: ScanProgressCallback | None,
) -> ScanStats:
    return await scan_channels(
        reader,
        db,
        run_id=run_id,
        guild_id=settings.guild_id,
        channel_ids=settings.stats_channel_ids,
        after_utc=after_utc,
        before_utc=before_utc,
        emoji_names=settings.emoji_names,
        settings=settings,
        checkpoint=checkpoint,
        on_progress=on_progress,
    )

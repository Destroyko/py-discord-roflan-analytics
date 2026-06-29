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
from bot.services.channel_top_service import (
    NamedChannelTop,
    format_named_channel_tops_embed,
    load_leaderboard_post_channel_tops,
)
from bot.services.leaderboard_service import (
    LeaderboardEntry,
    build_leaderboard,
    format_console_top,
)
from bot.services.run_lock import (
    PipelineBusyError,
    release_memory_run,
    scan_busy_message,
    try_acquire_memory_run,
)
from bot.services.scan_checkpoint import (
    ScanCheckpoint,
    CheckpointBusy,
    claim_checkpoint,
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


class CheckpointError(Exception):
    """Preflight failure: resume lock, nothing to resume, etc."""

    def __init__(self, user_message: str) -> None:
        self.user_message = user_message
        super().__init__(user_message)


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
    channel_post_tops: list[NamedChannelTop] = field(default_factory=list)
    failed_channel_ids: list[int] = field(default_factory=list)
    incomplete_channel_ids: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _embed_channel_error(channel_id: int, exc: Exception) -> str:
    """Plain-Russian warning when LEADERBOARD_CHANNEL_ID cannot be posted to."""
    if isinstance(exc, discord.Forbidden):
        return (
            f"Не удалось опубликовать TOP в канал `{channel_id}` "
            f"(LEADERBOARD_CHANNEL_ID): у бота нет доступа. "
            "Проверьте ID канала и права (просмотр канала, отправка сообщений)."
        )
    if isinstance(exc, discord.NotFound):
        return (
            f"Канал `{channel_id}` (LEADERBOARD_CHANNEL_ID) не найден. "
            "Проверьте ID в `.env`."
        )
    if isinstance(exc, discord.HTTPException):
        detail = exc.text or str(exc)
        return (
            f"Не удалось опубликовать TOP в канал `{channel_id}`: {detail}"
        )
    return f"Не удалось опубликовать TOP в канал `{channel_id}`: {exc}"


async def _post_leaderboard_embed(
    bot: commands.Bot,
    settings: Settings,
    *,
    year: int,
    month: int,
    channel_tops: list[NamedChannelTop],
) -> str | None:
    """Post per-channel TOP embed to ``LEADERBOARD_CHANNEL_ID``."""
    channel_id = settings.leaderboard_channel_id
    if channel_id is None:
        logger.warning(
            "post_embed requested but LEADERBOARD_CHANNEL_ID is not set; skipping."
        )
        return (
            "Публикация TOP пропущена: `LEADERBOARD_CHANNEL_ID` не задан в `.env`."
        )

    try:
        settings.validate_leaderboard_post_channel_settings()
    except ValueError as exc:
        logger.warning("Leaderboard post channel config invalid: %s", exc)
        return f"Публикация TOP пропущена: {exc}"

    try:
        channel = await bot.fetch_channel(channel_id)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
        logger.warning("Cannot fetch LEADERBOARD_CHANNEL_ID %s: %s", channel_id, exc)
        return _embed_channel_error(channel_id, exc)

    if not isinstance(channel, discord.TextChannel):
        logger.warning(
            "LEADERBOARD_CHANNEL_ID %s is not a text channel; skipping embed.",
            channel_id,
        )
        return (
            f"Канал `{channel_id}` (LEADERBOARD_CHANNEL_ID) не текстовый — "
            "embed не отправлен."
        )

    description = format_named_channel_tops_embed(
        channel_tops,
        year=year,
        month=month,
        tz_label=settings.timezone,
        emoji_names=settings.emoji_names,
        top_n=settings.leaderboard_channel_top_n,
    )
    embed = discord.Embed(
        title=f"Рейтинг {year}-{month:02d}",
        description=description,
        colour=discord.Colour.blue(),
    )
    embed.set_footer(text="Источник: SQLite")
    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
        logger.warning("Cannot post embed to channel %s: %s", channel_id, exc)
        return _embed_channel_error(channel_id, exc)

    logger.info("Posted leaderboard embed to channel %s.", channel_id)
    return None


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
            raise CheckpointError(
                f"Нечего продолжать за **{year}-{month:02d}**: незавершённого скана нет "
                "(пересчёт уже завершён или ещё не начинался).\n"
                f"Запустите **/recalculate_leaderboard** с тем же годом и месяцем "
                "без **resume**."
            )
        if existing.phase == "committed":
            raise CheckpointError(
                f"Пересчёт за **{year}-{month:02d}** уже завершён.\n"
                "Чтобы сканировать заново, удалите чекпоинт на сервере "
                "или обратитесь к администратору."
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
        raise CheckpointError(scan_busy_message(year, month))

    stale_run_id = existing.run_id if existing is not None else None
    checkpoint = new_checkpoint(
        run_id=uuid.uuid4().hex,
        guild_id=settings.guild_id,
        year=year,
        month=month,
        channel_ids=settings.stats_channel_ids,
    )
    _claim_new_checkpoint(settings, checkpoint)
    return checkpoint, checkpoint.run_id, stale_run_id


def _claim_new_checkpoint(settings: Settings, checkpoint: ScanCheckpoint) -> None:
    try:
        claim_checkpoint(settings, checkpoint)
    except CheckpointBusy:
        raise CheckpointError(
            scan_busy_message(checkpoint.year, checkpoint.month)
        ) from None


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

    if not try_acquire_memory_run(settings.guild_id, year, month):
        raise PipelineBusyError(year, month)

    try:
        return await _run_pipeline_body(
            year,
            month,
            settings=settings,
            reader=reader,
            post_embed=post_embed,
            assign_roles=assign_roles,
            bot=bot,
            print_top=print_top,
            resume=resume,
            on_progress=on_progress,
        )
    finally:
        release_memory_run(settings.guild_id, year, month)


async def _run_pipeline_body(
    year: int,
    month: int,
    *,
    settings: Settings,
    reader: ChannelReader | None = None,
    post_embed: bool = False,
    assign_roles: bool = False,
    bot: commands.Bot | None = None,
    print_top: bool = True,
    resume: bool = False,
    on_progress: ScanProgressCallback | None = None,
) -> PipelineResult:

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

    channel_post_tops: list[NamedChannelTop] = []
    try:
        settings.validate_leaderboard_post_channel_settings()
        channel_post_tops = await load_leaderboard_post_channel_tops(
            year, month, settings=settings
        )
    except ValueError:
        pass

    warnings: list[str] = []
    if post_embed:
        if bot is None:
            raise ValueError("post_embed=True requires a running bot instance")
        if not channel_post_tops:
            warning = (
                "Публикация TOP пропущена: задайте "
                "`ROLE_DURKICHI_CHANNEL_ID` и `ROLE_ROFLINKICHI_CHANNEL_ID` "
                "(оба в `STATS_CHANNEL_IDS`)."
            )
            logger.warning(warning)
            warnings.append(warning)
        else:
            embed_warning = await _post_leaderboard_embed(
                bot,
                settings,
                year=year,
                month=month,
                channel_tops=channel_post_tops,
            )
            if embed_warning is not None:
                warnings.append(embed_warning)

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
        channel_post_tops=channel_post_tops,
        failed_channel_ids=list(stats.failed_channel_ids),
        incomplete_channel_ids=list(stats.incomplete_channel_ids),
        warnings=warnings,
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

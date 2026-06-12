"""Slash command and monthly scheduler for the leaderboard pipeline."""

from __future__ import annotations

import asyncio
import time as _time
from datetime import datetime, time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.client import BotChannelReader
from bot.config import get_settings
from bot.pipeline import PipelineResult, ScanFailedError, run_pipeline
from bot.services.channel_top_service import load_channel_leaderboard_for_period
from bot.services.leaderboard_service import (
    format_console_top,
    format_embed_description,
)
from bot.utils.dates import validate_period
from bot.services.scanner import ScanProgressCallback, ScanProgressEvent
from bot.utils.dates import get_tz, next_monthly_run_at, previous_calendar_month
from bot.utils.logger import get_logger

logger = get_logger(__name__)

_PROGRESS_THROTTLE_SEC = 7.0


def _can_recalculate(interaction: discord.Interaction) -> bool:
    user = interaction.user
    if not isinstance(user, discord.Member):
        return False
    if user.guild_permissions.administrator:
        return True
    role_id = get_settings().manual_recalc_role_id
    if role_id is None:
        return False
    return any(role.id == role_id for role in user.roles)


def _make_progress_editor(
    interaction: discord.Interaction,
) -> ScanProgressCallback:
    """Return a throttled progress callback editing the deferred response."""
    last_edit = 0.0

    async def on_progress(event: ScanProgressEvent) -> None:
        nonlocal last_edit
        now = _time.monotonic()
        if now - last_edit < _PROGRESS_THROTTLE_SEC:
            return
        last_edit = now
        try:
            await interaction.edit_original_response(
                content=(
                    f"Scanning channel {event.channel_index}/{event.channels_total} "
                    f"#{event.channel_name} — {event.messages_seen} msgs, "
                    f"{event.messages_matched} matched…"
                )
            )
        except discord.HTTPException:
            pass

    return on_progress


_SHOW_LEADERBOARD_TOP_N = 5


class LeaderboardCog(commands.Cog):
    """Leaderboard slash commands and the monthly auto-run."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        if not self.monthly_leaderboard.is_running():
            self.monthly_leaderboard.start()

    async def cog_unload(self) -> None:
        self.monthly_leaderboard.cancel()

    @app_commands.command(
        name="recalculate_leaderboard",
        description="Rescan Discord and rebuild the monthly reaction leaderboard.",
    )
    @app_commands.describe(
        year="Calendar year, e.g. 2026",
        month="Month 1-12",
        post_results="Post TOP-N embed to LEADERBOARD_CHANNEL_ID",
        assign_roles="Reassign the Rofler role to monthly channel TOP winners",
        resume="Resume an interrupted scan for this month instead of starting fresh",
    )
    async def recalculate_leaderboard(
        self,
        interaction: discord.Interaction,
        year: int,
        month: int,
        post_results: bool = True,
        assign_roles: bool = False,
        resume: bool = False,
    ) -> None:
        if not _can_recalculate(interaction):
            await interaction.response.send_message(
                "You do not have permission to run this command.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            reader = BotChannelReader(self.bot)
            result = await run_pipeline(
                year,
                month,
                reader=reader,
                post_embed=post_results,
                assign_roles=assign_roles,
                bot=self.bot,
                print_top=False,
                resume=resume,
                on_progress=_make_progress_editor(interaction),
            )
            await self._report_success(interaction, year, month, result)
        except ScanFailedError as exc:
            logger.warning("Slash recalculate did not commit: %s", exc)
            await self._edit(
                interaction,
                f"Scan for **{year}-{month:02d}** did not finish.\n"
                f"Failed channels: {exc.stats.failed_channel_ids or '-'}\n"
                f"Incomplete channels: {exc.stats.incomplete_channel_ids or '-'}\n"
                "Database was not updated. Re-run with `resume: true` once access "
                "is restored.",
            )
        except Exception as exc:  # noqa: BLE001 - report to invoker
            logger.exception("Slash recalculate failed.")
            await self._edit(interaction, f"Pipeline failed: {exc}")

    @app_commands.command(
        name="show_leaderboard",
        description="Show TOP 5 for a month in one stats channel (from SQLite, no scan).",
    )
    @app_commands.describe(
        year="Calendar year, e.g. 2026",
        month="Month 1-12",
        channel="Stats text channel (must be in STATS_CHANNEL_IDS)",
    )
    async def show_leaderboard(
        self,
        interaction: discord.Interaction,
        year: int,
        month: int,
        channel: discord.TextChannel,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            validate_period(year, month)
            settings = get_settings()
            entries = await load_channel_leaderboard_for_period(
                year,
                month,
                channel.id,
                limit=_SHOW_LEADERBOARD_TOP_N,
            )
            channel_label = f"#{channel.name}"
            description = format_embed_description(
                entries,
                year=year,
                month=month,
                tz_label=settings.timezone,
                emoji_names=settings.emoji_names,
                top_n=_SHOW_LEADERBOARD_TOP_N,
                channel_label=channel_label,
            )
            embed = discord.Embed(
                title=f"Leaderboard {year}-{month:02d} · {channel_label}",
                description=description,
                colour=discord.Colour.green(),
            )
            embed.set_footer(
                text="From SQLite · use /recalculate_leaderboard to rescan Discord"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except ValueError as exc:
            await interaction.followup.send(f"Invalid input: {exc}", ephemeral=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("show_leaderboard failed.")
            await interaction.followup.send(f"Failed to load leaderboard: {exc}", ephemeral=True)

    async def _report_success(
        self,
        interaction: discord.Interaction,
        year: int,
        month: int,
        result: PipelineResult,
    ) -> None:
        settings = get_settings()
        top_text = format_console_top(
            result.top_entries,
            year=year,
            month=month,
            tz_label=settings.timezone,
            emoji_names=settings.emoji_names,
            top_n=settings.top_n,
        )
        storage = (
            f"SQLite: `{settings.database_path}` · `/show_leaderboard`"
        )
        await self._edit(
            interaction,
            f"Done **{year}-{month:02d}**.\n"
            f"Messages: {result.messages_matched}, "
            f"channels: {result.channels_completed} "
            f"(skipped: {result.channels_skipped}).\n"
            f"{storage}\n\n"
            f"```\n{top_text}\n```",
        )

    @staticmethod
    async def _edit(interaction: discord.Interaction, content: str) -> None:
        try:
            await interaction.edit_original_response(content=content)
        except discord.HTTPException:
            await interaction.followup.send(content, ephemeral=True)

    @tasks.loop(time=time(hour=0, minute=5, tzinfo=get_tz()))
    async def monthly_leaderboard(self) -> None:
        now = datetime.now(tz=get_tz())
        if now.day != 1:
            return

        year, month = previous_calendar_month()
        logger.info("Monthly job: recalculating %s-%02d", year, month)
        reader = BotChannelReader(self.bot)
        try:
            await run_pipeline(
                year,
                month,
                reader=reader,
                post_embed=True,
                assign_roles=True,
                bot=self.bot,
                print_top=False,
            )
        except ScanFailedError as exc:
            logger.warning("Monthly job did not commit: %s", exc)
            await self._notify_failure(
                f"Monthly leaderboard for {year}-{month:02d} did not finish: "
                f"failed {exc.stats.failed_channel_ids or '-'}, "
                f"incomplete {exc.stats.incomplete_channel_ids or '-'}. "
                "Database was not updated."
            )
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            logger.exception("Monthly job failed.")
            await self._notify_failure(
                f"Monthly leaderboard for {year}-{month:02d} crashed: {exc}"
            )

    async def _notify_failure(self, message: str) -> None:
        channel_id = get_settings().leaderboard_channel_id
        if channel_id is None:
            return
        try:
            channel = await self.bot.fetch_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                await channel.send(message)
        except discord.HTTPException:
            logger.exception("Failed to post failure notice to channel %s.", channel_id)

    @monthly_leaderboard.before_loop
    async def _before_monthly(self) -> None:
        await self.bot.wait_until_ready()
        target = next_monthly_run_at()
        now = datetime.now(tz=get_tz())
        delay = (target - now).total_seconds()
        logger.info(
            "Next monthly leaderboard at %s (sleep %.0fs).",
            target.isoformat(),
            max(delay, 0),
        )
        if delay > 0:
            await asyncio.sleep(delay)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LeaderboardCog(bot))

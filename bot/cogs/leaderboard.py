"""Slash commands and monthly scheduler for the leaderboard pipeline."""



from __future__ import annotations



import asyncio

import time as _time

from datetime import datetime



import discord

from discord import app_commands

from discord.ext import commands, tasks



from bot.client import BotChannelReader

from bot.config import get_settings

from bot.pipeline import (
    CheckpointError,
    PipelineBusyError,
    PipelineResult,
    ScanFailedError,
    run_pipeline,
)
from bot.services.daily_sync import run_daily_sync

from bot.services.channel_top_service import (
    format_last_sync_footer,
    format_named_channel_tops_console,
    load_channel_last_scanned_for_period,
    load_channel_leaderboard_for_period,
)

from bot.services.leaderboard_service import (

    format_embed_description,

)

from bot.utils.dates import validate_period

from bot.services.scanner import ScanProgressCallback, ScanProgressEvent

from bot.utils.dates import (
    current_calendar_month,
    daily_sync_time_of_day,
    get_tz,
    monthly_run_time_of_day,
    next_daily_sync_at,
    next_monthly_run_at,
    previous_calendar_month,
)

from bot.utils.logger import get_logger



logger = get_logger(__name__)



_PROGRESS_THROTTLE_SEC = 7.0

_SHOW_LEADERBOARD_TOP_N = 5





def _can_recalculate(interaction: discord.Interaction) -> bool:

    """Administrator or any role listed in ``MANUAL_RECALC_ROLE_IDS``."""

    user = interaction.user

    if not isinstance(user, discord.Member):

        return False

    if user.guild_permissions.administrator:

        return True

    allowed = get_settings().manual_recalc_role_ids

    if not allowed:

        return False

    member_role_ids = {role.id for role in user.roles}

    return bool(member_role_ids & allowed)





def _make_progress_editor(

    interaction: discord.Interaction,

) -> ScanProgressCallback:

    """Throttled progress updates on the ephemeral deferred response."""

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
                    f"Сканирую канал {event.channel_index}/{event.channels_total} "
                    f"#{event.channel_name} — сообщений: {event.messages_seen}, "
                    f"подошло: {event.messages_matched}…"
                )

            )

        except discord.HTTPException:

            pass



    return on_progress


def _format_scan_failed_message(
    year: int, month: int, exc: ScanFailedError
) -> str:
    failed = ", ".join(str(c) for c in exc.stats.failed_channel_ids) or "—"
    incomplete = ", ".join(str(c) for c in exc.stats.incomplete_channel_ids) or "—"
    return (
        f"Скан **{year}-{month:02d}** не завершён.\n"
        f"Проблемные каналы: {failed}\n"
        f"Неполные каналы: {incomplete}\n"
        "База не обновлена.\n\n"
        f"Повторите **/recalculate_leaderboard** с тем же годом и месяцем "
        "и включите **resume: да**."
    )


class LeaderboardCog(commands.Cog):

    """Slash commands and the monthly auto-run."""



    def __init__(self, bot: commands.Bot) -> None:

        self.bot = bot



    async def cog_load(self) -> None:

        if not self.daily_channel_sync.is_running():

            self.daily_channel_sync.start()

        if not self.monthly_leaderboard.is_running():

            self.monthly_leaderboard.start()



    async def cog_unload(self) -> None:

        self.daily_channel_sync.cancel()

        self.monthly_leaderboard.cancel()



    @app_commands.command(

        name="recalculate_leaderboard",

        description="Полный скан Discord и пересчёт рейтинга реакций за месяц.",
    )
    @app_commands.describe(
        year="Год, например 2026",
        month="Месяц 1–12",
        post_results="Опубликовать TOP по дурке и рофлинкам в LEADERBOARD_CHANNEL_ID",
        assign_roles="Перевыдать роль «Рофлер» победителям месяца",
        resume="Продолжить прерванный скан этого месяца",
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

                "У вас нет прав на эту команду.",

                ephemeral=True,

            )

            return



        if assign_roles and not get_settings().role_reassign_enabled:

            await interaction.response.send_message(

                "Перевыдача ролей отключена (`ROLE_REASSIGN_ENABLED=false`).",

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

            await self._report_recalculate_success(interaction, year, month, result)

        except ScanFailedError as exc:

            logger.warning("Slash recalculate did not commit: %s", exc)

            await self._edit_ephemeral(
                interaction,
                _format_scan_failed_message(year, month, exc),
            )

        except CheckpointError as exc:

            logger.warning("Slash recalculate checkpoint: %s", exc)

            await self._edit_ephemeral(interaction, exc.user_message)

        except PipelineBusyError as exc:

            logger.info("Slash recalculate rejected (busy): %s", exc)

            await self._edit_ephemeral(interaction, exc.user_message)

        except Exception as exc:  # noqa: BLE001 - report to invoker only

            logger.exception("Slash recalculate failed.")

            await self._edit_ephemeral(
                interaction,
                f"Неожиданная ошибка: {exc}",
            )



    @app_commands.command(

        name="show_leaderboard",

        description="TOP 5 за месяц по одному stats-каналу (из SQLite, без скана).",
    )
    @app_commands.describe(
        year="Год, например 2026",
        month="Месяц 1–12",
        channel="Текстовый канал из STATS_CHANNEL_IDS",
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

            last_scanned = await load_channel_last_scanned_for_period(

                year,

                month,

                channel.id,

            )

            channel_label = f"#{channel.name}"

            description = format_embed_description(

                entries,

                year=year,

                month=month,

                tz_label=settings.timezone,

                emoji_names=settings.emoji_names,

                top_n=_SHOW_LEADERBOARD_TOP_N,

                include_header=False,

            )

            embed = discord.Embed(

                title=f"Рейтинг {year}-{month:02d} · {channel_label}",

                description=description,

                colour=discord.Colour.green(),

            )

            embed.set_footer(text=format_last_sync_footer(last_scanned))

            await interaction.followup.send(embed=embed, ephemeral=True)

        except ValueError as exc:

            await interaction.followup.send(f"Некорректные данные: {exc}", ephemeral=True)

        except Exception as exc:  # noqa: BLE001

            logger.exception("show_leaderboard failed.")

            await interaction.followup.send(
                f"Не удалось загрузить рейтинг: {exc}", ephemeral=True
            )



    async def _report_recalculate_success(

        self,

        interaction: discord.Interaction,

        year: int,

        month: int,

        result: PipelineResult,

    ) -> None:

        await self._edit_ephemeral(

            interaction,

            self._build_recalculate_success_text(year, month, result),

        )



    @staticmethod
    def _build_recalculate_success_text(
        year: int,
        month: int,
        result: PipelineResult,
    ) -> str:
        settings = get_settings()

        if result.channel_post_tops:
            top_text = format_named_channel_tops_console(
                result.channel_post_tops,
                year=year,
                month=month,
                tz_label=settings.timezone,
                emoji_names=settings.emoji_names,
                top_n=settings.leaderboard_channel_top_n,
            )
        else:
            top_text = (
                "TOP по каналам недоступен: задайте ROLE_DURKICHI_CHANNEL_ID и "
                "ROLE_ROFLINKICHI_CHANNEL_ID в .env."
            )

        text = (
            f"Готово **{year}-{month:02d}**.\n"
            f"Сообщений: {result.messages_matched}, "
            f"каналов: {result.channels_completed} "
            f"(пропущено: {result.channels_skipped}).\n"
            f"База: `{settings.database_path}`"
        )
        if result.warnings:
            text += "\n\n**Внимание:**\n" + "\n".join(
                f"• {w}" for w in result.warnings
            )
        text += f"\n\n```\n{top_text}\n```"
        return text



    @staticmethod

    async def _edit_ephemeral(interaction: discord.Interaction, content: str) -> None:

        try:

            await interaction.edit_original_response(content=content)

        except discord.HTTPException:

            await interaction.followup.send(content, ephemeral=True)



    @tasks.loop(time=daily_sync_time_of_day())

    async def daily_channel_sync(self) -> None:

        settings = get_settings()

        if not settings.daily_sync_enabled:

            return



        year, month = current_calendar_month()

        logger.info("Daily sync job: updating %s-%02d", year, month)

        reader = BotChannelReader(self.bot)

        try:

            await run_daily_sync(year, month, reader=reader)

        except Exception as exc:  # noqa: BLE001 - keep the loop alive

            logger.exception("Daily sync job failed for %s-%02d.", year, month)

            await self._notify_failure(

                f"Daily sync for {year}-{month:02d} crashed: {exc}"

            )



    @daily_channel_sync.before_loop

    async def _before_daily_sync(self) -> None:

        await self.bot.wait_until_ready()

        target = next_daily_sync_at()

        now = datetime.now(tz=get_tz())

        delay = (target - now).total_seconds()

        logger.info(

            "Next daily channel sync at %s (sleep %.0fs).",

            target.isoformat(),

            max(delay, 0),

        )

        if delay > 0:

            await asyncio.sleep(delay)



    @tasks.loop(time=monthly_run_time_of_day())

    async def monthly_leaderboard(self) -> None:

        now = datetime.now(tz=get_tz())

        if now.day != 1:

            return



        year, month = previous_calendar_month()

        logger.info("Monthly job: recalculating %s-%02d", year, month)

        settings = get_settings()

        reader = BotChannelReader(self.bot)

        try:

            await run_pipeline(

                year,

                month,

                reader=reader,

                post_embed=True,

                assign_roles=settings.role_reassign_enabled,

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



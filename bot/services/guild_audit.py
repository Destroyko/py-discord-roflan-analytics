"""Startup audit: verify the bot has required guild/channel permissions."""

from __future__ import annotations

from dataclasses import dataclass, field

import discord
from discord.ext import commands

from bot.config import Settings
from bot.utils.logger import get_logger

logger = get_logger(__name__)

# (Permissions attribute, human label) — no Administrator; only what the bot uses.
_GUILD_BASE = (
    ("view_channel", "View Channels"),
    ("read_message_history", "Read Message History"),
    ("send_messages", "Send Messages"),
)

_CHANNEL_SCAN = (
    ("view_channel", "View Channel"),
    ("read_message_history", "Read Message History"),
)
_CHANNEL_POST = (
    ("view_channel", "View Channel"),
    ("send_messages", "Send Messages"),
    ("embed_links", "Embed Links"),
)


@dataclass
class GuildAuditReport:
    """Result of a permission check for one guild."""

    guild_name: str
    guild_id: int
    ok_messages: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return not self.issues


def missing_permission_labels(
    perms: discord.Permissions,
    required: tuple[tuple[str, str], ...],
) -> tuple[list[str], list[str]]:
    """Return (present labels, missing labels) for ``required`` flags."""
    present: list[str] = []
    missing: list[str] = []
    for attr, label in required:
        if getattr(perms, attr, False):
            present.append(label)
        else:
            missing.append(label)
    return present, missing


def _check_flags(
    perms: discord.Permissions,
    required: tuple[tuple[str, str], ...],
    scope: str,
    report: GuildAuditReport,
) -> None:
    present, missing = missing_permission_labels(perms, required)
    if missing:
        report.issues.append(f"{scope}: missing {', '.join(missing)}")
    elif present:
        report.ok_messages.append(f"{scope}: {', '.join(present)}")


async def _resolve_guild(
    bot: commands.Bot, guild_id: int
) -> discord.Guild | None:
    guild = bot.get_guild(guild_id)
    if guild is not None:
        return guild
    try:
        return await bot.fetch_guild(guild_id)
    except discord.HTTPException as exc:
        logger.warning("Cannot fetch guild %s: %s", guild_id, exc)
        return None


async def _resolve_member(
    guild: discord.Guild, bot_user: discord.ClientUser
) -> discord.Member | None:
    member = guild.get_member(bot_user.id)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(bot_user.id)
    except discord.HTTPException as exc:
        logger.warning("Cannot fetch bot member in guild %s: %s", guild.id, exc)
        return None


async def _resolve_channel(
    bot: commands.Bot,
    guild: discord.Guild,
    channel_id: int,
) -> discord.abc.GuildChannel | None:
    channel = guild.get_channel(channel_id)
    if channel is not None:
        return channel
    try:
        return await bot.fetch_channel(channel_id)
    except discord.HTTPException:
        return None


def channels_requiring_post_permissions(
    settings: Settings,
) -> list[tuple[str, int]]:
    """Env label + channel id for startup send/embed permission checks."""
    candidates: list[tuple[str, int | None]] = [
        ("LEADERBOARD_CHANNEL_ID", settings.leaderboard_channel_id),
    ]
    return [(label, channel_id) for label, channel_id in candidates if channel_id is not None]


async def audit_guild_permissions(
    bot: commands.Bot,
    settings: Settings,
) -> GuildAuditReport:
    """Check guild, channel, and role hierarchy; log OK / missing to the console."""
    report = GuildAuditReport(guild_name="?", guild_id=settings.guild_id)

    if bot.user is None:
        report.issues.append("Bot user is not available yet")
        _log_report(report)
        return report

    guild = await _resolve_guild(bot, settings.guild_id)
    if guild is None:
        report.issues.append(
            f"Guild {settings.guild_id} not found — is the bot invited?"
        )
        _log_report(report)
        return report

    report.guild_name = guild.name

    member = await _resolve_member(guild, bot.user)
    if member is None:
        report.issues.append(
            f"Bot is not a member of guild {guild.name} ({guild.id})"
        )
        _log_report(report)
        return report

    _check_flags(member.guild_permissions, _GUILD_BASE, "Guild", report)

    seen_channels: set[int] = set()
    for channel_id in settings.stats_channel_ids:
        if channel_id in seen_channels:
            continue
        seen_channels.add(channel_id)
        await _audit_channel(
            bot,
            guild,
            member,
            channel_id,
            label=f"stats channel {channel_id}",
            required=_CHANNEL_SCAN,
            report=report,
            require_text=True,
        )

    for env_name, channel_id in channels_requiring_post_permissions(settings):
        if channel_id in seen_channels:
            continue
        seen_channels.add(channel_id)
        await _audit_channel(
            bot,
            guild,
            member,
            channel_id,
            label=env_name,
            required=_CHANNEL_POST,
            report=report,
            require_text=True,
        )

    _log_report(report)
    return report


async def _audit_channel(
    bot: commands.Bot,
    guild: discord.Guild,
    member: discord.Member,
    channel_id: int,
    *,
    label: str,
    required: tuple[tuple[str, str], ...],
    report: GuildAuditReport,
    require_text: bool,
) -> None:
    channel = await _resolve_channel(bot, guild, channel_id)
    if channel is None:
        report.issues.append(f"{label} ({channel_id}): channel not found")
        return
    if require_text and not isinstance(channel, discord.TextChannel):
        report.issues.append(
            f"{label} ({channel_id}): expected text channel, got "
            f"{type(channel).__name__}"
        )
        return

    perms = channel.permissions_for(member)
    _check_flags(perms, required, label, report)


def _log_report(report: GuildAuditReport) -> None:
    logger.info(
        "=== Permission audit (guild «%s», id=%s) ===",
        report.guild_name,
        report.guild_id,
    )
    for msg in report.ok_messages:
        logger.info("OK: %s", msg)
    for issue in report.issues:
        logger.warning("MISSING: %s", issue)
    if report.success:
        logger.info("Summary: all required permissions present.")
    else:
        logger.warning(
            "Summary: %s issue(s) — fix Discord permissions before scan / embed.",
            len(report.issues),
        )

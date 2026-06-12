"""Monthly «Рофлер» role strip/assign and Discord notifications."""

from __future__ import annotations

from dataclasses import dataclass, field

import discord
from discord.ext import commands

from bot.config import Settings, get_settings
from bot.services.channel_top_service import load_channel_leaderboard_for_period
from bot.services.leaderboard_service import LeaderboardEntry
from bot.utils.logger import get_logger

logger = get_logger(__name__)

SECTION_DURKICHI = "Дуркичи"
SECTION_ROFLINKICHI = "Рофлинкичи"


@dataclass(frozen=True)
class RoleSection:
    """Named TOP list for one stats channel."""

    title: str
    entries: list[LeaderboardEntry]


@dataclass
class RoleApplyResult:
    """Outcome of strip + assign for the Rofler role."""

    success: bool
    stripped_count: int = 0
    assigned_count: int = 0
    winner_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def select_top_n_unique(
    pool: list[LeaderboardEntry],
    *,
    n: int,
    skip_author_ids: frozenset[str],
    excluded_user_ids: frozenset[str],
) -> list[LeaderboardEntry]:
    """Pick ``n`` distinct authors from ``pool``, skipping prior winners.

    Used so the Рофлинкичи TOP-2 list shows the next eligible people when someone
    already appears in the Дуркичи TOP-3 (5 unique winners total).
    """
    taken = set(skip_author_ids)
    result: list[LeaderboardEntry] = []
    for entry in pool:
        if entry.author_id in excluded_user_ids or entry.author_id in taken:
            continue
        taken.add(entry.author_id)
        result.append(
            LeaderboardEntry(
                rank=len(result) + 1,
                author_id=entry.author_id,
                total_reactions=entry.total_reactions,
            )
        )
        if len(result) >= n:
            break
    return result


async def compute_rofler_winners(
    year: int,
    month: int,
    *,
    settings: Settings | None = None,
) -> tuple[RoleSection, RoleSection]:
    """Load TOP lists for Дуркичи and Рофлинкичи channels from SQLite.

    Дуркичи keeps the channel TOP-N as-is. Рофлинкичи skips anyone already in
    Дуркичи and fills from the next ranks in that channel's leaderboard so the
    combined lists name ``top_n + top_n`` distinct people when enough candidates
    exist.
    """
    cfg = settings or get_settings()
    cfg.validate_role_settings()
    excluded = cfg.excluded_user_ids

    durkichi = await load_channel_leaderboard_for_period(
        year,
        month,
        cfg.role_durkichi_channel_id,  # type: ignore[arg-type]
        limit=cfg.role_durkichi_top_n,
        excluded_user_ids=excluded,
    )
    roflinkichi_pool = await load_channel_leaderboard_for_period(
        year,
        month,
        cfg.role_roflinkichi_channel_id,  # type: ignore[arg-type]
        limit=None,
        excluded_user_ids=excluded,
    )
    roflinkichi = select_top_n_unique(
        roflinkichi_pool,
        n=cfg.role_roflinkichi_top_n,
        skip_author_ids=frozenset(e.author_id for e in durkichi),
        excluded_user_ids=excluded,
    )
    return (
        RoleSection(title=SECTION_DURKICHI, entries=durkichi),
        RoleSection(title=SECTION_ROFLINKICHI, entries=roflinkichi),
    )


def collect_winner_user_ids(
    durkichi: RoleSection,
    roflinkichi: RoleSection,
    *,
    excluded_user_ids: frozenset[str],
) -> list[int]:
    """Unique Discord user IDs to receive the Rofler role.

    Expects ``roflinkichi`` entries already deduped against ``durkichi`` (see
    ``compute_rofler_winners``); still skips duplicates and excluded IDs.
    """
    seen: set[str] = set()
    ids: list[int] = []
    for entry in durkichi.entries + roflinkichi.entries:
        if entry.author_id in excluded_user_ids or entry.author_id in seen:
            continue
        seen.add(entry.author_id)
        ids.append(int(entry.author_id))
    return ids


def format_rofler_success_message(
    role_id: int,
    durkichi: RoleSection,
    roflinkichi: RoleSection,
) -> str:
    """Plain-text success notice with clickable role and user mentions."""
    lines = [
        f"Перевыдача роли <@&{role_id}> прошла успешно.",
        "",
        _format_section(durkichi),
        "",
        _format_section(roflinkichi),
    ]
    return "\n".join(lines)


def format_rofler_failure_message(
    *,
    year: int,
    month: int,
    role_id: int,
    errors: list[str],
) -> str:
    """Plain-text failure notice for the error channel."""
    lines = [
        f"Перевыдача роли <@&{role_id}> за **{year}-{month:02d}** не удалась.",
        "",
    ]
    if errors:
        lines.append("Причины:")
        lines.extend(f"• {err}" for err in errors)
    else:
        lines.append("Причина не указана.")
    return "\n".join(lines)


def _format_section(section: RoleSection) -> str:
    lines = [f"{section.title}:"]
    if not section.entries:
        lines.append("(нет данных за период)")
        return "\n".join(lines)
    for entry in section.entries:
        lines.append(
            f"{entry.rank}. <@{entry.author_id}> - {entry.total_reactions}"
        )
    return "\n".join(lines)


async def _send_text_to_channel(
    bot: commands.Bot,
    channel_id: int,
    content: str,
    *,
    label: str,
) -> None:
    try:
        channel = await bot.fetch_channel(channel_id)
    except discord.HTTPException as exc:
        logger.exception("Failed to fetch %s channel %s.", label, channel_id)
        raise RuntimeError(f"Cannot fetch {label} channel: {exc}") from exc

    if not isinstance(channel, discord.TextChannel):
        raise RuntimeError(f"{label} channel {channel_id} is not a text channel")

    await channel.send(content)
    logger.info("Posted %s message to channel %s.", label, channel_id)


async def post_rofler_notify(bot: commands.Bot, content: str) -> None:
    """Send success text to ``ROLE_NOTIFY_CHANNEL_ID``."""
    settings = get_settings()
    settings.validate_role_settings()
    await _send_text_to_channel(
        bot,
        settings.role_notify_channel_id,  # type: ignore[arg-type]
        content,
        label="role notify",
    )


async def post_rofler_error(bot: commands.Bot, content: str) -> None:
    """Send failure text to ``ROLE_ERROR_CHANNEL_ID``."""
    settings = get_settings()
    settings.validate_role_settings()
    await _send_text_to_channel(
        bot,
        settings.role_error_channel_id,  # type: ignore[arg-type]
        content,
        label="role error",
    )


async def apply_rofler_role(
    bot: commands.Bot,
    guild: discord.Guild,
    user_ids: list[int],
    *,
    settings: Settings | None = None,
) -> RoleApplyResult:
    """Strip the Rofler role from all holders, then assign to winners."""
    cfg = settings or get_settings()
    cfg.validate_role_settings()
    role_id = cfg.role_rofler_id
    assert role_id is not None

    role = guild.get_role(role_id)
    if role is None:
        try:
            roles = await guild.fetch_roles()
            role = discord.utils.get(roles, id=role_id)
        except discord.HTTPException as exc:
            return RoleApplyResult(
                success=False,
                errors=[f"Cannot resolve role {role_id}: {exc}"],
            )
    if role is None:
        return RoleApplyResult(
            success=False,
            errors=[f"Role {role_id} not found in guild {guild.id}"],
        )

    result = RoleApplyResult(success=True, winner_ids=[str(uid) for uid in user_ids])
    errors: list[str] = []

    holders = list(role.members)
    if not holders and not guild.chunked:
        try:
            await guild.chunk()
            holders = list(role.members)
        except discord.HTTPException as exc:
            errors.append(f"Could not load guild members for strip: {exc}")

    for member in holders:
        try:
            await member.remove_roles(role, reason="Monthly Rofler reassignment")
            result.stripped_count += 1
        except discord.Forbidden:
            errors.append(f"No permission to remove role from {member.id}")
        except discord.HTTPException as exc:
            errors.append(f"Failed to remove role from {member.id}: {exc}")

    for user_id in user_ids:
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                errors.append(f"User {user_id} not in guild")
                continue
            except discord.HTTPException as exc:
                errors.append(f"Failed to fetch member {user_id}: {exc}")
                continue

        if role in member.roles:
            result.assigned_count += 1
            continue

        try:
            await member.add_roles(role, reason="Monthly Rofler winner")
            result.assigned_count += 1
        except discord.Forbidden:
            errors.append(f"No permission to assign role to {user_id}")
        except discord.HTTPException as exc:
            errors.append(f"Failed to assign role to {user_id}: {exc}")

    result.errors = errors
    if errors:
        result.success = False
    return result


async def run_rofler_role_reassignment(
    bot: commands.Bot,
    *,
    year: int,
    month: int,
) -> RoleApplyResult:
    """Full flow: winners → strip/assign → notify or error channel."""
    settings = get_settings()
    try:
        settings.validate_role_settings()
    except ValueError as exc:
        logger.error("Role reassignment config invalid: %s", exc)
        await _report_role_failure_best_effort(
            bot, year, month, [str(exc)], settings
        )
        return RoleApplyResult(success=False, errors=[str(exc)])

    durkichi, roflinkichi = await compute_rofler_winners(
        year, month, settings=settings
    )
    winner_ids = collect_winner_user_ids(
        durkichi,
        roflinkichi,
        excluded_user_ids=settings.excluded_user_ids,
    )

    guild = bot.get_guild(settings.guild_id)
    if guild is None:
        try:
            guild = await bot.fetch_guild(settings.guild_id)
        except discord.HTTPException as exc:
            msg = f"Guild {settings.guild_id} not available: {exc}"
            await _report_role_failure(bot, year, month, [msg], settings)
            return RoleApplyResult(success=False, errors=[msg])

    apply_result = await apply_rofler_role(bot, guild, winner_ids, settings=settings)

    role_id = settings.role_rofler_id
    assert role_id is not None

    if apply_result.success:
        text = format_rofler_success_message(role_id, durkichi, roflinkichi)
        try:
            await post_rofler_notify(bot, text)
        except RuntimeError as exc:
            apply_result.success = False
            apply_result.errors.append(str(exc))
            await _report_role_failure(
                bot,
                year,
                month,
                apply_result.errors,
                settings,
            )
    else:
        await _report_role_failure(
            bot,
            year,
            month,
            apply_result.errors,
            settings,
        )

    return apply_result


async def _report_role_failure(
    bot: commands.Bot,
    year: int,
    month: int,
    errors: list[str],
    settings: Settings,
) -> None:
    await _report_role_failure_best_effort(bot, year, month, errors, settings)


async def _report_role_failure_best_effort(
    bot: commands.Bot,
    year: int,
    month: int,
    errors: list[str],
    settings: Settings,
) -> None:
    role_id = settings.role_rofler_id
    channel_id = settings.role_error_channel_id
    if role_id is None or channel_id is None:
        logger.error(
            "Role failure (not posted to Discord; set ROLE_ROFLER_ID and "
            "ROLE_ERROR_CHANNEL_ID): %s",
            errors,
        )
        return
    text = format_rofler_failure_message(
        year=year,
        month=month,
        role_id=role_id,
        errors=errors,
    )
    try:
        await _send_text_to_channel(
            bot, channel_id, text, label="role error"
        )
    except RuntimeError as exc:
        logger.exception("Could not post role failure to error channel: %s", exc)

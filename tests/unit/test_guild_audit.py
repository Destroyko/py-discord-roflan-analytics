"""Unit tests for guild permission audit helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.config import Settings
from bot.services.guild_audit import (
    audit_guild_permissions,
    channels_requiring_post_permissions,
    missing_permission_labels,
    role_features_configured,
)


def _settings(**kwargs) -> Settings:
    base = dict(
        discord_bot_token="t",
        guild_id=1,
        stats_channel_ids=[111],
        leaderboard_channel_id=3333,
    )
    base.update(kwargs)
    return Settings(**base)


def test_missing_permission_labels():
    perms = discord.Permissions(read_message_history=True, send_messages=True)
    present, missing = missing_permission_labels(
        perms,
        (
            ("view_channel", "View Channel"),
            ("read_message_history", "Read Message History"),
            ("send_messages", "Send Messages"),
        ),
    )
    assert "Read Message History" in present
    assert "Send Messages" in present
    assert missing == ["View Channel"]


def test_role_features_configured_incomplete():
    s = _settings(role_rofler_id=1)
    assert role_features_configured(s) is False


def test_role_features_configured_complete():
    s = _settings(
        role_reassign_enabled=True,
        role_rofler_id=9001,
        role_notify_channel_id=8001,
        role_error_channel_id=8002,
        role_durkichi_channel_id=111,
        role_roflinkichi_channel_id=222,
        stats_channel_ids=[111, 222],
    )
    assert role_features_configured(s) is True


def test_channels_requiring_post_permissions_notify_without_roles():
    s = _settings(
        leaderboard_channel_id=7001,
        role_notify_channel_id=8001,
        role_reassign_enabled=False,
    )
    labels = [label for label, _ in channels_requiring_post_permissions(s)]
    assert labels == ["LEADERBOARD_CHANNEL_ID", "ROLE_NOTIFY_CHANNEL_ID"]


def test_channels_requiring_post_permissions_includes_error_when_roles_on():
    s = _settings(
        role_reassign_enabled=True,
        role_rofler_id=9001,
        role_notify_channel_id=8001,
        role_error_channel_id=8002,
        role_durkichi_channel_id=111,
        role_roflinkichi_channel_id=222,
        stats_channel_ids=[111, 222],
        leaderboard_channel_id=7001,
    )
    labels = [label for label, _ in channels_requiring_post_permissions(s)]
    assert labels == [
        "LEADERBOARD_CHANNEL_ID",
        "ROLE_NOTIFY_CHANNEL_ID",
        "ROLE_ERROR_CHANNEL_ID",
    ]


@pytest.mark.asyncio
async def test_audit_uses_role_rofler_id_when_roles_enabled():
    settings = _settings(
        role_reassign_enabled=True,
        role_rofler_id=9001,
        role_notify_channel_id=8001,
        role_error_channel_id=8002,
        role_durkichi_channel_id=111,
        role_roflinkichi_channel_id=222,
        stats_channel_ids=[111, 222],
    )

    member = MagicMock()
    member.guild_permissions = discord.Permissions(
        view_channel=True,
        read_message_history=True,
        send_messages=True,
        manage_roles=True,
    )

    stats_channel = MagicMock(spec=discord.TextChannel)
    stats_channel.permissions_for.return_value = discord.Permissions(
        view_channel=True,
        read_message_history=True,
    )
    notify_channel = MagicMock(spec=discord.TextChannel)
    notify_channel.permissions_for.return_value = discord.Permissions(
        view_channel=True,
        send_messages=True,
        embed_links=True,
    )
    error_channel = MagicMock(spec=discord.TextChannel)
    error_channel.permissions_for.return_value = discord.Permissions(
        view_channel=True,
        send_messages=True,
        embed_links=True,
    )

    def get_channel(channel_id: int):
        return {111: stats_channel, 222: stats_channel, 8001: notify_channel, 8002: error_channel}.get(
            channel_id
        )

    guild = MagicMock()
    guild.name = "Test Guild"
    guild.id = 1
    guild.get_member = MagicMock(return_value=member)
    guild.get_channel = MagicMock(side_effect=get_channel)
    guild.get_role = MagicMock(return_value=None)
    guild.fetch_roles = AsyncMock(return_value=[])

    bot = MagicMock()
    bot.user = MagicMock(id=42)
    bot.get_guild = MagicMock(return_value=guild)
    bot.fetch_channel = AsyncMock(side_effect=lambda cid: get_channel(cid))

    report = await audit_guild_permissions(bot, settings)

    assert any("ROLE_ROFLER_ID: role 9001 not found" in issue for issue in report.issues)


def _audit_harness(settings: Settings, *, bot_above_rofler: bool):
    bot_role = MagicMock()
    bot_role.name = "Bot"
    rofler_role = MagicMock()
    rofler_role.name = "Rofler"
    bot_role.__gt__ = MagicMock(return_value=bot_above_rofler)

    member = MagicMock()
    member.top_role = bot_role
    member.guild_permissions = discord.Permissions(
        view_channel=True,
        read_message_history=True,
        send_messages=True,
        manage_roles=True,
    )

    stats_channel = MagicMock(spec=discord.TextChannel)
    stats_channel.permissions_for.return_value = discord.Permissions(
        view_channel=True,
        read_message_history=True,
    )
    notify_channel = MagicMock(spec=discord.TextChannel)
    notify_channel.permissions_for.return_value = discord.Permissions(
        view_channel=True,
        send_messages=True,
        embed_links=True,
    )
    error_channel = MagicMock(spec=discord.TextChannel)
    error_channel.permissions_for.return_value = discord.Permissions(
        view_channel=True,
        send_messages=True,
        embed_links=True,
    )
    leaderboard_channel = MagicMock(spec=discord.TextChannel)
    leaderboard_channel.permissions_for.return_value = discord.Permissions(
        view_channel=True,
        send_messages=True,
        embed_links=True,
    )

    def get_channel(channel_id: int):
        return {
            111: stats_channel,
            222: stats_channel,
            8001: notify_channel,
            8002: error_channel,
            settings.leaderboard_channel_id: leaderboard_channel,
        }.get(channel_id)

    guild = MagicMock()
    guild.name = "Test Guild"
    guild.id = 1
    guild.get_member = MagicMock(return_value=member)
    guild.get_channel = MagicMock(side_effect=get_channel)
    guild.get_role = MagicMock(return_value=rofler_role)

    bot = MagicMock()
    bot.user = MagicMock(id=42)
    bot.get_guild = MagicMock(return_value=guild)
    bot.fetch_channel = AsyncMock(side_effect=lambda cid: get_channel(cid))
    return bot, settings


@pytest.mark.asyncio
async def test_audit_hierarchy_ok_when_bot_role_is_above_rofler():
    settings = _settings(
        role_reassign_enabled=True,
        role_rofler_id=9001,
        role_notify_channel_id=8001,
        role_error_channel_id=8002,
        role_durkichi_channel_id=111,
        role_roflinkichi_channel_id=222,
        stats_channel_ids=[111, 222],
    )
    bot, settings = _audit_harness(settings, bot_above_rofler=True)

    report = await audit_guild_permissions(bot, settings)

    assert report.success
    assert any("Role hierarchy" in msg for msg in report.ok_messages)


@pytest.mark.asyncio
async def test_audit_hierarchy_fails_when_bot_role_not_above_rofler():
    settings = _settings(
        role_reassign_enabled=True,
        role_rofler_id=9001,
        role_notify_channel_id=8001,
        role_error_channel_id=8002,
        role_durkichi_channel_id=111,
        role_roflinkichi_channel_id=222,
        stats_channel_ids=[111, 222],
    )
    bot, settings = _audit_harness(settings, bot_above_rofler=False)

    report = await audit_guild_permissions(bot, settings)

    assert any("Role hierarchy" in issue for issue in report.issues)

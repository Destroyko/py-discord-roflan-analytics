"""Unit tests for guild permission audit helpers."""

from __future__ import annotations

import discord

from bot.config import Settings
from bot.services.guild_audit import (
    missing_permission_labels,
    role_features_configured,
)


def _settings(**kwargs) -> Settings:
    base = dict(
        discord_bot_token="t",
        guild_id=1,
        stats_channel_ids=[111],
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
        role_rofler_id=9001,
        role_notify_channel_id=8001,
        role_error_channel_id=8002,
        role_durkichi_channel_id=111,
        role_roflinkichi_channel_id=222,
        stats_channel_ids=[111, 222],
    )
    assert role_features_configured(s) is True

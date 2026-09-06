"""Unit tests for guild permission audit helpers."""

from __future__ import annotations

import discord

from bot.services.guild_audit import missing_permission_labels


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

"""Tests for public embed posting warnings."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.pipeline import _embed_channel_error, _post_leaderboard_embed


def test_embed_channel_error_forbidden():
    exc = discord.Forbidden(MagicMock(), {"message": "Missing Access", "code": 50001})
    text = _embed_channel_error(999, exc)
    assert "999" in text
    assert "нет доступа" in text


@pytest.mark.asyncio
async def test_post_embed_returns_warning_on_forbidden(env_settings):
    bot = MagicMock()
    bot.fetch_channel = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(), {"message": "Missing Access"})
    )
    settings = env_settings
    object.__setattr__(settings, "leaderboard_channel_id", 123456)

    warning = await _post_leaderboard_embed(
        bot,
        settings,
        year=2026,
        month=5,
        entries=[],
    )

    assert warning is not None
    assert "123456" in warning
    assert "нет доступа" in warning

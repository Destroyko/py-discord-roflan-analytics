"""Tests for per-channel TOP posts to LEADERBOARD_CHANNEL_ID."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.pipeline import _post_leaderboard_embed
from bot.services.channel_top_service import (
    NamedChannelTop,
    format_named_channel_tops_console,
    format_named_channel_tops_embed,
    load_leaderboard_post_channel_tops,
)
from bot.services.leaderboard_service import LeaderboardEntry


@pytest.mark.asyncio
async def test_load_leaderboard_post_channel_tops(env_settings):
    from bot.database.db import Database

    settings = env_settings
    object.__setattr__(settings, "role_durkichi_channel_id", 111)
    object.__setattr__(settings, "role_roflinkichi_channel_id", 222)
    object.__setattr__(settings, "leaderboard_channel_top_n", 5)

    async with Database(settings.database_path) as db:
        await db.init_db()

    tops = await load_leaderboard_post_channel_tops(2026, 5, settings=settings)

    assert len(tops) == 2
    assert tops[0].channel_id == 111
    assert tops[1].channel_id == 222
    assert tops[0].title == "Дуркичи"
    assert tops[1].title == "Рофлинкичи"


def test_format_named_channel_tops_embed_lists_both_sections():
    tops = [
        NamedChannelTop(
            title="Дуркичи",
            channel_id=111,
            entries=[
                LeaderboardEntry(rank=1, author_id="10", total_reactions=7),
            ],
        ),
        NamedChannelTop(
            title="Рофлинкичи",
            channel_id=222,
            entries=[],
        ),
    ]
    text = format_named_channel_tops_embed(
        tops,
        year=2026,
        month=5,
        tz_label="Europe/Moscow",
        emoji_names=frozenset({"EBALO"}),
        top_n=5,
    )
    assert "Дуркичи" in text
    assert "Рофлинкичи" in text
    assert "<#111>" in text
    assert "<#222>" in text
    assert "<@10>" in text
    assert "топ 5 по каналу" in text


def test_format_named_channel_tops_console():
    tops = [
        NamedChannelTop(
            title="Дуркичи",
            channel_id=111,
            entries=[
                LeaderboardEntry(rank=1, author_id="10", total_reactions=3),
            ],
        ),
        NamedChannelTop(
            title="Рофлинкичи",
            channel_id=222,
            entries=[],
        ),
    ]
    text = format_named_channel_tops_console(
        tops,
        year=2026,
        month=5,
        tz_label="Europe/Moscow",
        emoji_names=frozenset({"EBALO"}),
        top_n=5,
    )
    assert "канал 111" in text
    assert "канал 222" in text
    assert "пользователь 10" in text


@pytest.mark.asyncio
async def test_post_embed_returns_warning_on_forbidden(env_settings):
    bot = MagicMock()
    bot.fetch_channel = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(), {"message": "Missing Access"})
    )
    settings = env_settings
    object.__setattr__(settings, "leaderboard_channel_id", 123456)
    object.__setattr__(settings, "role_durkichi_channel_id", 111)
    object.__setattr__(settings, "role_roflinkichi_channel_id", 222)
    channel_tops = [
        NamedChannelTop(title="Дуркичи", channel_id=111, entries=[]),
        NamedChannelTop(title="Рофлинкичи", channel_id=222, entries=[]),
    ]

    warning = await _post_leaderboard_embed(
        bot,
        settings,
        year=2026,
        month=5,
        channel_tops=channel_tops,
    )

    assert warning is not None
    assert "123456" in warning
    assert "нет доступа" in warning


@pytest.mark.asyncio
async def test_post_embed_skips_when_role_channels_missing(env_settings):
    bot = MagicMock()
    settings = env_settings
    object.__setattr__(settings, "leaderboard_channel_id", 123456)

    warning = await _post_leaderboard_embed(
        bot,
        settings,
        year=2026,
        month=5,
        channel_tops=[
            NamedChannelTop(title="Дуркичи", channel_id=111, entries=[]),
            NamedChannelTop(title="Рофлинкичи", channel_id=222, entries=[]),
        ],
    )

    assert warning is not None
    assert "ROLE_DURKICHI_CHANNEL_ID" in warning
    bot.fetch_channel.assert_not_called()

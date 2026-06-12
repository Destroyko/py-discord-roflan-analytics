"""Unit tests for Rofler role messages and winner selection."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from bot.config import Settings
from bot.services.leaderboard_service import LeaderboardEntry
from bot.services.role_service import (
    RoleSection,
    SECTION_DURKICHI,
    SECTION_ROFLINKICHI,
    apply_rofler_role,
    collect_winner_user_ids,
    format_rofler_failure_message,
    format_rofler_success_message,
    select_top_n_unique,
)


def _settings(tmp_path) -> Settings:
    return Settings(
        discord_bot_token="t",
        guild_id=1000,
        stats_channel_ids=[111, 222],
        role_rofler_id=9001,
        role_notify_channel_id=8001,
        role_error_channel_id=8002,
        role_durkichi_channel_id=111,
        role_durkichi_top_n=3,
        role_roflinkichi_channel_id=222,
        role_roflinkichi_top_n=2,
        database_path=tmp_path / "db.sqlite",
    )


def test_format_success_message_mentions():
    durkichi = RoleSection(
        title=SECTION_DURKICHI,
        entries=[
            LeaderboardEntry(rank=1, author_id="111", total_reactions=10),
            LeaderboardEntry(rank=2, author_id="222", total_reactions=8),
        ],
    )
    roflinkichi = RoleSection(title=SECTION_ROFLINKICHI, entries=[])

    text = format_rofler_success_message(9001, durkichi, roflinkichi)

    assert "<@&9001>" in text
    assert "успешно" in text
    assert "Дуркичи:" in text
    assert "<@111> - 10" in text
    assert "Рофлинкичи:" in text
    assert "(нет данных за период)" in text


def test_format_failure_message_lists_errors():
    text = format_rofler_failure_message(
        year=2026,
        month=3,
        role_id=9001,
        errors=["No permission", "User left"],
    )
    assert "<@&9001>" in text
    assert "не удалась" in text
    assert "No permission" in text


def test_collect_winner_user_ids_dedupes():
    durkichi = RoleSection(
        title=SECTION_DURKICHI,
        entries=[LeaderboardEntry(rank=1, author_id="42", total_reactions=5)],
    )
    roflinkichi = RoleSection(
        title=SECTION_ROFLINKICHI,
        entries=[LeaderboardEntry(rank=1, author_id="42", total_reactions=3)],
    )
    ids = collect_winner_user_ids(
        durkichi, roflinkichi, excluded_user_ids=frozenset({"99"})
    )
    assert ids == [42]


def test_select_top_n_unique_skips_prior_winners():
    pool = [
        LeaderboardEntry(rank=1, author_id="10", total_reactions=99),
        LeaderboardEntry(rank=2, author_id="11", total_reactions=50),
        LeaderboardEntry(rank=3, author_id="12", total_reactions=1),
    ]
    picked = select_top_n_unique(
        pool,
        n=2,
        skip_author_ids=frozenset({"10"}),
        excluded_user_ids=frozenset(),
    )
    assert [e.author_id for e in picked] == ["11", "12"]
    assert [e.rank for e in picked] == [1, 2]


def test_validate_role_settings_rejects_unknown_channel(tmp_path):
    settings = _settings(tmp_path)
    bad = Settings(
        discord_bot_token="t",
        guild_id=1000,
        stats_channel_ids=[111],
        role_rofler_id=1,
        role_notify_channel_id=2,
        role_error_channel_id=3,
        role_durkichi_channel_id=111,
        role_roflinkichi_channel_id=999,
    )
    with pytest.raises(ValueError, match="STATS_CHANNEL_IDS"):
        bad.validate_role_settings()


async def test_apply_rofler_role_strip_and_assign(tmp_path):
    settings = _settings(tmp_path)

    role = MagicMock()
    role.id = 9001

    holder = MagicMock()
    holder.id = 10
    holder.roles = [role]
    holder.remove_roles = AsyncMock()

    winner = MagicMock()
    winner.id = 20
    winner.roles = []
    winner.add_roles = AsyncMock()

    guild = MagicMock()
    guild.id = 1000
    guild.get_role.return_value = role
    type(role).members = PropertyMock(return_value=[holder])
    guild.chunked = True
    guild.get_member.side_effect = lambda uid: winner if uid == 20 else None

    bot = MagicMock()

    result = await apply_rofler_role(bot, guild, [20], settings=settings)

    assert result.success is True
    assert result.stripped_count == 1
    assert result.assigned_count == 1
    holder.remove_roles.assert_awaited_once()
    winner.add_roles.assert_awaited_once()

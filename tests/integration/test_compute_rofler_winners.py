"""Integration: Rofler winners loaded from SQLite per stats channel."""

from __future__ import annotations

import pytest

from bot.config import get_settings
from bot.database.db import Database, MessageRow
from bot.services.role_service import (
    SECTION_DURKICHI,
    SECTION_ROFLINKICHI,
    compute_rofler_winners,
)


@pytest.fixture
def role_env_settings(env_settings, monkeypatch):
    """env_settings plus ROLE_* variables required by validate_role_settings."""
    monkeypatch.setenv("ROLE_REASSIGN_ENABLED", "true")
    monkeypatch.setenv("ROLE_ROFLER_ID", "9001")
    monkeypatch.setenv("ROLE_NOTIFY_CHANNEL_ID", "8001")
    monkeypatch.setenv("ROLE_ERROR_CHANNEL_ID", "8002")
    monkeypatch.setenv("ROLE_DURKICHI_CHANNEL_ID", "111")
    monkeypatch.setenv("ROLE_DURKICHI_TOP_N", "3")
    monkeypatch.setenv("ROLE_ROFLINKICHI_CHANNEL_ID", "222")
    monkeypatch.setenv("ROLE_ROFLINKICHI_TOP_N", "2")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


async def test_compute_rofler_winners_per_channel(role_env_settings):
    settings = role_env_settings
    async with Database(settings.database_path) as db:
        await db.init_db()
        await db.upsert_messages(
            [
                MessageRow(
                    message_id="d1",
                    author_id="10",
                    channel_id="111",
                    guild_id="1000",
                    created_at="2026-03-05 12:00:00",
                    reaction_count=30,
                    last_scanned_at="2026-03-05 12:00:00",
                ),
                MessageRow(
                    message_id="d2",
                    author_id="11",
                    channel_id="111",
                    guild_id="1000",
                    created_at="2026-03-06 12:00:00",
                    reaction_count=20,
                    last_scanned_at="2026-03-06 12:00:00",
                ),
                MessageRow(
                    message_id="d3",
                    author_id="12",
                    channel_id="111",
                    guild_id="1000",
                    created_at="2026-03-07 12:00:00",
                    reaction_count=10,
                    last_scanned_at="2026-03-07 12:00:00",
                ),
                MessageRow(
                    message_id="d4",
                    author_id="13",
                    channel_id="111",
                    guild_id="1000",
                    created_at="2026-03-08 12:00:00",
                    reaction_count=5,
                    last_scanned_at="2026-03-08 12:00:00",
                ),
                MessageRow(
                    message_id="r1",
                    author_id="20",
                    channel_id="222",
                    guild_id="1000",
                    created_at="2026-03-09 12:00:00",
                    reaction_count=99,
                    last_scanned_at="2026-03-09 12:00:00",
                ),
                MessageRow(
                    message_id="r2",
                    author_id="21",
                    channel_id="222",
                    guild_id="1000",
                    created_at="2026-03-10 12:00:00",
                    reaction_count=50,
                    last_scanned_at="2026-03-10 12:00:00",
                ),
                MessageRow(
                    message_id="r3",
                    author_id="22",
                    channel_id="222",
                    guild_id="1000",
                    created_at="2026-03-11 12:00:00",
                    reaction_count=1,
                    last_scanned_at="2026-03-11 12:00:00",
                ),
            ]
        )

    durkichi, roflinkichi = await compute_rofler_winners(2026, 3, settings=settings)

    assert durkichi.title == SECTION_DURKICHI
    assert roflinkichi.title == SECTION_ROFLINKICHI
    assert len(durkichi.entries) == 3
    assert [e.author_id for e in durkichi.entries] == ["10", "11", "12"]
    assert [e.total_reactions for e in durkichi.entries] == [30, 20, 10]
    assert len(roflinkichi.entries) == 2
    assert [e.author_id for e in roflinkichi.entries] == ["20", "21"]


async def test_compute_rofler_winners_skips_overlap_in_second_list(role_env_settings):
    """If a Дуркичи winner is #1 in Рофлинкичи, the second list takes #2 and #3."""
    settings = role_env_settings
    async with Database(settings.database_path) as db:
        await db.init_db()
        await db.upsert_messages(
            [
                MessageRow(
                    message_id="d1",
                    author_id="10",
                    channel_id="111",
                    guild_id="1000",
                    created_at="2026-03-05 12:00:00",
                    reaction_count=30,
                    last_scanned_at="2026-03-05 12:00:00",
                ),
                MessageRow(
                    message_id="d2",
                    author_id="11",
                    channel_id="111",
                    guild_id="1000",
                    created_at="2026-03-06 12:00:00",
                    reaction_count=20,
                    last_scanned_at="2026-03-06 12:00:00",
                ),
                MessageRow(
                    message_id="d3",
                    author_id="12",
                    channel_id="111",
                    guild_id="1000",
                    created_at="2026-03-07 12:00:00",
                    reaction_count=10,
                    last_scanned_at="2026-03-07 12:00:00",
                ),
                MessageRow(
                    message_id="r1",
                    author_id="10",
                    channel_id="222",
                    guild_id="1000",
                    created_at="2026-03-09 12:00:00",
                    reaction_count=99,
                    last_scanned_at="2026-03-09 12:00:00",
                ),
                MessageRow(
                    message_id="r2",
                    author_id="21",
                    channel_id="222",
                    guild_id="1000",
                    created_at="2026-03-10 12:00:00",
                    reaction_count=50,
                    last_scanned_at="2026-03-10 12:00:00",
                ),
                MessageRow(
                    message_id="r3",
                    author_id="22",
                    channel_id="222",
                    guild_id="1000",
                    created_at="2026-03-11 12:00:00",
                    reaction_count=40,
                    last_scanned_at="2026-03-11 12:00:00",
                ),
            ]
        )

    durkichi, roflinkichi = await compute_rofler_winners(2026, 3, settings=settings)

    assert [e.author_id for e in durkichi.entries] == ["10", "11", "12"]
    assert [e.author_id for e in roflinkichi.entries] == ["21", "22"]
    assert len({e.author_id for e in durkichi.entries + roflinkichi.entries}) == 5

"""Shared fixtures: tmp-path-backed Settings, in-memory DB, settings cache reset."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from bot.config import Settings, get_settings
from bot.database.db import Database


def build_settings(tmp_path: Path, **overrides) -> Settings:
    """Construct a frozen ``Settings`` pointing at a temp dir, with overrides."""
    base = dict(
        discord_bot_token="test-token",
        guild_id=1000,
        stats_channel_ids=[111, 222],
        emoji_names=frozenset({"EBALO"}),
        database_path=tmp_path / "leaderboard.db",
        scan_checkpoint_dir=tmp_path / "checkpoints",
        scan_channel_delay_sec=0.0,
        scan_retry_max_attempts=2,
        scan_strict_channels=True,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Keep the ``get_settings`` lru_cache from leaking between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def make_settings(tmp_path):
    def _make(**overrides) -> Settings:
        return build_settings(tmp_path, **overrides)

    return _make


@pytest.fixture
def settings(make_settings) -> Settings:
    return make_settings()


@pytest_asyncio.fixture
async def db(settings):
    async with Database(settings.database_path) as database:
        await database.init_db()
        yield database


@pytest.fixture
def env_settings(monkeypatch, tmp_path):
    """Populate the environment so the real ``get_settings()`` resolves cleanly.

    Used by tests that exercise code paths calling ``get_settings()`` directly
    (the pipeline, CLI and bot entry points) rather than receiving ``Settings``.
    """
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("GUILD_ID", "1000")
    monkeypatch.setenv("STATS_CHANNEL_IDS", "111,222")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "leaderboard.db"))
    monkeypatch.setenv("SCAN_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setenv("SCAN_CHANNEL_DELAY_SEC", "0")
    monkeypatch.setenv("SCAN_RETRY_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("SCAN_STRICT_CHANNELS", "true")
    monkeypatch.setenv("DAILY_SYNC_MESSAGE_DELAY_SEC", "0")
    monkeypatch.setenv("LEADERBOARD_EMOJIS", "EBALO")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()

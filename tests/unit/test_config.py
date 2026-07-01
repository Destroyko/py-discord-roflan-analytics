"""Configuration loading from environment."""

from __future__ import annotations

import pytest

from bot.config import get_settings


def _set_minimal_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("GUILD_ID", "1000")
    monkeypatch.setenv("STATS_CHANNEL_IDS", "111,222")
    monkeypatch.setenv("LEADERBOARD_EMOJIS", "EBALO")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "leaderboard.db"))
    monkeypatch.setenv("SCAN_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))


def test_leaderboard_channel_id_required(monkeypatch, tmp_path):
    _set_minimal_env(monkeypatch, tmp_path)
    monkeypatch.delenv("LEADERBOARD_CHANNEL_ID", raising=False)
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="LEADERBOARD_CHANNEL_ID"):
        get_settings()


def test_leaderboard_channel_id_empty_string_rejected(monkeypatch, tmp_path):
    _set_minimal_env(monkeypatch, tmp_path)
    monkeypatch.setenv("LEADERBOARD_CHANNEL_ID", "   ")
    get_settings.cache_clear()

    with pytest.raises(ValueError, match="LEADERBOARD_CHANNEL_ID"):
        get_settings()


def test_leaderboard_channel_id_loaded_when_set(monkeypatch, tmp_path):
    _set_minimal_env(monkeypatch, tmp_path)
    monkeypatch.setenv("LEADERBOARD_CHANNEL_ID", "3333")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.leaderboard_channel_id == 3333

"""Tests for public embed posting warnings."""

from __future__ import annotations

import discord
import pytest

from bot.pipeline import _embed_channel_error


def test_embed_channel_error_forbidden():
    from unittest.mock import MagicMock

    exc = discord.Forbidden(MagicMock(), {"message": "Missing Access", "code": 50001})
    text = _embed_channel_error(999, exc)
    assert "999" in text
    assert "нет доступа" in text

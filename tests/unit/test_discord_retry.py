"""Unit tests for ``retry_discord``: which Discord errors are retried and how."""

from __future__ import annotations

import asyncio

import pytest

from bot.services import discord_retry
from bot.services.discord_retry import retry_discord
from tests.fakes.channel_reader import make_http_exception


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Make backoff instant and record the requested delays."""
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(discord_retry.asyncio, "sleep", fake_sleep)
    return delays


async def test_returns_immediately_on_success(_no_real_sleep):
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        return "ok"

    result = await retry_discord(factory, max_attempts=5, label="t")

    assert result == "ok"
    assert calls == 1
    assert _no_real_sleep == []


async def test_retries_rate_limited_then_succeeds(_no_real_sleep):
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise make_http_exception(429, retry_after=1.5)
        return "done"

    result = await retry_discord(factory, max_attempts=5, label="t")

    assert result == "done"
    assert calls == 2
    # retry_after honoured (plus a small jitter <= 0.5).
    assert 1.5 <= _no_real_sleep[0] <= 2.0


async def test_retries_server_error_with_backoff(_no_real_sleep):
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise make_http_exception(503)
        return "done"

    result = await retry_discord(factory, max_attempts=5, label="t")

    assert result == "done"
    assert calls == 3
    assert len(_no_real_sleep) == 2
    # Exponential backoff is capped at 30s plus jitter.
    assert all(d <= 30.5 for d in _no_real_sleep)


@pytest.mark.parametrize("status", [403, 404])
async def test_permission_errors_not_retried(status, _no_real_sleep):
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        raise make_http_exception(status)

    with pytest.raises(Exception) as exc_info:
        await retry_discord(factory, max_attempts=5, label="t")

    assert getattr(exc_info.value, "status", None) == status
    assert calls == 1
    assert _no_real_sleep == []


async def test_other_4xx_not_retried(_no_real_sleep):
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        raise make_http_exception(400)

    with pytest.raises(Exception):
        await retry_discord(factory, max_attempts=5, label="t")

    assert calls == 1
    assert _no_real_sleep == []


async def test_gives_up_after_max_attempts(_no_real_sleep):
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        raise make_http_exception(503)

    with pytest.raises(Exception) as exc_info:
        await retry_discord(factory, max_attempts=3, label="t")

    assert getattr(exc_info.value, "status", None) == 503
    assert calls == 3
    assert len(_no_real_sleep) == 2  # slept between the 3 attempts, not after the last

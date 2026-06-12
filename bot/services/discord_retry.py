"""Retry helper for Discord REST calls.

``discord.py`` already retries rate limits internally for most calls. This adds
a coarse, per-call retry around scan units (an entire channel scan, see Block B
§ADR p.7) so a transient 429/5xx does not fail the whole run. Permission errors
(403/404) are not retried — they mean the channel is genuinely unavailable.
"""

from __future__ import annotations

import asyncio
import random
from typing import Awaitable, Callable, TypeVar

import discord

from bot.utils.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

_MAX_BACKOFF_SEC = 30.0


async def retry_discord(
    coro_factory: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    label: str,
) -> T:
    """Run ``coro_factory`` with retries on transient Discord HTTP errors.

    Retries on HTTP 429 (honouring ``retry_after`` when present) and 5xx with
    exponential backoff plus jitter. Re-raises 403/404 and other 4xx immediately,
    and re-raises any error once ``max_attempts`` is exhausted.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return await coro_factory()
        except discord.HTTPException as exc:
            status = getattr(exc, "status", None)
            if status in (403, 404):
                raise
            is_rate_limited = status == 429
            is_server_error = status is not None and 500 <= status < 600
            if not (is_rate_limited or is_server_error):
                raise
            if attempt >= max_attempts:
                logger.error(
                    "Giving up on %s after %s attempts (HTTP %s).",
                    label,
                    attempt,
                    status,
                )
                raise

            retry_after = getattr(exc, "retry_after", None)
            if is_rate_limited and retry_after:
                delay = float(retry_after)
            else:
                delay = min(2.0 ** attempt, _MAX_BACKOFF_SEC)
            delay += random.uniform(0.0, 0.5)
            logger.warning(
                "%s failed (HTTP %s); retry %s/%s in %.1fs.",
                label,
                status,
                attempt,
                max_attempts,
                delay,
            )
            await asyncio.sleep(delay)

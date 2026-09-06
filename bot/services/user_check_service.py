"""Third-party opt-out/moderation check for displayed leaderboards.

Unlike ``EXCLUDED_USER_IDS`` (baked in at scan time — see ``bot/services/scanner.py``),
this check runs against a remote API **at display time only**: the public monthly
TOP post and the ``/show_leaderboard`` admin command both call it right before
rendering. Stored message counts are never touched, so a user's flagged status
can change from one report to the next without rescanning.

Fails open by design: a network error, timeout, non-200 status, or an unexpected
response body is treated as "not flagged" so a flaky third-party service can never
block a report from posting.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import aiohttp

from bot.config import Settings
from bot.services.leaderboard_service import LeaderboardEntry
from bot.utils.logger import get_logger

logger = get_logger(__name__)

# Accepted keys when the API wraps its boolean in a JSON object instead of
# returning a bare `true`/`false` body.
_BOOL_KEYS = ("result", "value", "exists", "found", "flagged", "banned")


async def is_user_flagged(
    session: aiohttp.ClientSession,
    user_id: str,
    *,
    settings: Settings,
) -> bool:
    """True when the external API says ``user_id`` should be hidden from display."""
    if not settings.user_check_api_url:
        return False

    headers = {"Authorization": f"Bearer {settings.user_check_api_token}"}
    timeout = aiohttp.ClientTimeout(total=settings.user_check_api_timeout_sec)
    try:
        async with session.get(
            settings.user_check_api_url,
            params={"id": user_id},
            headers=headers,
            timeout=timeout,
        ) as resp:
            if resp.status != 200:
                logger.warning(
                    "User check API returned status %s for user %s; "
                    "treating as not flagged.",
                    resp.status,
                    user_id,
                )
                return False
            # The API is not guaranteed to send an application/json content
            # type for a bare `true`/`false` body.
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning(
            "User check API call failed for user %s: %s; treating as not flagged.",
            user_id,
            exc,
        )
        return False

    if isinstance(data, bool):
        return data
    if isinstance(data, dict):
        for key in _BOOL_KEYS:
            if isinstance(data.get(key), bool):
                return data[key]
    logger.warning(
        "User check API returned an unexpected payload for user %s: %r; "
        "treating as not flagged.",
        user_id,
        data,
    )
    return False


async def filter_flagged_entries(
    pool: list[LeaderboardEntry],
    *,
    limit: int,
    settings: Settings,
    session: aiohttp.ClientSession | None = None,
) -> list[LeaderboardEntry]:
    """Drop entries the external API flags, backfilling from the next ranks.

    ``pool`` must already be rank-ordered (best first). Returns up to ``limit``
    survivors: the first ``limit`` candidates are checked, and each one the API
    flags is dropped and replaced by the next candidate down the pool — which
    is itself checked before being added, one replacement at a time, until the
    list is full or the pool runs out. Only candidates actually needed to fill
    the list are ever sent to the API (never the whole pool). Survivors are
    renumbered ``1..len(result)`` so the displayed ranks stay contiguous.
    No-op (aside from slicing to ``limit``) when ``USER_CHECK_API_URL`` is not
    configured.
    """
    if not settings.user_check_api_url or not pool:
        return pool[:limit]

    if session is not None:
        survivors = await _collect_unflagged(
            pool, limit=limit, settings=settings, session=session
        )
    else:
        async with aiohttp.ClientSession() as owned_session:
            survivors = await _collect_unflagged(
                pool, limit=limit, settings=settings, session=owned_session
            )
    return [replace(entry, rank=i) for i, entry in enumerate(survivors, start=1)]


async def _collect_unflagged(
    pool: list[LeaderboardEntry],
    *,
    limit: int,
    settings: Settings,
    session: aiohttp.ClientSession,
) -> list[LeaderboardEntry]:
    result: list[LeaderboardEntry] = []
    offset = 0
    while len(result) < limit and offset < len(pool):
        needed = limit - len(result)
        batch = pool[offset : offset + needed]
        offset += len(batch)
        flags = await asyncio.gather(
            *(is_user_flagged(session, e.author_id, settings=settings) for e in batch)
        )
        result.extend(entry for entry, flagged in zip(batch, flags) if not flagged)
    return result

"""Unit tests for the external per-user opt-out/moderation check."""

from __future__ import annotations

import asyncio

from bot.services.leaderboard_service import LeaderboardEntry
from bot.services.user_check_service import filter_flagged_entries, is_user_flagged


class _FakeResponse:
    def __init__(self, status: int, payload):
        self.status = status
        self._payload = payload

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _RaisingResponse:
    def __init__(self, exc: BaseException):
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *exc_info):
        return False


class _FakeSession:
    """Maps a user id (via the ``id`` query param) to a canned response."""

    def __init__(self, by_user_id: dict):
        self._by_user_id = by_user_id
        self.calls: list[str] = []

    def get(self, url, *, params, headers, timeout):
        user_id = params["id"]
        self.calls.append(user_id)
        return self._by_user_id[user_id]


async def test_is_user_flagged_disabled_when_no_url(make_settings):
    settings = make_settings(user_check_api_url=None)
    session = _FakeSession({})
    assert await is_user_flagged(session, "1", settings=settings) is False
    assert session.calls == []


async def test_is_user_flagged_true_on_bare_true_body(make_settings):
    settings = make_settings(
        user_check_api_url="https://example.com/api/anti",
        user_check_api_token="secret",
    )
    session = _FakeSession({"1": _FakeResponse(200, True)})
    assert await is_user_flagged(session, "1", settings=settings) is True


async def test_is_user_flagged_false_on_bare_false_body(make_settings):
    settings = make_settings(user_check_api_url="https://example.com/api/anti")
    session = _FakeSession({"1": _FakeResponse(200, False)})
    assert await is_user_flagged(session, "1", settings=settings) is False


async def test_is_user_flagged_reads_boolean_wrapped_in_object(make_settings):
    settings = make_settings(user_check_api_url="https://example.com/api/anti")
    session = _FakeSession({"1": _FakeResponse(200, {"result": True})})
    assert await is_user_flagged(session, "1", settings=settings) is True


async def test_is_user_flagged_fails_open_on_401(make_settings):
    settings = make_settings(user_check_api_url="https://example.com/api/anti")
    session = _FakeSession(
        {"1": _FakeResponse(401, {"error": "Unauthenticated"})}
    )
    assert await is_user_flagged(session, "1", settings=settings) is False


async def test_is_user_flagged_fails_open_on_timeout(make_settings):
    settings = make_settings(user_check_api_url="https://example.com/api/anti")
    session = _FakeSession({"1": _RaisingResponse(asyncio.TimeoutError())})
    assert await is_user_flagged(session, "1", settings=settings) is False


async def test_is_user_flagged_fails_open_on_unexpected_payload(make_settings):
    settings = make_settings(user_check_api_url="https://example.com/api/anti")
    session = _FakeSession({"1": _FakeResponse(200, {"unexpected": "shape"})})
    assert await is_user_flagged(session, "1", settings=settings) is False


async def test_filter_flagged_entries_noop_when_disabled(make_settings):
    settings = make_settings(user_check_api_url=None)
    pool = [
        LeaderboardEntry(rank=1, author_id="1", total_reactions=10),
        LeaderboardEntry(rank=2, author_id="2", total_reactions=5),
    ]
    result = await filter_flagged_entries(pool, limit=1, settings=settings)
    assert result == pool[:1]


async def test_filter_flagged_entries_backfills_from_next_rank(make_settings):
    settings = make_settings(user_check_api_url="https://example.com/api/anti")
    pool = [
        LeaderboardEntry(rank=1, author_id="1", total_reactions=50),
        LeaderboardEntry(rank=2, author_id="2", total_reactions=40),
        LeaderboardEntry(rank=3, author_id="3", total_reactions=30),
        LeaderboardEntry(rank=4, author_id="4", total_reactions=20),
        LeaderboardEntry(rank=5, author_id="5", total_reactions=10),
    ]
    # user "2" is flagged; the API is only ever asked about the exact
    # candidates needed to fill 3 slots (1, 2, 3, then backfill 4).
    session = _FakeSession(
        {
            "1": _FakeResponse(200, False),
            "2": _FakeResponse(200, True),
            "3": _FakeResponse(200, False),
            "4": _FakeResponse(200, False),
        }
    )
    result = await filter_flagged_entries(
        pool, limit=3, settings=settings, session=session
    )

    assert [e.author_id for e in result] == ["1", "3", "4"]
    assert [e.rank for e in result] == [1, 2, 3]
    assert session.calls == ["1", "2", "3", "4"]


async def test_filter_flagged_entries_only_checks_candidates_needed(make_settings):
    """Only the exact candidates needed to fill the list are ever sent to the
    API — a large pool must not turn into a large number of requests when the
    top of the list is already clean."""
    settings = make_settings(user_check_api_url="https://example.com/api/anti")
    pool = [
        LeaderboardEntry(rank=i, author_id=str(i), total_reactions=1000 - i)
        for i in range(1, 501)
    ]
    session = _FakeSession(
        {str(i): _FakeResponse(200, False) for i in range(1, 501)}
    )

    result = await filter_flagged_entries(
        pool, limit=5, settings=settings, session=session
    )

    assert [e.author_id for e in result] == ["1", "2", "3", "4", "5"]
    assert sorted(session.calls, key=int) == ["1", "2", "3", "4", "5"]


async def test_filter_flagged_entries_runs_out_of_pool(make_settings):
    """If everyone left in the pool is flagged, the list just comes back
    shorter than the requested limit instead of looping forever."""
    settings = make_settings(user_check_api_url="https://example.com/api/anti")
    pool = [
        LeaderboardEntry(rank=1, author_id="1", total_reactions=10),
        LeaderboardEntry(rank=2, author_id="2", total_reactions=5),
    ]
    session = _FakeSession(
        {
            "1": _FakeResponse(200, True),
            "2": _FakeResponse(200, True),
        }
    )

    result = await filter_flagged_entries(
        pool, limit=5, settings=settings, session=session
    )

    assert result == []
    assert sorted(session.calls) == ["1", "2"]

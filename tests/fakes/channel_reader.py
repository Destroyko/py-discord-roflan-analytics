"""Test doubles for the Discord read surface used by the scanner.

These fakes implement just enough of ``discord.TextChannel`` /
``discord.Message`` / ``discord.Reaction`` for ``scan_channels`` and
``count_emoji_reactions`` to run without a real gateway connection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import discord


def make_http_exception(status: int, *, retry_after: float | None = None) -> discord.HTTPException:
    """Build a ``discord.HTTPException`` with a given HTTP status for tests."""
    response = SimpleNamespace(status=status, reason="test-error")
    exc = discord.HTTPException(response, f"HTTP {status}")
    if retry_after is not None:
        exc.retry_after = retry_after
    return exc


class FakeReaction:
    """Minimal stand-in for ``discord.Reaction``."""

    def __init__(self, name: str, count: int) -> None:
        self.emoji = SimpleNamespace(name=name)
        self.count = count


class FakeMessage:
    """Minimal stand-in for ``discord.Message``."""

    def __init__(
        self,
        message_id: int,
        author_id: int,
        *,
        reactions: list[FakeReaction] | None = None,
        created_at: datetime | None = None,
        bot: bool = False,
        system: bool = False,
    ) -> None:
        self.id = message_id
        self.author = SimpleNamespace(id=author_id, bot=bot)
        self.reactions = reactions or []
        self.created_at = created_at or datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        self._system = system

    def is_system(self) -> bool:
        return self._system


class FakeChannel:
    """Stand-in for ``discord.TextChannel`` with a scriptable history.

    ``raise_exc`` lets a test simulate a transient/permanent Discord error. When
    ``raise_after`` is set the channel yields that many messages first and then
    raises (useful for partial-read scenarios); otherwise it raises immediately.
    """

    def __init__(
        self,
        channel_id: int,
        name: str,
        *,
        messages: list[FakeMessage] | None = None,
        raise_exc: Exception | None = None,
        raise_after: int | None = None,
    ) -> None:
        self.id = channel_id
        self.name = name
        self._messages = messages or []
        self._raise_exc = raise_exc
        self._raise_after = raise_after
        self.history_calls = 0

    async def history(self, *, limit=None, after=None, before=None, oldest_first=True):
        self.history_calls += 1
        if self._raise_exc is not None and self._raise_after is None:
            raise self._raise_exc
        emitted = 0
        for message in self._messages:
            yield message
            emitted += 1
            if self._raise_after is not None and emitted >= self._raise_after:
                raise self._raise_exc


class FakeChannelReader:
    """``ChannelReader`` implementation backed by a static channel map.

    A ``None`` value models an unavailable channel (the real
    ``_resolve_text_channel`` returns ``None`` on 403/404).
    """

    def __init__(self, channels: dict[int, FakeChannel | None]) -> None:
        self.channels = channels
        self.fetched: list[int] = []

    async def fetch_text_channel(self, channel_id: int) -> FakeChannel | None:
        self.fetched.append(channel_id)
        return self.channels.get(channel_id)

"""Discord channel access for CLI (HTTP-only) and the running bot gateway."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import discord
from discord.ext import commands

from bot.config import Settings
from bot.utils.logger import get_logger

logger = get_logger(__name__)


@runtime_checkable
class ChannelReader(Protocol):
    """Minimal read-only surface used by the channel scanner."""

    async def fetch_text_channel(
        self, channel_id: int
    ) -> discord.TextChannel | None: ...


async def _resolve_text_channel(
    bot: commands.Bot,
    channel_id: int,
) -> discord.TextChannel | None:
    """Fetch a channel by ID, returning it only if it is a plain text channel."""
    try:
        channel = await bot.fetch_channel(channel_id)
    except discord.NotFound:
        logger.warning("Channel %s not found; skipping.", channel_id)
        return None
    except discord.Forbidden:
        logger.warning("No access to channel %s; skipping.", channel_id)
        return None
    except discord.HTTPException as exc:
        logger.warning("Failed to fetch channel %s: %s; skipping.", channel_id, exc)
        return None

    if not isinstance(channel, discord.TextChannel):
        logger.warning(
            "Channel %s is %s, not a text channel; skipping.",
            channel_id,
            type(channel).__name__,
        )
        return None
    return channel


class BotHttpReader:
    """One-shot HTTP session for CLI runs (``login`` without gateway)."""

    def __init__(self, token: str) -> None:
        self._token = token
        intents = discord.Intents.default()
        intents.guilds = True
        self._bot = commands.Bot(command_prefix="!", intents=intents)

    async def __aenter__(self) -> BotHttpReader:
        await self._bot.login(self._token)
        logger.info("Logged in to Discord (HTTP session).")
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._bot.close()
        logger.info("Discord session closed.")

    async def fetch_text_channel(
        self, channel_id: int
    ) -> discord.TextChannel | None:
        return await _resolve_text_channel(self._bot, channel_id)


def create_bot_http_reader(settings: Settings) -> BotHttpReader:
    """Context manager that logs in with ``settings.discord_bot_token``."""
    return BotHttpReader(settings.discord_bot_token)


class BotChannelReader:
    """Channel reader backed by a running ``commands.Bot`` (gateway)."""

    def __init__(self, bot: commands.Bot) -> None:
        self._bot = bot

    async def fetch_text_channel(
        self, channel_id: int
    ) -> discord.TextChannel | None:
        return await _resolve_text_channel(self._bot, channel_id)

"""Discord bot gateway: slash commands and monthly scheduler."""

from __future__ import annotations

import sys

import discord
from discord.ext import commands

from bot.config import get_settings
from bot.database.db import Database
from bot.utils.logger import get_logger

logger = get_logger(__name__)


class LeaderboardBot(commands.Bot):
    """Bot with DB init and slash sync on startup."""

    async def setup_hook(self) -> None:
        settings = get_settings()
        async with Database(settings.database_path) as db:
            await db.init_db()
        await self.load_extension("bot.cogs.leaderboard")
        synced = await self.tree.sync()
        logger.info("Synced %s application command(s).", len(synced))

    async def on_ready(self) -> None:
        if self.user:
            logger.info("Logged in as %s (%s).", self.user, self.user.id)
        else:
            logger.info("Bot is ready.")


def main() -> int:
    try:
        settings = get_settings()
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    intents = discord.Intents.default()
    intents.guilds = True
    intents.members = True

    bot = LeaderboardBot(command_prefix="!", intents=intents)

    try:
        bot.run(settings.discord_bot_token)
    except KeyboardInterrupt:
        logger.info("Shutting down.")
    except Exception:  # noqa: BLE001
        logger.exception("Bot crashed.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Application configuration loaded from environment variables / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_TIMEZONE = "Europe/Moscow"
DEFAULT_EMOJI = "EBALO"
DEFAULT_DATABASE_PATH = "./data/leaderboard.db"
DEFAULT_TOP_N = 10
DEFAULT_SCAN_BATCH_SIZE = 100
DEFAULT_SCAN_PROGRESS_EVERY = 500
DEFAULT_SCAN_CHECKPOINT_DIR = "./data"
DEFAULT_SCAN_RETRY_MAX_ATTEMPTS = 5
DEFAULT_SCAN_CHANNEL_DELAY_SEC = 0.5
DEFAULT_ROLE_DURKICHI_TOP_N = 3
DEFAULT_ROLE_ROFLINKICHI_TOP_N = 2


@dataclass(frozen=True)
class Settings:
    """Resolved configuration for a single-guild scan."""

    discord_bot_token: str
    guild_id: int
    stats_channel_ids: list[int]
    timezone: str = DEFAULT_TIMEZONE
    emoji_names: frozenset[str] = field(
        default_factory=lambda: frozenset({DEFAULT_EMOJI})
    )
    database_path: Path = field(default_factory=lambda: Path(DEFAULT_DATABASE_PATH))
    top_n: int = DEFAULT_TOP_N
    leaderboard_channel_id: int | None = None
    manual_recalc_role_id: int | None = None
    scan_batch_size: int = DEFAULT_SCAN_BATCH_SIZE
    scan_progress_every: int = DEFAULT_SCAN_PROGRESS_EVERY
    scan_max_messages_per_channel: int = 0
    scan_fetch_if_empty_reactions: bool = False
    scan_checkpoint_dir: Path = field(
        default_factory=lambda: Path(DEFAULT_SCAN_CHECKPOINT_DIR)
    )
    scan_retry_max_attempts: int = DEFAULT_SCAN_RETRY_MAX_ATTEMPTS
    scan_channel_delay_sec: float = DEFAULT_SCAN_CHANNEL_DELAY_SEC
    scan_strict_channels: bool = True
    excluded_user_ids: frozenset[str] = field(default_factory=frozenset)
    role_rofler_id: int | None = None
    role_notify_channel_id: int | None = None
    role_error_channel_id: int | None = None
    role_durkichi_channel_id: int | None = None
    role_durkichi_top_n: int = DEFAULT_ROLE_DURKICHI_TOP_N
    role_roflinkichi_channel_id: int | None = None
    role_roflinkichi_top_n: int = DEFAULT_ROLE_ROFLINKICHI_TOP_N

    def validate_role_settings(self) -> None:
        """Ensure role reassignment env is complete and consistent."""
        missing = [
            name
            for name, value in (
                ("ROLE_ROFLER_ID", self.role_rofler_id),
                ("ROLE_NOTIFY_CHANNEL_ID", self.role_notify_channel_id),
                ("ROLE_ERROR_CHANNEL_ID", self.role_error_channel_id),
                ("ROLE_DURKICHI_CHANNEL_ID", self.role_durkichi_channel_id),
                ("ROLE_ROFLINKICHI_CHANNEL_ID", self.role_roflinkichi_channel_id),
            )
            if value is None
        ]
        if missing:
            raise ValueError(
                "Role reassignment requires: " + ", ".join(missing)
            )
        for channel_id, label in (
            (self.role_durkichi_channel_id, "ROLE_DURKICHI_CHANNEL_ID"),
            (self.role_roflinkichi_channel_id, "ROLE_ROFLINKICHI_CHANNEL_ID"),
        ):
            if channel_id not in self.stats_channel_ids:
                raise ValueError(
                    f"{label} ({channel_id}) must be listed in STATS_CHANNEL_IDS"
                )
        if self.role_durkichi_top_n < 1 or self.role_roflinkichi_top_n < 1:
            raise ValueError("ROLE_*_TOP_N must be at least 1")


def _normalize_emoji_token(raw: str) -> str:
    return raw.strip().strip(":")


def _parse_emoji_names() -> frozenset[str]:
    """Parse ``LEADERBOARD_EMOJIS`` (comma-separated) or legacy ``LEADERBOARD_EMOJI``."""
    multi = os.getenv("LEADERBOARD_EMOJIS")
    single = os.getenv("LEADERBOARD_EMOJI")
    source = multi if multi and multi.strip() else (single or DEFAULT_EMOJI)
    names: list[str] = []
    for part in source.split(","):
        token = _normalize_emoji_token(part)
        if token:
            names.append(token)
    if not names:
        raise ValueError(
            "LEADERBOARD_EMOJIS / LEADERBOARD_EMOJI must list at least one emoji name"
        )
    return frozenset(names)


def _parse_id_list(raw: str | None) -> list[int]:
    if not raw:
        return []
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        ids.append(int(part))
    return ids


def _parse_optional_int(raw: str | None) -> int | None:
    if not raw or not raw.strip():
        return None
    return int(raw.strip())


def _parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _require(name: str, value: str | None) -> str:
    if not value or not value.strip():
        if name == "DISCORD_BOT_TOKEN":
            raise ValueError(
                "Missing DISCORD_BOT_TOKEN; add your bot token to .env "
                "(copy .env.example and paste the token from Developer Portal)."
            )
        raise ValueError(f"Missing required environment variable: {name}")
    return value.strip()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load, validate and cache settings from the environment."""
    load_dotenv()

    token = _require("DISCORD_BOT_TOKEN", os.getenv("DISCORD_BOT_TOKEN"))
    guild_id = int(_require("GUILD_ID", os.getenv("GUILD_ID")))

    stats_ids = _parse_id_list(os.getenv("STATS_CHANNEL_IDS"))
    ignore_ids = set(_parse_id_list(os.getenv("IGNORE_CHANNEL_IDS")))
    stats_ids = [cid for cid in stats_ids if cid not in ignore_ids]
    if not stats_ids:
        raise ValueError(
            "STATS_CHANNEL_IDS is empty (after applying IGNORE_CHANNEL_IDS); "
            "at least one channel ID is required."
        )

    emoji_names = _parse_emoji_names()
    timezone = (os.getenv("LEADERBOARD_TIMEZONE") or DEFAULT_TIMEZONE).strip()
    database_path = Path((os.getenv("DATABASE_PATH") or DEFAULT_DATABASE_PATH).strip())
    top_n = int((os.getenv("LEADERBOARD_TOP_N") or str(DEFAULT_TOP_N)).strip())
    if top_n < 1:
        raise ValueError("LEADERBOARD_TOP_N must be at least 1")

    scan_batch_size = int(
        (os.getenv("SCAN_BATCH_SIZE") or str(DEFAULT_SCAN_BATCH_SIZE)).strip()
    )
    if scan_batch_size < 1:
        raise ValueError("SCAN_BATCH_SIZE must be at least 1")
    scan_progress_every = int(
        (os.getenv("SCAN_PROGRESS_EVERY") or str(DEFAULT_SCAN_PROGRESS_EVERY)).strip()
    )
    if scan_progress_every < 1:
        raise ValueError("SCAN_PROGRESS_EVERY must be at least 1")
    scan_max_messages = int(
        (os.getenv("SCAN_MAX_MESSAGES_PER_CHANNEL") or "0").strip()
    )
    if scan_max_messages < 0:
        raise ValueError("SCAN_MAX_MESSAGES_PER_CHANNEL must be >= 0 (0 = no limit)")
    scan_checkpoint_dir = Path(
        (os.getenv("SCAN_CHECKPOINT_DIR") or DEFAULT_SCAN_CHECKPOINT_DIR).strip()
    )
    scan_retry_max_attempts = int(
        (os.getenv("SCAN_RETRY_MAX_ATTEMPTS") or str(DEFAULT_SCAN_RETRY_MAX_ATTEMPTS))
        .strip()
    )
    if scan_retry_max_attempts < 1:
        raise ValueError("SCAN_RETRY_MAX_ATTEMPTS must be at least 1")
    scan_channel_delay_sec = float(
        (os.getenv("SCAN_CHANNEL_DELAY_SEC") or str(DEFAULT_SCAN_CHANNEL_DELAY_SEC))
        .strip()
    )
    if scan_channel_delay_sec < 0:
        raise ValueError("SCAN_CHANNEL_DELAY_SEC must be >= 0")

    role_durkichi_top_n = int(
        (
            os.getenv("ROLE_DURKICHI_TOP_N")
            or str(DEFAULT_ROLE_DURKICHI_TOP_N)
        ).strip()
    )
    role_roflinkichi_top_n = int(
        (
            os.getenv("ROLE_ROFLINKICHI_TOP_N")
            or str(DEFAULT_ROLE_ROFLINKICHI_TOP_N)
        ).strip()
    )

    return Settings(
        discord_bot_token=token,
        guild_id=guild_id,
        stats_channel_ids=stats_ids,
        timezone=timezone,
        emoji_names=emoji_names,
        database_path=database_path,
        top_n=top_n,
        leaderboard_channel_id=_parse_optional_int(
            os.getenv("LEADERBOARD_CHANNEL_ID")
        ),
        manual_recalc_role_id=_parse_optional_int(
            os.getenv("MANUAL_RECALC_ROLE_ID")
        ),
        scan_batch_size=scan_batch_size,
        scan_progress_every=scan_progress_every,
        scan_max_messages_per_channel=scan_max_messages,
        scan_fetch_if_empty_reactions=_parse_bool(
            os.getenv("SCAN_FETCH_IF_EMPTY_REACTIONS"), False
        ),
        scan_checkpoint_dir=scan_checkpoint_dir,
        scan_retry_max_attempts=scan_retry_max_attempts,
        scan_channel_delay_sec=scan_channel_delay_sec,
        scan_strict_channels=_parse_bool(os.getenv("SCAN_STRICT_CHANNELS"), True),
        excluded_user_ids=frozenset(
            str(uid) for uid in _parse_id_list(os.getenv("EXCLUDED_USER_IDS"))
        ),
        role_rofler_id=_parse_optional_int(os.getenv("ROLE_ROFLER_ID")),
        role_notify_channel_id=_parse_optional_int(
            os.getenv("ROLE_NOTIFY_CHANNEL_ID")
        ),
        role_error_channel_id=_parse_optional_int(
            os.getenv("ROLE_ERROR_CHANNEL_ID")
        ),
        role_durkichi_channel_id=_parse_optional_int(
            os.getenv("ROLE_DURKICHI_CHANNEL_ID")
        ),
        role_durkichi_top_n=role_durkichi_top_n,
        role_roflinkichi_channel_id=_parse_optional_int(
            os.getenv("ROLE_ROFLINKICHI_CHANNEL_ID")
        ),
        role_roflinkichi_top_n=role_roflinkichi_top_n,
    )

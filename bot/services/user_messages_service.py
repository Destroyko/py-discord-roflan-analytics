"""Export and display per-author message lists with Discord links."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from bot.services.leaderboard_service import format_emoji_label
from bot.utils.discord_links import message_jump_url
from bot.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class UserMessageRow:
    """One message contributing to a user's reaction total."""

    message_id: str
    channel_id: str
    reaction_count: int
    created_at: str
    message_link: str


def build_user_message_rows(
    raw_rows: list[tuple[str, str, int, str]],
    *,
    guild_id: int,
) -> list[UserMessageRow]:
    return [
        UserMessageRow(
            message_id=message_id,
            channel_id=channel_id,
            reaction_count=reaction_count,
            created_at=created_at,
            message_link=message_jump_url(guild_id, channel_id, message_id),
        )
        for message_id, channel_id, reaction_count, created_at in raw_rows
    ]


def format_console_user_messages(
    author_id: str,
    rows: list[UserMessageRow],
    *,
    year: int,
    month: int,
    emoji_names: frozenset[str],
) -> str:
    total = sum(r.reaction_count for r in rows)
    header = (
        f"Messages for user {author_id} ({year}-{month:02d}), "
        f"emoji {format_emoji_label(emoji_names)}, count={len(rows)}, sum={total}"
    )
    if not rows:
        return f"{header}\n  (no messages in database for this user/period)"

    lines = [header]
    for row in rows:
        lines.append(
            f"  reactions x{row.reaction_count}  {row.created_at}  {row.message_link}"
        )
    return "\n".join(lines)


def save_user_messages_csv(path: Path, author_id: str, rows: list[UserMessageRow]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "author_id",
                "message_id",
                "channel_id",
                "reaction_count",
                "created_at_utc",
                "message_link",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    author_id,
                    row.message_id,
                    row.channel_id,
                    row.reaction_count,
                    row.created_at,
                    row.message_link,
                ]
            )
    logger.info("Saved CSV to %s", path)
    return path

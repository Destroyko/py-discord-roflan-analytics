"""Per-channel cursor for incremental daily sync (newest indexed message id)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from bot.config import Settings
from bot.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ChannelSyncCursor:
    """Newest message snowflake indexed from channel history for this month."""

    last_message_id: int | None = None


@dataclass
class SyncState:
    """Cursors for one guild + calendar month."""

    guild_id: int
    year: int
    month: int
    channels: dict[str, ChannelSyncCursor] = field(default_factory=dict)

    def channel(self, channel_id: int) -> ChannelSyncCursor:
        return self.channels.setdefault(str(channel_id), ChannelSyncCursor())


def sync_state_path(settings: Settings, year: int, month: int) -> Path:
    return settings.scan_checkpoint_dir / (
        f"sync_state_{settings.guild_id}_{year}-{month:02d}.json"
    )


def load_sync_state(settings: Settings, year: int, month: int) -> SyncState:
    path = sync_state_path(settings, year, month)
    if not path.exists():
        return SyncState(
            guild_id=settings.guild_id,
            year=year,
            month=month,
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    channels: dict[str, ChannelSyncCursor] = {}
    for channel_id, data in (raw.get("channels") or {}).items():
        last_id = data.get("last_message_id")
        channels[str(channel_id)] = ChannelSyncCursor(
            last_message_id=int(last_id) if last_id is not None else None,
        )
    return SyncState(
        guild_id=int(raw.get("guild_id", settings.guild_id)),
        year=int(raw.get("year", year)),
        month=int(raw.get("month", month)),
        channels=channels,
    )


def save_sync_state(settings: Settings, state: SyncState) -> None:
    path = sync_state_path(settings, state.year, state.month)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "guild_id": state.guild_id,
        "year": state.year,
        "month": state.month,
        "channels": {
            channel_id: {"last_message_id": cursor.last_message_id}
            for channel_id, cursor in state.channels.items()
            if cursor.last_message_id is not None
        },
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    logger.debug("Saved sync state to %s.", path)

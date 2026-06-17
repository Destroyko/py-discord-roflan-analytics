"""Lightweight JSON checkpoint for resumable scans (Block B).

Stores only per-channel status and a run phase — never the full set of scanned
message ids (those live in ``messages_staging``). The checkpoint file doubles as
a run lock: while it exists with phase ``scanning``/``ready_to_commit``, a new
non-resumed run for the same period is refused.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from bot.config import Settings
from bot.utils.logger import get_logger

logger = get_logger(__name__)

ChannelStatus = Literal[
    "pending", "in_progress", "completed", "failed", "incomplete", "skipped"
]
ScanPhase = Literal["scanning", "ready_to_commit", "committed"]


@dataclass
class ChannelScanState:
    """Per-channel progress within a run."""

    status: ChannelStatus = "pending"
    matched: int = 0
    messages_seen: int = 0
    error: str | None = None


@dataclass
class ScanCheckpoint:
    """Resumable state for one ``(guild, year, month)`` scan run."""

    run_id: str
    guild_id: int
    year: int
    month: int
    phase: ScanPhase = "scanning"
    locked_at: str = ""
    channels: dict[str, ChannelScanState] = field(default_factory=dict)

    def channel(self, channel_id: int) -> ChannelScanState:
        return self.channels.setdefault(str(channel_id), ChannelScanState())


def checkpoint_path(settings: Settings, year: int, month: int) -> Path:
    return settings.scan_checkpoint_dir / (
        f"scan_checkpoint_{settings.guild_id}_{year}-{month:02d}.json"
    )


def new_checkpoint(
    *, run_id: str, guild_id: int, year: int, month: int, channel_ids: list[int]
) -> ScanCheckpoint:
    return ScanCheckpoint(
        run_id=run_id,
        guild_id=guild_id,
        year=year,
        month=month,
        phase="scanning",
        locked_at=datetime.now(tz=timezone.utc).isoformat(),
        channels={str(cid): ChannelScanState() for cid in channel_ids},
    )


def load_checkpoint(
    settings: Settings, year: int, month: int
) -> ScanCheckpoint | None:
    path = checkpoint_path(settings, year, month)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read checkpoint %s: %s; ignoring.", path, exc)
        return None

    channels = {
        cid: ChannelScanState(**state)
        for cid, state in (raw.get("channels") or {}).items()
    }
    return ScanCheckpoint(
        run_id=raw["run_id"],
        guild_id=int(raw["guild_id"]),
        year=int(raw["year"]),
        month=int(raw["month"]),
        phase=raw.get("phase", "scanning"),
        locked_at=raw.get("locked_at", ""),
        channels=channels,
    )


def _checkpoint_payload(checkpoint: ScanCheckpoint) -> dict:
    return {
        "run_id": checkpoint.run_id,
        "guild_id": checkpoint.guild_id,
        "year": checkpoint.year,
        "month": checkpoint.month,
        "phase": checkpoint.phase,
        "locked_at": checkpoint.locked_at,
        "channels": {
            cid: asdict(state) for cid, state in checkpoint.channels.items()
        },
    }


class CheckpointBusy(Exception):
    """Another process holds an in-progress checkpoint for this period."""


def claim_checkpoint(settings: Settings, checkpoint: ScanCheckpoint) -> None:
    """Create the checkpoint file immediately; refuse if a run is in progress."""
    path = checkpoint_path(settings, checkpoint.year, checkpoint.month)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(_checkpoint_payload(checkpoint), indent=2)
    try:
        with open(path, "x", encoding="utf-8") as handle:
            handle.write(serialized)
    except FileExistsError:
        existing = load_checkpoint(settings, checkpoint.year, checkpoint.month)
        if existing is not None and existing.phase in ("scanning", "ready_to_commit"):
            raise CheckpointBusy(checkpoint.year, checkpoint.month)
        save_checkpoint(settings, checkpoint)


def save_checkpoint(settings: Settings, checkpoint: ScanCheckpoint) -> None:
    """Atomically persist the checkpoint (temp file + rename)."""
    path = checkpoint_path(settings, checkpoint.year, checkpoint.month)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _checkpoint_payload(checkpoint)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def clear_checkpoint(settings: Settings, year: int, month: int) -> None:
    path = checkpoint_path(settings, year, month)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Failed to remove checkpoint %s: %s.", path, exc)

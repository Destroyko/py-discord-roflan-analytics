"""Unit tests for checkpoint persistence: round-trip, atomicity, corruption."""

from __future__ import annotations

from bot.services.scan_checkpoint import (
    checkpoint_path,
    clear_checkpoint,
    load_checkpoint,
    new_checkpoint,
    save_checkpoint,
)


def test_new_checkpoint_starts_pending(settings):
    cp = new_checkpoint(
        run_id="r1", guild_id=settings.guild_id, year=2026, month=1,
        channel_ids=[111, 222],
    )
    assert cp.phase == "scanning"
    assert set(cp.channels) == {"111", "222"}
    assert all(state.status == "pending" for state in cp.channels.values())
    assert cp.locked_at  # timestamp set


def test_save_and_load_round_trip(settings):
    cp = new_checkpoint(
        run_id="r1", guild_id=settings.guild_id, year=2026, month=1,
        channel_ids=[111, 222],
    )
    cp.channel(111).status = "completed"
    cp.channel(111).matched = 7
    cp.phase = "ready_to_commit"

    save_checkpoint(settings, cp)
    loaded = load_checkpoint(settings, 2026, 1)

    assert loaded is not None
    assert loaded.run_id == "r1"
    assert loaded.phase == "ready_to_commit"
    assert loaded.channel(111).status == "completed"
    assert loaded.channel(111).matched == 7
    assert loaded.channel(222).status == "pending"


def test_save_leaves_no_temp_file(settings):
    cp = new_checkpoint(
        run_id="r1", guild_id=settings.guild_id, year=2026, month=1,
        channel_ids=[111],
    )
    save_checkpoint(settings, cp)

    path = checkpoint_path(settings, 2026, 1)
    assert path.exists()
    assert not path.with_suffix(path.suffix + ".tmp").exists()


def test_load_missing_returns_none(settings):
    assert load_checkpoint(settings, 2026, 1) is None


def test_load_corrupted_returns_none(settings):
    path = checkpoint_path(settings, 2026, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not valid json", encoding="utf-8")

    assert load_checkpoint(settings, 2026, 1) is None


def test_clear_checkpoint_removes_file(settings):
    cp = new_checkpoint(
        run_id="r1", guild_id=settings.guild_id, year=2026, month=1,
        channel_ids=[111],
    )
    save_checkpoint(settings, cp)
    assert checkpoint_path(settings, 2026, 1).exists()

    clear_checkpoint(settings, 2026, 1)
    assert not checkpoint_path(settings, 2026, 1).exists()
    # Idempotent: clearing a missing checkpoint does not raise.
    clear_checkpoint(settings, 2026, 1)

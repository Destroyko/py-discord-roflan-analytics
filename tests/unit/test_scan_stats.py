"""Unit tests for the ``ScanStats.success`` gate that guards the prod commit."""

from __future__ import annotations

from bot.services.scanner import ScanStats


def _stats(**overrides) -> ScanStats:
    base = dict(run_id="r", channels_total=3)
    base.update(overrides)
    return ScanStats(**base)


def test_success_when_all_completed():
    assert _stats(channels_completed=3).success is True


def test_success_when_completed_plus_skipped_cover_total():
    assert _stats(channels_completed=2, channels_skipped=1).success is True


def test_not_success_with_failed_channel():
    stats = _stats(channels_completed=2, channels_failed=1, failed_channel_ids=[222])
    assert stats.success is False


def test_not_success_with_incomplete_channel():
    stats = _stats(
        channels_completed=2, channels_incomplete=1, incomplete_channel_ids=[222]
    )
    assert stats.success is False


def test_not_success_when_channels_unaccounted():
    # Only one of three channels finished; the rest never ran.
    assert _stats(channels_completed=1).success is False

"""Monthly scheduler time helpers."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from bot.utils.dates import next_monthly_run_at


def test_next_monthly_run_at_defaults_to_10_00_moscow(env_settings):
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 3, 1, 9, 30, tzinfo=tz)
    target = next_monthly_run_at(now=now)
    assert target == datetime(2026, 3, 1, 10, 0, tzinfo=tz)


def test_next_monthly_run_at_rolls_to_next_month_after_run(env_settings):
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 3, 1, 10, 1, tzinfo=tz)
    target = next_monthly_run_at(now=now)
    assert target == datetime(2026, 4, 1, 10, 0, tzinfo=tz)

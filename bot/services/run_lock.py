"""In-process lock for concurrent pipeline runs (same guild + month)."""

from __future__ import annotations

_active_runs: set[tuple[int, int, int]] = set()


def scan_busy_message(year: int, month: int) -> str:
    """User-facing text when a recalc for this period is already running."""
    return (
        f"Пересчёт за **{year}-{month:02d}** уже выполняется.\n"
        "Дождитесь завершения. Если предыдущий запуск оборвался — "
        f"повторите **/recalculate_leaderboard** с **resume: да** "
        "(тот же год и месяц)."
    )


class PipelineBusyError(Exception):
    """Another pipeline run for this period is active in this process."""

    def __init__(self, year: int, month: int) -> None:
        self.user_message = scan_busy_message(year, month)
        super().__init__(self.user_message)


def try_acquire_memory_run(guild_id: int, year: int, month: int) -> bool:
    """Claim the in-memory run slot; returns False if already taken."""
    key = (guild_id, year, month)
    if key in _active_runs:
        return False
    _active_runs.add(key)
    return True


def release_memory_run(guild_id: int, year: int, month: int) -> None:
    _active_runs.discard((guild_id, year, month))

"""The bot entry point must exit non-zero (not hang) when the gateway crashes."""

from __future__ import annotations

from bot import main as main_module


def test_main_returns_1_when_bot_run_raises(env_settings, monkeypatch):
    class FakeBot:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def run(self, _token: str) -> None:
            raise RuntimeError("gateway exploded")

    monkeypatch.setattr(main_module, "LeaderboardBot", FakeBot)
    assert main_module.main() == 1


def test_main_returns_1_on_bad_config(monkeypatch):
    def boom() -> None:
        raise ValueError("missing token")

    monkeypatch.setattr(main_module, "get_settings", boom)
    assert main_module.main() == 1

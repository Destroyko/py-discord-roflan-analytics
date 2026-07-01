"""Pipeline embed_posted and role reassignment isolation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.pipeline import _run_pipeline_body
from bot.services.channel_top_service import NamedChannelTop
from bot.services.leaderboard_service import LeaderboardEntry
from bot.services.scanner import ScanStats


@pytest.mark.asyncio
async def test_embed_posted_true_when_send_succeeds(make_settings, monkeypatch):
    settings = make_settings()
    object.__setattr__(settings, "role_durkichi_channel_id", 111)
    object.__setattr__(settings, "role_roflinkichi_channel_id", 222)

    stats = ScanStats(run_id="r1", channels_total=1, channels_completed=1)
    monkeypatch.setattr(
        "bot.pipeline._prepare_checkpoint",
        lambda *a, **k: (MagicMock(phase="scanning"), "r1", None),
    )
    monkeypatch.setattr(
        "bot.pipeline._scan",
        AsyncMock(return_value=stats),
    )
    monkeypatch.setattr(
        "bot.pipeline.save_checkpoint",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "bot.pipeline.clear_checkpoint",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "bot.pipeline.build_leaderboard",
        AsyncMock(
            return_value=[LeaderboardEntry(rank=1, author_id="1", total_reactions=3)]
        ),
    )
    monkeypatch.setattr(
        "bot.pipeline.load_leaderboard_post_channel_tops",
        AsyncMock(
            return_value=[
                NamedChannelTop(title="Дуркичи", channel_id=111, entries=[]),
                NamedChannelTop(title="Рофлинкичи", channel_id=222, entries=[]),
            ]
        ),
    )
    monkeypatch.setattr(
        "bot.pipeline._post_leaderboard_embed",
        AsyncMock(return_value=None),
    )
    role_mock = AsyncMock(side_effect=RuntimeError("roles broke"))
    monkeypatch.setattr("bot.pipeline.role_service.run_rofler_role_reassignment", role_mock)

    bot = MagicMock()
    reader = MagicMock()
    monkeypatch.setattr("bot.pipeline.get_settings", lambda: settings)
    monkeypatch.setattr("bot.utils.dates.get_settings", lambda: settings)

    with patch("bot.pipeline.Database") as db_cls:
        db = AsyncMock()
        db_cls.return_value.__aenter__.return_value = db
        db.init_db = AsyncMock()
        db.discard_staging_run = AsyncMock()
        db.commit_scan_run = AsyncMock(return_value=1)
        db.count_staging_run = AsyncMock(return_value=1)

        result = await _run_pipeline_body(
            2026,
            5,
            settings=settings,
            reader=reader,
            post_embed=True,
            assign_roles=True,
            bot=bot,
            print_top=False,
            resume=False,
        )

    assert result.embed_posted is True
    role_mock.assert_awaited_once()

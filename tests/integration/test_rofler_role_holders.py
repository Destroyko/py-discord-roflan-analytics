"""SQLite persistence for Rofler role holder IDs."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_rofler_role_holders_roundtrip(db):
    guild_id = "1000"
    assert await db.get_rofler_role_holder_ids(guild_id) == []

    await db.replace_rofler_role_holders(guild_id, [10, 20, 30])
    assert sorted(await db.get_rofler_role_holder_ids(guild_id)) == [10, 20, 30]

    await db.replace_rofler_role_holders(guild_id, [20, 40])
    assert sorted(await db.get_rofler_role_holder_ids(guild_id)) == [20, 40]

    await db.replace_rofler_role_holders(guild_id, [])
    assert await db.get_rofler_role_holder_ids(guild_id) == []

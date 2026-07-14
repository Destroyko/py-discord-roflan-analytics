"""Unit tests for ``count_emoji_reactions`` emoji selection and summation."""

from __future__ import annotations

from bot.services.scanner import count_emoji_reactions
from tests.fakes.channel_reader import FakeReaction


def test_counts_single_configured_emoji():
    reactions = [FakeReaction("EBALO", 5)]
    assert (
        count_emoji_reactions(reactions, frozenset({"EBALO"}), guild_id_str="1000") == 5
    )


def test_sums_multiple_configured_emojis():
    reactions = [FakeReaction("EBALO", 5), FakeReaction("ROFL", 6)]
    total = count_emoji_reactions(
        reactions, frozenset({"EBALO", "ROFL"}), guild_id_str="1000"
    )
    assert total == 11


def test_ignores_unconfigured_emoji():
    reactions = [FakeReaction("EBALO", 5), FakeReaction("THUMBSUP", 99)]
    assert (
        count_emoji_reactions(reactions, frozenset({"EBALO"}), guild_id_str="1000") == 5
    )


def test_no_matching_reactions_returns_zero():
    reactions = [FakeReaction("THUMBSUP", 3)]
    assert (
        count_emoji_reactions(reactions, frozenset({"EBALO"}), guild_id_str="1000") == 0
    )


def test_empty_reactions_returns_zero():
    assert count_emoji_reactions([], frozenset({"EBALO"}), guild_id_str="1000") == 0


def test_ignores_same_name_custom_emoji_from_another_guild():
    reactions = [
        FakeReaction("EBALO", 5, guild_id=1000),
        FakeReaction("EBALO", 99, guild_id=2000),
    ]

    assert (
        count_emoji_reactions(reactions, frozenset({"EBALO"}), guild_id_str="1000") == 5
    )


def test_ignores_custom_emoji_when_source_guild_is_unknown():
    reactions = [FakeReaction("EBALO", 5, guild_id=None)]

    assert (
        count_emoji_reactions(reactions, frozenset({"EBALO"}), guild_id_str="1000") == 0
    )

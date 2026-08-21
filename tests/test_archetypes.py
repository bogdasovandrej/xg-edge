"""Deterministic archetype tagging for cross-match portfolio exposure."""
from __future__ import annotations

import pytest

from xgedge.markets.archetypes import KNOWN_ARCHETYPES, MatchArchetypeContext, tag_archetypes


def test_btts_tags() -> None:
    assert tag_archetypes(market_family="BTTS", market_cluster="BTTS_NO", selection="no") == [
        "BTTS_NO"
    ]
    assert tag_archetypes(market_family="BTTS", market_cluster="BTTS_YES", selection="yes") == [
        "BTTS_YES"
    ]


def test_second_leg_totals_require_leg_context() -> None:
    ctx = MatchArchetypeContext(leg_number=2)
    tags = tag_archetypes(
        market_family="TOTALS", market_cluster="TOTAL_UNDER", selection="under", context=ctx
    )
    assert "SECOND_LEG_UNDER" in tags
    # First leg (or unknown leg) must not be tagged as a second-leg archetype.
    first_leg = tag_archetypes(market_family="TOTALS", market_cluster="TOTAL_UNDER", selection="under")
    assert "SECOND_LEG_UNDER" not in first_leg


def test_big_dog_handicap_requires_large_underdog_line() -> None:
    ctx = MatchArchetypeContext(favorite_side="home")
    dog = tag_archetypes(
        market_family="ASIAN_HANDICAP",
        market_cluster="HANDICAP_AWAY",
        selection="away",
        line=1.5,
        context=ctx,
    )
    assert "BIG_DOG_HANDICAP" in dog
    small_dog = tag_archetypes(
        market_family="ASIAN_HANDICAP",
        market_cluster="HANDICAP_AWAY",
        selection="away",
        line=0.5,
        context=ctx,
    )
    assert "BIG_DOG_HANDICAP" not in small_dog
    favorite_leg = tag_archetypes(
        market_family="ASIAN_HANDICAP",
        market_cluster="HANDICAP_HOME",
        selection="home",
        line=1.5,
        context=ctx,
    )
    assert "BIG_DOG_HANDICAP" not in favorite_leg


def test_home_comeback_requires_trailing_home_second_leg() -> None:
    ctx = MatchArchetypeContext(leg_number=2, aggregate_leader="away", favorite_side="away")
    tags = tag_archetypes(
        market_family="MATCH_RESULT", market_cluster="HOME_RESULT", selection="home", context=ctx
    )
    assert "HOME_COMEBACK" in tags
    not_trailing = tag_archetypes(
        market_family="MATCH_RESULT",
        market_cluster="HOME_RESULT",
        selection="home",
        context=MatchArchetypeContext(leg_number=2, aggregate_leader="home", favorite_side="home"),
    )
    assert "HOME_COMEBACK" not in not_trailing


def test_qualification_favorite_vs_dog() -> None:
    ctx = MatchArchetypeContext(favorite_side="home")
    fav = tag_archetypes(
        market_family="QUALIFICATION", market_cluster="QUALIFICATION_HOME", selection="home", context=ctx
    )
    dog = tag_archetypes(
        market_family="QUALIFICATION", market_cluster="QUALIFICATION_AWAY", selection="away", context=ctx
    )
    assert fav == ["FAVORITE_CONTROL", "QUALIFICATION_FAVORITE"]
    assert dog == ["QUALIFICATION_DOG"]


def test_all_tags_are_known() -> None:
    ctx = MatchArchetypeContext(leg_number=2, aggregate_leader="away", favorite_side="away")
    tags = tag_archetypes(
        market_family="ASIAN_HANDICAP",
        market_cluster="HANDICAP_HOME",
        selection="home",
        line=2.0,
        context=ctx,
    )
    assert set(tags) <= KNOWN_ARCHETYPES


def test_invalid_context_rejected() -> None:
    with pytest.raises(ValueError):
        MatchArchetypeContext(aggregate_leader="draw")

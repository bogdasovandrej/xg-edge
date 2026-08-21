"""Qualification settlement must fail closed without an official winner."""
from __future__ import annotations

import pytest

from xgedge.markets.qualification import (
    CompetitionAdvanceRules,
    qualification_probability_distribution,
    settle_qualification_market,
)


def test_settles_by_official_qualified_team_id_only() -> None:
    assert settle_qualification_market(
        selection="home", home_team_id="A", away_team_id="B", qualified_team_id="A"
    ) == "win"
    assert settle_qualification_market(
        selection="away", home_team_id="A", away_team_id="B", qualified_team_id="A"
    ) == "loss"


def test_rejects_settlement_without_confirmed_winner() -> None:
    with pytest.raises(ValueError):
        settle_qualification_market(
            selection="home", home_team_id="A", away_team_id="B", qualified_team_id=None
        )


def test_rejects_winner_outside_the_fixture() -> None:
    with pytest.raises(ValueError):
        settle_qualification_market(
            selection="home", home_team_id="A", away_team_id="B", qualified_team_id="C"
        )


def test_extra_time_and_penalties_do_not_change_settlement_by_score() -> None:
    # A drawn aggregate that went to penalties still settles purely on the
    # confirmed winner, never on any regulation/extra-time score field.
    assert settle_qualification_market(
        selection="away", home_team_id="A", away_team_id="B", qualified_team_id="B"
    ) == "win"


def test_probability_distribution_matches_advance_probability() -> None:
    distribution = qualification_probability_distribution(selection="home", home_to_advance=0.63)
    assert distribution.fair_odds() == pytest.approx(1.0 / 0.63)
    away = qualification_probability_distribution(selection="away", home_to_advance=0.63)
    assert away.fair_odds() == pytest.approx(1.0 / 0.37)


def test_advance_rules_contract_requires_identity() -> None:
    rules = CompetitionAdvanceRules(competition_id="UCL_QUAL", season_id="2026-27")
    payload = rules.as_dict()
    assert payload["schema_version"] == "competition-advance-rules/1.0"
    assert payload["away_goals_rule"] is False
    with pytest.raises(ValueError):
        CompetitionAdvanceRules(competition_id="", season_id="2026-27").validate()

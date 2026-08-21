"""Same-match joint probability must not fall back to a naive product."""
from __future__ import annotations

import pytest

from xgedge.markets.joint import (
    JointLeg,
    joint_combo_expected_value,
    joint_fair_combo_odds,
    joint_settlement_distribution,
    joint_win_probability,
    naive_independent_probability,
)
from xgedge.markets.paper_markets import score_matrix


MATRIX = score_matrix(1.55, 1.05)


def test_joint_probability_diverges_from_naive_product() -> None:
    legs = (
        JointLeg("totals", "under", 2.5),
        JointLeg("btts", "no"),
    )
    joint = joint_win_probability(MATRIX, legs)
    naive = naive_independent_probability(MATRIX, legs)
    # Under 2.5 and BTTS No overlap heavily (most no-BTTS games are under 2.5),
    # so the true joint mass must sit well above the independent product.
    assert joint > naive
    assert 0.0 < joint < 1.0
    assert 0.0 < naive < 1.0


def test_joint_distribution_sums_to_one_and_rejects_single_leg() -> None:
    legs = (JointLeg("totals", "over", 2.5), JointLeg("draw_no_bet", "home"))
    distribution = joint_settlement_distribution(MATRIX, legs)
    assert abs(sum(distribution.values()) - 1.0) < 1e-9
    with pytest.raises(ValueError):
        joint_settlement_distribution(MATRIX, (legs[0],))


def test_joint_rejects_non_score_resolvable_leg() -> None:
    with pytest.raises(ValueError):
        JointLeg("qualification", "home")


def test_fair_combo_odds_matches_win_probability() -> None:
    legs = (JointLeg("totals", "under", 3.5), JointLeg("team_totals", "away_under", 1.5))
    fair = joint_fair_combo_odds(MATRIX, legs)
    probability = joint_win_probability(MATRIX, legs)
    assert fair == pytest.approx(1.0 / probability)


def test_combo_expected_value_is_push_aware_for_quarter_lines() -> None:
    legs = (JointLeg("totals", "under", 2.75), JointLeg("btts", "no"))
    # A push on the quarter leg should not zero out the whole combo return.
    ev_at_fair = joint_combo_expected_value(
        MATRIX, legs, (joint_fair_combo_odds(MATRIX, legs), 1.01)
    )
    assert ev_at_fair > -1.0
    with pytest.raises(ValueError):
        joint_combo_expected_value(MATRIX, legs, (2.0,))

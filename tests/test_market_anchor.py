"""Tests for market anchoring, nested selection and the CLV safety gate."""
from __future__ import annotations

import numpy as np
import pytest

from xgedge.decision.market_anchor import (
    AnchorConfig,
    MarketAnchor,
    candidate_bets_1x2,
    centered_log_ratio,
    clv_betting_gate,
    devig_opening_odds,
    guarded_bet_decision,
    select_anchor_on_late_development,
)


def _odds_from_probs(probs: np.ndarray, margin: float = 1.04) -> np.ndarray:
    return 1.0 / (np.asarray(probs, dtype=float) * margin)


def test_devig_and_clr_are_compositional() -> None:
    odds = np.array([[2.0, 3.5, 4.0], [1.5, 5.0, 8.0]])
    probs = devig_opening_odds(odds)
    np.testing.assert_allclose(probs.sum(axis=1), 1.0)
    np.testing.assert_allclose(centered_log_ratio(probs).sum(axis=1), 0.0, atol=1e-12)
    with pytest.raises(ValueError):
        devig_opening_odds(np.array([[2.0, 1.0, 3.0]]))


def test_longshot_bucket_shrinks_model_disagreement() -> None:
    market = np.array([[0.70, 0.20, 0.10]])
    opening = _odds_from_probs(market)
    raw = np.array([[0.50, 0.20, 0.30]])
    unrestricted = MarketAnchor(
        AnchorConfig(residual_weight=1.0, longshot_weight=1.0),
        bias=np.zeros(3),
    ).predict_proba(raw, opening)
    controlled = MarketAnchor(
        AnchorConfig(residual_weight=1.0, longshot_weight=0.0, longshot_probability=0.15),
        bias=np.zeros(3),
    ).predict_proba(raw, opening)
    prior = devig_opening_odds(opening)
    assert abs(controlled[0, 2] - prior[0, 2]) < abs(
        unrestricted[0, 2] - prior[0, 2]
    )


def test_fit_is_deterministic_and_probabilities_are_valid() -> None:
    market = np.tile([0.50, 0.27, 0.23], (90, 1))
    raw = np.tile([0.55, 0.25, 0.20], (90, 1))
    outcomes = np.array((["H"] * 45) + (["D"] * 24) + (["A"] * 21))
    opening = _odds_from_probs(market)
    config = AnchorConfig(residual_weight=0.30)
    first = MarketAnchor(config).fit(raw, opening, outcomes)
    second = MarketAnchor(config).fit(raw, opening, outcomes)
    np.testing.assert_allclose(first.bias_, second.bias_)
    predicted = first.predict_proba(raw, opening)
    np.testing.assert_allclose(predicted.sum(axis=1), 1.0)
    assert np.all(predicted > 0.0)


def test_candidate_selection_is_one_per_match_and_respects_max_odds() -> None:
    probs = np.array([[0.60, 0.25, 0.15], [0.35, 0.20, 0.45]])
    taken = np.array([[2.0, 4.0, 12.0], [3.0, 5.5, 2.5]])
    closing = np.array([[1.9, 4.2, 10.0], [2.8, 5.0, 2.7]])
    bets = candidate_bets_1x2(
        probs,
        taken,
        closing,
        ["m1", "m2"],
        edge_threshold=0.05,
        max_odds=6.0,
    )
    assert bets["match_id"].is_unique
    assert (bets["odds"] <= 6.0).all()
    assert (bets["point_ev"] > 0.05).all()


def test_gate_rejects_point_ev_without_positive_clv_confidence() -> None:
    decision = guarded_bet_decision(
        0.60,
        2.0,
        historical_clv=[],
        historical_match_ids=[],
        min_independent_matches=20,
        n_boot=200,
    )
    assert decision["point_ev"] == pytest.approx(0.20)
    assert decision["action"] == "NO BET"
    assert decision["reason"] == "insufficient_independent_matches"

    negative = clv_betting_gate(
        np.full(40, -0.02),
        [f"m{i}" for i in range(40)],
        min_independent_matches=30,
        n_boot=300,
    )
    assert negative["action"] == "NO BET"
    assert negative["reason"] == "clv_lower_ci_not_positive"


def test_gate_opens_only_when_cluster_lower_ci_is_positive() -> None:
    groups = np.repeat([f"m{i}" for i in range(60)], 2)
    clv = np.tile([0.04, 0.06], 60)
    decision = clv_betting_gate(
        clv,
        groups,
        min_independent_matches=50,
        n_boot=500,
        seed=9,
    )
    assert decision["action"] == "BET"
    assert decision["clv"]["n_clusters"] == 60
    assert decision["clv"]["ci_low"] > 0.0


def test_nested_selector_has_no_holdout_and_uses_late_development() -> None:
    rng = np.random.default_rng(4)
    n_early, n_late = 180, 120
    market_early = np.tile([0.55, 0.25, 0.20], (n_early, 1))
    market_late = np.tile([0.55, 0.25, 0.20], (n_late, 1))
    early_y = rng.choice(list("HDA"), n_early, p=market_early[0])
    late_y = rng.choice(list("HDA"), n_late, p=market_late[0])
    # The raw model is intentionally overconfident and directionally wrong.
    raw_early = np.tile([0.20, 0.25, 0.55], (n_early, 1))
    raw_late = np.tile([0.20, 0.25, 0.55], (n_late, 1))
    early_open = _odds_from_probs(market_early)
    late_open = _odds_from_probs(market_late)
    configs = [
        AnchorConfig(residual_weight=0.0),
        AnchorConfig(residual_weight=1.0),
    ]
    selected, table = select_anchor_on_late_development(
        early_raw_probs=raw_early,
        early_opening_odds=early_open,
        early_outcomes=early_y,
        late_raw_probs=raw_late,
        late_opening_odds=late_open,
        late_taken_odds=late_open,
        late_closing_odds=late_open,
        late_outcomes=late_y,
        late_match_ids=[f"late-{i}" for i in range(n_late)],
        configs=configs,
        min_selection_matches=500,
        n_boot=100,
        seed=2,
    )
    assert sum(row["selected"] for row in table) == 1
    chosen = next(row for row in table if row["selected"])
    assert chosen["selection_rule"] == "fallback_lowest_late_development_logloss"
    assert chosen["metrics"]["logloss"] == min(
        row["metrics"]["logloss"] for row in table
    )
    assert selected.config == configs[chosen["grid_index"]]

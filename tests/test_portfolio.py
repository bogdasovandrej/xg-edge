"""Portfolio engine: staking, acca legality, exposure limits."""
from __future__ import annotations

import pytest

from xgedge.decision.portfolio import (
    PortfolioConfig,
    build_portfolio,
    evaluate_ticket,
)


def _candidate(
    candidate_id: str,
    fixture_id: str,
    *,
    odds: float = 1.9,
    probability: float = 0.58,
    value: float = 7.0,
    robustness: float = 7.0,
    data_quality: float = 70.0,
    archetypes: tuple[str, ...] = (),
    approved: bool = True,
    final_checked: bool = True,
    independently_approved: bool = True,
    has_unresolved_warning: bool = False,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "fixture_id": fixture_id,
        "market_family": "TOTALS",
        "odds": odds,
        "conservative_probability": probability,
        "value": value,
        "robustness": robustness,
        "data_quality": data_quality,
        "archetypes": list(archetypes),
        "status": "APPROVED" if approved else "BORDERLINE",
        "final_check_status": "FINAL_CHECK_PASSED" if final_checked else "WAITING_XI",
        "independently_approved": independently_approved,
        "has_unresolved_warning": has_unresolved_warning,
    }


def test_unapproved_or_not_final_checked_leg_is_rejected() -> None:
    result = build_portfolio([
        _candidate("c1", "f1", approved=False),
        _candidate("c2", "f2", final_checked=False),
    ])
    assert result["singles"] == []
    reasons = {r["candidate_id"]: r["reason"] for r in result["rejections"]["intake"]}
    assert reasons == {"c1": "not_approved_and_final_checked", "c2": "not_approved_and_final_checked"}


def test_negative_conservative_ev_leg_is_rejected() -> None:
    result = build_portfolio([_candidate("c1", "f1", odds=1.5, probability=0.5)])
    assert result["singles"] == []
    assert result["rejections"]["intake"][0]["reason"] == "conservative_ev_not_positive"


def test_evaluate_ticket_rejects_negative_combo_ev() -> None:
    # Two independently positive-EV legs always combine to a positive naive
    # combo EV, so the negative case has to come from a same-match joint
    # probability that is materially lower than the naive product implies.
    legs = [
        _candidate("c1", "f1", odds=1.4, probability=0.75),
        _candidate("c2", "f1", odds=1.4, probability=0.75),
    ]
    result = evaluate_ticket(
        legs, config=PortfolioConfig(), same_match_joint_probability=0.4
    )
    assert result["status"] == "REJECTED"
    assert result["reason"] == "conservative_ev_not_positive"


def test_same_match_two_legs_rejected_without_joint_probability() -> None:
    legs = [_candidate("c1", "f1"), _candidate("c2", "f1", odds=2.1, probability=0.55)]
    result = evaluate_ticket(legs, config=PortfolioConfig())
    assert result == {"status": "REJECTED", "reason": "REJECT_CORRELATED_SAME_MATCH_LEGS", "legs": ["c1", "c2"]}

    portfolio = build_portfolio([legs[0], legs[1]])
    same_match_rejections = [
        r for r in portfolio["rejections"]["accumulators_rejected"]
        if r.get("reason") == "REJECT_CORRELATED_SAME_MATCH_LEGS"
    ]
    assert same_match_rejections


def test_same_match_two_legs_allowed_with_supplied_joint_probability() -> None:
    c1 = _candidate("c1", "f1", odds=1.9, probability=0.58)
    c2 = _candidate("c2", "f1", odds=1.9, probability=0.58)
    result = evaluate_ticket(
        [c1, c2], config=PortfolioConfig(), same_match_joint_probability=0.45
    )
    assert result["status"] == "VALID"
    assert result["same_match_joint_probability_used"] is True
    assert result["joint_probability"] == pytest.approx(0.45)


def test_four_leg_accumulator_is_rejected() -> None:
    legs = [_candidate(f"c{i}", f"f{i}") for i in range(4)]
    result = evaluate_ticket(legs, config=PortfolioConfig())
    assert result["status"] == "REJECTED"
    assert result["reason"] == "too_many_legs"


def test_short_odds_glue_leg_rejected_when_not_independently_approved() -> None:
    legs = [
        _candidate("c1", "f1", odds=1.2, independently_approved=False),
        _candidate("c2", "f2", odds=2.0, probability=0.6),
    ]
    result = evaluate_ticket(legs, config=PortfolioConfig())
    assert result["status"] == "REJECTED"
    assert result["reason"] == "leg_not_independently_approved"


def test_short_odds_glue_leg_allowed_with_warning_when_independently_approved() -> None:
    legs = [
        _candidate("c1", "f1", odds=1.2, probability=0.9),
        _candidate("c2", "f2", odds=2.0, probability=0.6),
    ]
    result = evaluate_ticket(legs, config=PortfolioConfig())
    assert result["status"] == "VALID"
    assert "c1" in result["short_odds_glue_warning"]


def test_match_cluster_cap_keeps_only_two_markets_per_fixture() -> None:
    candidates = [
        _candidate("c1", "f1", value=9.0),
        _candidate("c2", "f1", value=8.0),
        _candidate("c3", "f1", value=7.0),
    ]
    result = build_portfolio(candidates)
    singles_ids = {s["candidate_id"] for s in result["singles"]}
    assert singles_ids == {"c1", "c2"}
    assert any(
        r["candidate_id"] == "c3" and r["reason"] == "match_market_cap_exceeded"
        for r in result["rejections"]["match_cluster_cap"]
    )


def test_leg_reuse_capped_at_two_total_uses() -> None:
    # c1 sits in every fixture-distinct pair; with cap=2, it may be used as
    # its own single plus exactly one accumulator, never a second one.
    candidates = [_candidate("c1", "f1")] + [_candidate(f"c{i}", f"f{i}") for i in range(2, 6)]
    result = build_portfolio(candidates)
    uses = sum(1 for s in result["singles"] if s["candidate_id"] == "c1")
    uses += sum(1 for a in result["accumulators"] if "c1" in a["legs"])
    assert uses <= 2
    assert any(r.get("reason") == "max_leg_uses_exceeded" for r in result["rejections"]["accumulators_rejected"])


def test_archetype_exposure_warning_when_cap_exceeded() -> None:
    candidates = [
        _candidate(f"c{i}", f"f{i}", archetypes=("BIG_DOG_HANDICAP",), value=6.0)
        for i in range(6)
    ]
    result = build_portfolio(candidates, config=PortfolioConfig(archetype_exposure_cap=0.30))
    assert "BIG_DOG_HANDICAP" in result["exposure"]["warnings"]
    assert result["exposure"]["by_archetype"]["BIG_DOG_HANDICAP"] > 0.30


def test_unused_bankroll_is_accepted_not_forced_to_zero() -> None:
    result = build_portfolio([_candidate("c1", "f1")])
    assert result["bankroll"]["unused_rub"] > 0
    assert result["bankroll"]["unused_rub"] == pytest.approx(
        result["bankroll"]["bankroll_rub"] - result["bankroll"]["staked_rub"]
    )


def test_at_most_one_candidate_gets_the_500_rub_single() -> None:
    strong = dict(value=9.0, robustness=9.0, data_quality=90.0)
    candidates = [
        _candidate("c1", "f1", **strong),
        _candidate("c2", "f2", **strong),
        _candidate("c3", "f3", value=5.0, robustness=5.0, data_quality=50.0),
    ]
    result = build_portfolio(candidates)
    doubles = [s for s in result["singles"] if s["stake_rub"] == PortfolioConfig().double_stake_rub]
    assert len(doubles) == 1
    assert doubles[0]["candidate_id"] == "c1"


def test_config_rejects_four_plus_leg_policy() -> None:
    with pytest.raises(ValueError):
        PortfolioConfig(max_acca_legs=4).validate()


def test_reserve_is_never_breached() -> None:
    cfg = PortfolioConfig(bankroll_rub=1000.0, unit_rub=250.0, minimum_cash_reserve_rub=900.0)
    candidates = [_candidate(f"c{i}", f"f{i}") for i in range(5)]
    result = build_portfolio(candidates, config=cfg)
    assert result["bankroll"]["staked_rub"] <= cfg.bankroll_rub - cfg.minimum_cash_reserve_rub
    assert result["rejections"]["singles_skipped"]

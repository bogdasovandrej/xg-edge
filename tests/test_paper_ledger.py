"""Persistence and settlement tests for the offline PAPER ledger."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from xgedge.simulation.ledger import (
    EVENT_SCHEMA_VERSION,
    event_from_json,
    event_to_json,
    new_paper_ledger,
    public_paper_summary,
    update_paper_ledger,
    validate_paper_ledger,
)

T0 = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def _candidate(fixture_id: str = "m1", *, outcome: str = "home") -> dict:
    odds = 2.0
    probability = .58
    return {
        "fixture_id": fixture_id,
        "competition": "UEFA Champions League",
        "stage": "Qualifying",
        "kickoff_utc": (T0 + timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
        "home": "Home",
        "away": "Away",
        "selection": "П1",
        "outcome": outcome,
        "model_probability": probability,
        "break_even_probability": 1 / odds,
        "probability_edge": probability - 1 / odds,
        "odds": odds,
        "bookmaker": "Book A",
        "bookmaker_key": "book-a",
        "quote_source": "the_odds_api",
        "quote_captured_at": (T0 - timedelta(minutes=10)).isoformat().replace(
            "+00:00", "Z"
        ),
        "point_edge": probability * odds - 1,
        "robust_edge": .07,
        "data_quality_score": 90.0,
        "market_period": "REGULATION_90_MINUTES",
        "status": "PAPER_ONLY",
        "real_money_eligible": False,
        "rank": 1,
    }


def _payload(*rows: dict) -> dict:
    return {
        "generated_at": T0.isoformat().replace("+00:00", "Z"),
        "paper_candidate_ranking": {
            "schema_version": "paper-candidate-ranking/1.0",
            "status": "PAPER_ONLY",
            "real_money_execution": False,
            "candidates": list(rows),
        },
    }


def _prospective(
    *,
    result: str = "home",
    benchmark: str = "pinnacle",
    tier: str = "confirmatory",
    clv_status: str = "ready",
) -> dict:
    goals = {"home": (2, 0), "draw": (1, 1), "away": (0, 2)}[result]
    return {
        "schema_version": "prospective-clv/1.2",
        "policy": {"market": "1X2", "closing_benchmark": "pinnacle"},
        "fixtures": {
            "m1": {
                "fixture_id": "m1",
                "result": {
                    "home_goals_90": goals[0],
                    "away_goals_90": goals[1],
                    "outcome": result,
                },
                "closing": {
                    "snapshot_at": (T0 + timedelta(hours=1, minutes=30))
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "benchmark": benchmark,
                    "method": (
                        "pinnacle_proportional_devig"
                        if benchmark == "pinnacle"
                        else "median_bookmaker_proportional_devig"
                    ),
                    "evaluation_tier": tier,
                    "bookmakers": [benchmark],
                    "probabilities": {"home": .55, "draw": .25, "away": .20},
                },
                "clv": {"status": clv_status, "evaluation_tier": tier},
            }
        },
    }


def test_versioned_event_roundtrip_and_unknown_fields_fail_closed() -> None:
    ledger = new_paper_ledger(created_at=T0)
    source = ledger["strategies"]["flat_1pct"]["events"][0]
    assert source["schema_version"] == EVENT_SCHEMA_VERSION
    assert event_to_json(event_from_json(source)) == source

    wrong_schema = {**source, "schema_version": "paper-event/99"}
    with pytest.raises(ValueError, match="unsupported paper event schema"):
        event_from_json(wrong_schema)
    extra = {**source, "unexpected": True}
    with pytest.raises(ValueError, match="fields mismatch"):
        event_from_json(extra)


def test_new_ledger_has_exactly_three_fixed_10k_strategies() -> None:
    ledger = validate_paper_ledger(new_paper_ledger(created_at=T0))
    summary = public_paper_summary(ledger)
    assert summary["status"] == "PAPER_ONLY_EMPTY"
    assert summary["real_money_execution"] is False
    assert summary["starting_balance_rub"] == 10_000.0
    assert summary["target_balance_rub"] == 1_000_000.0
    assert {row["strategy_id"] for row in summary["leaderboard"]} == {
        "flat_1pct", "fractional_kelly_025", "conservative_edge_5pp"
    }
    assert all(row["equity_balance_rub"] == 10_000.0 for row in summary["leaderboard"])
    assert ledger["policy"]["strategy_ranking_score"]["clv_weight"] == .65
    assert ledger["policy"]["strategy_ranking_score"]["roi_weight"] == .15


def test_enrollment_is_one_per_match_and_idempotent() -> None:
    ledger = new_paper_ledger(created_at=T0 - timedelta(hours=1))
    updated, operation = update_paper_ledger(
        ledger, _payload(_candidate()), now=T0
    )
    assert operation["enrolled"] == 1
    assert list(updated["enrollments"]) == ["m1"]
    actions = updated["enrollments"]["m1"]["strategy_actions"]
    assert all(action["accepted"] for action in actions.values())
    assert all(action["stake_rub"] == 100.0 for action in actions.values())
    assert updated["paper_trading"]["totals"]["open_bets"] == 3

    repeated, repeated_operation = update_paper_ledger(
        updated, _payload(_candidate()), now=T0
    )
    assert repeated_operation["status"] == "unchanged"
    assert repeated == updated
    assert len(repeated["update_history"]) == 1


def test_duplicate_candidates_for_same_match_abort_the_whole_update() -> None:
    ledger = new_paper_ledger(created_at=T0 - timedelta(hours=1))
    duplicate = _candidate()
    duplicate["outcome"] = "draw"
    with pytest.raises(ValueError, match="more than one candidate"):
        update_paper_ledger(
            ledger, _payload(_candidate(), duplicate), now=T0
        )
    assert ledger["enrollments"] == {}


def test_stale_quotes_are_not_enrolled() -> None:
    row = _candidate()
    row["quote_captured_at"] = (T0 - timedelta(minutes=31)).isoformat().replace(
        "+00:00", "Z"
    )
    ledger = new_paper_ledger(created_at=T0 - timedelta(hours=1))
    updated, operation = update_paper_ledger(ledger, _payload(row), now=T0)
    assert updated == ledger
    assert operation["candidate_rejections"] == {"stale_quote": 1}


def test_official_result_settles_all_paper_bets_with_valid_pinnacle_clv() -> None:
    ledger = new_paper_ledger(created_at=T0 - timedelta(hours=1))
    opened, _ = update_paper_ledger(ledger, _payload(_candidate()), now=T0)
    settled, operation = update_paper_ledger(
        opened,
        _payload(),
        now=T0 + timedelta(hours=3),
        prospective_ledger=_prospective(),
    )
    assert operation["settled"] == 1
    assert settled["settlements"]["m1"]["closing_benchmark"] == "pinnacle_fair_1x2"
    assert settled["settlements"]["m1"]["closing_odds"] == pytest.approx(1 / .55)
    for row in settled["paper_trading"]["leaderboard"]:
        assert row["settled_bets"] == 1
        assert row["wins"] == 1
        assert row["mean_clv"] == pytest.approx(.10)
        assert row["equity_balance_rub"] == 10_100.0


def test_result_settles_but_diagnostic_close_never_becomes_clv() -> None:
    ledger = new_paper_ledger(created_at=T0 - timedelta(hours=1))
    opened, _ = update_paper_ledger(ledger, _payload(_candidate()), now=T0)
    prospective = _prospective(
        benchmark="non_pinnacle_consensus", tier="diagnostic", clv_status="diagnostic"
    )
    settled, _ = update_paper_ledger(
        opened,
        _payload(),
        now=T0 + timedelta(hours=3),
        prospective_ledger=prospective,
    )
    assert settled["settlements"]["m1"]["closing_odds"] is None
    assert all(row["mean_clv"] is None for row in settled["paper_trading"]["leaderboard"])


def test_explicit_official_result_map_works_without_claiming_clv() -> None:
    ledger = new_paper_ledger(created_at=T0 - timedelta(hours=1))
    opened, _ = update_paper_ledger(ledger, _payload(_candidate()), now=T0)
    settled, _ = update_paper_ledger(
        opened,
        _payload(),
        now=T0 + timedelta(hours=3),
        official_results={"m1": {"status": "FINISHED", "outcome": "away"}},
    )
    assert settled["settlements"]["m1"]["outcome"] == "away"
    assert settled["settlements"]["m1"]["closing_odds"] is None
    assert all(row["losses"] == 1 for row in settled["paper_trading"]["leaderboard"])


def test_conflicting_official_results_fail_before_mutation() -> None:
    ledger = new_paper_ledger(created_at=T0 - timedelta(hours=1))
    opened, _ = update_paper_ledger(ledger, _payload(_candidate()), now=T0)
    pristine = deepcopy(opened)
    with pytest.raises(ValueError, match="conflicting official results"):
        update_paper_ledger(
            opened,
            _payload(),
            now=T0 + timedelta(hours=3),
            prospective_ledger=_prospective(result="home"),
            official_results={"m1": "away"},
        )
    assert opened == pristine


def test_summary_tampering_is_rejected() -> None:
    ledger = new_paper_ledger(created_at=T0)
    ledger["paper_trading"]["real_money_execution"] = True
    with pytest.raises(ValueError, match="summary does not match"):
        validate_paper_ledger(ledger)


@pytest.mark.parametrize(
    ("market", "selection", "outcome", "line", "goals", "expected"),
    [
        ("totals", "ТБ 2.5", "over", 2.5, (2, 1), "win"),
        ("btts", "ОЗ — да", "yes", None, (2, 1), "win"),
        ("asian_handicap", "Home -1", "home", -1.0, (2, 1), "push"),
        ("draw_no_bet", "Home DNB", "home", None, (1, 1), "push"),
    ],
)
def test_goal_market_enrollment_and_automatic_settlement(
    market: str,
    selection: str,
    outcome: str,
    line: float | None,
    goals: tuple[int, int],
    expected: str,
) -> None:
    candidate = _candidate()
    candidate.update({
        "market": market,
        "line": line,
        "selection": selection,
        "outcome": outcome,
    })
    ledger = new_paper_ledger(created_at=T0 - timedelta(hours=1))
    opened, operation = update_paper_ledger(
        ledger, _payload(candidate), now=T0
    )
    assert operation["enrolled"] == 1
    assert opened["enrollments"]["m1"]["market"] == market

    actual = "home" if goals[0] > goals[1] else "away" if goals[1] > goals[0] else "draw"
    settled, operation = update_paper_ledger(
        opened,
        _payload(),
        now=T0 + timedelta(hours=3),
        official_results={
            "m1": {
                "status": "FINISHED",
                "home_goals_90": goals[0],
                "away_goals_90": goals[1],
                "outcome": actual,
            }
        },
    )
    assert operation["settled"] == 1
    assert settled["settlements"]["m1"]["selection_result"] == expected
    assert settled["paper_trading"]["markets"][market]["settled"] == 1
    for row in settled["paper_trading"]["leaderboard"]:
        assert row[f"{expected}es" if expected == "push" else f"{expected}s"] == 1

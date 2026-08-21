"""The four golden invariants. Each one encodes a bug that already cost money.

These run in CI through the normal pytest job. Do not relax an assertion to
make a change pass — the assertion is the contract, the change is the
suspect.
"""
from __future__ import annotations

import pytest

from xgedge.decision.pricing import (
    MULTIPLIERS,
    calc_mode,
    fair,
    min_entry,
    passes_value_gate,
    states_from_distribution,
    value_pct,
)
from xgedge.data.official_results import ingest_result
from xgedge.markets.paper_markets import market_settlement_distribution, score_matrix


def _all_payload_markets() -> list[tuple[str, str, float | None]]:
    """Every market definition the public payload prices, plus quarter lines."""
    definitions: list[tuple[str, str, float | None]] = [
        ("1x2", "home", None), ("1x2", "draw", None), ("1x2", "away", None),
        ("double_chance", "home_draw", None), ("double_chance", "home_away", None),
        ("double_chance", "draw_away", None),
        ("draw_no_bet", "home", None), ("draw_no_bet", "away", None),
        ("btts", "yes", None), ("btts", "no", None),
    ]
    for line in (1.5, 2.5, 3.5, 4.5, 2.75, 3.25):
        definitions += [("totals", "over", line), ("totals", "under", line)]
    for line in (0.5, 1.0, 1.5, 2.5):
        definitions += [
            ("team_totals", "home_over", line), ("team_totals", "home_under", line),
            ("team_totals", "away_over", line), ("team_totals", "away_under", line),
        ]
    for line in (-1.5, -1.25, -0.5, 0.0, 0.5, 0.75, 1.25, 1.5):
        definitions += [("asian_handicap", "home", line), ("asian_handicap", "away", line)]
    return definitions


# --------------------------------------------------------------------------
# Golden test 1 — Hapoel ITM1: a bet that must not have been approved.
# --------------------------------------------------------------------------
def test_golden_hapoel_itm1() -> None:
    W, P, L = 0.41, 0.0, 0.59
    odds = 2.45
    assert abs(fair(W, L) - 2.439) < 0.01
    assert abs(min_entry(W, L) - 2.634) < 0.01
    assert min_entry(W, L) > fair(W, L)
    assert odds < min_entry(W, L)          # this bet must NOT pass
    assert not passes_value_gate(W, L, odds)
    assert P == 0.0 and abs(W + P + L - 1.0) < 1e-9

    # The contract's own worked example prints 4.55 here, but its formula
    # gives (0.41*1.45 - 0.59) / 1.0 * 100 = 0.45 — and the incident write-up
    # that produced this case also records +0.45%. The formula is the
    # contract; 4.55 is a typo in the example. Asserting the formula.
    assert abs(value_pct(W, L, odds) - 0.45) < 0.1


def test_value_rating_can_never_open_the_gate() -> None:
    """The exact incident: rating 8.1 while value_pct is only +0.45%."""
    W, L, odds = 0.41, 0.59, 2.45
    value_rating = 8.1
    assert value_rating >= 8.0                 # the rating looked fine
    assert not passes_value_gate(W, L, odds)   # the gate must still refuse


# --------------------------------------------------------------------------
# Golden test 2 — an integer line always has push mass.
# --------------------------------------------------------------------------
def test_integer_line_has_push() -> None:
    matrix = score_matrix(1.55, 1.05)
    distribution = market_settlement_distribution(
        matrix, market="team_totals", selection="home_under", line=1.0
    )
    states = states_from_distribution(distribution.probabilities)
    assert calc_mode(states["push"]) == "WPL_FULL"
    assert states["push"] > 0

    # A half line cannot push, and must be reported as binary.
    half = states_from_distribution(
        market_settlement_distribution(
            matrix, market="team_totals", selection="home_under", line=1.5
        ).probabilities
    )
    assert half["push"] == 0
    assert calc_mode(half["push"]) == "BINARY"


def test_fair_on_a_push_market_is_not_one_over_p() -> None:
    """1/p and 1 + L/W diverge exactly when push mass exists."""
    matrix = score_matrix(1.55, 1.05)
    states = states_from_distribution(
        market_settlement_distribution(
            matrix, market="team_totals", selection="home_under", line=1.0
        ).probabilities
    )
    naive = 1.0 / states["win"]
    assert abs(fair(states["win"], states["loss"]) - naive) > 0.5


# --------------------------------------------------------------------------
# Golden test 3 — disagreeing sources must not settle quietly.
# NEC Nijmegen v Bodo/Glimt: 1:3 from one feed, 0:3 from another. On Under 3
# that is PUSH versus LOSS.
# --------------------------------------------------------------------------
def test_conflicting_result_blocks_settlement() -> None:
    result = ingest_result(
        fixture_id="nec-bodo",
        sources=[{"source": "a", "score": "1:3"}, {"source": "b", "score": "0:3"}],
    )
    assert result["status"] == "BLOCKED"
    assert result["settled_state"] is None
    assert result["source_conflict"] is True


def test_agreeing_sources_settle() -> None:
    result = ingest_result(
        fixture_id="nec-bodo",
        sources=[
            {"source": "a", "score": "1:3"},
            {"source": "b", "home_goals_90": 1, "away_goals_90": 3},
        ],
    )
    assert result["status"] == "CONFIRMED"
    assert result["settled_state"] == {"home_goals_90": 1, "away_goals_90": 3}
    assert result["source_conflict"] is False


def test_no_usable_source_stays_pending_not_settled() -> None:
    result = ingest_result(fixture_id="nec-bodo", sources=[{"source": "a", "score": "?"}])
    assert result["status"] == "PENDING"
    assert result["settled_state"] is None
    assert result["source_conflict"] is False


def test_single_source_still_settles_but_is_recorded() -> None:
    result = ingest_result(fixture_id="x", sources=[{"source": "uefa", "score": "2:0"}])
    assert result["status"] == "CONFIRMED"
    assert len(result["observations"]) == 1


# --------------------------------------------------------------------------
# Golden test 4 — states always sum to one.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("market,selection,line", _all_payload_markets())
def test_states_sum_to_one(market: str, selection: str, line: float | None) -> None:
    matrix = score_matrix(1.55, 1.05)
    states = states_from_distribution(
        market_settlement_distribution(
            matrix, market=market, selection=selection, line=line
        ).probabilities
    )
    total = states["win"] + states["push"] + states["loss"]
    assert abs(total - 1.0) <= 0.001


def test_payout_multipliers_match_the_contract() -> None:
    odds = 2.40
    assert MULTIPLIERS["WIN"](odds) == pytest.approx(2.40)
    assert MULTIPLIERS["HALF_WIN"](odds) == pytest.approx(1.70)
    assert MULTIPLIERS["PUSH"](odds) == pytest.approx(1.00)
    assert MULTIPLIERS["HALF_LOSS"](odds) == pytest.approx(0.50)
    assert MULTIPLIERS["LOSS"](odds) == pytest.approx(0.00)

from __future__ import annotations

import numpy as np
import pytest

from xgedge.decision.live_market import (
    american_to_decimal,
    anchor_from_audit,
    anchor_live_1x2,
    validate_market_snapshot,
)
from xgedge.decision.market_anchor import AnchorConfig, MarketAnchor


def _snapshot() -> dict:
    return {
        "fixture_id": "eng-arg",
        "kickoff_utc": "2026-07-15T19:00:00Z",
        "captured_at_utc": "2026-07-13T00:30:43Z",
        "market": "regulation_1x2",
        "bookmaker": "test",
        "odds_american": {"home": 155, "draw": 200, "away": 205},
    }


def test_american_odds_conversion() -> None:
    assert american_to_decimal(155) == pytest.approx(2.55)
    assert american_to_decimal(-135) == pytest.approx(1.74074074)


def test_market_snapshot_rejects_post_kickoff_capture() -> None:
    row = _snapshot()
    row["captured_at_utc"] = row["kickoff_utc"]
    with pytest.raises(ValueError, match="before kickoff"):
        validate_market_snapshot(row)


def test_anchor_recognizes_england_as_market_favorite_and_blocks_bets() -> None:
    anchor = MarketAnchor(
        AnchorConfig(residual_weight=0.15, longshot_weight=0.0),
        np.zeros(3),
    )
    result = anchor_live_1x2(
        {"home": 0.3221, "draw": 0.2576, "away": 0.4203},
        _snapshot(),
        anchor,
    )

    assert result["market_fair"]["home"] > result["market_fair"]["away"]
    assert result["anchored"]["home"] > result["anchored"]["away"]
    assert result["betting_gate"]["allowed"] is False
    assert len(result["candidate_bets"]) == 3
    assert all(row["status"] == "WATCH_ONLY" for row in result["candidate_bets"])


def test_neutral_transfer_can_drop_domestic_fitted_bias() -> None:
    audit = {"selected_anchor": {
        "config": {"residual_weight": .15, "longshot_weight": 0,
                   "longshot_probability": .15, "edge_threshold": .03,
                   "max_odds": 6, "bias_l2": .1},
        "bias": [.2, -.1, -.1],
    }}
    neutral = anchor_from_audit(audit, use_fitted_bias=False)
    np.testing.assert_allclose(neutral.bias_, np.zeros(3))

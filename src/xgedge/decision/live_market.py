"""Point-in-time market snapshots for live, market-anchored forecasts."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

import numpy as np

from xgedge.decision.market_anchor import AnchorConfig, MarketAnchor, devig_opening_odds


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("market timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def american_to_decimal(value: float) -> float:
    """Convert a finite non-zero American price to decimal odds."""
    price = float(value)
    if not np.isfinite(price) or price == 0:
        raise ValueError("American odds must be finite and non-zero")
    return 1.0 + (price / 100.0 if price > 0 else 100.0 / abs(price))


def validate_market_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one pre-match 1X2 snapshot and return normalized decimal odds."""
    fixture_id = str(snapshot.get("fixture_id", "")).strip()
    if not fixture_id:
        raise ValueError("market fixture_id is required")
    captured = _utc(str(snapshot["captured_at_utc"]))
    kickoff = _utc(str(snapshot["kickoff_utc"]))
    if captured >= kickoff:
        raise ValueError("market snapshot must be captured before kickoff")
    if snapshot.get("market") != "regulation_1x2":
        raise ValueError("only regulation_1x2 snapshots are supported")
    odds = snapshot.get("odds_american")
    if not isinstance(odds, Mapping) or set(odds) != {"home", "draw", "away"}:
        raise ValueError("odds_american must contain home, draw and away")
    decimal = np.asarray(
        [american_to_decimal(odds[key]) for key in ("home", "draw", "away")],
        dtype=float,
    )
    return {
        **dict(snapshot),
        "fixture_id": fixture_id,
        "odds_decimal": {
            key: float(decimal[index])
            for index, key in enumerate(("home", "draw", "away"))
        },
    }


def market_index(document: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    if document is None:
        return {}
    rows = document.get("snapshots")
    if not isinstance(rows, list):
        raise ValueError("market document must contain a snapshots list")
    normalized = [validate_market_snapshot(row) for row in rows]
    if len({row["fixture_id"] for row in normalized}) != len(normalized):
        raise ValueError("duplicate market fixture_id")
    return {row["fixture_id"]: row for row in normalized}


def anchor_from_audit(
    document: Mapping[str, Any], *, use_fitted_bias: bool = True
) -> MarketAnchor:
    selected = document.get("selected_anchor")
    if not isinstance(selected, Mapping):
        raise ValueError("anchor audit is missing selected_anchor")
    config = selected.get("config")
    bias = selected.get("bias")
    if not isinstance(config, Mapping) or not isinstance(bias, list):
        raise ValueError("selected_anchor must contain config and bias")
    fitted_bias = np.asarray(bias, dtype=float) if use_fitted_bias else np.zeros(3)
    return MarketAnchor(AnchorConfig(**dict(config)), fitted_bias)


def anchor_live_1x2(
    raw_probabilities: Mapping[str, float],
    snapshot: Mapping[str, Any],
    anchor: MarketAnchor,
) -> dict[str, Any]:
    """Anchor raw probabilities and expose a gated three-outcome watchlist."""
    normalized = validate_market_snapshot(snapshot)
    labels = ("home", "draw", "away")
    raw = np.asarray([[float(raw_probabilities[key]) for key in labels]], dtype=float)
    decimal = np.asarray(
        [[float(normalized["odds_decimal"][key]) for key in labels]], dtype=float
    )
    market = devig_opening_odds(decimal)[0]
    anchored = anchor.predict_proba(raw, decimal)[0]
    candidates = []
    display = {"home": "П1", "draw": "X", "away": "П2"}
    for index, key in enumerate(labels):
        probability = float(anchored[index])
        edge = float(probability * decimal[0, index] - 1.0)
        candidates.append(
            {
                "selection": display[key],
                "probability": probability,
                "fair_odds": 1.0 / probability,
                "market_odds": float(decimal[0, index]),
                "point_edge": edge,
                "status": "WATCH_ONLY",
            }
        )
    candidates.sort(key=lambda row: (-row["point_edge"], row["selection"]))
    for rank, row in enumerate(candidates, start=1):
        row["rank"] = rank
    return {
        "basis": "opening_market_prior_plus_shrunk_model_residual",
        "calibration_scope": "domestic-development shrinkage transferred to neutral international match; fitted domestic intercept removed",
        "calibration_warning": "International prospective calibration is not yet available; uncertainty remains high.",
        "bookmaker": normalized.get("bookmaker"),
        "captured_at_utc": normalized["captured_at_utc"],
        "source_url": normalized.get("source_url"),
        "raw_model": {key: float(raw[0, index]) for index, key in enumerate(labels)},
        "market_fair": {key: float(market[index]) for index, key in enumerate(labels)},
        "anchored": {key: float(anchored[index]) for index, key in enumerate(labels)},
        "candidate_bets": candidates,
        "betting_gate": {
            "allowed": False,
            "reason": "Positive prospective CLV has not been demonstrated.",
        },
    }

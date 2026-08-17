"""Versioned market-family and diversity taxonomy."""
from __future__ import annotations

from typing import Final

from xgedge.markets.paper_markets import canonical_market


TAXONOMY_VERSION: Final[str] = "market-taxonomy/1.0"


def market_family(market: object) -> str:
    return {
        "1x2": "MATCH_RESULT",
        "double_chance": "DOUBLE_CHANCE",
        "draw_no_bet": "DRAW_NO_BET",
        "asian_handicap": "ASIAN_HANDICAP",
        "totals": "TOTALS",
        "team_totals": "TEAM_TOTALS",
        "btts": "BTTS",
        "qualification": "QUALIFICATION",
        "joint": "JOINT",
    }.get(canonical_market(market), "UNKNOWN")


def market_cluster(market: object, selection: object) -> str:
    kind = canonical_market(market)
    side = str(selection or "").strip().casefold()
    if kind == "totals":
        return "TOTAL_OVER" if side.startswith("over") else "TOTAL_UNDER"
    if kind == "team_totals":
        team = "HOME" if side.startswith("home") else "AWAY"
        direction = "OVER" if side.endswith("over") else "UNDER"
        return f"{team}_TEAM_{direction}"
    if kind == "asian_handicap":
        return "HANDICAP_HOME" if side == "home" else "HANDICAP_AWAY"
    if kind == "1x2":
        return {"home": "HOME_RESULT", "away": "AWAY_RESULT", "draw": "DRAW_RESULT"}.get(
            side, "MATCH_RESULT"
        )
    if kind == "draw_no_bet":
        return "DNB_HOME" if side == "home" else "DNB_AWAY"
    if kind == "double_chance":
        return f"DOUBLE_CHANCE_{side.upper()}"
    if kind == "btts":
        return "BTTS_YES" if side == "yes" else "BTTS_NO"
    if kind == "qualification":
        return "QUALIFICATION_HOME" if side == "home" else "QUALIFICATION_AWAY"
    return f"{market_family(kind)}_{side.upper() or 'UNKNOWN'}"

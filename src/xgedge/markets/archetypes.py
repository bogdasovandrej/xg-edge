"""Versioned archetype taxonomy for portfolio correlation/exposure control.

Archetypes group candidates that likely share one underlying research
hypothesis or model blind spot across *different* fixtures (e.g. three
distinct big-underdog handicaps that all rest on "the favourite will not
blow the game open"). ``xgedge.markets.taxonomy`` already separates same-match
diversity (``market_family``/``market_cluster``); this module is the
cross-match layer the portfolio engine needs for archetype-exposure limits.

Tagging is a deterministic, first-pass heuristic over already-known fields
(market family/cluster/selection plus optional leg/aggregate/favourite
context). It is not a statistically validated grouping and must not be
treated as one; see ``docs/UEFA_RESEARCH_WORKFLOW_V2_RU.md`` hypotheses A-D,
which stay observation-only until a prospective sample supports them.
"""
from __future__ import annotations

from dataclasses import dataclass

ARCHETYPE_TAXONOMY_VERSION = "market-archetype/1.0"

KNOWN_ARCHETYPES = frozenset({
    "SECOND_LEG_UNDER",
    "SECOND_LEG_OVER",
    "BIG_DOG_HANDICAP",
    "HOME_COMEBACK",
    "AGGREGATE_LEADER",
    "FAVORITE_CONTROL",
    "FAVORITE_TEAM_UNDER",
    "DOG_TEAM_OVER",
    "DNB_UNDERDOG",
    "QUALIFICATION_FAVORITE",
    "QUALIFICATION_DOG",
    "BTTS_YES",
    "BTTS_NO",
})


@dataclass(frozen=True, slots=True)
class MatchArchetypeContext:
    """Minimal game-state facts needed for archetype tagging.

    ``favorite_side`` should come from the model's own probabilities (the
    side with the higher win/advance probability), not from public opinion.
    Leave a field ``None`` when it is genuinely unknown rather than guessing.
    """

    leg_number: int | None = None
    aggregate_leader: str | None = None  # "home" | "away" | "level" | None
    favorite_side: str | None = None  # "home" | "away" | None

    def __post_init__(self) -> None:
        if self.aggregate_leader is not None and self.aggregate_leader not in {
            "home", "away", "level"
        }:
            raise ValueError("aggregate_leader must be 'home', 'away', 'level' or None")
        if self.favorite_side is not None and self.favorite_side not in {"home", "away"}:
            raise ValueError("favorite_side must be 'home', 'away' or None")


def _backs_side(market_family: str, market_cluster: str, selection: str) -> str | None:
    """Best-effort read of which fixture side a selection backs, or None."""
    side = str(selection or "").strip().casefold()
    cluster = str(market_cluster or "")
    if market_family in {"MATCH_RESULT", "ASIAN_HANDICAP", "DRAW_NO_BET"}:
        if side.startswith("home") or cluster.endswith("HOME") or cluster == "HOME_RESULT":
            return "home"
        if side.startswith("away") or cluster.endswith("AWAY") or cluster == "AWAY_RESULT":
            return "away"
    if market_family == "DOUBLE_CHANCE":
        if side == "home_draw":
            return "home"
        if side == "draw_away":
            return "away"
    if market_family == "QUALIFICATION":
        if side == "home":
            return "home"
        if side == "away":
            return "away"
    return None


def tag_archetypes(
    *,
    market_family: str,
    market_cluster: str,
    selection: str,
    line: float | None = None,
    context: MatchArchetypeContext | None = None,
) -> list[str]:
    """Return the (possibly empty) sorted list of archetype tags for one candidate."""
    ctx = context or MatchArchetypeContext()
    family = str(market_family or "")
    cluster = str(market_cluster or "")
    side = str(selection or "").strip().casefold()
    tags: set[str] = set()

    if family == "BTTS":
        tags.add("BTTS_YES" if side == "yes" else "BTTS_NO")

    if family == "TOTALS" and ctx.leg_number == 2:
        if side == "under":
            tags.add("SECOND_LEG_UNDER")
        elif side == "over":
            tags.add("SECOND_LEG_OVER")

    if family == "TEAM_TOTALS" and ctx.favorite_side is not None:
        team = "home" if cluster.startswith("HOME_TEAM") else "away" if cluster.startswith("AWAY_TEAM") else None
        direction = "OVER" if cluster.endswith("OVER") else "UNDER" if cluster.endswith("UNDER") else None
        if team is not None and direction is not None:
            if team == ctx.favorite_side and direction == "UNDER":
                tags.add("FAVORITE_TEAM_UNDER")
            elif team != ctx.favorite_side and direction == "OVER":
                tags.add("DOG_TEAM_OVER")

    if family == "ASIAN_HANDICAP" and line is not None and ctx.favorite_side is not None:
        backed = _backs_side(family, cluster, side)
        if backed is not None and backed != ctx.favorite_side and float(line) >= 1.0:
            tags.add("BIG_DOG_HANDICAP")

    if family == "DRAW_NO_BET" and ctx.favorite_side is not None:
        backed = _backs_side(family, cluster, side)
        if backed is not None and backed != ctx.favorite_side:
            tags.add("DNB_UNDERDOG")

    if family == "QUALIFICATION" and ctx.favorite_side is not None:
        backed = _backs_side(family, cluster, side)
        if backed == ctx.favorite_side:
            tags.add("QUALIFICATION_FAVORITE")
        elif backed is not None:
            tags.add("QUALIFICATION_DOG")

    backed_side = _backs_side(family, cluster, side)
    if backed_side is not None and ctx.favorite_side is not None and backed_side == ctx.favorite_side:
        tags.add("FAVORITE_CONTROL")
    if (
        backed_side is not None
        and ctx.leg_number == 2
        and ctx.aggregate_leader in {"home", "away"}
        and backed_side == ctx.aggregate_leader
    ):
        tags.add("AGGREGATE_LEADER")
    if (
        backed_side == "home"
        and ctx.leg_number == 2
        and ctx.aggregate_leader == "away"
    ):
        tags.add("HOME_COMEBACK")

    unknown = tags - KNOWN_ARCHETYPES
    if unknown:  # pragma: no cover - defensive; taxonomy drift would be a bug
        raise ValueError(f"tagger produced unknown archetype(s): {sorted(unknown)}")
    return sorted(tags)

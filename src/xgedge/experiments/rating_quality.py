"""Refuse to price bets off a rating ladder that carries no information.

The UEFA qualifying model turns an Elo difference into goal expectations. That
is only meaningful if the ratings actually separate strong clubs from weak
ones. When the ClubElo fetch fails, every club falls back to an Elo replayed
from a handful of official UEFA results anchored near a 1500 prior, and the
resulting ladder collapses: in the 2026-08-21 snapshot the whole 86-club field
spanned 1420-1624, placing Rangers 22 points above Lincoln Red Imps, clubs
whose true separation is several hundred points.

A collapsed ladder does not produce cautious probabilities — it produces
confident wrong ones. Two clubs of genuinely different strength look near
even, so the model reads a heavy favourite as a coin flip and reports a
"+84% edge" against a bookmaker who priced the match correctly. The edge is
an artefact of the missing ratings, and no downstream gate can tell it apart
from a real one, because the arithmetic is internally consistent.

So the check belongs here, at the source: when the rating basis is degraded,
the fixture is still forecast and still displayed, but it is marked ineligible
to produce betting candidates, with the reason attached.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

RATING_QUALITY_SCHEMA = "rating-quality/1.0"

# The rating source names emitted by the UEFA qualifying experiment.
TRUSTED_SOURCES = frozenset({"clubelo"})
FALLBACK_SOURCES = frozenset({"uefa_official_results", "uefa_cold_start_prior"})


@dataclass(frozen=True, slots=True)
class RatingQualityPolicy:
    """Thresholds separating a usable rating ladder from a degraded one.

    These are deliberately loose. They are not tuned to maximise anything;
    they exist to catch the collapse case, where the ladder has lost the
    ability to rank clubs at all.
    """

    version: str = RATING_QUALITY_SCHEMA
    # Share of rated teams that must come from a real rating provider.
    minimum_trusted_share: float = 0.5
    # A field of European clubs spanning fewer Elo points than this is not
    # separating anyone. ClubElo's real spread across UEFA qualifying is
    # several hundred points.
    minimum_elo_spread: float = 300.0

    def validate(self) -> None:
        if not 0.0 <= self.minimum_trusted_share <= 1.0:
            raise ValueError("minimum_trusted_share must be a share in [0, 1]")
        if self.minimum_elo_spread <= 0:
            raise ValueError("minimum_elo_spread must be positive")


def _rating_pairs(predictions: Sequence[Mapping[str, Any]]) -> list[tuple[str, float]]:
    pairs: list[tuple[str, float]] = []
    for prediction in predictions:
        if not isinstance(prediction, Mapping):
            continue
        ratings = prediction.get("ratings")
        if not isinstance(ratings, Mapping):
            continue
        for side in ("home", "away"):
            rating = ratings.get(side)
            if not isinstance(rating, Mapping):
                continue
            elo = rating.get("elo")
            if isinstance(elo, bool) or not isinstance(elo, (int, float)):
                continue
            pairs.append((str(rating.get("source") or "unknown"), float(elo)))
    return pairs


def assess_rating_quality(
    predictions: Sequence[Mapping[str, Any]],
    *,
    policy: RatingQualityPolicy | None = None,
) -> dict[str, Any]:
    """Judge whether this rating ladder may back betting candidates."""
    rules = policy or RatingQualityPolicy()
    rules.validate()
    pairs = _rating_pairs(predictions)
    if not pairs:
        return {
            "schema_version": RATING_QUALITY_SCHEMA,
            "status": "DEGRADED",
            "betting_eligible": False,
            "reasons": ["no_ratings_available"],
            "rated_teams": 0,
            "trusted_share": 0.0,
            "elo_spread": None,
            "policy": {
                "minimum_trusted_share": rules.minimum_trusted_share,
                "minimum_elo_spread": rules.minimum_elo_spread,
            },
        }

    elos = [elo for _, elo in pairs]
    trusted = sum(1 for source, _ in pairs if source in TRUSTED_SOURCES)
    trusted_share = trusted / len(pairs)
    spread = max(elos) - min(elos)

    reasons: list[str] = []
    if trusted_share < rules.minimum_trusted_share:
        reasons.append("insufficient_trusted_rating_coverage")
    if spread < rules.minimum_elo_spread:
        reasons.append("collapsed_elo_ladder")

    return {
        "schema_version": RATING_QUALITY_SCHEMA,
        "status": "DEGRADED" if reasons else "ACTIVE",
        "betting_eligible": not reasons,
        "reasons": reasons,
        "rated_teams": len(pairs),
        "trusted_share": trusted_share,
        "elo_spread": spread,
        "source_counts": {
            source: sum(1 for name, _ in pairs if name == source)
            for source in sorted({name for name, _ in pairs})
        },
        "policy": {
            "minimum_trusted_share": rules.minimum_trusted_share,
            "minimum_elo_spread": rules.minimum_elo_spread,
        },
        "note": (
            "Прогноз остаётся виден, но вырожденная лестница рейтингов не "
            "допускается к созданию ставочных кандидатов: она порождает "
            "мнимый эдж, неотличимый от настоящего по арифметике."
        ),
    }

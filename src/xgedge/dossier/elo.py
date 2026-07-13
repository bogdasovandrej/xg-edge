"""Point-in-time Elo for club and national-team match records.

The two scopes never share ratings.  Only records explicitly marked as
official update Elo; friendlies and records with an unknown match type are
ignored.  Matches with the same kickoff timestamp are updated as one batch so
no result can leak into another simultaneous fixture.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite, sqrt
from typing import Any, Iterable, Mapping

from xgedge.data.point_in_time import as_utc, iso_utc

VALID_SCOPES = {"club", "national"}


@dataclass(frozen=True)
class EloConfig:
    """Transparent Elo assumptions, not fitted claims."""

    initial_rating: float = 1500.0
    scale: float = 400.0
    k_factor: float = 20.0
    club_home_advantage: float = 55.0
    national_home_advantage: float = 35.0
    maximum_margin_multiplier: float = 2.5

    def validate(self) -> None:
        numeric = asdict(self)
        if any(not isfinite(float(value)) for value in numeric.values()):
            raise ValueError("Elo configuration must contain finite numbers")
        if self.scale <= 0 or self.k_factor <= 0:
            raise ValueError("Elo scale and k_factor must be positive")
        if self.maximum_margin_multiplier < 1:
            raise ValueError("maximum_margin_multiplier must be at least 1")


@dataclass(frozen=True)
class EloSnapshot:
    rating: float
    matches: int
    scope: str
    source: str
    as_of: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _scope(row: Mapping[str, Any]) -> str | None:
    value = str(row.get("scope") or row.get("team_type") or "").strip().lower()
    return value if value in VALID_SCOPES else None


def _goals(row: Mapping[str, Any]) -> tuple[int, int] | None:
    home = row.get("home_goals_90", row.get("home_goals"))
    away = row.get("away_goals_90", row.get("away_goals"))
    if (
        isinstance(home, bool)
        or isinstance(away, bool)
        or not isinstance(home, int)
        or not isinstance(away, int)
        or min(home, away) < 0
    ):
        return None
    return home, away


def _identity(row: Mapping[str, Any], side: str) -> str | None:
    value = row.get(f"{side}_id")
    return str(value).strip() if value is not None and str(value).strip() else None


class PointInTimeElo:
    """Replay official results and expose ratings strictly before a cutoff."""

    def __init__(
        self,
        matches: Iterable[Mapping[str, Any]],
        *,
        priors: Mapping[tuple[str, str], float] | None = None,
        config: EloConfig | None = None,
    ) -> None:
        self.config = config or EloConfig()
        self.config.validate()
        self._priors: dict[tuple[str, str], float] = {}
        for key, value in dict(priors or {}).items():
            if not isinstance(key, tuple) or len(key) != 2 or key[0] not in VALID_SCOPES:
                raise ValueError("Elo prior keys must be (club|national, team_id)")
            rating = float(value)
            if not isfinite(rating):
                raise ValueError("Elo priors must be finite")
            self._priors[(key[0], str(key[1]))] = rating

        eligible: list[tuple[Any, str, dict[str, Any]]] = []
        self.ignored_records: list[dict[str, str]] = []
        for index, source in enumerate(matches):
            row = dict(source)
            match_id = str(row.get("id") or row.get("match_id") or f"row-{index}")
            reason = self._ineligible_reason(row)
            if reason:
                self.ignored_records.append({"match_id": match_id, "reason": reason})
                continue
            kickoff = as_utc(row["kickoff_utc"], field="kickoff_utc")
            eligible.append((kickoff, match_id, row))

        eligible.sort(key=lambda item: (item[0], item[1]))
        self._match_before: dict[str, dict[str, EloSnapshot]] = {}
        self._history: dict[tuple[str, str], list[tuple[Any, float, int]]] = {}
        ratings: dict[tuple[str, str], float] = dict(self._priors)
        counts: dict[tuple[str, str], int] = {}

        cursor = 0
        while cursor < len(eligible):
            kickoff = eligible[cursor][0]
            end = cursor
            while end < len(eligible) and eligible[end][0] == kickoff:
                end += 1
            batch = eligible[cursor:end]
            seen: set[tuple[str, str]] = set()
            pending: dict[tuple[str, str], float] = {}
            for _, match_id, row in batch:
                scope = _scope(row)
                assert scope is not None
                home_id, away_id = _identity(row, "home"), _identity(row, "away")
                assert home_id is not None and away_id is not None
                home_key, away_key = (scope, home_id), (scope, away_id)
                if home_key in seen or away_key in seen:
                    raise ValueError(
                        f"team appears twice at the same kickoff: {match_id}"
                    )
                seen.update((home_key, away_key))
                home_rating = ratings.get(home_key, self.config.initial_rating)
                away_rating = ratings.get(away_key, self.config.initial_rating)
                home_count, away_count = counts.get(home_key, 0), counts.get(away_key, 0)
                source_home = "provided_prior" if home_key in self._priors else (
                    "official_results" if home_count else "cold_start_prior"
                )
                source_away = "provided_prior" if away_key in self._priors else (
                    "official_results" if away_count else "cold_start_prior"
                )
                self._match_before[match_id] = {
                    "home": EloSnapshot(home_rating, home_count, scope, source_home, iso_utc(kickoff)),
                    "away": EloSnapshot(away_rating, away_count, scope, source_away, iso_utc(kickoff)),
                }

                goals = _goals(row)
                assert goals is not None
                neutral = row.get("neutral_venue") is True
                advantage = 0.0 if neutral else (
                    self.config.club_home_advantage
                    if scope == "club"
                    else self.config.national_home_advantage
                )
                expected_home = 1.0 / (
                    1.0 + 10.0 ** (-(home_rating + advantage - away_rating) / self.config.scale)
                )
                actual_home = 1.0 if goals[0] > goals[1] else 0.0 if goals[0] < goals[1] else 0.5
                goal_difference = abs(goals[0] - goals[1])
                margin = min(
                    self.config.maximum_margin_multiplier,
                    1.0 if goal_difference <= 1 else sqrt(float(goal_difference)),
                )
                weight = row.get("elo_weight", 1.0)
                if isinstance(weight, bool):
                    raise ValueError(f"elo_weight must be numeric: {match_id}")
                weight = float(weight)
                if not isfinite(weight) or not 0 < weight <= 2:
                    raise ValueError(f"elo_weight must be in (0, 2]: {match_id}")
                delta = self.config.k_factor * weight * margin * (actual_home - expected_home)
                pending[home_key] = pending.get(home_key, 0.0) + delta
                pending[away_key] = pending.get(away_key, 0.0) - delta

            for key, delta in pending.items():
                ratings[key] = ratings.get(key, self.config.initial_rating) + delta
                counts[key] = counts.get(key, 0) + 1
                self._history.setdefault(key, []).append(
                    (kickoff, ratings[key], counts[key])
                )
            cursor = end

    @staticmethod
    def _ineligible_reason(row: Mapping[str, Any]) -> str | None:
        if row.get("official") is not True:
            return "not_explicitly_official"
        if str(row.get("status", "")).upper() != "FINISHED":
            return "not_finished"
        if _scope(row) is None:
            return "invalid_scope"
        if _identity(row, "home") is None or _identity(row, "away") is None:
            return "missing_team_id"
        if _identity(row, "home") == _identity(row, "away"):
            return "same_team"
        if _goals(row) is None:
            return "missing_regulation_score"
        try:
            as_utc(row.get("kickoff_utc"), field="kickoff_utc")
        except (TypeError, ValueError):
            return "invalid_kickoff"
        return None

    def before_match(self, match_id: str) -> dict[str, dict[str, Any]] | None:
        snapshots = self._match_before.get(str(match_id))
        if snapshots is None:
            return None
        return {side: snapshot.to_dict() for side, snapshot in snapshots.items()}

    def rating_at(self, team_id: str, scope: str, cutoff: Any) -> dict[str, Any]:
        """Return a rating after matches strictly earlier than ``cutoff``."""
        if scope not in VALID_SCOPES:
            raise ValueError("scope must be club or national")
        instant = as_utc(cutoff, field="cutoff")
        key = (scope, str(team_id))
        rating = self._priors.get(key, self.config.initial_rating)
        count = 0
        for updated_at, candidate, candidate_count in self._history.get(key, []):
            if updated_at >= instant:
                break
            rating, count = candidate, candidate_count
        source = "provided_prior" if key in self._priors else (
            "official_results" if count else "cold_start_prior"
        )
        return EloSnapshot(rating, count, scope, source, iso_utc(instant)).to_dict()


def rating_level(rating: float) -> str:
    """Stable model tier.  It is explicitly not an external team category."""
    if rating >= 1800:
        return "elite"
    if rating >= 1650:
        return "strong"
    if rating >= 1450:
        return "average"
    return "developing"

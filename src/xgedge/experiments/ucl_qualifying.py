"""Experimental UEFA Champions League qualifying predictor based on ClubElo.

This module deliberately stays separate from the validated xG pipeline.  It
turns a *published* ClubElo difference into an independent-Poisson score model
using an explicit, fixed calibration.  It does not claim a betting edge.

ClubElo API documentation: http://clubelo.com/API
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import numpy as np
import requests

from xgedge.models.dixon_coles import score_matrix


DEFAULT_CLUBELO_URL = "http://api.clubelo.com/{date}"
CLUBELO_ATTRIBUTION_URL = "http://clubelo.com/API"

# Explicit mappings only.  There is intentionally no fuzzy match: a bad match
# is worse than a missing prediction.  Callers can add aliases through the CLI.
DEFAULT_TEAM_ALIASES: dict[str, str] = {
    "Ararat-Armenia": "Ararat",
    "Borac": "Borac Banja Luka",
    "Győri ETO": "Gyoer",
    "Iberia Tbilisi": "Saburtalo",
    "Inter Escaldes": "Escaldes",
    "Kairat Almaty": "Kairat",
    "KuPS Kuopio": "Kuopio",
    "L. Red Imps": "Lincoln",
    "Levski Sofia": "Levski",
    "Riga": "FK Riga",
    "Shamrock Rovers": "Shamrock",
    "U. Craiova": "Craiova",
    "Víkingur R.": "Vikingur",
}


@dataclass(frozen=True)
class EloPoissonCalibration:
    """Fixed v1 calibration used to translate Elo to regulation-time goals.

    ``adjusted_diff = home_elo - away_elo + home_advantage_elo``
    ``goal_ratio = 10 ** (adjusted_diff / elo_denominator)``
    ``lambda_home = total_goals * goal_ratio / (1 + goal_ratio)``
    ``lambda_away = total_goals / (1 + goal_ratio)``

    The constants are intentionally configurable and exposed in every output.
    They are a broad European-football baseline, not evidence of positive CLV.
    """

    version: str = "clubelo-poisson-v1"
    total_goals: float = 2.65
    home_advantage_elo: float = 65.0
    elo_denominator: float = 400.0
    elo_uncertainty: float = 50.0
    max_goals: int = 10

    def validate(self) -> None:
        values = (
            self.total_goals,
            self.home_advantage_elo,
            self.elo_denominator,
            self.elo_uncertainty,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("calibration values must be finite")
        if self.total_goals <= 0 or self.elo_denominator <= 0:
            raise ValueError("total_goals and elo_denominator must be positive")
        if self.elo_uncertainty < 0:
            raise ValueError("elo_uncertainty must be non-negative")
        if (
            isinstance(self.max_goals, bool)
            or not isinstance(self.max_goals, (int, np.integer))
            or self.max_goals < 5
        ):
            raise ValueError("max_goals must be an integer of at least 5")


@dataclass(frozen=True)
class ClubEloRating:
    club: str
    country: str | None
    elo: float
    rank: int | None = None
    valid_from: str | None = None
    valid_to: str | None = None


def normalize_team_name(name: str) -> str:
    """Return a conservative accent/punctuation-insensitive comparison key."""
    if not isinstance(name, str):
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    asciiish = "".join(char for char in decomposed if not unicodedata.combining(char))
    asciiish = asciiish.casefold().replace("&", " and ")
    tokens = re.findall(r"[a-z0-9]+", asciiish)
    removable = {"afc", "cf", "fc", "fk", "sc", "sk"}
    return " ".join(token for token in tokens if token not in removable)


def parse_clubelo_csv(payload: str) -> list[ClubEloRating]:
    """Parse a ClubElo daily-ranking CSV response with strict Elo validation."""
    if not isinstance(payload, str) or not payload.strip():
        raise ValueError("ClubElo CSV is empty")
    reader = csv.DictReader(io.StringIO(payload.lstrip("\ufeff")))
    fields = {str(field).casefold(): field for field in (reader.fieldnames or [])}
    club_col = fields.get("club") or fields.get("name")
    elo_col = fields.get("elo")
    if not club_col or not elo_col:
        raise ValueError("ClubElo CSV must contain Club and Elo columns")

    ratings: list[ClubEloRating] = []
    for row in reader:
        club = str(row.get(club_col, "")).strip()
        if not club:
            continue
        try:
            elo = float(row.get(elo_col, ""))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(elo):
            continue
        rank_value = row.get(fields.get("rank", ""))
        try:
            rank = int(rank_value) if rank_value not in (None, "") else None
        except (TypeError, ValueError):
            rank = None
        ratings.append(
            ClubEloRating(
                club=club,
                country=(str(row.get(fields.get("country", ""), "")).strip() or None),
                elo=elo,
                rank=rank,
                valid_from=(str(row.get(fields.get("from", ""), "")).strip() or None),
                valid_to=(str(row.get(fields.get("to", ""), "")).strip() or None),
            )
        )
    if not ratings:
        raise ValueError("ClubElo CSV contains no valid ratings")
    return ratings


def clubelo_ranking_url(url_template: str, as_of: datetime) -> str:
    """Resolve a configurable ClubElo URL, optionally containing ``{date}``."""
    if not isinstance(url_template, str) or not url_template.strip():
        raise ValueError("ClubElo URL must not be empty")
    return url_template.format(date=as_of.astimezone(timezone.utc).date().isoformat())


def fetch_clubelo_ratings(
    *,
    as_of: datetime,
    url_template: str = DEFAULT_CLUBELO_URL,
    timeout: float = 30.0,
    session: requests.Session | None = None,
) -> tuple[list[ClubEloRating], str]:
    """Fetch a dated public ClubElo ranking and return ratings plus exact URL."""
    if timeout <= 0 or not math.isfinite(timeout):
        raise ValueError("timeout must be a positive finite number")
    url = clubelo_ranking_url(url_template, as_of)
    client = session or requests.Session()
    if session is None:
        # Some desktop environments inject an optional SOCKS proxy without the
        # requests extra.  Both public sources support a direct read-only call.
        client.trust_env = False
    response = client.get(
        url,
        headers={"Accept": "text/csv", "User-Agent": "xgedge-ucl-experiment/1"},
        timeout=timeout,
    )
    response.raise_for_status()
    return parse_clubelo_csv(response.text), url


class ClubEloIndex:
    """Exact normalized-name lookup with explicit aliases and ambiguity guards."""

    def __init__(
        self,
        ratings: Sequence[ClubEloRating],
        aliases: Mapping[str, str] | None = None,
    ) -> None:
        by_name: dict[str, ClubEloRating] = {}
        ambiguous: set[str] = set()
        for rating in ratings:
            key = normalize_team_name(rating.club)
            if not key:
                continue
            if key in by_name and by_name[key].club != rating.club:
                ambiguous.add(key)
            else:
                by_name[key] = rating
        self._ratings = {key: value for key, value in by_name.items() if key not in ambiguous}
        combined = dict(DEFAULT_TEAM_ALIASES)
        if aliases:
            combined.update(aliases)
        self._aliases = {
            normalize_team_name(source): normalize_team_name(target)
            for source, target in combined.items()
            if normalize_team_name(source) and normalize_team_name(target)
        }

    def lookup(self, team: str) -> ClubEloRating | None:
        key = normalize_team_name(team)
        if key in self._ratings:
            return self._ratings[key]
        target = self._aliases.get(key)
        return self._ratings.get(target) if target else None


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid ISO-8601 datetime: {value!r}") from exc
    if not isinstance(value, datetime):
        raise TypeError("datetime must be an ISO string or datetime")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _lambdas(adjusted_diff: float, calibration: EloPoissonCalibration) -> tuple[float, float]:
    ratio = 10.0 ** (adjusted_diff / calibration.elo_denominator)
    lam_home = calibration.total_goals * ratio / (1.0 + ratio)
    return lam_home, calibration.total_goals - lam_home


def _outcomes(matrix: np.ndarray) -> dict[str, float]:
    return {
        "home_win": float(np.tril(matrix, -1).sum()),
        "draw": float(np.trace(matrix)),
        "away_win": float(np.triu(matrix, 1).sum()),
    }


def _uncertainty_interval(
    adjusted_diff: float, calibration: EloPoissonCalibration
) -> dict[str, dict[str, float]]:
    values: dict[str, list[float]] = {name: [] for name in ("home_win", "draw", "away_win")}
    for diff in np.linspace(
        adjusted_diff - calibration.elo_uncertainty,
        adjusted_diff + calibration.elo_uncertainty,
        21,
    ):
        lh, la = _lambdas(float(diff), calibration)
        outcomes = _outcomes(score_matrix(lh, la, max_goals=calibration.max_goals))
        for name, probability in outcomes.items():
            values[name].append(probability)
    return {
        name: {"low": float(min(probabilities)), "high": float(max(probabilities))}
        for name, probabilities in values.items()
    }


def _top_scores(matrix: np.ndarray, count: int = 5) -> list[dict[str, float | int | str]]:
    order = np.argsort(matrix.ravel())[::-1][:count]
    scores = []
    for flat_index in order:
        home_goals, away_goals = np.unravel_index(flat_index, matrix.shape)
        scores.append(
            {
                "score": f"{home_goals}-{away_goals}",
                "home_goals": int(home_goals),
                "away_goals": int(away_goals),
                "probability": float(matrix[home_goals, away_goals]),
            }
        )
    return scores


def _fixture_seed(fixture_id: str, seed: int) -> int:
    digest = hashlib.blake2b(
        f"{seed}:{fixture_id}".encode("utf-8"), digest_size=8
    ).digest()
    return int.from_bytes(digest, "little", signed=False)


def simulate_qualification(
    *,
    matrix_90m: np.ndarray,
    lambda_home: float,
    lambda_away: float,
    aggregate_home: int,
    aggregate_away: int,
    simulations: int,
    seed: int,
) -> dict[str, Any]:
    """Simulate advancement separately from the unconditional 90-minute model."""
    if (
        isinstance(simulations, bool)
        or not isinstance(simulations, (int, np.integer))
        or simulations < 1_000
    ):
        raise ValueError("simulations must be an integer of at least 1000")
    if min(aggregate_home, aggregate_away) < 0:
        raise ValueError("aggregate scores must be non-negative")
    rng = np.random.default_rng(seed)
    sampled = rng.choice(matrix_90m.size, simulations, p=matrix_90m.ravel())
    home_goals, away_goals = np.unravel_index(sampled, matrix_90m.shape)
    home_total = home_goals + aggregate_home
    away_total = away_goals + aggregate_away
    extra_time = home_total == away_total
    home_advances = home_total > away_total

    n_extra = int(extra_time.sum())
    if n_extra:
        extra_home = rng.poisson(lambda_home / 3.0, n_extra)
        extra_away = rng.poisson(lambda_away / 3.0, n_extra)
        extra_home_advances = extra_home > extra_away
        penalties = extra_home == extra_away
        extra_home_advances[penalties] = rng.random(int(penalties.sum())) < 0.5
        home_advances[extra_time] = extra_home_advances

    p_home = float(home_advances.mean())
    p_extra = float(extra_time.mean())
    return {
        "home_to_advance": p_home,
        "away_to_advance": 1.0 - p_home,
        "extra_time": p_extra,
        "home_to_advance_mc_se": math.sqrt(p_home * (1.0 - p_home) / simulations),
        "extra_time_mc_se": math.sqrt(p_extra * (1.0 - p_extra) / simulations),
        "simulations": simulations,
        "penalty_model": "50/50 after simulated extra time",
    }


def predict_fixture(
    fixture: Mapping[str, Any],
    ratings: ClubEloIndex,
    *,
    as_of: datetime,
    calibration: EloPoissonCalibration | None = None,
    simulations: int = 50_000,
    seed: int = 20260713,
) -> dict[str, Any]:
    """Predict one future fixture; unknown teams produce an explicit no-prediction."""
    calibration = calibration or EloPoissonCalibration()
    calibration.validate()
    fixture_id = str(fixture.get("id", ""))
    home = str(fixture.get("home", "")).strip()
    away = str(fixture.get("away", "")).strip()
    base = {
        "fixture_id": fixture_id or None,
        "kickoff_utc": fixture.get("kickoff_utc"),
        "competition": fixture.get("competition"),
        "round": fixture.get("round"),
        "leg": fixture.get("leg"),
        "home": home or None,
        "away": away or None,
    }
    if not fixture_id or not home or not away or not fixture.get("kickoff_utc"):
        return {**base, "status": "no_prediction", "reason": "invalid_fixture"}
    try:
        kickoff = _as_utc(str(fixture["kickoff_utc"]))
    except (TypeError, ValueError):
        return {**base, "status": "no_prediction", "reason": "invalid_fixture"}
    if kickoff <= _as_utc(as_of):
        return {**base, "status": "no_prediction", "reason": "not_a_future_fixture"}

    home_rating = ratings.lookup(home)
    away_rating = ratings.lookup(away)
    missing = [
        team
        for team, rating in ((home, home_rating), (away, away_rating))
        if rating is None
    ]
    if missing:
        return {
            **base,
            "status": "no_prediction",
            "reason": "clubelo_team_not_found",
            "missing_teams": missing,
        }

    assert home_rating is not None and away_rating is not None
    adjusted_diff = (
        home_rating.elo - away_rating.elo + calibration.home_advantage_elo
    )
    lambda_home, lambda_away = _lambdas(adjusted_diff, calibration)
    matrix = score_matrix(
        lambda_home, lambda_away, max_goals=calibration.max_goals
    )
    result: dict[str, Any] = {
        **base,
        "status": "ok",
        "ratings": {
            "home": asdict(home_rating),
            "away": asdict(away_rating),
            "adjusted_elo_difference": adjusted_diff,
        },
        "expected_goals_90m": {"home": lambda_home, "away": lambda_away},
        "probabilities_90m": _outcomes(matrix),
        "uncertainty_90m": {
            "method": "recalculate over adjusted Elo difference +/- configured band",
            "elo_points_plus_minus": calibration.elo_uncertainty,
            "intervals": _uncertainty_interval(adjusted_diff, calibration),
        },
        "most_likely_scores_90m": _top_scores(matrix),
        "qualification": None,
        "disclaimer": "Experimental baseline; no demonstrated betting or CLV edge.",
    }

    aggregate_home = fixture.get("aggregate_home_score")
    aggregate_away = fixture.get("aggregate_away_score")
    try:
        leg = int(fixture.get("leg"))
    except (TypeError, ValueError):
        leg = None
    if (
        leg == 2
        and isinstance(aggregate_home, int)
        and not isinstance(aggregate_home, bool)
        and isinstance(aggregate_away, int)
        and not isinstance(aggregate_away, bool)
    ):
        result["qualification"] = simulate_qualification(
            matrix_90m=matrix,
            lambda_home=lambda_home,
            lambda_away=lambda_away,
            aggregate_home=aggregate_home,
            aggregate_away=aggregate_away,
            simulations=simulations,
            seed=_fixture_seed(fixture_id, seed),
        )
        result["qualification"]["aggregate_before_kickoff"] = {
            "home": aggregate_home,
            "away": aggregate_away,
        }
    return result


def predict_fixtures(
    fixtures: Sequence[Mapping[str, Any]],
    rating_rows: Sequence[ClubEloRating],
    *,
    as_of: datetime,
    aliases: Mapping[str, str] | None = None,
    calibration: EloPoissonCalibration | None = None,
    simulations: int = 50_000,
    seed: int = 20260713,
) -> list[dict[str, Any]]:
    """Predict an ordered fixture sequence using one as-of ratings snapshot."""
    index = ClubEloIndex(rating_rows, aliases)
    return [
        predict_fixture(
            fixture,
            index,
            as_of=as_of,
            calibration=calibration,
            simulations=simulations,
            seed=seed,
        )
        for fixture in fixtures
    ]


def coverage_summary(predictions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(predictions)
    predicted = sum(item.get("status") == "ok" for item in predictions)
    missing = sorted(
        {
            str(team)
            for item in predictions
            for team in item.get("missing_teams", [])
        }
    )
    return {
        "fixtures": total,
        "predicted": predicted,
        "no_prediction": total - predicted,
        "coverage": predicted / total if total else 0.0,
        "missing_teams": missing,
    }

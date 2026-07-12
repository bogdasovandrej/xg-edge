"""A deliberately small, auditable World Cup 2026 national-team model."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from math import exp, log
from typing import Any, Iterable, Mapping

import numpy as np

from xgedge.international.fifa import iso_utc, parse_utc
from xgedge.models.dixon_coles import fit_rho, score_matrix


@dataclass(frozen=True)
class _Posterior:
    attack_shape: float
    attack_rate: float
    defence_shape: float
    defence_rate: float
    matches: int


class WorldCupModel:
    """Poisson/Dixon-Coles 90-minute model with FIFA-rating shrinkage.

    FIFA ratings provide the pre-tournament expectation.  Goals from completed
    matches in this World Cup update attack and defensive multipliers through
    Gamma-Poisson shrinkage.  The model is intentionally labelled experimental:
    the current-tournament sample is small and it is not a betting system.
    """

    label = "experimental"
    rating_scale = 1000.0
    baseline_total_goals = 2.60
    baseline_prior_matches = 12.0
    team_prior_expected_goals = 8.0
    rho_prior_matches = 200.0

    def __init__(
        self,
        rankings: Mapping[str, Any],
        matches: Iterable[Mapping[str, Any]],
        *,
        uncertainty_draws: int = 1000,
        random_seed: int = 2026,
    ) -> None:
        ranking_rows = rankings.get("rankings")
        if not isinstance(ranking_rows, list):
            raise ValueError("rankings must be normalized FIFA rankings")
        self.rankings = {str(row["team_id"]): dict(row) for row in ranking_rows}
        self.ranking_publication_utc = str(rankings.get("publication_utc"))
        self.matches = [dict(row) for row in matches]
        if uncertainty_draws < 100:
            raise ValueError("uncertainty_draws must be at least 100")
        self.uncertainty_draws = int(uncertainty_draws)
        self.random_seed = int(random_seed)

    def _rating(self, team_id: str) -> float:
        try:
            return float(self.rankings[str(team_id)]["rating"])
        except KeyError as exc:
            raise ValueError(f"team {team_id!r} is missing from FIFA rankings") from exc

    def _prior_lambdas(self, home_id: str, away_id: str, total: float) -> tuple[float, float]:
        difference = self._rating(home_id) - self._rating(away_id)
        # A rating difference of rating_scale means a 10:1 expected-goal ratio.
        log_ratio = np.clip(log(10.0) * difference / self.rating_scale, -2.0, 2.0)
        home_share = 1.0 / (1.0 + exp(-float(log_ratio)))
        return total * home_share, total * (1.0 - home_share)

    def _training(self, cutoff: datetime) -> list[dict[str, Any]]:
        rows = []
        for match in self.matches:
            if match.get("status") != "FINISHED":
                continue
            kickoff = parse_utc(match["kickoff_utc"])
            if kickoff >= cutoff:
                continue
            if not isinstance(match.get("home_goals_90"), int) or not isinstance(
                match.get("away_goals_90"), int
            ):
                continue
            rows.append(match)
        return sorted(rows, key=lambda row: (row["kickoff_utc"], row["id"]))

    def _fit(
        self, training: list[dict[str, Any]]
    ) -> tuple[float, dict[str, _Posterior], float]:
        observed_goals = sum(
            row["home_goals_90"] + row["away_goals_90"] for row in training
        )
        total = (
            observed_goals + self.baseline_prior_matches * self.baseline_total_goals
        ) / (len(training) + self.baseline_prior_matches)

        accum: dict[str, dict[str, float]] = {
            team_id: {"gf": 0.0, "ga": 0.0, "xgf": 0.0, "xga": 0.0, "n": 0.0}
            for team_id in self.rankings
        }
        for row in training:
            home_id, away_id = str(row["home_id"]), str(row["away_id"])
            if home_id not in accum or away_id not in accum:
                raise ValueError(f"match {row['id']} contains a team absent from rankings")
            expected_home, expected_away = self._prior_lambdas(home_id, away_id, total)
            goals_home, goals_away = row["home_goals_90"], row["away_goals_90"]
            accum[home_id]["gf"] += goals_home
            accum[home_id]["ga"] += goals_away
            accum[home_id]["xgf"] += expected_home
            accum[home_id]["xga"] += expected_away
            accum[home_id]["n"] += 1
            accum[away_id]["gf"] += goals_away
            accum[away_id]["ga"] += goals_home
            accum[away_id]["xgf"] += expected_away
            accum[away_id]["xga"] += expected_home
            accum[away_id]["n"] += 1

        prior = self.team_prior_expected_goals
        posteriors = {
            team_id: _Posterior(
                attack_shape=prior + values["gf"],
                attack_rate=prior + values["xgf"],
                defence_shape=prior + values["ga"],
                defence_rate=prior + values["xga"],
                matches=int(values["n"]),
            )
            for team_id, values in accum.items()
        }

        lam_h: list[float] = []
        lam_a: list[float] = []
        goals_h: list[int] = []
        goals_a: list[int] = []
        for row in training:
            ph, pa = self._point_lambdas(
                str(row["home_id"]), str(row["away_id"]), total, posteriors
            )
            lam_h.append(ph)
            lam_a.append(pa)
            goals_h.append(row["home_goals_90"])
            goals_a.append(row["away_goals_90"])
        rho_mle = fit_rho(
            np.asarray(lam_h), np.asarray(lam_a), np.asarray(goals_h), np.asarray(goals_a)
        )
        # Rho is particularly noisy in one short tournament.  Shrink its MLE
        # towards independent Poisson rather than reporting a boundary estimate.
        rho = rho_mle * len(training) / (len(training) + self.rho_prior_matches)
        return float(total), posteriors, float(rho)

    def _point_lambdas(
        self,
        home_id: str,
        away_id: str,
        total: float,
        posteriors: Mapping[str, _Posterior],
    ) -> tuple[float, float]:
        prior_home, prior_away = self._prior_lambdas(home_id, away_id, total)
        home, away = posteriors[home_id], posteriors[away_id]
        lam_home = prior_home * (home.attack_shape / home.attack_rate) * (
            away.defence_shape / away.defence_rate
        )
        lam_away = prior_away * (away.attack_shape / away.attack_rate) * (
            home.defence_shape / home.defence_rate
        )
        return float(np.clip(lam_home, 0.15, 4.5)), float(np.clip(lam_away, 0.15, 4.5))

    @staticmethod
    def _markets(matrix: np.ndarray) -> tuple[float, float, float, float, float]:
        home = float(np.tril(matrix, -1).sum())
        draw = float(np.trace(matrix))
        away = float(np.triu(matrix, 1).sum())
        over = float(sum(matrix[i, j] for i in range(matrix.shape[0]) for j in range(matrix.shape[1]) if i + j >= 3))
        btts = float(1.0 - matrix[0, :].sum() - matrix[:, 0].sum() + matrix[0, 0])
        return home, draw, away, over, btts

    def _uncertainty(
        self,
        fixture_id: str,
        home_id: str,
        away_id: str,
        total: float,
        rho: float,
        posteriors: Mapping[str, _Posterior],
    ) -> dict[str, Any]:
        digest = hashlib.sha256(fixture_id.encode("utf-8")).digest()
        seed = (self.random_seed + int.from_bytes(digest[:4], "little")) % (2**32)
        rng = np.random.default_rng(seed)
        home, away = posteriors[home_id], posteriors[away_id]
        ah = rng.gamma(home.attack_shape, 1.0 / home.attack_rate, self.uncertainty_draws)
        dh = rng.gamma(home.defence_shape, 1.0 / home.defence_rate, self.uncertainty_draws)
        aa = rng.gamma(away.attack_shape, 1.0 / away.attack_rate, self.uncertainty_draws)
        da = rng.gamma(away.defence_shape, 1.0 / away.defence_rate, self.uncertainty_draws)
        prior_home, prior_away = self._prior_lambdas(home_id, away_id, total)
        lh = np.clip(prior_home * ah * da, 0.15, 4.5)
        la = np.clip(prior_away * aa * dh, 0.15, 4.5)
        samples = np.empty((self.uncertainty_draws, 5), dtype=float)
        for index, (sample_h, sample_a) in enumerate(zip(lh, la)):
            samples[index] = self._markets(score_matrix(sample_h, sample_a, rho, 10))

        def interval(values: np.ndarray) -> list[float]:
            return [float(value) for value in np.quantile(values, [0.1, 0.9])]

        return {
            "method": "gamma_posterior_parameter_draws",
            "interval": "80% (10th-90th percentiles)",
            "draws": self.uncertainty_draws,
            "seed": int(seed),
            "lambda_home": interval(lh),
            "lambda_away": interval(la),
            "p_home": interval(samples[:, 0]),
            "p_draw": interval(samples[:, 1]),
            "p_away": interval(samples[:, 2]),
            "p_over_2_5": interval(samples[:, 3]),
            "p_btts": interval(samples[:, 4]),
        }

    def predict(self, fixture: Mapping[str, Any], *, as_of: str | datetime) -> dict[str, Any]:
        """Predict one fixture using only results available strictly before cutoff."""
        as_of_dt = parse_utc(as_of)
        kickoff = parse_utc(fixture["kickoff_utc"])
        if as_of_dt >= kickoff:
            raise ValueError("as_of must be earlier than the fixture kickoff")
        cutoff = min(as_of_dt, kickoff)
        training = self._training(cutoff)
        if not training:
            raise ValueError("no finished tournament matches exist before cutoff")
        total, posteriors, rho = self._fit(training)
        home_id, away_id = str(fixture["home_id"]), str(fixture["away_id"])
        lam_home, lam_away = self._point_lambdas(home_id, away_id, total, posteriors)
        matrix = score_matrix(lam_home, lam_away, rho, 10)
        p_home, p_draw, p_away, p_over, p_btts = self._markets(matrix)
        score_rows = [
            {"score": f"{i}-{j}", "probability": float(matrix[i, j])}
            for i in range(matrix.shape[0])
            for j in range(matrix.shape[1])
        ]
        score_rows.sort(key=lambda row: (-row["probability"], row["score"]))
        latest = max(parse_utc(row["kickoff_utc"]) for row in training)
        return {
            "model": "fifa_prior_poisson_dixon_coles_v1",
            "label": self.label,
            "scope": "90_minutes_including_stoppage_time_only",
            "warning": "Experimental probability estimate; not a betting recommendation.",
            "fixture_id": str(fixture["id"]),
            "stage": fixture.get("stage"),
            "kickoff_utc": iso_utc(kickoff),
            "generated_as_of_utc": iso_utc(as_of_dt),
            "home": fixture["home"],
            "away": fixture["away"],
            "lambda_home": lam_home,
            "lambda_away": lam_away,
            "rho": rho,
            "rho_method": "Dixon-Coles MLE shrunk toward 0 with 200 pseudo-matches",
            "probabilities": {
                "home": p_home,
                "draw": p_draw,
                "away": p_away,
                "over_2_5": p_over,
                "under_2_5": 1.0 - p_over,
                "btts_yes": p_btts,
                "btts_no": 1.0 - p_btts,
            },
            "top_scores": score_rows[:5],
            "uncertainty": self._uncertainty(
                str(fixture["id"]), home_id, away_id, total, rho, posteriors
            ),
            "data_provenance": {
                "rankings_source": "official_fifa_api",
                "ranking_publication_utc": self.ranking_publication_utc,
                "results_source": "official_fifa_2026_world_cup_calendar_and_timelines",
                "training_filter": "status == FINISHED and kickoff < generated_as_of_utc < fixture kickoff",
                "training_matches": len(training),
                "latest_training_kickoff_utc": iso_utc(latest),
                "home_team_tournament_matches": posteriors[home_id].matches,
                "away_team_tournament_matches": posteriors[away_id].matches,
            },
        }

    def predict_stage(
        self,
        *,
        stage: str,
        as_of: str | datetime,
    ) -> list[dict[str, Any]]:
        as_of_dt = parse_utc(as_of)
        fixtures = [
            row
            for row in self.matches
            if str(row.get("stage", "")).casefold() == stage.casefold()
            and row.get("status") != "FINISHED"
            and parse_utc(row["kickoff_utc"]) > as_of_dt
        ]
        return [self.predict(row, as_of=as_of_dt) for row in fixtures]

    def predict_upcoming(self, *, as_of: str | datetime) -> list[dict[str, Any]]:
        """Predict every future fixture whose two participants are known.

        Later knockout placeholders are excluded until FIFA replaces them with
        ranked team ids. This lets a scheduled job move from semi-finals to the
        third-place match/final without inventing placeholder-team strengths.
        """
        as_of_dt = parse_utc(as_of)
        fixtures = [
            row
            for row in self.matches
            if row.get("status") != "FINISHED"
            and parse_utc(row["kickoff_utc"]) > as_of_dt
            and str(row.get("home_id")) in self.rankings
            and str(row.get("away_id")) in self.rankings
        ]
        fixtures.sort(key=lambda row: (row["kickoff_utc"], row["id"]))
        return [self.predict(row, as_of=as_of_dt) for row in fixtures]

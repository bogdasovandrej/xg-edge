"""Reference baselines: uniform 1X2 and a decay-free multiplicative Poisson."""
from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
import pandas as pd

from xgedge.contracts import Col


class UniformBaseline:
    """Predicts 1/3 for each 1X2 outcome; the weakest sensible benchmark."""

    def fit(self, matches: pd.DataFrame) -> "UniformBaseline":
        """No-op; kept for interface symmetry."""
        return self

    def predict_1x2(self, matches: pd.DataFrame) -> np.ndarray:
        """Return an (n, 3) array of uniform [H, D, A] probabilities."""
        return np.full((len(matches), 3), 1.0 / 3.0)


class GoalsAvgPoisson:
    """Multiplicative goal-average Poisson baseline (no decay, equal weights).

    lam_home = league_home_avg * att_factor(home) * def_factor(away)
    lam_away = league_away_avg * att_factor(away) * def_factor(home)

    Factors are team average goals scored (resp. conceded) over all matches,
    relative to the league average goals per team-match. Teams unseen during
    fit get factor 1.0.

    Attributes set by :meth:`fit`:
        league_home_avg_, league_away_avg_: league mean home/away goals.
        att_factor_, def_factor_: per-team factors keyed by canonical id.
    """

    league_home_avg_: float
    league_away_avg_: float
    att_factor_: Dict[str, float]
    def_factor_: Dict[str, float]

    def fit(self, matches: pd.DataFrame) -> "GoalsAvgPoisson":
        """Estimate league averages and per-team attack/defence factors."""
        fthg = matches[Col.FTHG].to_numpy(float)
        ftag = matches[Col.FTAG].to_numpy(float)
        self.league_home_avg_ = float(fthg.mean())
        self.league_away_avg_ = float(ftag.mean())

        long = pd.DataFrame(
            {
                "team": pd.concat(
                    [matches[Col.HOME], matches[Col.AWAY]], ignore_index=True
                ),
                "scored": np.concatenate([fthg, ftag]),
                "conceded": np.concatenate([ftag, fthg]),
            }
        )
        league_avg = float(long["scored"].mean())
        by_team = long.groupby("team")[["scored", "conceded"]].mean()
        if league_avg > 0:
            self.att_factor_ = (by_team["scored"] / league_avg).to_dict()
            self.def_factor_ = (by_team["conceded"] / league_avg).to_dict()
        else:
            # Degenerate all-0-0 sample: neutral factors keep lambdas finite.
            self.att_factor_ = {t: 1.0 for t in by_team.index}
            self.def_factor_ = {t: 1.0 for t in by_team.index}
        return self

    def predict_lambdas(self, matches: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        """Return (lam_home, lam_away) for each match row."""
        att_h = matches[Col.HOME].map(self.att_factor_).fillna(1.0).to_numpy(float)
        def_h = matches[Col.HOME].map(self.def_factor_).fillna(1.0).to_numpy(float)
        att_a = matches[Col.AWAY].map(self.att_factor_).fillna(1.0).to_numpy(float)
        def_a = matches[Col.AWAY].map(self.def_factor_).fillna(1.0).to_numpy(float)
        lam_h = self.league_home_avg_ * att_h * def_a
        lam_a = self.league_away_avg_ * att_a * def_h
        return lam_h, lam_a

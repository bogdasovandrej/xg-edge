"""Poisson goal models on pre-match feature ratings.

Both models share the same long-format design: every match contributes two
observations, one per side::

    (goals=FTHG, att=ATT_H, def_opp=DEF_A, is_home=1)
    (goals=FTAG, att=ATT_A, def_opp=DEF_H, is_home=0)

so a single regression learns log(lambda) = f(att, def_opp, is_home) and
``predict_lambdas`` evaluates it once per side.
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd
import statsmodels.api as sm
from sklearn.ensemble import HistGradientBoostingRegressor

from xgedge.contracts import Col, Feat


def _long_arrays(feats: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
    """Stack a features table into long format (y, X=[att, def_opp, is_home])."""
    att = np.concatenate(
        [feats[Feat.ATT_H].to_numpy(float), feats[Feat.ATT_A].to_numpy(float)]
    )
    def_opp = np.concatenate(
        [feats[Feat.DEF_A].to_numpy(float), feats[Feat.DEF_H].to_numpy(float)]
    )
    n = len(feats)
    is_home = np.concatenate([np.ones(n), np.zeros(n)])
    y = np.concatenate(
        [feats[Col.FTHG].to_numpy(float), feats[Col.FTAG].to_numpy(float)]
    )
    X = np.column_stack([att, def_opp, is_home])
    return y, X


def _side_design(feats: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
    """Per-side prediction features: (X_home, X_away), columns [att, def_opp, is_home]."""
    n = len(feats)
    x_home = np.column_stack(
        [
            feats[Feat.ATT_H].to_numpy(float),
            feats[Feat.DEF_A].to_numpy(float),
            np.ones(n),
        ]
    )
    x_away = np.column_stack(
        [
            feats[Feat.ATT_A].to_numpy(float),
            feats[Feat.DEF_H].to_numpy(float),
            np.zeros(n),
        ]
    )
    return x_home, x_away


class PoissonGLMModel:
    """Poisson GLM: log(lambda) = b0 + b1*att + b2*def_opp + b3*is_home.

    Attributes set by :meth:`fit`:
        params_: coefficient vector ``[b0, b1, b2, b3]``.
        fallback_lambda_: mean of training goals; equals ``exp(b0)`` of an
            intercept-only Poisson fit, used for rows with NaN features at
            predict time (league-average lambda).
    """

    params_: np.ndarray
    fallback_lambda_: float

    def fit(self, feats: pd.DataFrame) -> "PoissonGLMModel":
        """Fit on long-format observations, dropping rows with NaN features."""
        y, X = _long_arrays(feats)
        mask = np.isfinite(X).all(axis=1) & np.isfinite(y)
        if not mask.any():
            raise ValueError("PoissonGLMModel.fit: no rows with finite features")
        y, X = y[mask], X[mask]
        self.fallback_lambda_ = float(y.mean())
        design = sm.add_constant(X, has_constant="add")
        result = sm.GLM(y, design, family=sm.families.Poisson()).fit()
        self.params_ = np.asarray(result.params, dtype=float)
        return self

    def predict_lambdas(self, feats: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        """Return (lam_home, lam_away); NaN-feature sides get the fallback lambda."""
        lams = []
        for X in _side_design(feats):
            design = np.column_stack([np.ones(len(X)), X])
            ok = np.isfinite(X).all(axis=1)
            lam = np.full(len(X), self.fallback_lambda_)
            lam[ok] = np.exp(design[ok] @ self.params_)
            lams.append(lam)
        return lams[0], lams[1]


class PoissonGBMModel:
    """Gradient-boosted Poisson regression on [att, def_opp, is_home].

    Same interface and NaN handling as :class:`PoissonGLMModel`: NaN-feature
    rows are dropped at fit time and receive the league-average lambda
    (training goal mean) at predict time.
    """

    fallback_lambda_: float

    def __init__(self) -> None:
        self._model = HistGradientBoostingRegressor(
            loss="poisson",
            max_depth=3,
            learning_rate=0.05,
            max_iter=300,
            l2_regularization=1.0,
            random_state=0,
        )

    def fit(self, feats: pd.DataFrame) -> "PoissonGBMModel":
        """Fit on long-format observations, dropping rows with NaN features."""
        y, X = _long_arrays(feats)
        mask = np.isfinite(X).all(axis=1) & np.isfinite(y)
        if not mask.any():
            raise ValueError("PoissonGBMModel.fit: no rows with finite features")
        y, X = y[mask], X[mask]
        self.fallback_lambda_ = float(y.mean())
        self._model.fit(X, y)
        return self

    def predict_lambdas(self, feats: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        """Return (lam_home, lam_away); NaN-feature sides get the fallback lambda."""
        lams = []
        for X in _side_design(feats):
            ok = np.isfinite(X).all(axis=1)
            lam = np.full(len(X), self.fallback_lambda_)
            if ok.any():
                lam[ok] = self._model.predict(X[ok])
            lams.append(lam)
        return lams[0], lams[1]

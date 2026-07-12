"""Dixon-Coles (1997) machinery: low-score correction, score matrix, rho MLE,
and the classic time-decayed attack/defence rating model."""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar
from scipy.stats import poisson

from xgedge.contracts import Col

_PROB_FLOOR = 1e-10
_L2 = 1e-3
# exp() argument guard so a wild optimizer step cannot overflow to inf.
_LOG_LAM_CLIP = 30.0


def tau(x: int, y: int, lh: float, la: float, rho: float) -> float:
    """Dixon-Coles low-score dependence correction for the (x, y) cell."""
    if x == 0 and y == 0:
        return 1.0 - lh * la * rho
    if x == 1 and y == 0:
        return 1.0 + la * rho
    if x == 0 and y == 1:
        return 1.0 + lh * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def score_matrix(
    lh: float, la: float, rho: float = 0.0, max_goals: int = 10
) -> np.ndarray:
    """Tau-corrected independent-Poisson scoreline matrix, summing exactly to 1.

    Rows index home goals 0..max_goals, columns away goals.
    """
    if not np.isfinite([lh, la, rho]).all():
        raise ValueError("lh, la and rho must be finite")
    if lh < 0.0 or la < 0.0:
        raise ValueError("Poisson lambdas must be non-negative")
    if (
        isinstance(max_goals, bool)
        or not isinstance(max_goals, (int, np.integer))
        or max_goals < 1
    ):
        raise ValueError("max_goals must be an integer of at least 1")
    corrections = np.array(
        [
            tau(0, 0, lh, la, rho),
            tau(1, 0, lh, la, rho),
            tau(0, 1, lh, la, rho),
            tau(1, 1, lh, la, rho),
        ],
        dtype=float,
    )
    if np.any(corrections < 0.0):
        raise ValueError("rho produces a negative Dixon-Coles cell correction")

    goals = np.arange(max_goals + 1)
    m = np.outer(poisson.pmf(goals, lh), poisson.pmf(goals, la))
    for x in (0, 1):
        for y in (0, 1):
            m[x, y] *= tau(x, y, lh, la, rho)
    m = np.maximum(m, 0.0)
    return m / m.sum()


def fit_rho(
    lam_h: np.ndarray,
    lam_a: np.ndarray,
    goals_h: np.ndarray,
    goals_a: np.ndarray,
    weights: Optional[np.ndarray] = None,
) -> float:
    """Profile MLE of rho given per-match lambdas and observed goals."""
    lam_h = np.asarray(lam_h, dtype=float)
    lam_a = np.asarray(lam_a, dtype=float)
    gh = np.asarray(goals_h, dtype=float)
    ga = np.asarray(goals_a, dtype=float)
    arrays = (lam_h, lam_a, gh, ga)
    if len({arr.size for arr in arrays}) != 1:
        raise ValueError("lambda and goal arrays must have equal lengths")
    if lam_h.size == 0:
        return 0.0
    if any(arr.ndim != 1 for arr in arrays):
        raise ValueError("lambda and goal arrays must be one-dimensional")
    if not all(np.isfinite(arr).all() for arr in arrays):
        raise ValueError("lambda and goal arrays must be finite")
    if np.any(lam_h < 0.0) or np.any(lam_a < 0.0):
        raise ValueError("Poisson lambdas must be non-negative")
    if np.any(gh < 0.0) or np.any(ga < 0.0):
        raise ValueError("goal counts must be non-negative")

    w = np.ones_like(lam_h) if weights is None else np.asarray(weights, dtype=float)
    if w.ndim != 1 or w.size != lam_h.size:
        raise ValueError("weights must be one-dimensional and match lambdas")
    if not np.isfinite(w).all() or np.any(w < 0.0):
        raise ValueError("weights must be finite and non-negative")

    base = poisson.pmf(gh, lam_h) * poisson.pmf(ga, lam_a)
    is00 = (gh == 0) & (ga == 0)
    is10 = (gh == 1) & (ga == 0)
    is01 = (gh == 0) & (ga == 1)
    is11 = (gh == 1) & (ga == 1)
    informative = is00 | is10 | is01 | is11
    if not np.any(informative & (w > 0.0)):
        return 0.0

    def nll(rho: float) -> float:
        t = np.ones_like(base)
        t[is00] = 1.0 - lam_h[is00] * lam_a[is00] * rho
        t[is10] = 1.0 + lam_a[is10] * rho
        t[is01] = 1.0 + lam_h[is01] * rho
        t[is11] = 1.0 - rho
        if np.any(t[informative] <= 0.0):
            return float("inf")
        p = np.clip(base * t, _PROB_FLOOR, None)
        return -float(np.sum(w * np.log(p)))

    max_lh = float(np.max(lam_h))
    max_la = float(np.max(lam_a))
    max_product = float(np.max(lam_h * lam_a))
    lower = max(
        -0.2,
        -1.0 / max_lh if max_lh > 0.0 else -0.2,
        -1.0 / max_la if max_la > 0.0 else -0.2,
    )
    upper = min(
        0.2,
        1.0 / max_product if max_product > 0.0 else 0.2,
    )
    margin = 1e-9
    if lower + 2.0 * margin >= upper:
        return 0.0

    res = minimize_scalar(
        nll,
        bounds=(lower + margin, upper - margin),
        method="bounded",
    )
    if not res.success or not np.isfinite(res.x):
        return 0.0
    return float(res.x)


class DixonColesClassic:
    """Classic Dixon-Coles team-strength model with exponential time decay.

    log lam_home = mu + home_adv + att[home] - deff[away]
    log lam_away = mu + att[away] - deff[home]

    Identifiability: att and deff are shifted to mean zero after optimization,
    with the shift absorbed into mu (mu += mean(att) - mean(deff)), which
    leaves both predicted lambdas invariant. Unknown teams at predict time get
    att = deff = 0, i.e. league-average strength.

    Attributes set by :meth:`fit`: mu_, home_adv_, att_, deff_ (dicts keyed
    by canonical team id).
    """

    mu_: float
    home_adv_: float
    att_: Dict[str, float]
    deff_: Dict[str, float]

    def fit(
        self, matches: pd.DataFrame, half_life_days: float = 365.0
    ) -> "DixonColesClassic":
        """Fit by weighted MLE (scipy L-BFGS-B, analytic gradient, L2 1e-3)."""
        teams = sorted(set(matches[Col.HOME]) | set(matches[Col.AWAY]))
        index = {t: i for i, t in enumerate(teams)}
        n_teams = len(teams)

        h = matches[Col.HOME].map(index).to_numpy()
        a = matches[Col.AWAY].map(index).to_numpy()
        gh = matches[Col.FTHG].to_numpy(float)
        ga = matches[Col.FTAG].to_numpy(float)

        dates = pd.to_datetime(matches[Col.DATE])
        age_days = (dates.max() - dates).dt.total_seconds().to_numpy() / 86400.0
        w = np.exp(-np.log(2.0) * age_days / half_life_days)

        def nll_grad(theta: np.ndarray) -> Tuple[float, np.ndarray]:
            mu, home_adv = theta[0], theta[1]
            att = theta[2 : 2 + n_teams]
            deff = theta[2 + n_teams :]

            log_lh = np.clip(mu + home_adv + att[h] - deff[a], None, _LOG_LAM_CLIP)
            log_la = np.clip(mu + att[a] - deff[h], None, _LOG_LAM_CLIP)
            lam_h = np.exp(log_lh)
            lam_a = np.exp(log_la)

            nll = (
                float(np.sum(w * (lam_h - gh * log_lh)))
                + float(np.sum(w * (lam_a - ga * log_la)))
                + _L2 * (float(att @ att) + float(deff @ deff))
            )

            # d(nll)/d(log_lam) = w * (lam - goals); chain through linear terms.
            r_h = w * (lam_h - gh)
            r_a = w * (lam_a - ga)
            grad = np.zeros_like(theta)
            grad[0] = r_h.sum() + r_a.sum()
            grad[1] = r_h.sum()
            g_att = np.zeros(n_teams)
            g_def = np.zeros(n_teams)
            np.add.at(g_att, h, r_h)
            np.add.at(g_att, a, r_a)
            np.add.at(g_def, a, -r_h)
            np.add.at(g_def, h, -r_a)
            grad[2 : 2 + n_teams] = g_att + 2.0 * _L2 * att
            grad[2 + n_teams :] = g_def + 2.0 * _L2 * deff
            return nll, grad

        x0 = np.zeros(2 + 2 * n_teams)
        x0[0] = np.log(max((gh.mean() + ga.mean()) / 2.0, 1e-6))
        res = minimize(nll_grad, x0, jac=True, method="L-BFGS-B")

        mu, home_adv = res.x[0], res.x[1]
        att = res.x[2 : 2 + n_teams]
        deff = res.x[2 + n_teams :]
        mean_att, mean_def = att.mean(), deff.mean()
        att = att - mean_att
        deff = deff - mean_def
        mu = mu + mean_att - mean_def

        self.mu_ = float(mu)
        self.home_adv_ = float(home_adv)
        self.att_ = {t: float(att[i]) for t, i in index.items()}
        self.deff_ = {t: float(deff[i]) for t, i in index.items()}
        return self

    def predict_lambdas(self, matches: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        """Return (lam_home, lam_away); unknown teams get league-average strength."""
        att_h = matches[Col.HOME].map(self.att_).fillna(0.0).to_numpy(float)
        deff_h = matches[Col.HOME].map(self.deff_).fillna(0.0).to_numpy(float)
        att_a = matches[Col.AWAY].map(self.att_).fillna(0.0).to_numpy(float)
        deff_a = matches[Col.AWAY].map(self.deff_).fillna(0.0).to_numpy(float)
        lam_h = np.exp(self.mu_ + self.home_adv_ + att_h - deff_a)
        lam_a = np.exp(self.mu_ + att_a - deff_h)
        return lam_h, lam_a

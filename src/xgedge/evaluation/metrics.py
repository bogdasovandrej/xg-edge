"""Probabilistic scoring rules for 1X2 and binary market predictions."""
from __future__ import annotations

from typing import Sequence

import numpy as np

from xgedge.contracts import OUTCOMES

_CLIP_LO = 1e-12


def _onehot_1x2(y: Sequence[str]) -> np.ndarray:
    """Encode 'H'/'D'/'A' labels as one-hot rows ordered like OUTCOMES."""
    idx = np.array([OUTCOMES.index(label) for label in y])
    out = np.zeros((idx.size, len(OUTCOMES)))
    out[np.arange(idx.size), idx] = 1.0
    return out


def brier_1x2(p: np.ndarray, y: Sequence[str]) -> float:
    """Multiclass Brier score: mean over matches of the summed squared error.

    ``p`` is (n, 3) ordered [H, D, A]; ``y`` holds 'H'/'D'/'A' labels.
    Perfect predictions score 0; uniform predictions score 2/3.
    """
    p = np.asarray(p, dtype=float)
    return float(np.mean(np.sum((p - _onehot_1x2(y)) ** 2, axis=1)))


def logloss_1x2(p: np.ndarray, y: Sequence[str]) -> float:
    """Mean negative log-likelihood of the realized outcome.

    Probabilities are clipped to [1e-12, 1] so a zero assigned to the true
    outcome yields a large finite penalty instead of infinity.
    """
    p = np.clip(np.asarray(p, dtype=float), _CLIP_LO, 1.0)
    idx = np.array([OUTCOMES.index(label) for label in y])
    return float(-np.mean(np.log(p[np.arange(idx.size), idx])))


def brier_binary(p: np.ndarray, y: np.ndarray) -> float:
    """Binary Brier score: mean squared error of probabilities vs outcomes."""
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    return float(np.mean((p - y) ** 2))


def logloss_binary(p: np.ndarray, y: np.ndarray) -> float:
    """Binary log loss; both p and 1-p are clipped at 1e-12 to stay finite."""
    p = np.clip(np.asarray(p, dtype=float), _CLIP_LO, 1.0 - _CLIP_LO)
    y = np.asarray(y, dtype=float)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))

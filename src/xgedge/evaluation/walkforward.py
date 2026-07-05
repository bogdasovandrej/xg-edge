"""Leak-free walk-forward splitting on match dates.

Splits are expanding-window: each test window is a ``step_days``-wide date
interval, and the training set is every observation strictly before the
window start, so ``max(train date) < min(test date)`` always holds.
"""
from __future__ import annotations

from typing import Iterator, Union

import numpy as np
import pandas as pd


def walk_forward_splits(
    dates: pd.Series,
    initial_train_end: Union[str, pd.Timestamp],
    step_days: int = 30,
    min_train: int = 200,
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Yield (train_idx, test_idx) positional-index pairs for walk-forward CV.

    The first test window starts at ``initial_train_end``; subsequent windows
    advance by ``step_days``. Train rows are those dated strictly before the
    window start; test rows fall in ``[window_start, window_start+step_days)``.
    Windows with no test rows, or with fewer than ``min_train`` training rows,
    are skipped. Indices are positions into ``dates`` regardless of its index
    labels or sort order.
    """
    values = pd.to_datetime(pd.Series(dates)).to_numpy()
    if values.size == 0:
        return
    window_start = pd.Timestamp(initial_train_end)
    max_date = pd.Timestamp(values.max())
    step = pd.Timedelta(days=step_days)

    while window_start <= max_date:
        window_end = window_start + step
        train_idx = np.flatnonzero(values < window_start.to_datetime64())
        test_idx = np.flatnonzero(
            (values >= window_start.to_datetime64())
            & (values < window_end.to_datetime64())
        )
        if test_idx.size > 0 and train_idx.size >= min_train:
            yield train_idx, test_idx
        window_start = window_end

"""Tests for xgedge.features.builder — leak-free feature construction."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from xgedge.contracts import Col, Feat
from xgedge.features.builder import build_features


def make_matches(rows: list) -> pd.DataFrame:
    """Build a cleaned-schema matches DataFrame from compact row specs."""
    recs = []
    for i, r in enumerate(rows):
        rec = {
            Col.MATCH_ID: f"m{i}",
            Col.SEASON: "2024-25",
            Col.DATE: pd.Timestamp(r["date"]),
            Col.HOME: r["home"],
            Col.AWAY: r["away"],
            Col.FTHG: r.get("fthg", 1),
            Col.FTAG: r.get("ftag", 1),
            Col.FTR: r.get("ftr", "D"),
            Col.NPXG_H: float(r["xh"]),
            Col.NPXG_A: float(r["xa"]),
            Col.RED_H: r.get("red_h", 0),
            Col.RED_A: r.get("red_a", 0),
        }
        rec.update(r.get("extra", {}))
        recs.append(rec)
    return pd.DataFrame(recs)


def test_no_leakage_prefix_invariance():
    # Features of the first k matches must be identical whether or not
    # later matches are present in the input.
    teams = ["arsenal", "chelsea", "everton", "fulham"]
    rng = np.random.default_rng(0)
    rows = []
    for i, d in enumerate(pd.date_range("2024-08-01", periods=16, freq="7D")):
        rows.append({
            "date": d,
            "home": teams[i % 4],
            "away": teams[(i + 1) % 4],
            "xh": round(float(rng.uniform(0.3, 2.5)), 2),
            "xa": round(float(rng.uniform(0.3, 2.5)), 2),
        })
    full = make_matches(rows)
    k = 9
    out_full = build_features(full)
    out_prefix = build_features(full.iloc[:k].copy())
    pdt.assert_frame_equal(out_full.iloc[:k].reset_index(drop=True), out_prefix)


def test_cold_start_nan_and_is_valid():
    rows = [
        {"date": "2024-08-01", "home": "arsenal", "away": "chelsea", "xh": 1.0, "xa": 0.5},
        {"date": "2024-08-08", "home": "chelsea", "away": "arsenal", "xh": 0.7, "xa": 1.4},
        {"date": "2024-08-15", "home": "arsenal", "away": "chelsea", "xh": 2.0, "xa": 1.0},
        {"date": "2024-08-22", "home": "chelsea", "away": "arsenal", "xh": 1.1, "xa": 0.9},
    ]
    out = build_features(make_matches(rows), min_history=2)

    first = out.iloc[0]
    assert math.isnan(first[Feat.ATT_H]) and math.isnan(first[Feat.DEF_H])
    assert math.isnan(first[Feat.ATT_A]) and math.isnan(first[Feat.DEF_A])
    assert first[Feat.N_HIST_H] == 0 and first[Feat.N_HIST_A] == 0
    assert not first[Feat.IS_VALID]

    second = out.iloc[1]
    assert np.isfinite(second[Feat.ATT_H]) and np.isfinite(second[Feat.ATT_A])
    assert second[Feat.N_HIST_H] == 1 and second[Feat.N_HIST_A] == 1
    assert not second[Feat.IS_VALID]  # 1 < min_history

    third = out.iloc[2]
    assert third[Feat.N_HIST_H] == 2 and third[Feat.N_HIST_A] == 2
    assert third[Feat.IS_VALID]


def test_decay_direction():
    # Two past matches: recent high npxG, old low npxG. A short half-life
    # must pull the rating towards the recent value; a huge one towards
    # the plain mean.
    rows = [
        {"date": "2024-01-01", "home": "arsenal", "away": "chelsea", "xh": 0.5, "xa": 1.0},
        {"date": "2024-10-01", "home": "arsenal", "away": "everton", "xh": 3.0, "xa": 1.0},
        {"date": "2024-10-11", "home": "arsenal", "away": "fulham", "xh": 1.0, "xa": 1.0},
    ]
    df = make_matches(rows)
    kw = dict(adjust_opponent=False, venue_blend=0.0, min_history=1)
    fast = build_features(df, half_life_days=10.0, **kw).iloc[2][Feat.ATT_H]
    slow = build_features(df, half_life_days=1e9, **kw).iloc[2][Feat.ATT_H]
    none = build_features(df, decay=False, **kw).iloc[2][Feat.ATT_H]
    assert fast > slow
    assert fast == pytest.approx(3.0, abs=0.01)     # old match nearly forgotten
    assert slow == pytest.approx(1.75, abs=1e-6)    # ~plain mean
    assert none == pytest.approx(1.75, abs=1e-12)   # exactly plain mean


def test_red_card_downweight():
    rows = [
        {"date": "2024-01-01", "home": "arsenal", "away": "chelsea",
         "xh": 3.0, "xa": 1.0, "red_h": 1},
        {"date": "2024-01-08", "home": "arsenal", "away": "everton", "xh": 1.0, "xa": 1.0},
        {"date": "2024-01-15", "home": "arsenal", "away": "fulham", "xh": 1.0, "xa": 1.0},
    ]
    df = make_matches(rows)
    kw = dict(adjust_opponent=False, venue_blend=0.0, decay=False, min_history=1)
    down = build_features(df, red_card_weight=0.5, **kw).iloc[2][Feat.ATT_H]
    flat = build_features(df, red_card_weight=1.0, **kw).iloc[2][Feat.ATT_H]
    assert flat == pytest.approx(2.0)
    assert down == pytest.approx((0.5 * 3.0 + 1.0 * 1.0) / 1.5)
    assert down < flat


def test_adjust_opponent_false_is_plain_decayed_mean():
    rows = [
        {"date": "2024-01-01", "home": "arsenal", "away": "chelsea", "xh": 1.2, "xa": 0.9},
        {"date": "2024-02-01", "home": "everton", "away": "arsenal", "xh": 1.1, "xa": 0.8},
        {"date": "2024-03-01", "home": "arsenal", "away": "fulham", "xh": 2.0, "xa": 1.5},
        {"date": "2024-04-01", "home": "arsenal", "away": "everton", "xh": 1.0, "xa": 1.0},
    ]
    hl = 180.0
    out = build_features(make_matches(rows), half_life_days=hl,
                         adjust_opponent=False, venue_blend=0.0, min_history=1)
    target = pd.Timestamp("2024-04-01")
    # Arsenal's past (date, own npxG, conceded npxG), venue-agnostic.
    past = [
        (pd.Timestamp("2024-01-01"), 1.2, 0.9),
        (pd.Timestamp("2024-02-01"), 0.8, 1.1),
        (pd.Timestamp("2024-03-01"), 2.0, 1.5),
    ]
    w = [math.exp(-math.log(2.0) * (target - d).days / hl) for d, _, _ in past]
    exp_att = sum(wi * a for wi, (_, a, _) in zip(w, past)) / sum(w)
    exp_def = sum(wi * c for wi, (_, _, c) in zip(w, past)) / sum(w)
    row = out.iloc[3]
    assert row[Col.HOME] == "arsenal"
    assert row[Feat.ATT_H] == pytest.approx(exp_att, rel=1e-12)
    assert row[Feat.DEF_H] == pytest.approx(exp_def, rel=1e-12)


def test_passthrough_shape_and_order():
    # Input deliberately not date-sorted; odds must survive per match.
    rows = [
        {"date": "2024-08-15", "home": "everton", "away": "fulham", "xh": 1.0, "xa": 1.0,
         "extra": {Col.B365H: 2.5, Col.B365D: 3.3, Col.B365A: 2.9,
                   Col.PSCH: 2.45, Col.B365_O25: 1.9}},
        {"date": "2024-08-01", "home": "arsenal", "away": "chelsea", "xh": 1.5, "xa": 0.7,
         "extra": {Col.B365H: 1.8, Col.B365D: 3.6, Col.B365A: 4.4,
                   Col.PSCH: 1.75, Col.B365_O25: np.nan}},
        {"date": "2024-08-08", "home": "chelsea", "away": "everton", "xh": 0.9, "xa": 1.1,
         "extra": {Col.B365H: 2.1, Col.B365D: 3.2, Col.B365A: 3.5,
                   Col.PSCH: 2.05, Col.B365_O25: 2.05}},
    ]
    df = make_matches(rows)
    out = build_features(df)

    assert len(out) == len(df)
    assert out[Col.DATE].is_monotonic_increasing
    assert list(out[Col.MATCH_ID]) == ["m1", "m2", "m0"]  # chronological

    got = out.set_index(Col.MATCH_ID).sort_index()
    src = df.set_index(Col.MATCH_ID).sort_index()
    for c in (Col.B365H, Col.B365D, Col.B365A, Col.PSCH, Col.B365_O25):
        pdt.assert_series_equal(got[c], src[c], check_names=False)

    for c in (Feat.ATT_H, Feat.DEF_H, Feat.ATT_A, Feat.DEF_A,
              Feat.N_HIST_H, Feat.N_HIST_A, Feat.IS_VALID):
        assert c in out.columns


def test_nan_xg_match_does_not_poison_history():
    # A match with missing npxG must behave as if absent from history:
    # it must not NaN the involved teams ratings nor the league average.
    teams = ["arsenal", "chelsea", "everton", "fulham"]
    rng = np.random.default_rng(3)
    rows = []
    for i, d in enumerate(pd.date_range("2024-08-01", periods=20, freq="7D")):
        rows.append({
            "date": d,
            "home": teams[i % 4],
            "away": teams[(i + 1) % 4],
            "xh": round(float(rng.uniform(0.3, 2.5)), 2),
            "xa": round(float(rng.uniform(0.3, 2.5)), 2),
        })
    j = 8
    with_nan = [dict(r) for r in rows]
    with_nan[j]["xh"] = float("nan")
    without = [r for i, r in enumerate(rows) if i != j]

    out_a = build_features(make_matches(with_nan))
    out_b = build_features(make_matches(without))

    # Later ratings of every team stay finite (no NaN poisoning).
    tail = out_a.iloc[j + 1:]
    for col in (Feat.ATT_H, Feat.DEF_H, Feat.ATT_A, Feat.DEF_A):
        assert tail[col].notna().all(), col

    # And every other match gets identical features to a dataset where the
    # NaN match never existed (match ids differ by construction).
    cmp_cols = [Col.DATE, Col.HOME, Col.AWAY,
                Feat.ATT_H, Feat.DEF_H, Feat.ATT_A, Feat.DEF_A,
                Feat.N_HIST_H, Feat.N_HIST_A, Feat.IS_VALID]
    a = out_a[out_a[Col.MATCH_ID] != f"m{j}"][cmp_cols].reset_index(drop=True)
    b = out_b[cmp_cols].reset_index(drop=True)
    pdt.assert_frame_equal(a, b)

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.stats import poisson

from scripts.audit_score_and_high_totals import (
    POSTMATCH_COLUMNS,
    PREMATCH_MARKERS,
    _benjamini_hochberg,
    _calibration_summary,
    _poisson_lambda_from_over25,
    _proportional_devig,
    _validate_and_select,
)
from xgedge.contracts import Col


def _row(match_id: str, date: str, goals: tuple[int, int]) -> dict:
    home_goals, away_goals = goals
    result = "H" if home_goals > away_goals else "A" if away_goals > home_goals else "D"
    return {
        Col.MATCH_ID: match_id,
        Col.SEASON: "2025-26",
        Col.DATE: date,
        Col.HOME: f"home_{match_id}",
        Col.AWAY: f"away_{match_id}",
        Col.FTHG: home_goals,
        Col.FTAG: away_goals,
        Col.FTR: result,
        Col.XG_H: 1.2,
        Col.XG_A: 0.8,
        Col.RED_H: 0,
        Col.RED_A: 0,
        Col.B365CH: 2.0,
        Col.B365CD: 3.5,
        Col.B365CA: 4.0,
        Col.B365C_O25: 1.8,
        Col.B365C_U25: 2.1,
    }


def test_sample_rule_is_stable_last_n() -> None:
    matches = pd.DataFrame(
        [
            _row("b", "2026-01-02", (1, 0)),
            _row("c", "2026-01-02", (0, 0)),
            _row("a", "2026-01-01", (2, 1)),
        ]
    )

    sample, quality = _validate_and_select(matches, season="2025-26", sample_size=2)

    assert sample[Col.MATCH_ID].tolist() == ["b", "c"]
    assert quality["rows_in_audit_sample"] == 2
    assert quality["result_goal_mismatches"] == 0


def test_sample_validation_rejects_result_goal_mismatch() -> None:
    bad = _row("bad", "2026-01-01", (2, 0))
    bad[Col.FTR] = "A"

    with pytest.raises(ValueError, match="completeness/domain"):
        _validate_and_select(pd.DataFrame([bad]), season="2025-26", sample_size=1)


def test_market_poisson_transform_is_a_derived_monotone_tail() -> None:
    fair_over25 = float(_proportional_devig([1.8, 2.1])[0])
    implied_lambda = _poisson_lambda_from_over25(fair_over25)

    assert 1.0 - poisson.cdf(2, implied_lambda) == pytest.approx(fair_over25)
    assert 1.0 - poisson.cdf(4, implied_lambda) < 1.0 - poisson.cdf(3, implied_lambda)
    assert 1.0 - poisson.cdf(3, implied_lambda) < fair_over25


def test_benjamini_hochberg_preserves_order_and_monotonicity() -> None:
    adjusted = _benjamini_hochberg([0.01, 0.04, 0.03])

    np.testing.assert_allclose(adjusted, [0.03, 0.04, 0.04])


def test_marker_registry_excludes_postmatch_fields() -> None:
    assert POSTMATCH_COLUMNS.isdisjoint(PREMATCH_MARKERS)
    assert all("source" in metadata for metadata in PREMATCH_MARKERS.values())
    assert all("availability" in metadata for metadata in PREMATCH_MARKERS.values())


def test_calibration_summary_is_deterministic() -> None:
    probabilities = np.array([0.1, 0.2, 0.6, 0.7, 0.8])
    outcomes = np.array([0, 0, 1, 0, 1])

    first = _calibration_summary(probabilities, outcomes, seed=7, n_boot=100)
    second = _calibration_summary(probabilities, outcomes, seed=7, n_boot=100)

    assert first == second
    assert first["events"] == 2
    assert first["observed_rate"] == pytest.approx(0.4)
    assert first["mean_probability"] == pytest.approx(0.48)


def test_checked_in_audit_contract_and_known_base_rates() -> None:
    path = Path(__file__).resolve().parents[1] / "reports" / "high_totals_audit.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["status"] == "RESEARCH_ONLY_NO_BET"
    assert payload["protocol"]["sample_size"] == 100
    assert payload["high_totals"]["O3.5"]["events"] == 27
    assert payload["high_totals"]["O4.5"]["events"] == 8
    assert (
        payload["market_availability"]["direct_o35_or_o45_odds_columns_present"]
        is False
    )
    assert payload["prematch_marker_screen"]["n_surviving_fdr_0_05"] == 0
    assert payload["conclusion"]["betting_action"] == "NO_BET_FOR_O3.5_OR_O4.5"

"""Future-fixture contract, causal fitting and CSV CLI tests."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest

from scripts.predict_fixtures import main as cli_main
from xgedge.contracts import FIXTURE_COLUMNS, Col, Pred
from xgedge.prediction.fixtures import predict_fixtures, validate_fixtures


def _history(n: int = 90, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    teams = ["arsenal", "chelsea", "liverpool", "man_city", "everton", "fulham"]
    strength = dict(zip(teams, np.linspace(-0.3, 0.3, len(teams))))
    rows = []
    for i in range(n):
        home = teams[i % len(teams)]
        away = teams[(i * 2 + 1) % len(teams)]
        if home == away:
            away = teams[(teams.index(away) + 1) % len(teams)]
        lam_h = 1.45 * np.exp(strength[home] - strength[away])
        lam_a = 1.10 * np.exp(strength[away] - strength[home])
        gh, ga = int(rng.poisson(lam_h)), int(rng.poisson(lam_a))
        xg_h = max(0.05, float(lam_h + rng.normal(0, 0.2)))
        xg_a = max(0.05, float(lam_a + rng.normal(0, 0.2)))
        rows.append({
            Col.MATCH_ID: f"history_{i}",
            Col.SEASON: "2024-25",
            Col.DATE: pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
            Col.HOME: home,
            Col.AWAY: away,
            Col.FTHG: gh,
            Col.FTAG: ga,
            Col.FTR: "H" if gh > ga else ("A" if ga > gh else "D"),
            Col.XG_H: xg_h,
            Col.XG_A: xg_a,
            Col.NPXG_H: xg_h,
            Col.NPXG_A: xg_a,
            Col.RED_H: 0,
            Col.RED_A: 0,
        })
    return pd.DataFrame(rows)


def _fixtures() -> pd.DataFrame:
    return pd.DataFrame([{
        Col.MATCH_ID: "future_1",
        Col.SEASON: "2025-26",
        Col.DATE: "2024-04-15T20:00:00+05:00",
        Col.HOME: "arsenal",
        Col.AWAY: "chelsea",
    }])


def test_fixture_contract_normalizes_utc_and_rejects_results() -> None:
    validated = validate_fixtures(_fixtures())
    assert list(validated.columns) == list(FIXTURE_COLUMNS)
    assert validated.loc[0, Col.DATE] == pd.Timestamp("2024-04-15 15:00:00")
    assert validated[Col.DATE].dt.tz is None

    with_result = _fixtures().assign(**{Col.FTHG: [2]})
    with pytest.raises(ValueError, match="must not contain results"):
        validate_fixtures(with_result)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda frame: frame.drop(columns=Col.AWAY), "missing required"),
        (
            lambda frame: pd.concat([frame, frame], ignore_index=True),
            "match_id must be unique",
        ),
        (lambda frame: frame.assign(home="chelsea"), "must be different"),
        (lambda frame: frame.assign(date="not-a-date"), "invalid dates"),
    ],
)
def test_fixture_contract_rejects_bad_format(mutate, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_fixtures(mutate(_fixtures()))


def test_unknown_team_is_rejected_at_its_cutoff() -> None:
    fixture = _fixtures().assign(away="real_madrid")
    with pytest.raises(ValueError, match="teams unseen.*real_madrid"):
        predict_fixtures(
            _history(), fixture, feature_params={"min_history": 2}
        )


def test_future_and_same_day_results_cannot_change_prediction() -> None:
    history = _history()
    fixture = _fixtures()
    base = predict_fixtures(
        history, fixture, feature_params={"min_history": 2}
    )

    leaked = _history(2, seed=9)
    leaked[Col.MATCH_ID] = ["same_day_result", "later_result"]
    leaked[Col.DATE] = [
        pd.Timestamp("2024-04-15 01:00:00"),
        pd.Timestamp("2024-04-16 01:00:00"),
    ]
    leaked[[Col.FTHG, Col.XG_H, Col.NPXG_H]] = [20, 20.0, 20.0]
    leaked[[Col.FTAG, Col.XG_A, Col.NPXG_A]] = [0, 0.01, 0.01]
    leaked[Col.FTR] = "H"
    augmented = predict_fixtures(
        pd.concat([history, leaked], ignore_index=True),
        fixture,
        feature_params={"min_history": 2},
    )

    pdt.assert_frame_equal(base, augmented, check_exact=True)
    assert base.loc[0, Pred.TRAIN_END] < pd.Timestamp("2024-04-15")


def test_prediction_output_probabilities_and_exact_scores() -> None:
    fixture = pd.concat(
        [
            _fixtures().assign(match_id="future_2", home="liverpool", away="man_city"),
            _fixtures(),
        ],
        ignore_index=True,
    )
    result = predict_fixtures(
        _history(), fixture, feature_params={"min_history": 2}, top_k=3
    )

    assert result[Col.MATCH_ID].tolist() == ["future_2", "future_1"]
    np.testing.assert_allclose(
        result[[Pred.P_HOME, Pred.P_DRAW, Pred.P_AWAY]].sum(axis=1), 1.0
    )
    np.testing.assert_allclose(result[Pred.P_OVER25] + result[Pred.P_UNDER25], 1.0)
    np.testing.assert_allclose(result[Pred.P_BTTS] + result[Pred.P_NO_BTTS], 1.0)
    for encoded in result[Pred.EXACT_SCORES]:
        scores = json.loads(encoded)
        assert len(scores) == 3
        assert all(set(item) == {"score", "probability"} for item in scores)
    assert result[Pred.P_TOP_SCORE].between(0.0, 1.0).all()


@pytest.mark.parametrize(
    "model", ["glm_dc", "gbm_dc", "dc_classic", "goals_poisson"]
)
def test_all_supported_goal_models_share_the_fixture_interface(model: str) -> None:
    result = predict_fixtures(
        _history(),
        _fixtures(),
        model=model,
        feature_params={"min_history": 2},
        force_rho_zero=True,
    )
    assert result.loc[0, Pred.MODEL] == model
    total = result.loc[0, [Pred.P_HOME, Pred.P_DRAW, Pred.P_AWAY]].sum()
    assert total == pytest.approx(1.0)


def test_csv_cli_writes_predictions(tmp_path, capsys) -> None:
    history_path = tmp_path / "history.csv"
    fixtures_path = tmp_path / "fixtures.csv"
    output_path = tmp_path / "nested" / "predictions.csv"
    _history().to_csv(history_path, index=False)
    _fixtures().to_csv(fixtures_path, index=False)

    cli_main([
        "--history", str(history_path),
        "--fixtures", str(fixtures_path),
        "--output", str(output_path),
        "--min-history", "2",
        "--rho-zero",
    ])

    written = pd.read_csv(output_path)
    assert len(written) == 1
    assert written.loc[0, Col.MATCH_ID] == "future_1"
    assert "wrote 1 fixture predictions" in capsys.readouterr().out

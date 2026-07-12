"""Audit raw versus market-anchored 1X2 probabilities without holdout tuning.

The early development period fits the centered intercept.  The later
development period selects shrinkage, longshot and candidate controls.  Only
then is the 2025/26 holdout evaluated once.  The output path is mandatory so
an audit cannot silently overwrite the official reports.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from xgedge.contracts import CLEANED_MATCHES, Col
from xgedge.decision.market_anchor import (
    MarketAnchor,
    candidate_bets_1x2,
    clv_betting_gate,
    devig_opening_odds,
    probability_metrics,
    select_anchor_on_late_development,
)
from xgedge.pipeline import PRIMARY_MODEL, run_walkforward_eval

RAW_PROB_COLS = ["glm_dc_ph", "glm_dc_pd", "glm_dc_pa"]
OPENING_ODDS_COLS = [Col.PSH, Col.PSD, Col.PSA]
TAKEN_ODDS_COLS = [Col.B365H, Col.B365D, Col.B365A]
CLOSING_ODDS_COLS = [Col.PSCH, Col.PSCD, Col.PSCA]


def _arrays(frame: pd.DataFrame) -> tuple[np.ndarray, ...]:
    return (
        frame[RAW_PROB_COLS].to_numpy(dtype=float),
        frame[OPENING_ODDS_COLS].to_numpy(dtype=float),
        frame[TAKEN_ODDS_COLS].to_numpy(dtype=float),
        frame[CLOSING_ODDS_COLS].to_numpy(dtype=float),
        frame[Col.FTR].to_numpy(),
        frame[Col.MATCH_ID].to_numpy(),
    )


def _evaluate(
    frame: pd.DataFrame,
    model: MarketAnchor,
    *,
    min_gate_matches: int,
    n_boot: int,
    seed: int,
) -> dict:
    raw, opening, taken, closing, outcomes, match_ids = _arrays(frame)
    anchored = model.predict_proba(raw, opening)
    market = devig_opening_odds(opening)

    def variant(name: str, probabilities: np.ndarray) -> dict:
        candidates = candidate_bets_1x2(
            probabilities,
            taken,
            closing,
            match_ids,
            edge_threshold=model.config.edge_threshold,
            max_odds=model.config.max_odds,
        )
        return {
            "name": name,
            "probability_metrics": probability_metrics(probabilities, outcomes),
            "shadow_candidate_count": int(len(candidates)),
            "shadow_candidate_clv": clv_betting_gate(
                candidates["clv"],
                candidates["match_id"],
                min_independent_matches=min_gate_matches,
                n_boot=n_boot,
                seed=seed,
            ),
        }

    return {
        "n_matches": int(len(frame)),
        "raw_model": variant("raw_glm_dc", raw),
        "opening_market": {
            "name": "de_vigged_pinnacle_opening",
            "probability_metrics": probability_metrics(market, outcomes),
        },
        "anchored_model": variant("market_anchored_glm_dc", anchored),
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=CLEANED_MATCHES)
    parser.add_argument("--early-start", default="2022-07-01")
    parser.add_argument("--selection-start", default="2024-07-01")
    parser.add_argument("--holdout-start", default="2025-07-01")
    parser.add_argument("--step-days", type=int, default=30)
    parser.add_argument("--min-selection-matches", type=int, default=50)
    parser.add_argument("--min-gate-matches", type=int, default=100)
    parser.add_argument("--selection-bootstrap", type=int, default=2_000)
    parser.add_argument("--report-bootstrap", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260713)
    args = parser.parse_args(argv)

    early_start = pd.Timestamp(args.early_start)
    selection_start = pd.Timestamp(args.selection_start)
    holdout_start = pd.Timestamp(args.holdout_start)
    if not early_start < selection_start < holdout_start:
        parser.error("dates must satisfy early-start < selection-start < holdout-start")

    matches = pd.read_parquet(args.data).copy()
    matches[Col.DATE] = pd.to_datetime(matches[Col.DATE])
    result = run_walkforward_eval(
        matches,
        initial_train_end=str(early_start.date()),
        step_days=args.step_days,
        models=[PRIMARY_MODEL],
    )
    odds = matches[
        [Col.MATCH_ID]
        + OPENING_ODDS_COLS
        + TAKEN_ODDS_COLS
        + CLOSING_ODDS_COLS
    ]
    frame = result["predictions"].merge(
        odds, on=Col.MATCH_ID, how="inner", validate="one_to_one"
    )
    required = RAW_PROB_COLS + OPENING_ODDS_COLS + TAKEN_ODDS_COLS + CLOSING_ODDS_COLS
    frame = frame.dropna(subset=required).sort_values(
        [Col.DATE, Col.MATCH_ID], kind="stable"
    )
    frame = frame[
        np.isfinite(frame[required].to_numpy(dtype=float)).all(axis=1)
    ].reset_index(drop=True)

    early = frame[
        (frame[Col.DATE] >= early_start) & (frame[Col.DATE] < selection_start)
    ].copy()
    late = frame[
        (frame[Col.DATE] >= selection_start) & (frame[Col.DATE] < holdout_start)
    ].copy()
    holdout = frame[frame[Col.DATE] >= holdout_start].copy()
    if min(len(early), len(late), len(holdout)) == 0:
        raise ValueError("early, selection and holdout periods must all be non-empty")

    eraw, eopen, _, _, ey, _ = _arrays(early)
    lraw, lopen, ltaken, lclose, ly, lids = _arrays(late)
    selected, grid = select_anchor_on_late_development(
        early_raw_probs=eraw,
        early_opening_odds=eopen,
        early_outcomes=ey,
        late_raw_probs=lraw,
        late_opening_odds=lopen,
        late_taken_odds=ltaken,
        late_closing_odds=lclose,
        late_outcomes=ly,
        late_match_ids=lids,
        min_selection_matches=args.min_selection_matches,
        n_boot=args.selection_bootstrap,
        seed=args.seed,
    )

    development = _evaluate(
        late,
        selected,
        min_gate_matches=args.min_gate_matches,
        n_boot=args.report_bootstrap,
        seed=args.seed,
    )
    deployment_gate = development["anchored_model"]["shadow_candidate_clv"]

    # This is the only holdout evaluation, after all parameters and the gate
    # have been frozen from observations strictly before holdout_start.
    holdout_report = _evaluate(
        holdout,
        selected,
        min_gate_matches=args.min_gate_matches,
        n_boot=args.report_bootstrap,
        seed=args.seed,
    )
    holdout_clv = holdout_report["anchored_model"]["shadow_candidate_clv"]["clv"]
    confirmed_holdout_clv = (
        holdout_clv["n_clusters"] >= args.min_gate_matches
        and np.isfinite(holdout_clv["ci_low"])
        and holdout_clv["ci_low"] > 0.0
    )
    conclusion = (
        "POSITIVE_CLV_CONFIRMED_ON_HOLDOUT"
        if confirmed_holdout_clv
        else "POSITIVE_CLV_NOT_CONFIRMED; KEEP_NO_BET"
    )

    payload = {
        "protocol": {
            "early_development_fit": [str(early_start.date()), str(selection_start.date())],
            "late_development_selection": [
                str(selection_start.date()),
                str(holdout_start.date()),
            ],
            "holdout_report_only": [str(holdout_start.date()), None],
            "holdout_parameter_access": False,
            "holdout_evaluations": 1,
            "opening_prior": "Pinnacle 1X2 opening odds, proportional de-vig",
            "closing_reference": "Pinnacle 1X2 closing odds, proportional de-vig",
            "candidate_price": "Bet365 opening odds",
            "bootstrap_unit": "match_id cluster",
            "seed": args.seed,
        },
        "sample_sizes": {
            "early_development": int(len(early)),
            "late_development": int(len(late)),
            "holdout": int(len(holdout)),
        },
        "selected_anchor": selected.to_dict(),
        "development_grid": grid,
        "late_development_report": development,
        "deployment_gate_from_development_only": deployment_gate,
        "holdout_shadow_report": holdout_report,
        "conclusion": conclusion,
    }
    safe_payload = _json_safe(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(safe_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "selected_anchor": safe_payload["selected_anchor"],
        "deployment_action": deployment_gate["action"],
        "holdout_conclusion": conclusion,
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

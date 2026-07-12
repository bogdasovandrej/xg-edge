"""Predict result-free fixtures from a CSV using strictly earlier history."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from xgedge.contracts import CLEANED_MATCHES, REPORTS_DIR
from xgedge.prediction.fixtures import SUPPORTED_MODELS, predict_fixtures


def _read_history(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    raise ValueError("history must be a .parquet or .csv file")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures",
        type=Path,
        required=True,
        help="CSV columns: match_id,season,date,home,away",
    )
    parser.add_argument("--history", type=Path, default=CLEANED_MATCHES)
    parser.add_argument(
        "--output", type=Path, default=REPORTS_DIR / "future_predictions.csv"
    )
    parser.add_argument("--model", choices=SUPPORTED_MODELS, default="glm_dc")
    parser.add_argument("--half-life", type=float, default=180.0)
    parser.add_argument("--min-history", type=int, default=5)
    parser.add_argument("--max-goals", type=int, default=10)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--rho-zero", action="store_true")
    args = parser.parse_args(argv)

    history = _read_history(args.history)
    fixtures = pd.read_csv(args.fixtures)
    predictions = predict_fixtures(
        history,
        fixtures,
        model=args.model,
        feature_params={
            "half_life_days": args.half_life,
            "min_history": args.min_history,
        },
        max_goals=args.max_goals,
        top_k=args.top_k,
        force_rho_zero=args.rho_zero,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(
        args.output, index=False, date_format="%Y-%m-%dT%H:%M:%SZ"
    )
    print(f"wrote {len(predictions)} fixture predictions to {args.output}")


if __name__ == "__main__":
    main()

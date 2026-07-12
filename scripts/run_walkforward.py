"""Run the walk-forward evaluation on the cleaned dataset and write reports."""
from __future__ import annotations

import argparse

import pandas as pd

from xgedge.contracts import CLEANED_MATCHES, REPORTS_DIR, Col
from xgedge.evaluation.calibration import plot_reliability, reliability_table
from xgedge.evaluation.report import write_metrics_json, write_summary_md
from xgedge.pipeline import PRIMARY_MODEL, run_walkforward_eval


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--half-life", type=float, default=180.0)
    parser.add_argument("--edge-threshold", type=float, default=0.03)
    parser.add_argument("--initial-train-end", default="2025-07-01")
    parser.add_argument("--step-days", type=int, default=30)
    args = parser.parse_args(argv)

    matches = pd.read_parquet(CLEANED_MATCHES)
    results = run_walkforward_eval(
        matches,
        feature_params={"half_life_days": args.half_life},
        initial_train_end=args.initial_train_end,
        step_days=args.step_days,
        edge_threshold=args.edge_threshold,
    )

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    pred: pd.DataFrame = results.pop("predictions")
    bets: pd.DataFrame = results.pop("bets")
    pred.to_csv(REPORTS_DIR / "predictions.csv", index=False)
    bets.to_csv(REPORTS_DIR / "bets.csv", index=False)
    write_metrics_json(results, REPORTS_DIR / "metrics.json")
    write_summary_md(results, REPORTS_DIR / "summary.md")

    y = pred[Col.FTR]
    tables = {
        "home win": reliability_table(
            pred[f"{PRIMARY_MODEL}_ph"].to_numpy(), (y == "H").to_numpy()
        ),
        "draw": reliability_table(
            pred[f"{PRIMARY_MODEL}_pd"].to_numpy(), (y == "D").to_numpy()
        ),
        "away win": reliability_table(
            pred[f"{PRIMARY_MODEL}_pa"].to_numpy(), (y == "A").to_numpy()
        ),
    }
    plot_reliability(tables, REPORTS_DIR / "reliability_1x2.png",
                     title=f"{PRIMARY_MODEL} 1X2 reliability")
    y_over = ((pred[Col.FTHG] + pred[Col.FTAG]) > 2.5).to_numpy()
    plot_reliability(
        {"over 2.5": reliability_table(
            pred[f"{PRIMARY_MODEL}_pover25"].to_numpy(), y_over)},
        REPORTS_DIR / "reliability_over25.png",
        title=f"{PRIMARY_MODEL} over 2.5 reliability",
    )

    print(f"\n{'model':<14}{'brier':>8}{'logloss':>9}{'n':>6}"
          f"{'brier*':>9}{'logloss*':>10}")
    for name, m in results["models_1x2"].items():
        print(f"{name:<14}{m['brier']:>8.4f}{m['logloss']:>9.4f}{m['n']:>6}"
              f"{m['brier_common']:>9.4f}{m['logloss_common']:>10.4f}")
    print("(* on the common subset where every model has predictions)")

    if "bankroll" in results:
        k, f = results["bankroll"]["kelly"], results["bankroll"]["flat"]
        print(f"\nbets: {len(bets)} | kelly ROI {k['roi']:+.3f} "
              f"(bankroll {k['final_bankroll']:.3f}, maxDD {k['max_drawdown']:.3f})"
              f" | flat ROI {f['roi']:+.3f}")
    if "clv" in results:
        c = results["clv"]
        print(f"CLV: mean {c['mean']:+.4f} [{c['ci_low']:+.4f}, {c['ci_high']:+.4f}]"
              f" | share>0 {c['share_positive']:.3f} | n={c['n']}")
    print(f"\nreports written to {REPORTS_DIR}")


if __name__ == "__main__":
    main()

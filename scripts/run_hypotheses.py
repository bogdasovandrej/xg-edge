"""Hypothesis ablations: each registered metric earns its place, or it goes.

Runs the primary model (glm_dc) through the same walk-forward protocol with
one mechanism added, removed or changed at a time. Verdicts describe only the
variant relative to BASE; the hypothesis registry interprets the direction.
"""
from __future__ import annotations

import argparse

import pandas as pd

from xgedge.contracts import CLEANED_MATCHES, REPORTS_DIR, Col
from xgedge.pipeline import PRIMARY_MODEL, run_walkforward_eval

# variant -> (feature_params overrides, hypothesis, what the variant tests)
VARIANTS = {
    "BASE": ({}, "-", "reference configuration"),
    "H2_with_opp_adjust": ({"adjust_opponent": True}, "H2",
                           "adding opponent-strength normalization of xG"),
    "H7_npxg": ({"use_npxg": True}, "H7",
                 "replacing raw xG with npxG"),
    "H8_no_decay": ({"decay": False}, "H8",
                    "removing exponential time-decay"),
    "H9_rho_zero": ({"force_rho_zero": True}, "H9",
                    "forcing Dixon-Coles rho to zero"),
    "HL_90": ({"half_life_days": 90.0}, "-", "half-life sensitivity: 90d"),
    "HL_365": ({"half_life_days": 365.0}, "-", "half-life sensitivity: 365d"),
}
LOGLOSS_THRESHOLD = 0.002


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step-days", type=int, default=30)
    parser.add_argument("--initial-train-end", default="2023-07-01")
    parser.add_argument("--data-end", default="2025-07-01")
    args = parser.parse_args(argv)

    matches = pd.read_parquet(CLEANED_MATCHES)
    data_end = pd.Timestamp(args.data_end)
    matches = matches[matches[Col.DATE] < data_end].copy()
    rows = []
    for name, (overrides, hyp, desc) in VARIANTS.items():
        res = run_walkforward_eval(
            matches,
            feature_params=dict(overrides),
            initial_train_end=args.initial_train_end,
            step_days=args.step_days,
            models=[PRIMARY_MODEL],
        )
        m = res["models_1x2"][PRIMARY_MODEL]
        rows.append({"variant": name, "hypothesis": hyp, "tests": desc,
                     "brier": m["brier"], "logloss": m["logloss"], "n": m["n"]})
        print(f"{name:<20} brier {m['brier']:.4f}  logloss {m['logloss']:.4f}"
              f"  n {m['n']}")

    df = pd.DataFrame(rows)
    base = df.loc[df["variant"] == "BASE"].iloc[0]
    df["delta_brier_vs_base"] = df["brier"] - base["brier"]
    df["delta_logloss_vs_base"] = df["logloss"] - base["logloss"]

    def verdict(row: pd.Series) -> str:
        if row["variant"] == "BASE" or row["hypothesis"] == "-":
            return "-"
        if row["delta_logloss_vs_base"] >= LOGLOSS_THRESHOLD:
            return "variant_worse"
        if row["delta_logloss_vs_base"] <= -LOGLOSS_THRESHOLD:
            return "variant_better"
        return "inconclusive"

    df["verdict"] = df.apply(verdict, axis=1)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(REPORTS_DIR / "hypotheses.csv", index=False)
    def fmt(v: object) -> str:
        return f"{v:.4f}" if isinstance(v, float) else str(v)

    header = "| " + " | ".join(df.columns) + " |"
    sep = "| " + " | ".join(["---"] * len(df.columns)) + " |"
    body = ["| " + " | ".join(fmt(v) for v in row) + " |"
            for row in df.itertuples(index=False)]
    lines = [
        "# Development-only hypothesis ablations (walk-forward, glm_dc)",
        "",
        f"Data strictly before {args.data_end}; test windows start {args.initial_train_end}.",
        "",
        f"Decision threshold: |variant log-loss - BASE| >= {LOGLOSS_THRESHOLD}.",
        "",
        header, sep, *body,
        "",
    ]
    (REPORTS_DIR / "hypotheses.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nwritten to {REPORTS_DIR / 'hypotheses.csv'} and hypotheses.md")


if __name__ == "__main__":
    main()

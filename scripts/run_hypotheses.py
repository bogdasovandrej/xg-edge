"""Hypothesis ablations: each registered metric earns its place, or it goes.

Runs the primary model (glm_dc) through the same walk-forward protocol with
one mechanism removed (or changed) at a time and compares log-loss/Brier
against the BASE configuration. A positive delta (variant worse than base)
supports the hypothesis that the mechanism adds value.
"""
from __future__ import annotations

import argparse

import pandas as pd

from xgedge.contracts import CLEANED_MATCHES, REPORTS_DIR
from xgedge.pipeline import PRIMARY_MODEL, run_walkforward_eval

# variant -> (feature_params overrides, hypothesis, what removing it tests)
VARIANTS = {
    "BASE": ({}, "-", "reference configuration"),
    "H2_no_opp_adjust": ({"adjust_opponent": False}, "H2",
                         "opponent-strength normalization of xG"),
    "H7_xg_with_pens": ({"use_npxg": False}, "H7",
                        "using npxG instead of raw xG"),
    "H8_no_decay": ({"decay": False}, "H8",
                    "exponential time-decay of match weights"),
    "H9_rho_zero": ({"force_rho_zero": True}, "H9",
                    "Dixon-Coles low-score correction"),
    "HL_90": ({"half_life_days": 90.0}, "-", "half-life sensitivity: 90d"),
    "HL_365": ({"half_life_days": 365.0}, "-", "half-life sensitivity: 365d"),
}
LOGLOSS_THRESHOLD = 0.002


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step-days", type=int, default=30)
    parser.add_argument("--initial-train-end", default="2023-07-01")
    args = parser.parse_args(argv)

    matches = pd.read_parquet(CLEANED_MATCHES)
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
            return "supported"        # removing the mechanism hurt
        if row["delta_logloss_vs_base"] <= 0:
            return "failed"           # model is no worse without it
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
        "# Hypothesis ablations (walk-forward, glm_dc)",
        "",
        f"Support threshold: variant log-loss at least {LOGLOSS_THRESHOLD}"
        " worse than BASE.",
        "",
        header, sep, *body,
        "",
    ]
    (REPORTS_DIR / "hypotheses.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nwritten to {REPORTS_DIR / 'hypotheses.csv'} and hypotheses.md")


if __name__ == "__main__":
    main()

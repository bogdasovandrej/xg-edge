# High-total and exact-score audit: EPL 2025/26

## Technical summary

- In the final **100** league matches (2026-03-01 to 2026-05-24), O3.5 occurred **27/100 (27.0%)** and O4.5 **8/100 (8.0%)**.
- The only complete totals pair is Bet365 closing O/U2.5. Converting it to higher tails with a Poisson assumption predicts **34.2%** O3.5 and **17.9%** O4.5, versus observed 27.0% and 8.0%. These are derived diagnostics, not direct bookmaker quotes.
- The GLM/Dixon-Coles modal exact score hit **11/100 (11.0%)**. Its top five covered **44.0%**, while an average **55.4%** probability mass remained outside the displayed five outcomes. A single score is therefore not a truthful summary.
- Across 18 pre-registered marker/endpoint tests, **0** survived Benjamini-Hochberg FDR 5%. This audit does not identify a deployable easy-over subset.

The decision remains **NO_BET_FOR_O3.5_OR_O4.5**. The sample is exploratory and cannot establish profitable O3.5/O4.5 betting without timestamped direct prices and prospective CLV.

## Higher-score tails are overestimated by the O2.5 Poisson transform

| Endpoint | Observed | Market-derived mean | Bias (predicted − observed) | Brier | 95% bootstrap bias interval |
|---|---:|---:|---:|---:|---:|
| O3.5 | 27.0% | 34.2% | 7.2% | 0.2099 | -2.1% to 16.0% |
| O4.5 | 8.0% | 17.9% | 9.9% | 0.0873 | 4.0% to 15.0% |

The transformation assumes a single Poisson total-goal rate inferred from the de-vigged O/U2.5 pair. Tail miscalibration is visible at both thresholds, especially O4.5. The paired bootstrap comparison in the JSON artifact does not turn either model into a betting claim; it only compares two probability diagnostics on the same 100 outcomes.

## Exact score needs a distribution, not one label

| Coverage set | Hits | Rate |
|---|---:|---:|
| top 1 | 11 | 11.0% |
| top 3 | 32 | 32.0% |
| top 5 | 44 | 44.0% |
| top 10 | 82 | 82.0% |

Mean exact-score negative log likelihood is **2.871**. The fixed always-1:1 baseline hits **12/100 (12.0%)**. The model's most frequent modal score is **1:1**, used in **89/100** predictions. This concentration explains why a repeated visible score looks unrealistic even when the underlying matrix contains many alternatives.

Product rule: show expected home/away goals, ranked top-five score probabilities, and probability outside the top five. Do not present the modal score as a promised result.

## No pre-match marker passed the corrected exploratory screen

Each marker was defined before reading the outcome row and tested separately with a one-standard-deviation univariate logistic coefficient. The correction family contains both endpoints and all registered markers. The six smallest adjusted p-values are shown below; full results are in `reports/high_totals_audit.json`.

| Endpoint | Marker | Odds ratio / 1 SD | AUC | Raw p | BH q | FDR 5% |
|---|---|---:|---:|---:|---:|---|
| O3.5 | xg_defence_form_sum | 1.371 | 0.561 | 0.1725 | 0.8570 | no |
| O3.5 | xg_attack_form_sum | 0.759 | 0.426 | 0.2241 | 0.8570 | no |
| O4.5 | model_expected_total | 0.668 | 0.416 | 0.2971 | 0.8570 | no |
| O4.5 | model_p_over35 | 0.670 | 0.416 | 0.3002 | 0.8570 | no |
| O4.5 | model_match_balance | 1.367 | 0.572 | 0.4404 | 0.8570 | no |
| O4.5 | market_implied_total | 0.757 | 0.457 | 0.4778 | 0.8570 | no |

Closing-market markers are benchmark-only: a closing quote is pre-kickoff, but it is not available at an earlier execution horizon. Any marker that survives in future must be frozen and retested on a new chronological holdout before promotion.

## Scope, data quality, and definitions

- Grain: one completed EPL match per row; stable sample rule is the last 100 rows after sorting by date and `match_id`.
- Required fields are complete in all 100 sample rows; duplicate `match_id` rows: 0; result/goal mismatches: 0.
- O3.5 means at least four regulation-time goals; O4.5 means at least five. Denominator is all 100 selected matches.
- The GLM/Dixon-Coles model is refit in expanding 30-day windows. Training rows predate the test-window start; same-date feature state is frozen.
- Direct O3.5/O4.5 odds present: **false**. Pinnacle closing O/U2.5 complete rows: **0**. Bet365 closing O/U2.5 complete rows: **100**.

## Post-match evidence explains outcomes but cannot predict them

Observed total xG averaged **3.17** overall and **3.74** in O3.5 matches, while finishing residual (goals minus xG) averaged **0.71** in O3.5 matches. Red-card match rates were **9.0%** overall and **7.4%** within O3.5.

These fields are deliberately descriptive only. Match xG, finishing residual, PPDA, deep completions and red cards are known after kickoff and are prohibited from the pre-match marker registry.

## Limitations and next validation

- There are only 8 O4.5 events. Confidence intervals are wide, and the sample cannot support a rare-event production rule.
- Direct O3.5/O4.5 opening, taken and closing prices are absent. EV, best price and CLV for those markets are therefore not identifiable.
- Coach changes, new/young goalkeepers, rivalry labels, lineups, injuries and tactical formations are absent from this dataset and were not fabricated.
- The marker screen is univariate and exploratory, not causal. It does not capture interactions and must not be tuned repeatedly on these same 100 matches.
- Next step: collect timestamped direct O3.5/O4.5 quotes and point-in-time lineup/context snapshots, freeze one small hypothesis set, then evaluate calibration and CLV on a new chronological sample.

## Reproduction

```powershell
.\.venv\Scripts\python.exe scripts\audit_score_and_high_totals.py --output reports\high_totals_audit.json --markdown docs\high-totals-audit.md
```

The script uses no network calls. Input SHA-256, parameters, seed, feature configuration, calibration bins, bootstrap intervals and every marker test are stored in the JSON artifact.

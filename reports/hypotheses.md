# Hypothesis ablations (walk-forward, glm_dc)

Support threshold: variant log-loss at least 0.002 worse than BASE.

| variant | hypothesis | tests | brier | logloss | n | delta_brier_vs_base | delta_logloss_vs_base | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BASE | - | reference configuration | 0.5835 | 0.9827 | 1120 | 0.0000 | 0.0000 | - |
| H2_no_opp_adjust | H2 | opponent-strength normalization of xG | 0.5824 | 0.9807 | 1120 | -0.0011 | -0.0020 | failed |
| H7_xg_with_pens | H7 | using npxG instead of raw xG | 0.5821 | 0.9804 | 1120 | -0.0014 | -0.0023 | failed |
| H8_no_decay | H8 | exponential time-decay of match weights | 0.5917 | 0.9932 | 1120 | 0.0083 | 0.0105 | supported |
| H9_rho_zero | H9 | Dixon-Coles low-score correction | 0.5835 | 0.9827 | 1120 | 0.0000 | -0.0000 | failed |
| HL_90 | - | half-life sensitivity: 90d | 0.5852 | 0.9854 | 1120 | 0.0018 | 0.0027 | - |
| HL_365 | - | half-life sensitivity: 365d | 0.5852 | 0.9848 | 1120 | 0.0018 | 0.0021 | - |

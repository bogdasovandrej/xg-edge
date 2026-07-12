# Development-only hypothesis ablations (walk-forward, glm_dc)

Data strictly before 2025-07-01; test windows start 2023-07-01.

Decision threshold: |variant log-loss - BASE| >= 0.002.

| variant | hypothesis | tests | brier | logloss | n | delta_brier_vs_base | delta_logloss_vs_base | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BASE | - | reference configuration | 0.5645 | 0.9554 | 745 | 0.0000 | 0.0000 | - |
| H2_with_opp_adjust | H2 | adding opponent-strength normalization of xG | 0.5656 | 0.9571 | 745 | 0.0010 | 0.0018 | inconclusive |
| H7_npxg | H7 | replacing raw xG with npxG | 0.5654 | 0.9568 | 745 | 0.0008 | 0.0014 | inconclusive |
| H8_no_decay | H8 | removing exponential time-decay | 0.5759 | 0.9705 | 745 | 0.0114 | 0.0151 | variant_worse |
| H9_rho_zero | H9 | forcing Dixon-Coles rho to zero | 0.5643 | 0.9549 | 745 | -0.0002 | -0.0005 | inconclusive |
| HL_90 | - | half-life sensitivity: 90d | 0.5661 | 0.9577 | 745 | 0.0016 | 0.0024 | - |
| HL_365 | - | half-life sensitivity: 365d | 0.5677 | 0.9596 | 745 | 0.0031 | 0.0042 | - |

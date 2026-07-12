# xg-edge evaluation summary

## Config

| Parameter | Value |
| --- | --- |
| initial_train_end | 2025-07-01 |
| step_days | 30 |
| edge_threshold | 0.0300 |
| kelly_fraction | 0.2500 |
| kelly_cap | 0.0200 |
| max_goals | 10 |
| force_rho_zero | False |
| models | glm_dc,gbm_dc,dc_classic,goals_poisson,uniform,market |
| feature_half_life_days | 180.0000 |
| feature_red_card_weight | 0.5000 |
| feature_adjust_opponent | False |
| feature_use_npxg | False |
| feature_decay | True |
| feature_min_history | 5 |
| feature_venue_blend | 0.3000 |
| feature_clamp | (0.5, 2.0) |

## 1X2 metrics

| Model | brier | logloss | n | brier_common | logloss_common | n_common |
| --- | --- | --- | --- | --- | --- | --- |
| glm_dc | 0.6157 | 1.0276 | 375 | 0.5959 | 1.0006 | 205 |
| gbm_dc | 0.6109 | 1.0210 | 375 | 0.5903 | 0.9933 | 205 |
| dc_classic | 0.6184 | 1.0375 | 375 | 0.5911 | 1.0068 | 205 |
| goals_poisson | 0.6253 | 1.1055 | 375 | 0.5907 | 1.1135 | 205 |
| uniform | 0.6667 | 1.0986 | 375 | 0.6667 | 1.0986 | 205 |
| market | 0.5874 | 0.9822 | 205 | 0.5874 | 0.9822 | 205 |

## Over/Under 2.5 metrics

| Model | brier | logloss | n | brier_common | logloss_common | n_common |
| --- | --- | --- | --- | --- | --- | --- |
| glm_dc | 0.2475 | 0.6884 | 375 | 0.2453 | 0.6837 | 205 |
| gbm_dc | 0.2496 | 0.6928 | 375 | 0.2490 | 0.6913 | 205 |
| dc_classic | 0.2516 | 0.6987 | 375 | 0.2434 | 0.6834 | 205 |
| goals_poisson | 0.2473 | 0.6905 | 375 | 0.2389 | 0.6749 | 205 |
| uniform | 0.2500 | 0.6931 | 375 | 0.2500 | 0.6931 | 205 |
| market | 0.2428 | 0.6784 | 205 | 0.2428 | 0.6784 | 205 |

## Betting simulation

| Staking | final_bankroll | roi | max_drawdown | n_bets | total_staked |
| --- | --- | --- | --- | --- | --- |
| kelly | 0.4918 | -0.0922 | 0.6827 | 467 | 5.5132 |
| flat | 0.7276 | -0.0689 | 0.4114 | 467 | 3.9553 |

## Closing line value

| Statistic | Value |
| --- | --- |
| mean | -0.0713 |
| median | -0.0533 |
| share_positive | 0.1519 |
| ci_low | -0.0812 |
| ci_high | -0.0616 |
| n | 270 |
| n_clusters | 188 |
| bootstrap_unit | cluster |

# xg-edge evaluation summary

## Config

| Parameter | Value |
| --- | --- |
| initial_train_end | 2023-07-01 |
| step_days | 30 |
| edge_threshold | 0.0300 |
| kelly_fraction | 0.2500 |
| kelly_cap | 0.0200 |
| max_goals | 10 |
| force_rho_zero | False |
| models | glm_dc,gbm_dc,dc_classic,goals_poisson,uniform,market |
| feature_half_life_days | 180.0000 |

## 1X2 metrics

| Model | brier | logloss | n | brier_common | logloss_common | n_common |
| --- | --- | --- | --- | --- | --- | --- |
| glm_dc | 0.5835 | 0.9827 | 1120 | 0.5737 | 0.9692 | 950 |
| gbm_dc | 0.5869 | 0.9875 | 1120 | 0.5766 | 0.9728 | 950 |
| dc_classic | 0.5884 | 0.9879 | 1120 | 0.5769 | 0.9720 | 950 |
| goals_poisson | 0.5938 | 0.9956 | 1120 | 0.5806 | 0.9775 | 950 |
| uniform | 0.6667 | 1.0986 | 1120 | 0.6667 | 1.0986 | 950 |
| market | 0.5607 | 0.9468 | 950 | 0.5607 | 0.9468 | 950 |

## Over/Under 2.5 metrics

| Model | brier | logloss | n |
| --- | --- | --- | --- |
| glm_dc | 0.2419 | 0.6778 | 1120 |
| gbm_dc | 0.2465 | 0.6885 | 1120 |
| dc_classic | 0.2443 | 0.6842 | 1120 |
| goals_poisson | 0.2438 | 0.6831 | 1120 |
| uniform | 0.2500 | 0.6931 | 1120 |
| market | 0.2383 | 0.6692 | 1120 |

## Betting simulation

| Staking | final_bankroll | roi | max_drawdown | n_bets | total_staked |
| --- | --- | --- | --- | --- | --- |
| kelly | 0.2843 | -0.0484 | 0.8208 | 1583 | 14.7741 |
| flat | 0.4303 | -0.0579 | 0.6432 | 1583 | 9.8337 |

## Closing line value

| Statistic | Value |
| --- | --- |
| mean | -0.0682 |
| median | -0.0569 |
| share_positive | 0.1767 |
| ci_low | -0.0731 |
| ci_high | -0.0632 |
| n | 1443 |

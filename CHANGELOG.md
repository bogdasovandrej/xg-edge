# Changelog

## 0.2.0 — 2026-07-13

### Methodology

- Split development (through 2024/25) from locked retrospective holdout 2025/26.
- Selected raw xG, no opponent adjustment, 180-day decay and GLM on development.
- Recomputed hypothesis and holdout reports.
- Standardized closing benchmark on Pinnacle for both 1X2 and total 2.5.
- Added common-subset totals metrics.

### Correctness

- Made feature updates atomic per calendar date.
- Removed result-order leakage from same-day bankroll compounding.
- Replaced iid CLV bootstrap with match-cluster bootstrap.
- Added strict Dixon–Coles parameter checks and neutral rho for flat likelihood.
- Added Pinnacle totals to raw, cleaned and feature contracts.

### Monte Carlo

- Added deterministic Dixon–Coles scoreline simulation.
- Added market estimates with Bernoulli standard errors.
- Added analytical-vs-simulation CLI and convergence tests.

### Verification

- Expanded the offline suite from 91 to 111 tests.
- Rebuilt the 1900-match cleaned dataset and all tracked reports.

## 0.1.0 — 2026-07-06

- Initial local implementation: Understat and football-data loaders, causal xG
  features, Poisson GLM/GBM, Dixon–Coles score matrix, market aggregation,
  Kelly staking, walk-forward evaluation, CLV and reports.
- The commit existed locally but was never pushed to the empty GitHub remote.

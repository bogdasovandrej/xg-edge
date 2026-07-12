# Changelog

## 0.3.1 — 2026-07-13

- Fixed GitHub Pages client asset paths so browser hydration and five-minute
  live snapshot refreshes work under the `/xg-edge/` repository prefix.
- Added a regression test that rejects unprefixed production asset URLs.

## 0.3.0 — 2026-07-13

### Live predictions

- Added result-free future fixtures with strict point-in-time cutoffs.
- Added official FIFA World Cup and UEFA Champions League fixture feeds.
- Added separate experimental World Cup and UCL qualifying models.
- Added deterministic public JSON/CSV snapshots and a scheduled refresh.

### Coverage and context

- Added 2026/27 registry/download support for the European top five leagues.
- Added point-in-time lineup, injury, referee and event-level red-card contracts.
- Kept FBref disabled under its published predictive-ML data-use restriction.
- Added optional licensed-provider boundaries for injuries and Opta-class data.

### Market discipline

- Added opening-market anchoring, centered-log-ratio residual shrinkage and
  longshot controls.
- Added a clustered CLV deployment gate that defaults to `NO BET`.
- The anchored holdout improved log-loss but still produced negative CLV, so
  no betting strategy was promoted.

### Product

- Added a responsive live forecast website.
- Added six-hour GitHub Actions refreshes with official-data provenance.

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

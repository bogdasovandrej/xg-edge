# Changelog

## 0.6.1 — 2026-07-22

- Added Odds-API.io as the primary bookmaker feed with a free-tier-friendly
  batched endpoint for up to ten matched events per request.
- Added pre-match 1X2 and totals normalization for Bet365, Unibet and Pinnacle,
  while retaining The Odds API as a fallback.
- Extended market capture to Top-5 fixtures and made hourly quota reset handling
  automatic.
- Kept credentials server-side and all fixture joins exact/fail-closed.

## 0.6.0 — 2026-07-22

- Added an immutable forecast evidence archive with hash-linked append events.
- Added scheduled PAPER challenger evaluation and a guarded model registry.
- Added a public predicted-vs-actual archive backed by frozen forecasts and
  official 90-minute results.
- Added a football-data.org v4 fixture adapter for the 2026/27 top-five league
  calendar, gated behind `FOOTBALL_DATA_API_KEY`.
- Added a high-score/totals audit for the last 100 EPL matches; no O3.5/O4.5
  betting signal passed multiple-testing discipline.
- Hardened score display: exact scores are shown as probability distributions,
  never as promised results.

## 0.5.0 — 2026-07-14

- Added an official The Odds API adapter and a quota-aware cloud market monitor.
- Added an append-only prospective ledger with post-kickoff CLV finalization,
  model/competition cohorts and fixed-horizon confirmation.
- Restricted confirmatory CLV to an auditable Pinnacle 1X2 price universe;
  non-sharp consensus remains diagnostic only.
- Added automatic official FIFA/UEFA result settlement and calibration metrics.
- Added a bounded StatsBomb Open Data adapter for historical event-level xG,
  npxG, penalties, lineups, referees and dismissals.
- Replaced hard-coded dashboard CLV numbers with the live evidence state.
- Hardened public snapshots against stale, in-play and pre-forecast odds, JSON
  NaN, secret-bearing HTTP errors and out-of-order captures.

## 0.4.1 — 2026-07-13

- Updated the dashboard to separate the anchored holdout calibration gain from
  the still-negative shadow CLV and the empty prospective sample.

## 0.4.0 — 2026-07-13

- Added expandable match dossiers and match search to the public site.
- Added separate point-in-time Elo ledgers for clubs and national teams.
- Added auditable npxG, opponent-strength and event-time red-card adjustment
  primitives that fail closed when provider fields are missing.
- Added referee, lineup, absence and weather availability sections with explicit
  provenance and no invented values.
- Added Open-Meteo kickoff forecasts using official UEFA stadium coordinates or
  a verified FIFA venue city.
- Added transparent tail-risk diagnostics; they measure forecast fragility and
  do not claim to predict black swans.
- Added timestamped World Cup opening prices and neutral-site market anchoring.
  England is now the anchored 90-minute favourite, while the disagreeing raw
  model remains visible for audit.
- Added top-three market watchlists with probability, fair price, quoted price
  and point edge. The global prospective CLV gate remains `NO BET`.

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

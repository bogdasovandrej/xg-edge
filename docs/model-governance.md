# Model governance and PAPER protocol

This document turns the user-supplied monitoring prompt into four testable
contracts.  It is deliberately stricter than a narrative betting preview.

## 1. Data contract

- A quote is identified by fixture, market, period, line, side, bookmaker,
  provider update time and receive time.
- Regulation-time (`90M`) prices and results exclude extra time and penalties.
- A 1X2 no-vig snapshot must contain all three outcomes from the same bookmaker
  observation.  A totals snapshot must contain Over and Under for the same line.
- Post-kickoff quotes, stale quotes, incomplete markets and ambiguous fixture
  matches are rejected, not repaired.
- `taken_price`, market baseline and closing benchmark are separate fields.
- Legacy observations with reconstructed or uncertain prices remain quarantined
  and never enter the confirmatory gate.
- Prematch and postmatch/event data are separate.  A red card, penalty or goal
  observed after kickoff can explain a result but cannot alter the original
  forecast.

## 2. Prediction contract

- Every forecast has a timestamp, immutable model version, probability basis
  and settlement period.
- The market is the prior.  Model corrections must be frozen and reproducible;
  manual narrative probability adjustments are forbidden.
- Exact scores are shown only as a distribution.  A modal score is not a
  prediction and is not a betting market selector.
- Poisson-derived O3.5/O4.5 tails are research diagnostics until calibrated
  against direct bookmaker lines at the same point and period.
- SRS, coach narratives, goalkeeper changes and tactical labels are shadow
  features until their incremental value survives a new time-based holdout.

## 3. Evaluation protocol

- Primary confirmatory market: regulation 1X2.  Other markets use isolated
  shadow cohorts and cannot open the primary gate.
- Closing-line value is evaluated independently of the match result:
  `CLV = taken_odds * closing_fair_probability - 1`.
- Closing benchmark: complete pre-kickoff Pinnacle snapshot.  Other books are
  diagnostic only unless a new policy is preregistered.
- Forecasts are evaluated in fixed chronological cohorts.  Intermediate CLV is
  not used to alter selection rules.
- Primary inference uses a paired/cluster bootstrap.  A positive sample mean by
  itself is not evidence of edge.
- Brier score, log loss and calibration are result-based diagnostics.  ROI is a
  late secondary metric and never repairs negative CLV.
- Multiple markets or feature challenges are discovery experiments.  A chosen
  challenger must be frozen and retested on entirely new matches.

## 4. Promotion gate

The public mode remains `PAPER_ONLY / MODEL_IN_QUARANTINE` until one isolated
cohort has all of the following:

1. complete timestamp/market integrity;
2. at least the preregistered fixed horizon;
3. a family-adjusted lower 95% confidence bound for mean execution CLV above 0;
4. a lower confidence bound for paired improvement over the current champion
   above 0;
5. closing-price coverage at or above the preregistered threshold;
6. no material degradation versus the synchronous no-vig market on log loss;
7. acceptable drawdown and ruin risk under the frozen staking rule.

Passing the gate authorizes only a small, separately governed execution pilot.
It does not prove future profit.

## PAPER strategy tournament

Every strategy starts each cycle with **10,000 RUB**.  Bankruptcy opens a new
cycle at 10,000 RUB but never deletes the failed cycle.  Reaching 1,000,000 RUB
is recorded only as a stress-test milestone; it is not the selection objective,
because fastest-to-target rewards variance and survivorship bias.

The on-site interim leaderboard uses a preregistered score only after shrinking
positive performance toward zero until 100 settled bets: 65% mean confirmatory
CLV, 20% log growth per bet and 15% ROI, followed by unshrunk drawdown and ruin
penalties.  It is descriptive; promotion still requires the confidence-bound
gate above.

Champion promotion uses:

- primary: prospective execution-CLV lower confidence bound;
- primary: median log bankroll growth under a frozen stake rule;
- guardrails: ruin rate, maximum drawdown, quote integrity and selection rate;
- diagnostics: ROI, hit rate, time/number of settled bets to 1,000,000 RUB.

Parlays are simulation-only and disabled by default.  They may enter a shadow
experiment only after every leg is individually eligible, prices are
synchronous and dependence is explicitly penalized.  Parlays cannot open a
real-money gate.

"use client";

import { useEffect, useMemo, useState } from "react";

type Forecast = {
  id: string;
  competition: string;
  stage: string;
  kickoff_utc: string;
  home: string;
  away: string;
  venue?: string | null;
  model?: string | null;
  p_home?: number | null;
  p_draw?: number | null;
  p_away?: number | null;
  p_over25?: number | null;
  p_over35?: number | null;
  p_over45?: number | null;
  p_btts?: number | null;
  p_home_advance?: number | null;
  p_away_advance?: number | null;
  top_score?: string | null;
  top_score_probability?: number | null;
  score_scenarios?: Array<{ score?: string | null; probability?: number | null }> | null;
  score_scenarios_coverage?: number | null;
  other_score_probability?: number | null;
  score_display?: string | null;
  tail_probability_status?: string | null;
  expected_goals?: { home?: number | null; away?: number | null; total?: number | null } | null;
  expected_goals_basis?: {
    method?: string | null;
    expected_total_goals?: number | null;
    prior_total_goals?: number | null;
    prior_matches?: number | null;
    recent_match_limit?: number | null;
    team_histories_used?: Array<{
      side?: string | null;
      matches?: number | null;
      raw_average_total_goals?: number | null;
      shrunk_total_goals?: number | null;
    }> | null;
  } | null;
  uncertainty?: string | null;
  recommendation?: string | null;
  decision_status?: string | null;
  model_status?: string | null;
  market_period?: string | null;
  betting_eligible?: boolean | null;
  first_leg?: string | null;
  probability_basis?: string | null;
  rating_basis?: {
    basis?: string | null;
    home?: {
      elo?: number | null;
      source?: string | null;
      matches?: number | null;
    } | null;
    away?: {
      elo?: number | null;
      source?: string | null;
      matches?: number | null;
    } | null;
  } | null;
  raw_model_1x2?: { home: number; draw: number; away: number } | null;
  model_market_forecasts?: ModelMarketForecast[] | null;
  evaluation_cohort_id?: string | null;
  cohort_gate?: {
    allowed?: boolean;
    action?: string | null;
    reason?: string | null;
    decision_status?: string | null;
  } | null;
  details?: MatchDetails | null;
};

type ModelMarketForecast = {
  market?: string | null;
  selection?: string | null;
  line?: number | null;
  label?: string | null;
  theoretical_probability?: number | null;
  reliability_haircut?: number | null;
  conservative_probability?: number | null;
  theoretical_fair_odds?: number | null;
  conservative_fair_odds?: number | null;
  recommendation_group?: string | null;
  recommendation_rank?: number | null;
  recommendation_role?: "VALUE_SINGLE" | "BALANCED_SINGLE" | "EXPRESS_LEG" | string | null;
  target_market_odds?: number | null;
  minimum_market_odds?: number | null;
  price_status?: string | null;
  status?: string | null;
};

type RecentMatch = {
  match_id: string;
  kickoff_utc: string;
  competition?: string | null;
  competition_level?: string | null;
  venue?: "home" | "away" | string | null;
  opponent?: string | null;
  score_90?: { for: number; against: number } | null;
  result_90?: string | null;
  opponent_level?: string | null;
  opponent_elo_before?: { rating?: number | null } | null;
  xg?: {
    raw?: number | null;
    non_penalty?: { status?: string; value?: number | null; reason?: string | null; source?: string | null } | null;
    red_and_opponent_adjusted_npxg?: { status?: string; value?: number | null; reason?: string | null } | null;
  } | null;
  red_cards?: unknown[] | null;
  provenance?: { source?: string | null; provider?: string | null; match_url?: string | null; xg?: string | null } | null;
};

type TeamDetail = {
  name?: string | null;
  elo?: number | null;
  level?: string | null;
  competition_level?: string | null;
  recent_matches?: RecentMatch[] | null;
  likely_lineup?: Array<{
    player_name?: string | null;
    status?: string | null;
    is_confirmed?: boolean | null;
    field_position?: string | null;
    detailed_field_position?: string | null;
    jersey_number?: number | null;
    is_late_update?: boolean | null;
  }> | null;
  absences?: Array<{ player_name?: string | null; status?: string | null }> | null;
  coach?: {
    coach_name?: string | null;
    role?: string | null;
    is_late_update?: boolean | null;
  } | null;
};

type CandidateBet = {
  rank?: number;
  selection?: string;
  outcome?: string;
  market?: string;
  line?: number | null;
  probability?: number | null;
  fair_odds?: number | null;
  market_odds?: number | null;
  point_edge?: number | null;
  status?: string | null;
  edge_status?: string | null;
  bookmaker?: string | null;
  bookmaker_key?: string | null;
  source_provider?: string | null;
};

type MarketPrice = {
  odds?: number | null;
  bookmaker?: string | null;
  bookmaker_key?: string | null;
};

type MarketSnapshot = {
  source_provider?: string | null;
  status?: "SHADOW_ONLY" | "STALE" | "REJECTED" | string | null;
  reason?: string | null;
  captured_at_utc?: string | null;
  bookmakers?: number | null;
  best_1x2?: {
    home?: MarketPrice | null;
    draw?: MarketPrice | null;
    away?: MarketPrice | null;
  } | null;
  best_totals?: Array<{
    line?: number | null;
    over?: MarketPrice | null;
    under?: MarketPrice | null;
  }> | null;
  best_btts?: { yes?: MarketPrice | null; no?: MarketPrice | null } | null;
  best_spreads?: Array<{
    line?: number | null;
    home?: MarketPrice | null;
    away?: MarketPrice | null;
  }> | null;
  source_url?: string | null;
};

type MatchDetails = {
  teams?: { home?: TeamDetail; away?: TeamDetail } | null;
  referee?: {
    status?: string;
    name?: string | null;
    season?: string | null;
    matches?: number | null;
    yellow_cards_per_match?: number | null;
    red_cards_per_match?: number | null;
    comparison?: { label?: string; difference?: number } | null;
  } | null;
  weather?: {
    status?: string;
    temperature_c?: number | null;
    wind_kph?: number | null;
    precipitation_mm?: number | null;
    condition?: string | null;
  } | null;
  adjustments?: Array<{ name?: string; method?: string; warning?: string }> | null;
  data_quality?: { score?: number; label?: string; sources?: string[]; warnings?: string[] } | null;
  tail_risk?: { label?: string; score?: number; drivers?: Array<{ name?: string; status?: string; contribution?: number }> } | null;
  market?: {
    bookmaker?: string;
    source_url?: string;
    raw_model?: { home: number; draw: number; away: number };
    market_fair?: { home: number; draw: number; away: number };
    anchored?: { home: number; draw: number; away: number };
    calibration_warning?: string;
  } | null;
  candidate_bets?: CandidateBet[] | null;
  market_snapshot?: MarketSnapshot | null;
  market_candidates?: CandidateBet[] | null;
  expanded_market_candidates?: CandidateBet[] | null;
  betting_gate?: { allowed?: boolean; reason?: string } | null;
};

type ProspectiveClvSummary = {
  action?: "BET" | "NO BET" | string | null;
  reason?: string | null;
  min_independent_matches?: number | null;
  clv?: {
    mean?: number | null;
    median?: number | null;
    share_positive?: number | null;
    ci_low?: number | null;
    ci_high?: number | null;
    n?: number | null;
    n_clusters?: number | null;
    bootstrap_unit?: string | null;
  } | null;
  calibration?: {
    n?: number | null;
    mean_logloss?: number | null;
    mean_brier?: number | null;
  } | null;
  tracked_fixtures?: number | null;
  shadow_candidates?: number | null;
  confirmatory_ready?: number | null;
  cohort_count?: number | null;
  cohorts?: Record<string, {
    action?: string | null;
    reason?: string | null;
    min_independent_matches?: number | null;
    confirmatory_ready?: number | null;
    tracked_fixtures?: number | null;
    dimensions?: {
      competition_or_sport?: string | null;
      model?: string | null;
      probability_basis?: string | null;
    } | null;
    clv?: ProspectiveClvSummary["clv"];
    decision?: { status?: string | null; locked?: boolean | null } | null;
  }> | null;
};

type PaperCandidate = {
  rank?: number | null;
  fixture_id: string;
  competition?: string | null;
  kickoff_utc?: string | null;
  home?: string | null;
  away?: string | null;
  selection?: string | null;
  market?: string | null;
  line?: number | null;
  model_probability?: number | null;
  break_even_probability?: number | null;
  probability_edge?: number | null;
  odds?: number | null;
  bookmaker?: string | null;
  robust_edge?: number | null;
  data_quality_score?: number | null;
  status?: string | null;
  real_money_eligible?: boolean | null;
};

type PaperCandidateRanking = {
  status?: string | null;
  real_money_execution?: boolean | null;
  eligible_matches?: number | null;
  displayed_candidates?: number | null;
  rejection_counts?: Record<string, number> | null;
  candidates?: PaperCandidate[] | null;
};

type PaperStrategyRow = {
  rank?: number | null;
  strategy_id?: string | null;
  label?: string | null;
  score?: number | null;
  equity_balance_rub?: number | null;
  available_balance_rub?: number | null;
  pnl_rub?: number | null;
  roi?: number | null;
  max_drawdown?: number | null;
  log_growth?: number | null;
  mean_clv?: number | null;
  settled_bets?: number | null;
  open_bets?: number | null;
  wins?: number | null;
  losses?: number | null;
  pushes?: number | null;
  cycle_count?: number | null;
  ruin_count?: number | null;
  target_hit_count?: number | null;
};

type PaperTradingSummary = {
  status?: string | null;
  real_money_execution?: boolean | null;
  updated_at?: string | null;
  starting_balance_rub?: number | null;
  target_balance_rub?: number | null;
  target_role?: string | null;
  totals?: {
    strategies?: number | null;
    enrolled_matches?: number | null;
    settled_matches?: number | null;
    open_matches?: number | null;
    settled_bets?: number | null;
    open_bets?: number | null;
  } | null;
  leaderboard?: PaperStrategyRow[] | null;
  markets?: Record<string, {
    enrolled?: number | null;
    settled?: number | null;
    open?: number | null;
  }> | null;
  selection_policy?: {
    minimum_settled_bets_for_full_evidence?: number | null;
    speed_to_target_used_for_ranking?: boolean | null;
    strategy_deletion?: boolean | null;
  } | null;
  parlays?: { status?: string | null; reason?: string | null } | null;
};

type OutcomeKey = "home" | "draw" | "away";

type ProspectiveFixtureRecord = {
  fixture_id?: string | null;
  evaluation_cohort_id?: string | null;
  home?: string | null;
  away?: string | null;
  kickoff_utc?: string | null;
  forecast?: {
    generated_at?: string | null;
    competition?: string | null;
    model?: string | null;
    probability_basis?: string | null;
    probabilities?: Partial<Record<OutcomeKey, number | null>> | null;
  } | null;
  result?: {
    home_goals_90?: number | null;
    away_goals_90?: number | null;
    outcome?: OutcomeKey | string | null;
  } | null;
  calibration?: {
    brier?: number | null;
    logloss?: number | null;
  } | null;
};

type ProspectiveLedger = {
  schema_version?: string | null;
  updated_at?: string | null;
  fixtures?: Record<string, ProspectiveFixtureRecord> | null;
};

type ForecastArchiveDocument = {
  schema_version?: string | null;
  updated_at?: string | null;
  fixture_snapshots?: Array<{
    fixture_key?: string | null;
    fixture?: {
      id?: string | null;
      competition?: string | null;
      kickoff_utc?: string | null;
      home?: string | null;
      away?: string | null;
    } | null;
  }> | null;
  forecasts?: Array<{
    forecast_id?: string | null;
    fixture_key?: string | null;
    fixture_id?: string | null;
    kickoff_utc?: string | null;
    generated_at?: string | null;
    model?: string | null;
    probability_basis?: string | null;
    probabilities?: Partial<Record<OutcomeKey, number | null>> | null;
    expected_goals?: { home?: number | null; away?: number | null } | null;
    model_market_forecasts?: ModelMarketForecast[] | null;
  }> | null;
  results?: Array<{
    fixture_key?: string | null;
    fixture_id?: string | null;
    home_goals_90?: number | null;
    away_goals_90?: number | null;
    outcome?: OutcomeKey | string | null;
  }> | null;
};

type ArchiveRow = {
  id: string;
  home: string;
  away: string;
  kickoffUtc: string;
  competition: string;
  model: string;
  cohortId: string;
  predicted: OutcomeKey | null;
  predictedProbability: number | null;
  probabilities: Record<OutcomeKey, number> | null;
  actual: OutcomeKey;
  homeGoals: number;
  awayGoals: number;
  correct: boolean | null;
  brier: number | null;
  logloss: number | null;
  marketSelections: Array<ModelMarketForecast & { settlement: "win" | "loss" | "push" }>;
  marketWins: number;
  marketLosses: number;
  marketPushes: number;
  marketBrier: number | null;
};

type LivePayload = {
  generated_at: string;
  status: string;
  betting_gate?: { allowed?: boolean; reason?: string | null } | null;
  prospective_clv?: ProspectiveClvSummary | null;
  validation_protocol?: {
    mode?: string | null;
    model_status?: string | null;
    real_money_execution?: boolean | null;
    parlays?: string | null;
  } | null;
  paper_candidate_ranking?: PaperCandidateRanking | null;
  paper_trading?: PaperTradingSummary | null;
  forecasts: Forecast[];
};

const SITE_DATA_ROOT = "/xg-edge/data";
const DATA_URL = `${SITE_DATA_ROOT}/live_predictions.json`;
const PROSPECTIVE_URL = `${SITE_DATA_ROOT}/prospective_clv.json`;
const FORECAST_ARCHIVE_URL = `${SITE_DATA_ROOT}/forecast_archive.json`;

const FALLBACK: LivePayload = {
  generated_at: "2026-07-21T00:00:00Z",
  status: "offline-empty-fallback",
  betting_gate: { allowed: false, reason: "insufficient_independent_matches" },
  prospective_clv: {
    action: "NO BET",
    reason: "insufficient_independent_matches",
    min_independent_matches: 100,
    clv: {
      mean: null,
      median: null,
      share_positive: null,
      ci_low: null,
      ci_high: null,
      n: 0,
      n_clusters: 0,
      bootstrap_unit: "cluster",
    },
    calibration: { n: 0, mean_logloss: null, mean_brier: null },
    tracked_fixtures: 0,
    shadow_candidates: 0,
  },
  validation_protocol: {
    mode: "PAPER_ONLY",
    model_status: "MODEL_IN_QUARANTINE",
    real_money_execution: false,
    parlays: "SIMULATION_ONLY_DISABLED_PENDING_INDIVIDUAL_EDGE",
  },
  paper_candidate_ranking: {
    status: "PAPER_ONLY",
    real_money_execution: false,
    eligible_matches: 0,
    displayed_candidates: 0,
    candidates: [],
  },
  paper_trading: {
    status: "PAPER_ONLY_EMPTY",
    real_money_execution: false,
    updated_at: "2026-07-21T00:00:00Z",
    starting_balance_rub: 10_000,
    target_balance_rub: 1_000_000,
    totals: {
      strategies: 3,
      enrolled_matches: 0,
      settled_matches: 0,
      open_matches: 0,
      settled_bets: 0,
      open_bets: 0,
    },
    leaderboard: [
      { rank: 1, strategy_id: "conservative_edge_5pp", label: "Только edge от 5 п.п.", equity_balance_rub: 10_000, pnl_rub: 0, roi: 0, max_drawdown: 0, settled_bets: 0, open_bets: 0, wins: 0, losses: 0, cycle_count: 1, ruin_count: 0 },
      { rank: 2, strategy_id: "flat_1pct", label: "Фиксированные 1%", equity_balance_rub: 10_000, pnl_rub: 0, roi: 0, max_drawdown: 0, settled_bets: 0, open_bets: 0, wins: 0, losses: 0, cycle_count: 1, ruin_count: 0 },
      { rank: 3, strategy_id: "fractional_kelly_025", label: "1/4 Kelly, лимит 1%", equity_balance_rub: 10_000, pnl_rub: 0, roi: 0, max_drawdown: 0, settled_bets: 0, open_bets: 0, wins: 0, losses: 0, cycle_count: 1, ruin_count: 0 },
    ],
    selection_policy: {
      minimum_settled_bets_for_full_evidence: 100,
      speed_to_target_used_for_ranking: false,
      strategy_deletion: false,
    },
    parlays: { status: "DISABLED" },
  },
  forecasts: [],
};

const percent = (value?: number | null) =>
  value == null ? "—" : `${(value * 100).toFixed(1)}%`;

const localTime = (iso: string) =>
  new Intl.DateTimeFormat("ru-RU", {
    timeZone: "Asia/Yekaterinburg",
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));

const archiveDate = (iso: string) =>
  new Intl.DateTimeFormat("ru-RU", {
    timeZone: "Asia/Yekaterinburg",
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(new Date(iso));

const competitionName = (name: string) => {
  if (name.includes("World Cup")) return "ЧМ-2026";
  if (name.includes("Champions")) return "Лига чемпионов";
  if (name.includes("Europa League")) return "Лига Европы";
  if (name.includes("Conference League")) return "Лига конференций";
  if (name.includes("Premier")) return "АПЛ";
  if (name.includes("La Liga")) return "Ла Лига";
  if (name.includes("Bundesliga")) return "Бундеслига";
  if (name.includes("Serie A")) return "Серия A";
  if (name.includes("Ligue 1")) return "Лига 1";
  return name;
};

const isTopFiveCompetition = (name: string) =>
  ["Premier League", "La Liga", "Bundesliga", "Serie A", "Ligue 1"]
    .some((competition) => name.includes(competition));

const finiteNumber = (value?: number | null) =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const signedPercent = (value?: number | null) => {
  const safe = finiteNumber(value);
  if (safe == null) return "—";
  const sign = safe > 0 ? "+" : safe < 0 ? "−" : "";
  return `${sign}${Math.abs(safe * 100).toFixed(2)}%`;
};

const rub = (value?: number | null) => {
  const safe = finiteNumber(value);
  return safe == null
    ? "—"
    : new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(safe) + " ₽";
};

const isOutcomeKey = (value?: string | null): value is OutcomeKey =>
  value === "home" || value === "draw" || value === "away";

const outcomeName = (value?: OutcomeKey | null) => ({
  home: "П1",
  draw: "X",
  away: "П2",
}[value || ""] || "—");

const settleModelMarket = (
  forecast: ModelMarketForecast,
  homeGoals: number,
  awayGoals: number,
): "win" | "loss" | "push" | null => {
  const market = forecast.market;
  const selection = forecast.selection;
  const line = finiteNumber(forecast.line);
  if (!market || !selection) return null;
  if (market === "1x2") {
    const actual = homeGoals > awayGoals ? "home" : awayGoals > homeGoals ? "away" : "draw";
    return selection === actual ? "win" : "loss";
  }
  if (market === "btts") {
    const yes = homeGoals > 0 && awayGoals > 0;
    return (selection === "yes") === yes ? "win" : "loss";
  }
  if (market === "double_chance") {
    const won = selection === "home_draw" ? homeGoals >= awayGoals
      : selection === "home_away" ? homeGoals !== awayGoals
        : selection === "draw_away" ? homeGoals <= awayGoals
          : null;
    return won == null ? null : won ? "win" : "loss";
  }
  if (market === "draw_no_bet") {
    if (homeGoals === awayGoals) return "push";
    const actual = homeGoals > awayGoals ? "home" : "away";
    return selection === actual ? "win" : "loss";
  }
  if (line == null) return null;
  if (market === "asian_handicap") {
    const difference = selection === "home" ? homeGoals - awayGoals
      : selection === "away" ? awayGoals - homeGoals
        : null;
    if (difference == null) return null;
    const adjusted = difference + line;
    return adjusted > 0 ? "win" : adjusted < 0 ? "loss" : "push";
  }
  let metric: number;
  let direction: "over" | "under";
  if (market === "totals" && (selection === "over" || selection === "under")) {
    metric = homeGoals + awayGoals;
    direction = selection;
  } else if (market === "team_totals" && /^(home|away)_(over|under)$/.test(selection)) {
    metric = selection.startsWith("home_") ? homeGoals : awayGoals;
    direction = selection.endsWith("over") ? "over" : "under";
  } else {
    return null;
  }
  if (metric === line) return "push";
  return (metric > line) === (direction === "over") ? "win" : "loss";
};

function ProspectiveClvPanel({
  summary,
  forecasts,
}: {
  summary?: ProspectiveClvSummary | null;
  forecasts: Forecast[];
}) {
  const cohortRows = Object.values(summary?.cohorts || {});
  const leading = cohortRows.sort((a, b) =>
    (finiteNumber(b.confirmatory_ready) ?? 0) - (finiteNumber(a.confirmatory_ready) ?? 0) ||
    (finiteNumber(b.tracked_fixtures) ?? 0) - (finiteNumber(a.tracked_fixtures) ?? 0)
  )[0];
  const clv = leading?.clv || summary?.clv;
  const observations = Math.max(0, Math.trunc(finiteNumber(clv?.n) ?? 0));
  const independentMatches = Math.max(0, Math.trunc(finiteNumber(clv?.n_clusters) ?? observations));
  const minimum = Math.max(1, Math.trunc(finiteNumber(leading?.min_independent_matches) ?? finiteNumber(summary?.min_independent_matches) ?? 100));
  const modelLines = forecasts.reduce(
    (sum, forecast) => sum + (forecast.model_market_forecasts || []).length,
    0,
  );
  const modelPicks = forecasts.reduce(
    (sum, forecast) => sum + (forecast.model_market_forecasts || [])
      .filter((row) => finiteNumber(row.recommendation_rank) != null).length,
    0,
  );
  const haircuts = forecasts.flatMap((forecast) => forecast.model_market_forecasts || [])
    .filter((row) => finiteNumber(row.recommendation_rank) != null)
    .map((row) => finiteNumber(row.reliability_haircut))
    .filter((value): value is number => value != null);
  const minimumHaircut = haircuts.length ? Math.min(...haircuts) : 0.03;
  const maximumHaircut = haircuts.length ? Math.max(...haircuts) : 0.06;

  return (
    <aside className="truth-panel" aria-label="Активность модели и фоновый CLV-аудит">
      <span className="panel-label">Активный режим</span>
      <strong className="gate-open">MODEL FORECAST</strong>
      <p><b>FULL LINE</b> · прогнозы публикуются, CLV проверяется в фоне</p>
      <dl>
        <div><dt>Матчи с моделью</dt><dd>{forecasts.filter((row) => (row.model_market_forecasts || []).length > 0).length}</dd></div>
        <div><dt>Рассчитано рынков</dt><dd>{modelLines}</dd></div>
        <div><dt>Сценариев в топ-3</dt><dd>{modelPicks}</dd></div>
        <div><dt>Поправка надёжности</dt><dd>−{(minimumHaircut * 100).toFixed(0)}…−{(maximumHaircut * 100).toFixed(0)} п.п.</dd></div>
      </dl>
      <small>
        Фоновый CLV-аудит: {independentMatches}/{minimum} независимых матчей, наблюдений {observations}.
        Он влияет на будущую оценку надёжности, но больше не скрывает модельные прогнозы.
      </small>
    </aside>
  );
}

function PaperCandidateBoard({
  ranking,
  forecasts,
  nowMs,
}: {
  ranking?: PaperCandidateRanking | null;
  forecasts: Forecast[];
  nowMs: number;
}) {
  const candidatePool = (ranking?.candidates || [])
    .filter((candidate) => {
      const kickoff = candidate.kickoff_utc ? new Date(candidate.kickoff_utc).getTime() : NaN;
      return Number.isFinite(kickoff) && kickoff > nowMs;
    });
  const valueCandidate = candidatePool.filter((candidate) =>
      (finiteNumber(candidate.odds) || 0) > 1.5 &&
      (finiteNumber(candidate.robust_edge) || 0) > 0
    ).sort((left, right) =>
      (finiteNumber(right.robust_edge) || 0) - (finiteNumber(left.robust_edge) || 0)
    )[0];
  const paperCandidateKey = (candidate: PaperCandidate) =>
    `${candidate.fixture_id}|${candidate.market}|${candidate.selection}|${candidate.line}`;
  const afterValue = candidatePool.filter((candidate) =>
    (finiteNumber(candidate.robust_edge) || 0) > 0 &&
    (!valueCandidate || paperCandidateKey(candidate) !== paperCandidateKey(valueCandidate))
  );
  const balancedCandidate = afterValue.sort((left, right) =>
      Math.abs((finiteNumber(left.odds) || 99) - 1.5) -
      Math.abs((finiteNumber(right.odds) || 99) - 1.5)
    )[0];
  const expressCandidate = afterValue.filter((candidate) =>
    !balancedCandidate || paperCandidateKey(candidate) !== paperCandidateKey(balancedCandidate)
  ).sort((left, right) =>
    Math.abs((finiteNumber(left.odds) || 99) - 1.3) -
    Math.abs((finiteNumber(right.odds) || 99) - 1.3)
  )[0];
  const candidates = [
    valueCandidate && { candidate: valueCandidate, role: "VALUE-ординар · кэф >1.50" },
    balancedCandidate && { candidate: balancedCandidate, role: "Ординар · около 1.50" },
    expressCandidate && { candidate: expressCandidate, role: "Плечо экспресса · около 1.30" },
  ].filter((row): row is { candidate: PaperCandidate; role: string } => Boolean(row));
  const modelPool = forecasts.flatMap((forecast) =>
    (forecast.model_market_forecasts || [])
      .filter((row) => finiteNumber(row.recommendation_rank) != null)
      .map((row) => ({ forecast, row }))
  );
  const modelCandidates = ["VALUE_SINGLE", "BALANCED_SINGLE", "EXPRESS_LEG"].flatMap((role) =>
    modelPool.filter(({ row }) => row.recommendation_role === role)
      .sort((left, right) =>
        (finiteNumber(right.row.conservative_probability) || 0) -
        (finiteNumber(left.row.conservative_probability) || 0)
      )
      .slice(0, 2)
  );
  const hasBookmakerCandidates = candidates.length > 0;
  return (
    <section className="paper-board" id="paper-picks" aria-label="Прогнозные сценарии ближайших матчей">
      <div className="paper-board-heading">
        <div>
          <p className="eyebrow">Полная линия · вероятность со штрафом за надёжность</p>
          <h2>{hasBookmakerCandidates ? "Bookmaker-value кандидаты" : "Сильнейшие модельные сценарии"}</h2>
        </div>
        <span className="paper-only-badge">{hasBookmakerCandidates ? "PAPER VALUE" : "MODEL FORECAST"}</span>
      </div>
      <p className="paper-board-intro">
        Модельные сценарии публикуются сразу и не ждут CLV-гейта. Если API даст свежую котировку,
        блок автоматически переключится на сравнение с ценой букмекера. До этого fair означает только
        консервативную расчётную границу модели.
      </p>
      {hasBookmakerCandidates ? (
        <div className="paper-candidate-list">
          {candidates.map(({ candidate, role }, index) => (
            <a className="paper-candidate" href={`#match-${candidate.fixture_id}`} key={`${candidate.fixture_id}-${candidate.selection}`}>
              <b>#{index + 1} · {role}</b>
              <div>
                <strong>{candidate.home} — {candidate.away}</strong>
                <span>{candidate.selection} · {marketName(candidate.market)}{candidate.line == null ? "" : ` ${candidate.line}`} · {candidate.bookmaker || "букмекер не указан"}</span>
              </div>
              <dl>
                <div><dt>Модель</dt><dd>{percent(candidate.model_probability)}</dd></div>
                <div><dt>Безубыток</dt><dd>{percent(candidate.break_even_probability)}</dd></div>
                <div><dt>Коэф.</dt><dd>{decimal(candidate.odds)}</dd></div>
                <div><dt>Robust EV</dt><dd>{signedPercent(candidate.robust_edge)}</dd></div>
              </dl>
              <small>{candidate.kickoff_utc ? `${localTime(candidate.kickoff_utc)} YEKT` : "время уточняется"}</small>
            </a>
          ))}
        </div>
      ) : modelCandidates.length ? (
        <div className="paper-candidate-list">
          {modelCandidates.map(({ forecast, row }, index) => (
            <a className="paper-candidate" href={`#match-${forecast.id}`} key={`${forecast.id}-${row.market}-${row.selection}-${row.line}`}>
              <b>#{index + 1}</b>
              <div>
                <strong>{forecast.home} — {forecast.away}</strong>
                <span>{recommendationRoleName(row.recommendation_role)} · {row.label} · {marketName(row.market)}</span>
              </div>
              <dl>
                <div><dt>Теория</dt><dd>{percent(row.theoretical_probability)}</dd></div>
                <div><dt>Надёжно</dt><dd>{percent(row.conservative_probability)}</dd></div>
                <div><dt>Мин. кэф</dt><dd>{decimal(row.minimum_market_odds || row.conservative_fair_odds)}</dd></div>
                <div><dt>Штраф</dt><dd>−{((finiteNumber(row.reliability_haircut) || 0) * 100).toFixed(0)} п.п.</dd></div>
              </dl>
              <small>{localTime(forecast.kickoff_utc)} YEKT</small>
            </a>
          ))}
        </div>
      ) : (
        <div className="paper-empty">
          <strong>Матчи загружены, но распределение счёта ещё не рассчитано.</strong>
          <span>Календарная запись без ожидаемых голов не превращается в выдуманную рекомендацию.</span>
        </div>
      )}
    </section>
  );
}

function PaperTradingLab({ summary, forecasts }: { summary?: PaperTradingSummary | null; forecasts: Forecast[] }) {
  const rows = (summary?.leaderboard || []).slice(0, 3);
  const minimum = Math.max(
    1,
    Math.trunc(finiteNumber(summary?.selection_policy?.minimum_settled_bets_for_full_evidence) ?? 100),
  );
  const settled = Math.max(0, Math.trunc(finiteNumber(summary?.totals?.settled_matches) ?? 0));
  const marketRows = Object.entries(summary?.markets || {})
    .filter(([, value]) => (finiteNumber(value.enrolled) ?? 0) > 0)
    .sort(([left], [right]) => left.localeCompare(right));
  const enrolled = Math.max(0, Math.trunc(finiteNumber(summary?.totals?.enrolled_matches) ?? 0));
  const open = Math.max(0, Math.trunc(finiteNumber(summary?.totals?.open_matches) ?? 0));
  const hasLedgerActivity = enrolled > 0 || settled > 0 || open > 0;
  const modelLines = forecasts.reduce((sum, row) => sum + (row.model_market_forecasts || []).length, 0);
  const modelPicks = forecasts.reduce(
    (sum, row) => sum + (row.model_market_forecasts || []).filter((market) => finiteNumber(market.recommendation_rank) != null).length,
    0,
  );
  return (
    <section className="paper-lab" id="paper-bank" aria-label="Турнир PAPER-стратегий">
      <div className="paper-lab-heading">
        <div>
          <p className="eyebrow">Автоматическая виртуальная лаборатория</p>
          <h2>{hasLedgerActivity ? "PAPER-банк и результаты" : "Модель считает сейчас"}</h2>
        </div>
        <div className="paper-lab-status">
          <span>{hasLedgerActivity ? "PAPER BANK" : "MODEL ACTIVE"}</span>
          <b>{hasLedgerActivity ? `${settled} / ${minimum}` : forecasts.length}</b>
          <small>{hasLedgerActivity ? "матчей до полной оценки" : "матчей рассчитано"}</small>
        </div>
      </div>
      <div className="paper-lab-facts">
        <div><span>Старт каждого цикла</span><b>{rub(summary?.starting_balance_rub ?? 10_000)}</b></div>
        <div><span>Цель-диагностика</span><b>{rub(summary?.target_balance_rub ?? 1_000_000)}</b></div>
        <div><span>Матчей в журнале</span><b>{enrolled}</b></div>
        <div><span>Открыто сейчас</span><b>{open}</b></div>
      </div>
      {hasLedgerActivity ? <div className="strategy-board">
        {rows.map((row, index) => {
          const n = Math.max(0, Math.trunc(finiteNumber(row.settled_bets) ?? 0));
          const evidence = Math.min(100, n / minimum * 100);
          return (
            <article key={row.strategy_id || index}>
              <header><b>#{row.rank || index + 1}</b><span>{n < minimum ? "малая выборка" : "полная оценка"}</span></header>
              <h3>{row.label || row.strategy_id || "Стратегия"}</h3>
              <strong>{rub(row.equity_balance_rub)}</strong>
              <dl>
                <div><dt>P&amp;L</dt><dd>{rub(row.pnl_rub)}</dd></div>
                <div><dt>ROI</dt><dd>{signedPercent(row.roi)}</dd></div>
                <div><dt>CLV</dt><dd>{signedPercent(row.mean_clv)}</dd></div>
                <div><dt>Max DD</dt><dd>{percent(row.max_drawdown)}</dd></div>
                <div><dt>W–L</dt><dd>{Math.trunc(finiteNumber(row.wins) ?? 0)}–{Math.trunc(finiteNumber(row.losses) ?? 0)}</dd></div>
                <div><dt>Циклы / крахи</dt><dd>{Math.trunc(finiteNumber(row.cycle_count) ?? 1)} / {Math.trunc(finiteNumber(row.ruin_count) ?? 0)}</dd></div>
              </dl>
              <div className="evidence-track"><i style={{ width: `${evidence}%` }} /></div>
              <small>{n} ставок · открыто {Math.trunc(finiteNumber(row.open_bets) ?? 0)}</small>
            </article>
          );
        })}
      </div> : <div className="model-tracker-active">
        <strong>{forecasts.length} матчей · {modelLines} модельных исходов · {modelPicks} сценариев в топ-3</strong>
        <span>Пустые карточки ROI скрыты: без букмекерской цены нельзя честно рассчитать денежный результат. Модельные прогнозы уже доступны выше и внутри каждого матча.</span>
      </div>}
      <div className="paper-lab-note">
        <p><b>Рынки в журнале:</b> {marketRows.length
          ? marketRows.map(([market, value]) => `${marketName(market)}: ${Math.trunc(finiteNumber(value.settled) ?? 0)}/${Math.trunc(finiteNumber(value.enrolled) ?? 0)}`).join(" · ")
          : `${modelLines} модельных исходов считаются; денежных PAPER-ставок без цены нет`}.</p>
        <p>После разорения новый цикл снова начинается с 10 000 ₽, но проигрыши и прошлые циклы не удаляются. Победитель определяется по CLV, логарифмическому росту и риску, а не по случайной скорости до 1 млн ₽.</p>
        <p><b>Экспрессы: {summary?.parlays?.status || "DISABLED"}.</b> Они останутся только симуляцией и не включатся, пока одиночные ставки не покажут устойчивый prospective CLV.</p>
      </div>
    </section>
  );
}

function CompletedForecastArchive({
  archive,
  ledger,
  status,
}: {
  archive?: ForecastArchiveDocument | null;
  ledger?: ProspectiveLedger | null;
  status: "loading" | "live" | "unavailable";
}) {
  const [archiveQuery, setArchiveQuery] = useState("");
  const [resultFilter, setResultFilter] = useState<"all" | "hit" | "miss">("all");

  const rows = useMemo<ArchiveRow[]>(() => {
    if (archive?.schema_version === "match-evidence-archive/1.0") {
      const fixtures = new Map<string, NonNullable<ForecastArchiveDocument["fixture_snapshots"]>[number]["fixture"]>();
      (archive.fixture_snapshots || []).forEach((snapshot) => {
        if (snapshot.fixture_key && snapshot.fixture) fixtures.set(snapshot.fixture_key, snapshot.fixture);
      });
      const forecasts = new Map<string, NonNullable<ForecastArchiveDocument["forecasts"]>[number]>();
      (archive.forecasts || []).forEach((forecast) => {
        const key = forecast.fixture_key || "";
        if (!key) return;
        const previous = forecasts.get(key);
        if (!previous || String(forecast.generated_at || "") > String(previous.generated_at || "")) {
          forecasts.set(key, forecast);
        }
      });
      return (archive.results || []).flatMap((result) => {
        const key = result.fixture_key || "";
        const forecast = forecasts.get(key);
        const fixture = fixtures.get(key);
        const actual = isOutcomeKey(result.outcome) ? result.outcome : null;
        const homeGoals = finiteNumber(result.home_goals_90);
        const awayGoals = finiteNumber(result.away_goals_90);
        const kickoff = forecast?.kickoff_utc || fixture?.kickoff_utc || "";
        const home = fixture?.home?.trim() || "";
        const away = fixture?.away?.trim() || "";
        if (
          !forecast || !actual || !home || !away ||
          !Number.isInteger(homeGoals) || !Number.isInteger(awayGoals) ||
          (homeGoals ?? -1) < 0 || (awayGoals ?? -1) < 0 ||
          !Number.isFinite(new Date(kickoff).getTime())
        ) return [];
        const pHome = finiteNumber(forecast.probabilities?.home);
        const pDraw = finiteNumber(forecast.probabilities?.draw);
        const pAway = finiteNumber(forecast.probabilities?.away);
        const hasProbabilityVector = pHome != null && pDraw != null && pAway != null &&
          pHome >= 0 && pDraw >= 0 && pAway >= 0 &&
          Math.abs(pHome + pDraw + pAway - 1) <= 0.01;
        const probabilities: Record<OutcomeKey, number> | null = hasProbabilityVector
          ? { home: pHome, draw: pDraw, away: pAway }
          : null;
        const prediction = probabilities
          ? (Object.entries(probabilities) as Array<[OutcomeKey, number]>)
            .sort((left, right) => right[1] - left[1])[0]
          : null;
        const predicted = prediction?.[0] || null;
        const predictedProbability = prediction?.[1] ?? null;
        const brier = probabilities
          ? (Object.entries(probabilities) as Array<[OutcomeKey, number]>)
            .reduce((sum, [outcome, probability]) => sum + (probability - (outcome === actual ? 1 : 0)) ** 2, 0)
          : null;
        const logloss = probabilities ? -Math.log(Math.max(probabilities[actual], 1e-12)) : null;
        const marketSelections = (forecast.model_market_forecasts || []).flatMap((marketForecast) => {
          const settlement = settleModelMarket(
            marketForecast,
            homeGoals as number,
            awayGoals as number,
          );
          return settlement ? [{ ...marketForecast, settlement }] : [];
        });
        const marketWins = marketSelections.filter((row) => row.settlement === "win").length;
        const marketLosses = marketSelections.filter((row) => row.settlement === "loss").length;
        const marketPushes = marketSelections.filter((row) => row.settlement === "push").length;
        const scoredMarkets = marketSelections.filter((row) =>
          row.settlement !== "push" && finiteNumber(row.conservative_probability) != null
        );
        const marketBrier = scoredMarkets.length
          ? scoredMarkets.reduce((sum, row) => {
            const probability = finiteNumber(row.conservative_probability) || 0;
            const actualValue = row.settlement === "win" ? 1 : 0;
            return sum + (probability - actualValue) ** 2;
          }, 0) / scoredMarkets.length
          : null;
        return [{
          id: forecast.forecast_id || result.fixture_id || key,
          home,
          away,
          kickoffUtc: kickoff,
          competition: fixture?.competition?.trim() || "турнир не записан",
          model: forecast.model?.trim() || "модель не записана",
          cohortId: forecast.probability_basis || "archive",
          predicted,
          predictedProbability,
          probabilities,
          actual,
          homeGoals: homeGoals as number,
          awayGoals: awayGoals as number,
          correct: predicted ? predicted === actual : null,
          brier,
          logloss,
          marketSelections,
          marketWins,
          marketLosses,
          marketPushes,
          marketBrier,
        }];
      }).sort((left, right) =>
        new Date(right.kickoffUtc).getTime() - new Date(left.kickoffUtc).getTime()
      );
    }
    const fixtures = ledger?.fixtures;
    if (!fixtures || typeof fixtures !== "object") return [];
    return Object.entries(fixtures).flatMap(([key, record]) => {
      const result = record?.result;
      const homeGoals = finiteNumber(result?.home_goals_90);
      const awayGoals = finiteNumber(result?.away_goals_90);
      const actual = isOutcomeKey(result?.outcome) ? result.outcome : null;
      const kickoff = record?.kickoff_utc || "";
      const home = record?.home?.trim() || "";
      const away = record?.away?.trim() || "";
      const scoreOutcome = homeGoals != null && awayGoals != null
        ? homeGoals > awayGoals ? "home" : awayGoals > homeGoals ? "away" : "draw"
        : null;
      if (
        !actual || scoreOutcome !== actual || !home || !away ||
        !Number.isInteger(homeGoals) || !Number.isInteger(awayGoals) ||
        (homeGoals ?? -1) < 0 || (awayGoals ?? -1) < 0 ||
        !Number.isFinite(new Date(kickoff).getTime())
      ) return [];

      const sourceProbabilities = record?.forecast?.probabilities;
      const pHome = finiteNumber(sourceProbabilities?.home);
      const pDraw = finiteNumber(sourceProbabilities?.draw);
      const pAway = finiteNumber(sourceProbabilities?.away);
      const hasProbabilityVector = pHome != null && pDraw != null && pAway != null &&
        pHome >= 0 && pDraw >= 0 && pAway >= 0 &&
        Math.abs(pHome + pDraw + pAway - 1) <= 0.01;
      const probabilities: Record<OutcomeKey, number> | null = hasProbabilityVector
        ? { home: pHome, draw: pDraw, away: pAway }
        : null;
      const prediction = probabilities
        ? (Object.entries(probabilities) as Array<[OutcomeKey, number]>)
          .sort((left, right) => right[1] - left[1])[0]
        : null;
      const predicted = prediction?.[0] || null;
      const predictedProbability = prediction?.[1] ?? null;
      const brier = finiteNumber(record?.calibration?.brier);
      const logloss = finiteNumber(record?.calibration?.logloss);

      return [{
        id: record.fixture_id || key,
        home,
        away,
        kickoffUtc: kickoff,
        competition: record.forecast?.competition?.trim() || "турнир не записан",
        model: record.forecast?.model?.trim() || "модель не записана",
        cohortId: record.evaluation_cohort_id || "когорта не записана",
        predicted,
        predictedProbability,
        probabilities,
        actual,
        homeGoals: homeGoals as number,
        awayGoals: awayGoals as number,
        correct: predicted ? predicted === actual : null,
        brier: brier != null && brier >= 0 ? brier : null,
        logloss: logloss != null && logloss >= 0 ? logloss : null,
        marketSelections: [],
        marketWins: 0,
        marketLosses: 0,
        marketPushes: 0,
        marketBrier: null,
      }];
    }).sort((left, right) =>
      new Date(right.kickoffUtc).getTime() - new Date(left.kickoffUtc).getTime()
    );
  }, [archive, ledger]);

  const filteredRows = useMemo(() => {
    const needle = archiveQuery.trim().toLocaleLowerCase("ru-RU");
    return rows.filter((row) => {
      const inResult = resultFilter === "all" ||
        (resultFilter === "hit" && row.correct === true) ||
        (resultFilter === "miss" && row.correct === false);
      if (!inResult) return false;
      if (!needle) return true;
      return [row.home, row.away, row.competition, row.model, row.cohortId]
        .join(" ").toLocaleLowerCase("ru-RU").includes(needle);
    });
  }, [archiveQuery, resultFilter, rows]);

  const scoredRows = rows.filter((row) => row.brier != null && row.logloss != null);
  const topOneRows = rows.filter((row) => row.predictedProbability != null && row.correct != null);
  const accuracy = topOneRows.length
    ? topOneRows.filter((row) => row.correct).length / topOneRows.length
    : null;
  const meanConfidence = topOneRows.length
    ? topOneRows.reduce((sum, row) => sum + (row.predictedProbability || 0), 0) / topOneRows.length
    : null;
  const meanBrier = scoredRows.length
    ? scoredRows.reduce((sum, row) => sum + (row.brier || 0), 0) / scoredRows.length
    : null;
  const meanLogloss = scoredRows.length
    ? scoredRows.reduce((sum, row) => sum + (row.logloss || 0), 0) / scoredRows.length
    : null;
  const fullLineRows = rows.filter((row) => row.marketSelections.length > 0);
  const fullLineSelections = fullLineRows.reduce(
    (sum, row) => sum + row.marketWins + row.marketLosses,
    0,
  );
  const fullLineWins = fullLineRows.reduce((sum, row) => sum + row.marketWins, 0);
  const marketScoredRows = fullLineRows.filter((row) => row.marketBrier != null);
  const meanMarketBrier = marketScoredRows.length
    ? marketScoredRows.reduce((sum, row) => sum + (row.marketBrier || 0), 0) /
      marketScoredRows.length
    : null;
  const recommendedSettlements = fullLineRows.flatMap((row) => row.marketSelections)
    .filter((market) => finiteNumber(market.recommendation_rank) != null && market.settlement !== "push");
  const recommendationHitRate = recommendedSettlements.length
    ? recommendedSettlements.filter((market) => market.settlement === "win").length / recommendedSettlements.length
    : null;
  const calibrationGap = topOneRows.length >= 30 && accuracy != null && meanConfidence != null
    ? accuracy - meanConfidence
    : null;
  const visibleRows = filteredRows.slice(0, 100);
  const sourceUpdated = archive?.updated_at && Number.isFinite(new Date(archive.updated_at).getTime())
    ? `${new Date(archive.updated_at).toLocaleString("ru-RU", { timeZone: "Asia/Yekaterinburg" })} YEKT`
    : ledger?.updated_at && Number.isFinite(new Date(ledger.updated_at).getTime())
      ? `${new Date(ledger.updated_at).toLocaleString("ru-RU", { timeZone: "Asia/Yekaterinburg" })} YEKT`
    : "—";

  return (
    <section className="archive-section" id="completed-archive" aria-label="Архив завершённых прогнозов">
      <div className="archive-heading">
        <div>
          <p className="eyebrow">Predicted vs actual · официальный результат за 90 минут</p>
          <h2>Архив проверенных<br />прогнозов</h2>
        </div>
        <span className="paper-only-badge">PAPER ONLY</span>
      </div>
      <p className="archive-intro">
        Здесь остаются только прогнозы, сохранённые до матча и затем закрытые официальным результатом.
        Записи берутся из контролируемого prospective-журнала: исходный прогноз после старта не перезаписывается,
        а сайт не скрывает проигрыши и не создаёт результаты.
      </p>

      <div className="archive-metrics" aria-label="Качество прогнозов на завершённых матчах">
        <div><span>Завершено</span><b>{rows.length || "—"}</b><small>официально закрытых матчей</small></div>
        <div><span>Top-1 1X2</span><b>{percent(accuracy)}</b><small>угадан самый вероятный исход · n={topOneRows.length}</small></div>
        <div><span>Mean Brier</span><b>{meanBrier == null ? "—" : meanBrier.toFixed(3)}</b><small>ниже лучше · диапазон 0–2</small></div>
        <div><span>Mean log loss</span><b>{meanLogloss == null ? "—" : meanLogloss.toFixed(3)}</b><small>ниже лучше · n={scoredRows.length}</small></div>
        <div><span>Вся линия</span><b>{fullLineSelections ? percent(fullLineWins / fullLineSelections) : "—"}</b><small>попадания без возвратов · n={fullLineSelections}</small></div>
        <div><span>Top-3 model</span><b>{percent(recommendationHitRate)}</b><small>рекомендованные сценарии · n={recommendedSettlements.length}</small></div>
        <div><span>Full-line Brier</span><b>{meanMarketBrier == null ? "—" : meanMarketBrier.toFixed(3)}</b><small>все сохранённые голевые рынки</small></div>
        <div><span>Top-1 calib. gap</span><b>{signedPercent(calibrationGap)}</b><small>{topOneRows.length < 30 ? `покажется после 30 матчей · сейчас ${topOneRows.length}` : "точность минус средняя уверенность"}</small></div>
      </div>

      <div className="archive-controls">
        <label htmlFor="archive-search">Поиск в архиве</label>
        <div className="archive-search-box">
          <input id="archive-search" type="search" value={archiveQuery} onChange={(event) => setArchiveQuery(event.target.value)} placeholder="Команда, турнир, модель или когорта" />
          <button type="button" onClick={() => setArchiveQuery("")} disabled={!archiveQuery}>Очистить</button>
        </div>
        <div className="archive-result-filter" role="group" aria-label="Фильтр попаданий">
          {([[
            "all", "Все",
          ], ["hit", "Попал"], ["miss", "Мимо"]] as const).map(([value, label]) => (
            <button type="button" key={value} className={resultFilter === value ? "active" : ""} onClick={() => setResultFilter(value)}>{label}</button>
          ))}
        </div>
        <span>{filteredRows.length} найдено</span>
      </div>

      {visibleRows.length ? (
        <div className="archive-table" role="table" aria-label="Predicted versus actual">
          <div className="archive-table-head" role="row">
            <span>Дата</span><span>Матч</span><span>Прогноз + вся линия</span><span>Факт 90&apos;</span><span>Brier</span><span>Log loss</span>
          </div>
          {visibleRows.map((row) => (
            <div className="archive-table-row" role="row" key={row.id}>
              <time className="archive-date" dateTime={row.kickoffUtc}><b>{archiveDate(row.kickoffUtc)}</b><small>{localTime(row.kickoffUtc).split(", ").at(-1)} YEKT</small></time>
              <span className="archive-match"><b>{row.home} — {row.away}</b><small>{competitionName(row.competition)} · {row.model}</small></span>
              <span className="archive-prediction">
                <b>{outcomeName(row.predicted)} · {percent(row.predictedProbability)}</b>
                <small>{row.probabilities ? `П1 ${percent(row.probabilities.home)} · X ${percent(row.probabilities.draw)} · П2 ${percent(row.probabilities.away)}` : "вектор вероятностей не прошёл проверку"}</small>
                <small className="archive-full-line">
                  {row.marketSelections.length
                    ? `Вся линия: ${row.marketWins} выиграло · ${row.marketLosses} проиграло · ${row.marketPushes} возврат`
                    : "Старый архив: полная линия ещё не сохранялась"}
                </small>
                {row.marketSelections.some((market) => finiteNumber(market.recommendation_rank) != null) && (
                  <small className="archive-recommendations">
                    Top-3: {row.marketSelections
                      .filter((market) => finiteNumber(market.recommendation_rank) != null)
                      .sort((left, right) => (finiteNumber(left.recommendation_rank) || 99) - (finiteNumber(right.recommendation_rank) || 99))
                      .map((market) => `${market.label} ${market.settlement === "win" ? "✓" : market.settlement === "push" ? "↔" : "✕"}`)
                      .join(" · ")}
                  </small>
                )}
              </span>
              <span className="archive-actual"><b>{row.homeGoals}:{row.awayGoals} · {outcomeName(row.actual)}</b><small className={row.correct ? "archive-hit" : row.correct === false ? "archive-miss" : ""}>{row.correct == null ? "top-1 недоступен" : row.correct ? "top-1 угадан" : "top-1 не угадан"}</small></span>
              <span className="archive-score archive-brier">{row.brier == null ? "—" : row.brier.toFixed(3)}</span>
              <span className="archive-score archive-logloss">{row.logloss == null ? "—" : row.logloss.toFixed(3)}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="archive-empty">
          <strong>{rows.length
            ? "По этому запросу завершённых матчей нет."
            : status === "loading"
              ? "Загружаем prospective-журнал…"
              : status === "unavailable"
                ? "Prospective-журнал сейчас недоступен."
                : "Архив пока пуст."}</strong>
          <span>{rows.length
            ? "Измените поиск или фильтр попаданий."
            : status === "live"
              ? "Ни один прогноз ещё не закрыт официальным результатом. Метрики показаны как «—», а не как нулевой успех."
              : "Сайт не подставляет демонстрационные матчи: дождитесь следующего автоматического обновления."}</span>
        </div>
      )}
      {filteredRows.length > visibleRows.length && <p className="archive-limit">Показаны последние 100 из {filteredRows.length}; поиск работает по всему журналу.</p>}
      <p className="archive-source">Источник: {archive?.schema_version || ledger?.schema_version || "match-evidence-archive/1.0 (ожидается)"} · обновлён {sourceUpdated}. Brier и log loss оценивают качество вероятностей; top-1 gap — только диагностический срез, а не полная reliability-кривая. Эти метрики сами по себе не доказывают прибыльность или CLV.</p>
    </section>
  );
}

function ProbabilityBar({ label, value }: { label: string; value?: number | null }) {
  const width = value == null ? 0 : Math.max(2, value * 100);
  return (
    <div className="probability-row">
      <span>{label}</span>
      <div className="probability-track" aria-hidden="true">
        <i style={{ width: `${width}%` }} />
      </div>
      <strong>{percent(value)}</strong>
    </div>
  );
}

const decimal = (value?: number | null, digits = 2) =>
  value == null ? "—" : value.toFixed(digits);

const marketName = (value?: string | null) => ({
  "1x2": "1X2",
  totals: "тотал",
  team_totals: "инд. тотал",
  btts: "ОЗ",
  asian_handicap: "азиатская фора",
  double_chance: "двойной шанс",
  draw_no_bet: "DNB",
}[String(value || "1x2")] || value || "рынок");

const recommendationRoleName = (role?: string | null) => ({
  VALUE_SINGLE: "VALUE-ординар · кэф >1.50",
  BALANCED_SINGLE: "Ординар · около 1.50",
  EXPRESS_LEG: "Плечо экспресса · около 1.30",
}[role || ""] || "Модельный кандидат");

const selectPricedCandidateRoles = (source: CandidateBet[]) => {
  const pool = source.filter((candidate) => (finiteNumber(candidate.point_edge) || 0) > 0);
  const selected: Array<{ bet: CandidateBet; role: string }> = [];
  const used = new Set<CandidateBet>();
  const pick = (
    role: string,
    eligible: (candidate: CandidateBet) => boolean,
    compare: (left: CandidateBet, right: CandidateBet) => number,
  ) => {
    const candidate = pool.filter((row) => !used.has(row) && eligible(row)).sort(compare)[0];
    if (candidate) {
      used.add(candidate);
      selected.push({ bet: candidate, role });
    }
  };
  pick(
    "VALUE-ординар · кэф >1.50",
    (candidate) => (finiteNumber(candidate.market_odds) || 0) > 1.5,
    (left, right) => (finiteNumber(right.point_edge) || 0) - (finiteNumber(left.point_edge) || 0),
  );
  pick(
    "Ординар · около 1.50",
    () => true,
    (left, right) =>
      Math.abs((finiteNumber(left.market_odds) || 99) - 1.5) -
      Math.abs((finiteNumber(right.market_odds) || 99) - 1.5) ||
      (finiteNumber(right.point_edge) || 0) - (finiteNumber(left.point_edge) || 0),
  );
  pick(
    "Плечо экспресса · около 1.30",
    () => true,
    (left, right) =>
      Math.abs((finiteNumber(left.market_odds) || 99) - 1.3) -
      Math.abs((finiteNumber(right.market_odds) || 99) - 1.3) ||
      (finiteNumber(right.probability) || 0) - (finiteNumber(left.probability) || 0),
  );
  return selected;
};

const levelName = (value?: string | null) => ({
  elite: "элитный", strong: "сильный", average: "средний", developing: "развивающийся",
  high: "высокий", medium: "средний", low: "низкий",
}[value || ""] || value || "не оценён");

const positionName = (value?: string | null) => ({
  GOALKEEPER: "вр",
  DEFENDER: "защ",
  MIDFIELDER: "пз",
  FORWARD: "нап",
}[String(value || "").toUpperCase()] || "позиция —");

const marketSnapshotReason = (status?: string | null, reason?: string | null) => {
  const explanations: Record<string, string> = {
    missing_received_at: "у снимка отсутствует время получения",
    missing_kickoff: "не подтверждено время начала матча",
    captured_at_or_after_kickoff: "снимок получен во время или после начала матча",
    captured_before_forecast: "снимок старше опубликованного прогноза",
    captured_in_future: "время снимка находится в будущем",
    older_than_ttl: "снимок старше допустимого TTL",
    incomplete_1x2: "нет полного набора цен П1/X/П2",
    invalid_forecast_probabilities: "вероятности прогноза не прошли проверку",
  };
  if (reason) return `${explanations[reason] || "снимок не прошёл проверку"} (${reason})`;
  if (status === "STALE") return "снимок устарел, код причины не передан";
  if (status === "REJECTED") return "снимок отклонён, код причины не передан";
  return "у SHADOW_ONLY-снимка отсутствует captured_at_utc";
};

function BookmakerSnapshot({ details }: { details?: MatchDetails | null }) {
  const snapshot = details?.market_snapshot;
  if (!snapshot) return null;

  const capturedAt = snapshot.captured_at_utc?.trim() || null;
  const eligibleFresh = snapshot.status === "SHADOW_ONLY" && capturedAt != null;
  const booksValue = finiteNumber(snapshot.bookmakers);
  const books = booksValue == null ? null : Math.max(0, Math.trunc(booksValue));
  const outcomes = [
    ["home", "П1"],
    ["draw", "X"],
    ["away", "П2"],
  ] as const;
  const priceRows = outcomes.flatMap(([key, label]) => {
    const price = snapshot.best_1x2?.[key];
    return finiteNumber(price?.odds) == null ? [] : [{ key, label, price }];
  });
  const shadowCandidates = (details?.market_candidates || [])
    .filter((candidate) => candidate.status === "SHADOW_ONLY")
    .slice(0, 3);
  const expandedCandidates = (details?.expanded_market_candidates || [])
    .filter((candidate) => candidate.status === "EXPERIMENTAL_SHADOW")
    .slice(0, 3);
  const totals = (snapshot.best_totals || []).filter((row) =>
    finiteNumber(row.line) != null && finiteNumber(row.over?.odds) != null && finiteNumber(row.under?.odds) != null
  );
  const spreads = (snapshot.best_spreads || []).filter((row) =>
    finiteNumber(row.line) != null && finiteNumber(row.home?.odds) != null && finiteNumber(row.away?.odds) != null
  );
  const btts = snapshot.best_btts;
  const hasBtts = finiteNumber(btts?.yes?.odds) != null && finiteNumber(btts?.no?.odds) != null;
  const hiddenReason = eligibleFresh
    ? "нет полного проверенного набора цен П1/X/П2 (incomplete_1x2)"
    : marketSnapshotReason(snapshot.status, snapshot.reason);

  return (
    <>
      <section className={`market-snapshot market-snapshot-${String(snapshot.status || "unknown").toLowerCase()}`}>
        <div className="dossier-title">
          <h4>Снимок рынка</h4>
          <span className={eligibleFresh ? "shadow-badge" : "snapshot-blocked"}>
            {snapshot.status || "UNKNOWN"}{eligibleFresh ? " · НЕ РЕКОМЕНДАЦИЯ" : ""}
          </span>
        </div>
        <dl className="snapshot-meta">
          <div><dt>captured_at_utc</dt><dd>{capturedAt ? <time dateTime={capturedAt}>{capturedAt}</time> : "отсутствует"}</dd></div>
          <div><dt>books</dt><dd>{books == null ? "не указано" : books}</dd></div>
          <div><dt>provider</dt><dd>{snapshot.source_provider || "не указан"}</dd></div>
        </dl>
        {eligibleFresh && priceRows.length === 3 ? (
          <div className="snapshot-prices" aria-label="Зафиксированные цены 1X2">
            {priceRows.map(({ key, label, price }) => (
              <div key={key}>
                <b>{label}</b>
                <strong>{decimal(price.odds)}</strong>
                <span>{price.bookmaker || price.bookmaker_key || "book не указан"}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="snapshot-warning">Цены и shadow-кандидаты скрыты: {hiddenReason}.</p>
        )}
        {eligibleFresh && priceRows.length === 3 && (
          <p className="audit-note">Это зафиксированный предматчевый SHADOW_ONLY-снимок, а не текущая цена.</p>
        )}

        {eligibleFresh && (totals.length > 0 || hasBtts || spreads.length > 0) && (
          <div className="expanded-market-board">
            <h5>Дополнительные рынки</h5>
            <div className="market-line-grid">
              {totals.map((row) => (
                <div key={`total-${row.line}`}>
                  <b>Тотал {decimal(row.line, 1)}</b>
                  <span>ТБ <strong>{decimal(row.over?.odds)}</strong> · {row.over?.bookmaker || row.over?.bookmaker_key || "book —"}</span>
                  <span>ТМ <strong>{decimal(row.under?.odds)}</strong> · {row.under?.bookmaker || row.under?.bookmaker_key || "book —"}</span>
                </div>
              ))}
              {hasBtts && (
                <div>
                  <b>Обе забьют</b>
                  <span>Да <strong>{decimal(btts?.yes?.odds)}</strong> · {btts?.yes?.bookmaker || btts?.yes?.bookmaker_key || "book —"}</span>
                  <span>Нет <strong>{decimal(btts?.no?.odds)}</strong> · {btts?.no?.bookmaker || btts?.no?.bookmaker_key || "book —"}</span>
                </div>
              )}
              {spreads.map((row) => (
                <div key={`spread-${row.line}`}>
                  <b>Азиатская фора хозяев {finiteNumber(row.line)! > 0 ? "+" : ""}{decimal(row.line, 1)}</b>
                  <span>Хозяева <strong>{decimal(row.home?.odds)}</strong> · {row.home?.bookmaker || row.home?.bookmaker_key || "book —"}</span>
                  <span>Гости <strong>{decimal(row.away?.odds)}</strong> · {row.away?.bookmaker || row.away?.bookmaker_key || "book —"}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      {eligibleFresh && priceRows.length === 3 && shadowCandidates.length > 0 && (
        <section className="shadow-candidate-section">
          <div className="dossier-title">
            <h4>Топ-3 shadow-кандидата</h4>
            <span className="shadow-badge">SHADOW_ONLY · НЕ РЕКОМЕНДАЦИЯ</span>
          </div>
          <div className="candidate-grid">
            {shadowCandidates.map((candidate, index) => (
              <div key={`${candidate.selection}-${candidate.bookmaker_key}-${index}`}>
                <b>#{candidate.rank || index + 1} · {candidate.selection || "исход не указан"}</b>
                <span>Вероятность {percent(candidate.probability)}</span>
                <span>Fair {decimal(candidate.fair_odds)} · снимок {decimal(candidate.market_odds)}</span>
                <span>{candidate.bookmaker || candidate.bookmaker_key || "book не указан"}</span>
                <strong className={(candidate.point_edge || 0) > 0 ? "positive-edge" : "negative-edge"}>
                  shadow edge {candidate.point_edge == null ? "—" : `${(candidate.point_edge * 100).toFixed(1)}%`}
                </strong>
              </div>
            ))}
          </div>
          <p className="audit-note">Автоматические кандидаты ведутся отдельно от ручного списка и используются только для prospective CLV-аудита.</p>
        </section>
      )}

      {eligibleFresh && expandedCandidates.length > 0 && (
        <section className="shadow-candidate-section expanded-candidate-section">
          <div className="dossier-title">
            <h4>Тоталы и ОЗ: модель против цены</h4>
            <span className="shadow-badge">EXPERIMENTAL SHADOW</span>
          </div>
          <div className="candidate-grid">
            {expandedCandidates.map((candidate, index) => (
              <div key={`${candidate.market}-${candidate.selection}-${candidate.bookmaker_key}-${index}`}>
                <b>#{candidate.rank || index + 1} · {candidate.selection || "рынок не указан"}</b>
                <span>Вероятность модели {percent(candidate.probability)}</span>
                <span>Fair {decimal(candidate.fair_odds)} · снимок {decimal(candidate.market_odds)}</span>
                <span>{candidate.bookmaker || candidate.bookmaker_key || "book не указан"}</span>
                <strong className={(candidate.point_edge || 0) > 0 ? "positive-edge" : "negative-edge"}>
                  shadow EV {candidate.point_edge == null ? "—" : `${(candidate.point_edge * 100).toFixed(1)}%`}
                </strong>
              </div>
            ))}
          </div>
          <p className="audit-note">Эти рынки ещё не допущены к ставкам: сначала нужен отдельный prospective CLV по каждой линии. Положительный расчётный EV не равен доказанной прибыли.</p>
        </section>
      )}
    </>
  );
}

function ModelMarketBoard({ forecast }: { forecast: Forecast }) {
  const rows = (forecast.model_market_forecasts || [])
    .filter((row) =>
      row.label &&
      finiteNumber(row.theoretical_probability) != null &&
      finiteNumber(row.conservative_probability) != null &&
      finiteNumber(row.conservative_fair_odds) != null
    );
  if (!rows.length) return null;
  const recommendations = rows
    .filter((row) => finiteNumber(row.recommendation_rank) != null)
    .sort((left, right) => (finiteNumber(left.recommendation_rank) || 99) - (finiteNumber(right.recommendation_rank) || 99))
    .slice(0, 3);
  const markets = Array.from(new Set(rows.map((row) => row.market).filter(Boolean)));
  const haircut = finiteNumber(rows[0]?.reliability_haircut);

  return (
    <section className="model-market-section" aria-label="Полная модельная линия">
      <div className="dossier-title">
        <h4>Модельные рекомендации по полной линии</h4>
        <span className="model-forecast-badge">MODEL FORECAST</span>
      </div>
      <p className="model-market-intro">
        Вероятность для решения уже уменьшена на {haircut == null ? "несколько" : (haircut * 100).toFixed(0)} п.п.
        из-за неопределённости. Без реальной котировки это лист ожидания: ставка появляется только
        если цена букмекера не ниже указанного минимального коэффициента.
      </p>
      <div className="candidate-grid model-recommendation-grid">
        {recommendations.map((row, index) => (
          <div key={`${row.market}-${row.selection}-${row.line}-${index}`}>
            <b>#{row.recommendation_rank || index + 1} · {recommendationRoleName(row.recommendation_role)}</b>
            <span><strong>{row.label}</strong></span>
            <span>{marketName(row.market)} · 90 минут</span>
            <span>Теория {percent(row.theoretical_probability)}</span>
            <strong>Консервативно {percent(row.conservative_probability)}</strong>
            <span>Искомый кэф ≈ {decimal(row.target_market_odds)}</span>
            <span>Ставить только от {decimal(row.minimum_market_odds || row.conservative_fair_odds)}</span>
          </div>
        ))}
      </div>
      <details className="full-model-line">
        <summary>Открыть всю линию · {rows.length} исходов · {markets.length} типов рынка</summary>
        <div className="market-line-grid">
          {rows.map((row, index) => (
            <div key={`${row.market}-${row.selection}-${row.line}-${index}`}>
              <b>{row.label}</b>
              <span>{marketName(row.market)}</span>
              <span>Теория <strong>{percent(row.theoretical_probability)}</strong></span>
              <span>После штрафа <strong>{percent(row.conservative_probability)}</strong></span>
              <span>Мин. безубыточный кэф <strong>{decimal(row.conservative_fair_odds)}</strong></span>
            </div>
          ))}
        </div>
      </details>
      <p className="audit-note">
        Это автоматический прогноз модели для проверки результатом. Когда появится реальная цена,
        отдельный bookmaker-value блок сравнит её с этой консервативной вероятностью.
      </p>
    </section>
  );
}

function ScoreDistribution({ forecast }: { forecast: Forecast }) {
  const scenarios = (forecast.score_scenarios || [])
    .filter((row) => row.score && finiteNumber(row.probability) != null)
    .slice(0, 5);
  const totalHistoryMatches = (forecast.expected_goals_basis?.team_histories_used || [])
    .reduce((sum, row) => sum + (finiteNumber(row.matches) || 0), 0);
  if (!scenarios.length && forecast.expected_goals?.total == null) return null;
  return (
    <section className="score-distribution">
      <div className="dossier-title">
        <h4>Сценарии счёта — не точный прогноз</h4>
        <span>90 минут · полное распределение</span>
      </div>
      {forecast.expected_goals && (
        <>
          <div className="expected-goals-strip">
            <div><b>{forecast.home}</b><strong>{decimal(forecast.expected_goals.home)}</strong></div>
            <div><b>Ожидаемый тотал</b><strong>{decimal(forecast.expected_goals.total)}</strong></div>
            <div><b>{forecast.away}</b><strong>{decimal(forecast.expected_goals.away)}</strong></div>
          </div>
          {forecast.expected_goals_basis?.method === "official_uefa_recent_totals_bayesian_shrinkage" && (
            <p className="audit-note">
              Тотал матча индивидуальный: использовано {totalHistoryMatches} последних официальных
              командных наблюдений, затем применено сжатие к базовому среднему
              {" "}{decimal(forecast.expected_goals_basis.prior_total_goals)}.
            </p>
          )}
        </>
      )}
      {scenarios.length > 0 && (
        <div className="score-scenario-grid">
          {scenarios.map((row, index) => (
            <div key={`${row.score}-${index}`}>
              <b>#{index + 1}</b><strong>{row.score}</strong><span>{percent(row.probability)}</span>
            </div>
          ))}
          <div className="all-other-scores">
            <b>Остальные счета</b><strong>{percent(forecast.other_score_probability)}</strong>
          </div>
        </div>
      )}
      {forecast.p_over35 != null && forecast.p_over45 != null && (
        <div className="tail-research-strip">
          <div><b>ТБ 3.5 · raw</b><strong>{percent(forecast.p_over35)}</strong></div>
          <div><b>ТБ 4.5 · raw</b><strong>{percent(forecast.p_over45)}</strong></div>
          <p>Пуассоновский хвост не откалиброван по прямым линиям 3.5/4.5 и не участвует в отборе ставок.</p>
        </div>
      )}
      <p className="audit-note">
        Даже верхний сценарий имеет вероятность только {percent(forecast.top_score_probability)}.
        Пять показанных сценариев покрывают {percent(forecast.score_scenarios_coverage)}; остальная масса распределена между другими счетами.
      </p>
    </section>
  );
}

const shortMatchDate = (value?: string | null) => {
  if (!value) return "дата —";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? "дата —"
    : new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit" }).format(parsed);
};

const resultLetter = (result?: string | null) => ({ win: "В", draw: "Н", loss: "П" }[result || ""] || "—");
const resultClass = (result?: string | null) =>
  result === "win" ? "form-win" : result === "draw" ? "form-draw" : result === "loss" ? "form-loss" : "form-unknown";

const confirmedMean = (matches: RecentMatch[], metric: "non_penalty" | "red_and_opponent_adjusted_npxg") => {
  const values = matches
    .map((match) => finiteNumber(match.xg?.[metric]?.value))
    .filter((value): value is number => value != null);
  return values.length ? { value: values.reduce((sum, value) => sum + value, 0) / values.length, n: values.length } : null;
};

function RecentFormHeader({ forecast }: { forecast: Forecast }) {
  const teams = [
    { side: "home", name: forecast.home, detail: forecast.details?.teams?.home },
    { side: "away", name: forecast.away, detail: forecast.details?.teams?.away },
  ] as const;
  return (
    <section className="recent-form-header">
      <div className="dossier-title">
        <h4>Последние официальные матчи</h4>
        <span>товарищеские исключены · до начала матча</span>
      </div>
      <div className="form-team-grid">
        {teams.map(({ side, name, detail }) => {
          const matches = (detail?.recent_matches || []).slice(0, 5);
          const npxg = confirmedMean(matches, "non_penalty");
          const adjusted = confirmedMean(matches, "red_and_opponent_adjusted_npxg");
          return (
            <article key={side}>
              <header><b>{detail?.name || name}</b><span>Elo {detail?.elo == null ? "—" : Math.round(detail.elo)}</span></header>
              {matches.length > 0 ? (
                <>
                  <div className="form-strip" aria-label={`Форма ${name}: последние ${matches.length} официальных матчей`}>
                    {matches.map((match) => (
                      <span className={resultClass(match.result_90)} key={match.match_id} title={`${match.opponent || "Соперник"} ${match.score_90 ? `${match.score_90.for}:${match.score_90.against}` : ""}`}>
                        {resultLetter(match.result_90)}
                      </span>
                    ))}
                  </div>
                  <div className="form-results">
                    {matches.map((match) => (
                      <div key={match.match_id}>
                        <time dateTime={match.kickoff_utc}>{shortMatchDate(match.kickoff_utc)}</time>
                        <small>{match.venue === "away" ? "в гостях" : match.venue === "home" ? "дома" : "поле —"}</small>
                        <b>{match.opponent || "Соперник"}</b>
                        <strong>{match.score_90 ? `${match.score_90.for}:${match.score_90.against}` : "—"}</strong>
                      </div>
                    ))}
                  </div>
                </>
              ) : <p className="unknown-data">Подтверждённая официальная история пока не загружена.</p>}
              <dl className="form-xg-summary">
                <div><dt>Средний npxG</dt><dd>{npxg ? `${decimal(npxg.value)} · ${npxg.n}/${matches.length}` : "нет event-data"}</dd></div>
                <div><dt>npxG* КК+Elo</dt><dd>{adjusted ? `${decimal(adjusted.value)} · ${adjusted.n}/${matches.length}` : "нет event-data"}</dd></div>
              </dl>
            </article>
          );
        })}
      </div>
      <p className="audit-note">npxG убирает пенальти; npxG* дополнительно учитывает подтверждённые красные карточки и силу соперника. Ошибки вратаря/защиты исключаются только при наличии event-level тега — счёт не превращается в выдуманный xG.</p>
    </section>
  );
}

function ModelAnalysisBrief({ forecast }: { forecast: Forecast }) {
  const probabilities = [
    { name: forecast.home, value: finiteNumber(forecast.p_home) },
    { name: "ничья", value: finiteNumber(forecast.p_draw) },
    { name: forecast.away, value: finiteNumber(forecast.p_away) },
  ].filter((row): row is { name: string; value: number } => row.value != null)
    .sort((a, b) => b.value - a.value);
  const leader = probabilities[0];
  const margin = leader && probabilities[1] ? leader.value - probabilities[1].value : null;
  const stage = `${forecast.competition} ${forecast.stage}`.toLocaleLowerCase("ru-RU");
  const highStakes = /квалиф|qualification|play.?off|плей.?офф|полуфин|semifinal|финал|final/.test(stage);
  const firstLeg = Boolean(forecast.first_leg && /first|1st|перв/i.test(forecast.first_leg));
  const homeHistory = forecast.details?.teams?.home?.recent_matches || [];
  const awayHistory = forecast.details?.teams?.away?.recent_matches || [];
  const knownXg = [...homeHistory.slice(0, 5), ...awayHistory.slice(0, 5)]
    .filter((match) => finiteNumber(match.xg?.non_penalty?.value) != null).length;
  const lineupKnown = [forecast.details?.teams?.home, forecast.details?.teams?.away]
    .every((team) => (team?.likely_lineup || []).some((player) => player.status === "starter"));
  const ratingRows = [
    { name: forecast.home, rating: forecast.rating_basis?.home },
    { name: forecast.away, rating: forecast.rating_basis?.away },
  ];
  const coldStartTeams = ratingRows
    .filter(({ rating }) => rating?.source === "uefa_cold_start_prior")
    .map(({ name }) => name);
  const ratingDescription = ratingRows.map(({ name, rating }) => {
    const source = rating?.source === "clubelo"
      ? "ClubElo"
      : rating?.source === "uefa_official_results"
        ? `UEFA Elo · ${Math.trunc(finiteNumber(rating.matches) || 0)} матч.`
        : rating?.source === "uefa_cold_start_prior"
          ? "нейтральный prior · истории нет"
          : "источник не указан";
    return `${name}: ${finiteNumber(rating?.elo) == null ? "Elo —" : `Elo ${Math.round(rating!.elo!)}`} (${source})`;
  }).join(" · ");
  const summary = leader
    ? `${leader.name === "ничья" ? "Самый вероятный отдельный исход — ничья" : `Модель отдаёт первое место исходу «${leader.name}»`} (${percent(leader.value)}), ${margin != null && margin < 0.08 ? "но преимущество небольшое — матч близкий" : "с заметным отрывом от следующего исхода"}.`
    : "Фундаментальная вероятность ещё не выпущена: данных недостаточно для численного вывода.";
  const goals = forecast.p_over25 == null
    ? "Тотальный рынок пока не рассчитан."
    : forecast.p_over25 >= 0.58
      ? `Профиль тяготеет к ТБ 2.5 (${percent(forecast.p_over25)}), но цена должна пройти отдельный CLV-фильтр.`
      : forecast.p_over25 <= 0.42
        ? `Профиль тяготеет к ТМ 2.5 (${percent(1 - forecast.p_over25)}), без статуса готовой ставки.`
        : `По тоталу 2.5 явного перевеса нет: ТБ ${percent(forecast.p_over25)}, ТМ ${percent(1 - forecast.p_over25)}.`;
  const risks = [
    homeHistory.length + awayHistory.length === 0 ? "официальная история команд пока не загружена" : null,
    knownXg < 6 && homeHistory.length + awayHistory.length > 0 ? `event-level npxG подтверждён лишь для ${knownXg} из ${Math.min(10, homeHistory.length + awayHistory.length)} последних показанных матчей` : null,
    !lineupKnown ? "составы предварительные или ещё не опубликованы" : null,
    coldStartTeams.length ? `нейтральный стартовый Elo без истории: ${coldStartTeams.join(", ")}` : null,
    forecast.details?.referee?.name ? null : "назначение судьи не подтверждено",
    forecast.details?.weather?.temperature_c == null ? "погода не подтверждена" : null,
    forecast.details?.tail_risk?.label ? `tail risk: ${levelName(forecast.details.tail_risk.label)}` : "tail risk не оценён",
  ].filter((risk): risk is string => Boolean(risk));
  return (
    <section className="model-analysis-brief">
      <div className="dossier-title"><h4>Алгоритмический разбор</h4><span>без выдуманных новостей и инсайдов</span></div>
      <div className="analysis-columns">
        <div><b>Сценарий матча</b><p>{summary} {goals} ОЗ — {percent(forecast.p_btts)}.</p></div>
        <div><b>Мотивация</b><p>{highStakes ? "Турнирная стадия предполагает высокую структурную мотивацию." : "По одной стадии мотивацию подтвердить нельзя."} {firstLeg ? "Первый матч пары повышает риск осторожного темпа." : "Точный стимул по таблице, ротации и заявлениям тренера пока не подтверждён."}</p></div>
        <div><b>Главные риски</b>{risks.length ? <ul>{risks.slice(0, 4).map((risk) => <li key={risk}>{risk}</li>)}</ul> : <p>Критических пробелов в загруженном срезе не обнаружено.</p>}</div>
      </div>
      {forecast.rating_basis && <p className="audit-note">Источник силы команд: {ratingDescription}.</p>}
      <p className="audit-note">Текст собран детерминированно из вероятностей, Elo, официальной формы и аудита качества. Он не подменяет отсутствующие травмы, новости или мотивацию «мнением нейросети».</p>
    </section>
  );
}

function TeamHistory({ team, fallbackName }: { team?: TeamDetail; fallbackName: string }) {
  const matches = team?.recent_matches || [];
  const starters = (team?.likely_lineup || [])
    .filter((player) => player.status === "starter" && player.player_name)
    .slice(0, 11);
  return (
    <section className="team-dossier">
      <div className="dossier-title">
        <h4>{team?.name || fallbackName}</h4>
        <span>Elo {team?.elo == null ? "—" : Math.round(team.elo)} · {levelName(team?.level)}</span>
      </div>
      {matches.length ? (
        <div className="history-table" role="table" aria-label={`Последние официальные матчи: ${fallbackName}`}>
          <div className="history-head" role="row"><span>Официальный матч</span><span>Счёт</span><span>npxG без пен.</span><span>npxG* КК+Elo</span></div>
          {matches.map((match) => (
            <div className="history-row" role="row" key={match.match_id}>
              <span><b>{shortMatchDate(match.kickoff_utc)} · {match.venue === "away" ? "гости" : match.venue === "home" ? "дома" : "поле —"} · {match.opponent || "Соперник"}</b><small>{match.competition || "официальный турнир"} · {match.opponent_level ? levelName(match.opponent_level) : "уровень не подтверждён"}{finiteNumber(match.opponent_elo_before?.rating) == null ? "" : ` · Elo ${Math.round(match.opponent_elo_before!.rating!)}`}</small></span>
              <span className={`history-result ${resultClass(match.result_90)}`}><b>{resultLetter(match.result_90)}</b>{match.score_90 ? `${match.score_90.for}:${match.score_90.against}` : "—"}</span>
              <span title={match.xg?.non_penalty?.reason || undefined}>{finiteNumber(match.xg?.non_penalty?.value) == null ? "нет" : decimal(match.xg?.non_penalty?.value)}</span>
              <span title={match.xg?.red_and_opponent_adjusted_npxg?.reason || undefined}>{finiteNumber(match.xg?.red_and_opponent_adjusted_npxg?.value) == null ? "нет" : decimal(match.xg?.red_and_opponent_adjusted_npxg?.value)}</span>
            </div>
          ))}
        </div>
      ) : <p className="unknown-data">Подтверждённая история официальных матчей не загружена — значения не подставлены.</p>}
      <div className="availability-grid">
        <div><b>Состав</b><span>{starters.length ? `${starters.length} стартеров · ${starters.every((p) => p.is_confirmed) ? "официальный" : "предварительный"}` : "не опубликован"}</span></div>
        <div><b>Тренер</b><span>{team?.coach?.coach_name || "не подтверждён"}</span></div>
        <div><b>Пропускают</b><span>{team?.absences?.length ? team.absences.map((p) => p.player_name).filter(Boolean).join(", ") : "нет подтверждённого источника"}</span></div>
      </div>
      {starters.length > 0 && (
        <div className="lineup-list" aria-label={`Состав: ${fallbackName}`}>
          {starters.map((player, index) => (
            <span key={`${player.player_name}-${index}`}>
              <b>{player.jersey_number ?? "—"}</b>
              <i>{player.player_name}</i>
              <small>{positionName(player.field_position)}</small>
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

function MatchDossier({ forecast }: { forecast: Forecast }) {
  const details = forecast.details;
  const marketSnapshotEligible = details?.market_snapshot?.status === "SHADOW_ONLY" &&
    Boolean(details.market_snapshot.captured_at_utc?.trim());
  const pricedCandidatePool = [
    ...(details?.market_candidates || []),
    ...(details?.expanded_market_candidates || []),
  ]
    .filter((candidate) =>
      marketSnapshotEligible &&
      (candidate.status === "SHADOW_ONLY" || candidate.status === "EXPERIMENTAL_SHADOW") &&
      finiteNumber(candidate.probability) != null &&
      finiteNumber(candidate.market_odds) != null &&
      finiteNumber(candidate.fair_odds) != null
    );
  const pricedCandidates = selectPricedCandidateRoles(pricedCandidatePool);
  const missingPriceReason = details?.market_snapshot?.reason
    ? marketSnapshotReason(details.market_snapshot.status, details.market_snapshot.reason)
    : "букмекерский API пока не сопоставил событие с проверенной предматчевой линией";
  return (
    <details className="match-dossier">
      <summary><span>Открыть полный разбор</span><small>Elo · форма · составы · рынок · риск</small></summary>
      <div className="dossier-content">
        <RecentFormHeader forecast={forecast} />
        <ModelAnalysisBrief forecast={forecast} />

        <div className="team-comparison">
          <TeamHistory team={details?.teams?.home} fallbackName={forecast.home} />
          <TeamHistory team={details?.teams?.away} fallbackName={forecast.away} />
        </div>

        <div className="context-grid">
          <section><h4>Судья</h4>{details?.referee?.name ? <><b>{details.referee.name}</b><p>{details.referee.yellow_cards_per_match == null ? "Статистика карточек пока не подтверждена." : `${decimal(details.referee.yellow_cards_per_match, 1)} ЖК/матч · ${details.referee.matches || "—"} игр`}</p></> : <p>Назначение или статистика недоступны.</p>}</section>
          <section><h4>Погода</h4>{details?.weather?.temperature_c == null ? <p>Проверенный прогноз погоды недоступен.</p> : <p>{decimal(details.weather.temperature_c, 0)}°C · ветер {decimal(details.weather.wind_kph, 0)} км/ч · осадки {decimal(details.weather.precipitation_mm, 1)} мм</p>}</section>
          <section><h4>Tail risk</h4>{details?.tail_risk ? <><b>{levelName(details.tail_risk.label)} · {decimal(details.tail_risk.score, 0)}/100</b><p>Это хрупкость прогноза, а не предсказание «чёрного лебедя».</p></> : <p>Не оценён из-за недостатка данных.</p>}</section>
          <section><h4>Качество данных</h4>{details?.data_quality ? <><b>{decimal(details.data_quality.score, 0)}/100 · {levelName(details.data_quality.label)}</b><p>{details.data_quality.warnings?.length || 0} предупреждений</p></> : <p>Детальный аудит ещё не сформирован.</p>}</section>
        </div>

        {details?.market && (
          <section className="market-comparison">
            <div className="dossier-title"><h4>Модель против рынка</h4><span>{details.market.bookmaker || "проверенная линия"}</span></div>
            <div className="comparison-grid">
              {["home", "draw", "away"].map((key, index) => {
                const label = ["П1", "X", "П2"][index];
                const typed = key as "home" | "draw" | "away";
                return <div key={key}><b>{label}</b><span>фундамент: {percent(details.market?.raw_model?.[typed])}</span><span>рынок: {percent(details.market?.market_fair?.[typed])}</span><strong>итог: {percent(details.market?.anchored?.[typed])}</strong></div>;
              })}
            </div>
            {details.market.calibration_warning && <p className="audit-note">{details.market.calibration_warning}</p>}
          </section>
        )}

        <ModelMarketBoard forecast={forecast} />
        <BookmakerSnapshot details={details} />
        <ScoreDistribution forecast={forecast} />

        <section className="candidate-section">
          <div className="dossier-title">
            <h4>Топ-3 ставок по реальным котировкам</h4>
            <span className={pricedCandidates.length ? "shadow-badge" : "no-bet"}>
              {pricedCandidates.length ? "PAPER ONLY" : "NO BET"}
            </span>
          </div>
          {pricedCandidates.length ? (
            <div className="candidate-grid">
              {pricedCandidates.map(({ bet, role }, index) => (
                <div key={`${bet.market}-${bet.selection}-${bet.bookmaker_key}-${index}`}>
                  <b>#{index + 1} · {role}</b>
                  <span><strong>{bet.selection || "рынок не указан"}</strong></span>
                  <span>{bet.market || "рынок"}{bet.line == null ? "" : ` · линия ${decimal(bet.line, 1)}`}</span>
                  <span>Вероятность {percent(bet.probability)}</span>
                  <span>Fair {decimal(bet.fair_odds)} · коэффициент {decimal(bet.market_odds)}</span>
                  <span>{bet.bookmaker || bet.bookmaker_key || "букмекер не указан"}</span>
                  <strong className="positive-edge">
                    оценка +{((finiteNumber(bet.point_edge) || 0) * 100).toFixed(1)}%
                  </strong>
                </div>
              ))}
            </div>
          ) : (
            <p className="unknown-data">
              Нет подтверждённой котировки: {missingPriceReason}. Модельные вероятности П1/X/П2 без цены
              не считаются ставками и не попадают в топ-3.
            </p>
          )}
          <p className="audit-note">PAPER-кандидат не является рекомендацией: cohort gate — {forecast.cohort_gate?.decision_status || "pending"} ({forecast.cohort_gate?.reason || "cohort_not_yet_tracked"}).</p>
        </section>
      </div>
    </details>
  );
}

function ForecastCard({ forecast }: { forecast: Forecast }) {
  const hasPrediction = forecast.p_home != null;
  const hasAdvance = forecast.p_home_advance != null;
  return (
    <article className="forecast-card" id={`match-${forecast.id}`}>
      <div className="card-topline">
        <span className="competition-pill">{competitionName(forecast.competition)}</span>
        <time dateTime={forecast.kickoff_utc}>{localTime(forecast.kickoff_utc)} YEKT</time>
      </div>
      <p className="stage">{forecast.stage}{forecast.first_leg ? ` · ${forecast.first_leg}` : ""}</p>
      <div className="teams">
        <h3>{forecast.home}</h3>
        <span>vs</span>
        <h3>{forecast.away}</h3>
      </div>
      <p className="venue">{forecast.venue || "Стадион уточняется"}</p>

      {hasPrediction ? (
        <div className="probabilities" aria-label="Вероятности исходов за 90 минут">
          <ProbabilityBar label="П1" value={forecast.p_home} />
          <ProbabilityBar label="X" value={forecast.p_draw} />
          <ProbabilityBar label="П2" value={forecast.p_away} />
        </div>
      ) : (
        <div className="pending-model">
          Вероятности появятся после проверки истории команд. Пустые данные не заменяются догадкой.
        </div>
      )}

      <div className="micro-metrics">
        {hasAdvance ? (
          <>
            <div><span>Проход хозяев</span><b>{percent(forecast.p_home_advance)}</b></div>
            <div><span>Проход гостей</span><b>{percent(forecast.p_away_advance)}</b></div>
            <div><span>Ожидаемый тотал</span><b>{decimal(forecast.expected_goals?.total)}</b></div>
          </>
        ) : (
          <>
            <div><span>ТБ 2.5</span><b>{percent(forecast.p_over25)}</b></div>
            <div><span>Обе забьют</span><b>{percent(forecast.p_btts)}</b></div>
            <div><span>Ожидаемый тотал</span><b>{decimal(forecast.expected_goals?.total)}</b></div>
          </>
        )}
      </div>
      <div className="card-footer">
        <span>{forecast.model || "Официальный календарь"}</span>
        <span className="uncertainty">{forecast.uncertainty || "не оценена"}</span>
          <strong className="model-forecast-card">{forecast.recommendation || "MODEL FORECAST"}</strong>
      </div>
      <MatchDossier forecast={forecast} />
    </article>
  );
}

export default function Home() {
  const [payload, setPayload] = useState<LivePayload>(FALLBACK);
  const [prospectiveLedger, setProspectiveLedger] = useState<ProspectiveLedger | null>(null);
  const [forecastArchive, setForecastArchive] = useState<ForecastArchiveDocument | null>(null);
  const [archiveStatus, setArchiveStatus] = useState<"loading" | "live" | "unavailable">("loading");
  const [nowMs, setNowMs] = useState(() => new Date(FALLBACK.generated_at).getTime());
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [live, setLive] = useState(false);

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      const cacheBuster = Date.now();
      const [next, nextLedger, nextArchive] = await Promise.all([
        fetch(`${DATA_URL}?t=${cacheBuster}`, { cache: "no-store" })
          .then(async (response) => response.ok ? response.json() as Promise<LivePayload> : null)
          .catch(() => null),
        fetch(`${PROSPECTIVE_URL}?t=${cacheBuster}`, { cache: "no-store" })
          .then(async (response) => response.ok ? response.json() as Promise<ProspectiveLedger> : null)
          .catch(() => null),
        fetch(`${FORECAST_ARCHIVE_URL}?t=${cacheBuster}`, { cache: "no-store" })
          .then(async (response) => response.ok ? response.json() as Promise<ForecastArchiveDocument> : null)
          .catch(() => null),
      ]);
      if (!active) return;
      setNowMs(cacheBuster);
      if (next && Array.isArray(next.forecasts)) {
        setPayload(next);
        setLive(true);
      }
      const validLedger = nextLedger &&
        typeof nextLedger.schema_version === "string" &&
        nextLedger.schema_version.startsWith("prospective-clv/") &&
        nextLedger.fixtures != null &&
        typeof nextLedger.fixtures === "object" &&
        !Array.isArray(nextLedger.fixtures);
      const validArchive = nextArchive &&
        nextArchive.schema_version === "match-evidence-archive/1.0" &&
        Array.isArray(nextArchive.forecasts) &&
        Array.isArray(nextArchive.results);
      if (validArchive) {
        setForecastArchive(nextArchive);
        setArchiveStatus("live");
      } else if (validLedger) {
        setProspectiveLedger(nextLedger);
        setArchiveStatus("live");
      } else {
        setArchiveStatus("unavailable");
      }
    };
    refresh();
    const timer = window.setInterval(refresh, 5 * 60 * 1000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  const forecasts = useMemo(
    () => payload.forecasts.filter((item) => {
      const kickoff = new Date(item.kickoff_utc).getTime();
      const isFuture = Number.isFinite(kickoff) && kickoff > nowMs;
      const inCompetition = filter === "all" ||
        (filter === "world-cup" && item.competition.includes("World Cup")) ||
        (filter === "ucl" && item.competition.includes("Champions")) ||
        (filter === "uel" && item.competition.includes("Europa League")) ||
        (filter === "uecl" && item.competition.includes("Conference League")) ||
        (filter === "top-five" && isTopFiveCompetition(item.competition));
      const needle = query.trim().toLocaleLowerCase("ru-RU");
      if (!needle) return isFuture && inCompetition;
      const referee = item.details?.referee?.name || "";
      return isFuture && inCompetition && [item.home, item.away, item.competition, item.stage, item.venue || "", referee]
        .join(" ").toLocaleLowerCase("ru-RU").includes(needle);
    }),
    [payload, filter, query, nowMs],
  );
  const hasFutureForecasts = useMemo(
    () => payload.forecasts.some((item) => {
      const kickoff = new Date(item.kickoff_utc).getTime();
      return Number.isFinite(kickoff) && kickoff > nowMs;
    }),
    [payload, nowMs],
  );

  return (
    <main>
      <header className="topbar">
        <a className="brand" href="#top" aria-label="xg-edge, начало страницы">
          <span className="brand-mark">xG</span>
          <span>xg-edge <small>LIVE LAB</small></span>
        </a>
        <div className="data-status"><i className={live ? "online" : "fallback"} />{live ? "live feed" : "резервный снимок"}</div>
      </header>

      <section className="hero" id="top">
        <div className="hero-copy">
          <p className="eyebrow">ЛЧ · ЛЕ · ЛК · Top-5 2026/27 · 90 минут</p>
          <h1>Вероятности<br />без обещаний.</h1>
          <p className="lead">
            Модель публикует полный набор прогнозов до матча и сразу показывает три наиболее устойчивых сценария.
            Bookmaker-value и CLV считаются отдельно и больше не блокируют модельный разбор.
          </p>
          <div className="hero-actions">
            <a href="#forecasts" className="primary-action">Смотреть матчи</a>
            <a href="#paper-bank" className="secondary-action">PAPER-банк</a>
            <a href="#completed-archive" className="secondary-action">Архив качества</a>
            <a href="https://github.com/bogdasovandrej/xg-edge" className="secondary-action">Открытый код ↗</a>
          </div>
        </div>
        <ProspectiveClvPanel
          summary={payload.prospective_clv}
          forecasts={payload.forecasts}
        />
      </section>

      <section className="ticker" aria-label="Принципы модели">
        <span>POINT-IN-TIME</span><i />
        <span>NO FUTURE LEAKAGE</span><i />
        <span>OFFICIAL FIFA + UEFA</span><i />
        <span>PROSPECTIVE CLV</span><i />
        <span>PAPER BANKROLL</span>
      </section>

      <PaperCandidateBoard ranking={payload.paper_candidate_ranking} forecasts={payload.forecasts} nowMs={nowMs} />

      <PaperTradingLab summary={payload.paper_trading} forecasts={payload.forecasts} />

      <CompletedForecastArchive archive={forecastArchive} ledger={prospectiveLedger} status={archiveStatus} />

      <section className="forecasts-section" id="forecasts">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Ближайшие события</p>
            <h2>Прогнозы и календарь</h2>
          </div>
          <div className="filters" role="group" aria-label="Фильтр соревнований">
            {[["all", "Все"], ["ucl", "ЛЧ"], ["uel", "ЛЕ"], ["uecl", "ЛК"], ["top-five", "Top-5"], ["world-cup", "ЧМ"]].map(([value, label]) => (
              <button key={value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>{label}</button>
            ))}
          </div>
        </div>
        <div className="match-search">
          <label htmlFor="match-search">Поиск матча</label>
          <div><input id="match-search" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Команда, турнир, судья или стадион" /><button type="button" onClick={() => setQuery("")} disabled={!query}>Очистить</button></div>
          <span>Найдено: {forecasts.length}</span>
        </div>
        <div className="forecast-grid">
          {forecasts.map((forecast) => <ForecastCard key={forecast.id} forecast={forecast} />)}
        </div>
        {!forecasts.length && <div className="empty-search">
          {hasFutureForecasts
            ? "Матчи не найдены. Измените запрос или сбросьте фильтр."
            : "Официальный источник пока не вернул будущих матчей с готовым прогнозом. Прошедшие матчи не показываются как будущие."}
        </div>}
        <p className="updated">
          Снимок: {new Date(payload.generated_at).toLocaleString("ru-RU", { timeZone: "Asia/Yekaterinburg" })} YEKT · обновление каждые 5 минут
        </p>
      </section>

      <section className="method-section">
        <div>
          <p className="eyebrow">Как читать цифры</p>
          <h2>Прогноз — это распределение,<br />а не обещание счёта.</h2>
        </div>
        <div className="method-grid">
          <article><b>01</b><h3>До матча</h3><p>Каждый прогноз сохраняется с временем. Результат, closing odds и поздние составы не могут попасть назад.</p></article>
          <article><b>02</b><h3>Рынок как prior</h3><p>Модель не игнорирует цену. Фундаментальный сигнал сжимается к de-vigged consensus, особенно на андердогах.</p></article>
          <article><b>03</b><h3>Право молчать</h3><p>Если данных мало или CLV-гейт не пройден, система показывает вероятности, но не рекомендует ставку.</p></article>
        </div>
      </section>

      <footer>
        <span>xg-edge · исследовательский проект</span>
        <span>Не является букмекерской рекомендацией</span>
      </footer>
    </main>
  );
}

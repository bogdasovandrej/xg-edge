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
  p_btts?: number | null;
  p_home_advance?: number | null;
  p_away_advance?: number | null;
  top_score?: string | null;
  uncertainty?: string | null;
  recommendation?: string | null;
  first_leg?: string | null;
  probability_basis?: string | null;
  raw_model_1x2?: { home: number; draw: number; away: number } | null;
  evaluation_cohort_id?: string | null;
  cohort_gate?: {
    allowed?: boolean;
    action?: string | null;
    reason?: string | null;
    decision_status?: string | null;
  } | null;
  details?: MatchDetails | null;
};

type RecentMatch = {
  match_id: string;
  kickoff_utc: string;
  opponent?: string | null;
  score_90?: { for: number; against: number } | null;
  result_90?: string | null;
  opponent_level?: string | null;
  opponent_elo_before?: { rating?: number | null } | null;
  xg?: {
    raw?: number | null;
    non_penalty?: { status?: string; value?: number | null } | null;
    red_and_opponent_adjusted_npxg?: { status?: string; value?: number | null } | null;
  } | null;
  red_cards?: unknown[] | null;
};

type TeamDetail = {
  name?: string | null;
  elo?: number | null;
  level?: string | null;
  competition_level?: string | null;
  recent_matches?: RecentMatch[] | null;
  likely_lineup?: Array<{ player_name?: string | null; status?: string | null; is_confirmed?: boolean | null }> | null;
  absences?: Array<{ player_name?: string | null; status?: string | null }> | null;
};

type CandidateBet = {
  rank?: number;
  selection?: string;
  outcome?: string;
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

type LivePayload = {
  generated_at: string;
  status: string;
  betting_gate?: { allowed?: boolean; reason?: string | null } | null;
  prospective_clv?: ProspectiveClvSummary | null;
  forecasts: Forecast[];
};

const DATA_URL =
  "https://raw.githubusercontent.com/bogdasovandrej/xg-edge/main/reports/live_predictions.json";

const FALLBACK: LivePayload = {
  generated_at: "2026-07-13T00:00:00Z",
  status: "official-fixtures-only",
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
  forecasts: [
    {
      id: "400021541",
      competition: "FIFA World Cup 2026",
      stage: "Полуфинал",
      kickoff_utc: "2026-07-14T19:00:00Z",
      home: "Франция",
      away: "Испания",
      venue: "Dallas Stadium",
      uncertainty: "модель готовится",
      recommendation: "NO BET",
    },
    {
      id: "400021540",
      competition: "FIFA World Cup 2026",
      stage: "Полуфинал",
      kickoff_utc: "2026-07-15T19:00:00Z",
      home: "Англия",
      away: "Аргентина",
      venue: "Atlanta Stadium",
      uncertainty: "модель готовится",
      recommendation: "NO BET",
    },
  ],
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

const competitionName = (name: string) =>
  name.includes("World Cup") ? "ЧМ-2026" : "Квалификация ЛЧ";

const finiteNumber = (value?: number | null) =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const signedPercent = (value?: number | null) => {
  const safe = finiteNumber(value);
  if (safe == null) return "—";
  const sign = safe > 0 ? "+" : safe < 0 ? "−" : "";
  return `${sign}${Math.abs(safe * 100).toFixed(2)}%`;
};

const gateReason = (reason?: string | null) => ({
  insufficient_independent_matches: "недостаточно независимых матчей",
  confirmatory_horizon_not_reached: "фиксированный горизонт ещё не достигнут",
  awaiting_one_shot_finalization: "ожидается однократная проверка",
  global_gate_disabled_cohort_specific_only: "общий гейт закрыт: модели проверяются раздельно",
  cohort_not_yet_tracked: "эта модель ещё не набрала live-наблюдений",
  clv_lower_ci_not_positive: "нижняя граница CLV не выше нуля",
  positive_clv_confirmed: "положительный CLV подтверждён",
}[reason || ""] || "prospective CLV ещё не подтверждён");

function ProspectiveClvPanel({
  summary,
  allowed,
}: {
  summary?: ProspectiveClvSummary | null;
  allowed: boolean;
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
  const ciLow = finiteNumber(clv?.ci_low);
  const ciHigh = finiteNumber(clv?.ci_high);
  const mean = finiteNumber(clv?.mean);
  const reason = leading?.reason || summary?.reason;
  const gateOpen = allowed && leading?.action === "BET" && reason === "positive_clv_confirmed" &&
    independentMatches >= minimum && ciLow != null && ciLow > 0;
  const waiting = independentMatches < minimum;
  const status = gateOpen ? "OPEN" : waiting ? "WAIT" : "CLOSED";
  const interval = observations > 0 && ciLow != null && ciHigh != null
    ? `${signedPercent(ciLow)}…${signedPercent(ciHigh)}`
    : "—";

  return (
    <aside className="truth-panel" aria-label="Статус prospective CLV">
      <span className="panel-label">Текущий вердикт</span>
      <strong className={gateOpen ? "gate-open" : undefined}>{gateOpen ? "BET" : "NO BET"}</strong>
      <p><b>{status}</b> · {gateReason(reason)}</p>
      <dl>
        <div><dt>Prospective CLV</dt><dd>{observations > 0 ? signedPercent(mean) : "—"}</dd></div>
        <div><dt>95% CI CLV</dt><dd>{interval}</dd></div>
        <div><dt>Независимая выборка</dt><dd>{independentMatches} / {minimum}</dd></div>
      </dl>
      <small>
        {!leading
          ? "CLV пока не измерен: нет отдельной когорты model × competition с сохранённой ценой и closing line."
          : observations < minimum
            ? `Лидирующая когорта: ${leading.dimensions?.competition_or_sport || "турнир"} · ${leading.dimensions?.model || "модель"}. Промежуточный CLV скрыт до заранее заданного n=${minimum}.`
            : `Однократная проверка зафиксирована на ${minimum} матчах; последующие матчи не меняют принятое решение.`}
      </small>
    </aside>
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

const levelName = (value?: string | null) => ({
  elite: "элитный", strong: "сильный", average: "средний", developing: "развивающийся",
  high: "высокий", medium: "средний", low: "низкий",
}[value || ""] || value || "не оценён");

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
    </>
  );
}

function TeamHistory({ team, fallbackName }: { team?: TeamDetail; fallbackName: string }) {
  const matches = team?.recent_matches || [];
  return (
    <section className="team-dossier">
      <div className="dossier-title">
        <h4>{team?.name || fallbackName}</h4>
        <span>Elo {team?.elo == null ? "—" : Math.round(team.elo)} · {levelName(team?.level)}</span>
      </div>
      {matches.length ? (
        <div className="history-table" role="table" aria-label={`Последние официальные матчи: ${fallbackName}`}>
          <div className="history-head" role="row"><span>Матч</span><span>Счёт</span><span>npxG</span><span>adj.</span></div>
          {matches.map((match) => (
            <div className="history-row" role="row" key={match.match_id}>
              <span><b>{match.opponent || "Соперник"}</b><small>{match.opponent_level ? `${levelName(match.opponent_level)} · Elo ${Math.round(match.opponent_elo_before?.rating || 0)}` : "уровень не подтверждён"}</small></span>
              <span>{match.score_90 ? `${match.score_90.for}:${match.score_90.against}` : "—"}</span>
              <span>{decimal(match.xg?.non_penalty?.value)}</span>
              <span>{decimal(match.xg?.red_and_opponent_adjusted_npxg?.value)}</span>
            </div>
          ))}
        </div>
      ) : <p className="unknown-data">Нет десяти подтверждённых официальных матчей с xG — значения не подставлены.</p>}
      <div className="availability-grid">
        <div><b>Состав</b><span>{team?.likely_lineup?.length ? `${team.likely_lineup.length} игроков · ${team.likely_lineup.every((p) => p.is_confirmed) ? "подтверждён" : "предварительный"}` : "не опубликован"}</span></div>
        <div><b>Пропускают</b><span>{team?.absences?.length ? team.absences.map((p) => p.player_name).filter(Boolean).join(", ") : "нет подтверждённого источника"}</span></div>
      </div>
    </section>
  );
}

function MatchDossier({ forecast }: { forecast: Forecast }) {
  const details = forecast.details;
  const candidates = details?.candidate_bets || [];
  return (
    <details className="match-dossier">
      <summary><span>Открыть полный разбор</span><small>Elo · форма · составы · рынок · риск</small></summary>
      <div className="dossier-content">
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

        <BookmakerSnapshot details={details} />

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

        <section className="candidate-section">
          <div className="dossier-title"><h4>Топ-3 кандидата рынка</h4><span className="no-bet">NO BET</span></div>
          {candidates.length ? <div className="candidate-grid">{candidates.slice(0, 3).map((bet, index) => <div key={`${bet.selection}-${index}`}><b>#{bet.rank || index + 1} · {bet.selection}</b><span>Вероятность {percent(bet.probability)}</span><span>Fair {decimal(bet.fair_odds)} · рынок {decimal(bet.market_odds)}</span><strong className={(bet.point_edge || 0) > 0 ? "positive-edge" : "negative-edge"}>оценка {bet.point_edge == null ? "нет цены" : `${(bet.point_edge * 100).toFixed(1)}%`}</strong></div>)}</div> : <p className="unknown-data">Нет синхронной котировки — оценка ставки невозможна.</p>}
          <p className="audit-note">Кандидат не является рекомендацией: cohort gate — {forecast.cohort_gate?.decision_status || "pending"} ({forecast.cohort_gate?.reason || "cohort_not_yet_tracked"}).</p>
        </section>
      </div>
    </details>
  );
}

function ForecastCard({ forecast }: { forecast: Forecast }) {
  const hasPrediction = forecast.p_home != null;
  const hasAdvance = forecast.p_home_advance != null;
  return (
    <article className="forecast-card">
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
          </>
        ) : (
          <>
            <div><span>ТБ 2.5</span><b>{percent(forecast.p_over25)}</b></div>
            <div><span>Обе забьют</span><b>{percent(forecast.p_btts)}</b></div>
          </>
        )}
        <div><span>Счёт-мода</span><b>{forecast.top_score || "—"}</b></div>
      </div>
      <div className="card-footer">
        <span>{forecast.model || "Официальный календарь"}</span>
        <span className="uncertainty">{forecast.uncertainty || "не оценена"}</span>
        <strong className="no-bet">{forecast.recommendation || "NO BET"}</strong>
      </div>
      <MatchDossier forecast={forecast} />
    </article>
  );
}

export default function Home() {
  const [payload, setPayload] = useState<LivePayload>(FALLBACK);
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [live, setLive] = useState(false);

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const response = await fetch(`${DATA_URL}?t=${Date.now()}`, { cache: "no-store" });
        if (!response.ok) return;
        const next = (await response.json()) as LivePayload;
        if (active && Array.isArray(next.forecasts) && next.forecasts.length) {
          setPayload(next);
          setLive(true);
        }
      } catch {
        // The embedded official-fixture fallback remains visible offline.
      }
    };
    refresh();
    const timer = window.setInterval(refresh, 5 * 60 * 1000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  const forecasts = useMemo(
    () => payload.forecasts.filter((item) => {
      const inCompetition = filter === "all" ||
        (filter === "world-cup" ? item.competition.includes("World Cup") : item.competition.includes("Champions"));
      const needle = query.trim().toLocaleLowerCase("ru-RU");
      if (!needle) return inCompetition;
      const referee = item.details?.referee?.name || "";
      return inCompetition && [item.home, item.away, item.competition, item.stage, item.venue || "", referee]
        .join(" ").toLocaleLowerCase("ru-RU").includes(needle);
    }),
    [payload, filter, query],
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
          <p className="eyebrow">ЧМ-2026 · Лига чемпионов · 90 минут</p>
          <h1>Вероятности<br />без обещаний.</h1>
          <p className="lead">
            Модель публикует прогноз до матча, показывает неопределённость и не создаёт ставку,
            пока преимущество над closing line не доказано на новых данных.
          </p>
          <div className="hero-actions">
            <a href="#forecasts" className="primary-action">Смотреть матчи</a>
            <a href="https://github.com/bogdasovandrej/xg-edge" className="secondary-action">Открытый код ↗</a>
          </div>
        </div>
        <ProspectiveClvPanel
          summary={payload.prospective_clv}
          allowed={payload.betting_gate?.allowed === true}
        />
      </section>

      <section className="ticker" aria-label="Принципы модели">
        <span>POINT-IN-TIME</span><i />
        <span>NO FUTURE LEAKAGE</span><i />
        <span>OFFICIAL FIFA + UEFA</span><i />
        <span>PROSPECTIVE CLV</span>
      </section>

      <section className="forecasts-section" id="forecasts">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Ближайшие события</p>
            <h2>Прогнозы и календарь</h2>
          </div>
          <div className="filters" role="group" aria-label="Фильтр соревнований">
            {[["all", "Все"], ["world-cup", "ЧМ"], ["ucl", "ЛЧ"]].map(([value, label]) => (
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
        {!forecasts.length && <div className="empty-search">Матчи не найдены. Измените запрос или сбросьте фильтр.</div>}
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

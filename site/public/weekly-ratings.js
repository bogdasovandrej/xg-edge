const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;
const DEFAULT_WINDOW_DAYS = 7;
const MAX_QUOTE_AGE_MS = 6 * HOUR_MS;

function finite(value) {
  if (value === null || value === undefined || typeof value === "boolean") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function clamp(value, minimum = 0, maximum = 100) {
  return Math.min(maximum, Math.max(minimum, value));
}

function utc(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) && /(?:Z|[+-]\d{2}:\d{2})$/i.test(value.trim())
    ? parsed
    : null;
}

function text(value) {
  return typeof value === "string" ? value.trim() : "";
}

function uncertaintyScore(label) {
  const normalized = text(label).toLocaleLowerCase("ru");
  if (["low", "низкая", "низкий"].includes(normalized)) return 25;
  if (["medium", "средняя", "средний"].includes(normalized)) return 55;
  if (["high", "высокая", "высокий"].includes(normalized)) return 80;
  return 65;
}

function parseAggregate(value) {
  const source = text(value);
  const match = source.match(/(\d+)\s*[:–—-]\s*(\d+)/u);
  if (!match) return null;
  const home = Number(match[1]);
  const away = Number(match[2]);
  return {
    home,
    away,
    margin: Math.abs(home - away),
    tied: home === away,
    label: `${home}:${away}`,
  };
}

function isSecondLeg(forecast) {
  const stage = `${text(forecast?.stage)} ${text(forecast?.first_leg)}`.toLocaleLowerCase("ru");
  return /ответн|second\s+leg|2nd\s+leg/u.test(stage) || Boolean(parseAggregate(forecast?.first_leg));
}

function candidateRows(forecast) {
  const details = forecast?.details;
  if (!details || typeof details !== "object") return [];
  return [
    ...(Array.isArray(details.market_candidates) ? details.market_candidates : []),
    ...(Array.isArray(details.expanded_market_candidates)
      ? details.expanded_market_candidates
      : []),
  ].filter((row) => row && typeof row === "object");
}

function candidateHasVerifiedPrice(candidate) {
  const odds = finite(candidate?.market_odds);
  const probability = finite(candidate?.probability);
  return (
    ["SHADOW_ONLY", "EXPERIMENTAL_SHADOW"].includes(text(candidate?.status)) &&
    odds !== null &&
    odds > 1 &&
    probability !== null &&
    probability > 0 &&
    probability < 1 &&
    Boolean(text(candidate?.bookmaker))
  );
}

function snapshotEvidence(forecast, now) {
  const snapshot = forecast?.details?.market_snapshot;
  const captured = utc(snapshot?.captured_at_utc);
  const generated = utc(forecast?.forecast_generated_at);
  const kickoff = utc(forecast?.kickoff_utc);
  const base = {
    eligible: false,
    reason: "missing_snapshot",
    capturedAt: captured?.toISOString() ?? null,
    ageHours: null,
    bookmakers: 0,
    pricedRows: 0,
    marketTypes: 0,
    score: 0,
  };

  if (!snapshot || typeof snapshot !== "object") return base;
  if (snapshot.status !== "SHADOW_ONLY") {
    return { ...base, reason: text(snapshot.reason) || "snapshot_not_shadow_only" };
  }
  if (!captured || !generated || !kickoff) {
    return { ...base, reason: "invalid_timestamp" };
  }
  if (captured < generated || captured >= kickoff) {
    return { ...base, reason: "quote_outside_forecast_window" };
  }
  if (captured > now) return { ...base, reason: "quote_from_future" };
  const ageMs = now.getTime() - captured.getTime();
  if (ageMs > MAX_QUOTE_AGE_MS) {
    return { ...base, reason: "quote_older_than_6h", ageHours: ageMs / HOUR_MS };
  }

  const verified = candidateRows(forecast).filter(candidateHasVerifiedPrice);
  if (!verified.length) {
    return { ...base, reason: "no_verified_candidate_rows", ageHours: ageMs / HOUR_MS };
  }
  const bookmakers = Math.max(0, finite(snapshot.bookmakers) ?? 0);
  const marketTypes = new Set(
    verified.map((row) => text(row.market) || text(row.market_key) || "unknown"),
  ).size;
  const recencyPoints = 30 * (1 - clamp(ageMs / MAX_QUOTE_AGE_MS, 0, 1));
  const score = clamp(
    30 + recencyPoints + Math.min(20, bookmakers * 4) + Math.min(20, marketTypes * 4),
  );
  return {
    eligible: true,
    reason: "verified",
    capturedAt: captured.toISOString(),
    ageHours: ageMs / HOUR_MS,
    bookmakers,
    pricedRows: verified.length,
    marketTypes,
    score: Math.round(score * 10) / 10,
  };
}

function dataConfidence(forecast) {
  return clamp(finite(forecast?.details?.data_quality?.score) ?? 0);
}

function scenarioFragility(forecast) {
  const tail = finite(forecast?.details?.tail_risk?.score);
  const uncertainty = uncertaintyScore(forecast?.uncertainty);
  return Math.round(clamp((tail ?? uncertainty) * 0.75 + uncertainty * 0.25) * 10) / 10;
}

function findPaperCandidate(forecast, paperRanking, now, marketEvidence) {
  const rows = Array.isArray(paperRanking?.candidates) ? paperRanking.candidates : [];
  const fixtureId = String(forecast?.id ?? "");
  const candidate = rows.find((row) => String(row?.fixture_id ?? "") === fixtureId);
  if (!candidate || !marketEvidence.eligible) return null;

  const kickoff = utc(forecast?.kickoff_utc);
  const captured = utc(candidate.quote_captured_at);
  const robustEdge = finite(candidate.robust_edge);
  const pointEdge = finite(candidate.point_edge);
  const odds = finite(candidate.odds);
  if (
    candidate.status !== "PAPER_ONLY" ||
    candidate.real_money_eligible !== false ||
    !captured ||
    !kickoff ||
    captured > now ||
    now.getTime() - captured.getTime() > MAX_QUOTE_AGE_MS ||
    captured >= kickoff ||
    robustEdge === null ||
    robustEdge <= 0 ||
    pointEdge === null ||
    pointEdge < 0.03 ||
    odds === null ||
    odds <= 1 ||
    !text(candidate.bookmaker)
  ) {
    return null;
  }
  return {
    selection: text(candidate.selection) || text(candidate.outcome) || "—",
    market: text(candidate.market) || "—",
    odds,
    bookmaker: text(candidate.bookmaker),
    robustEdge,
    pointEdge,
    quoteCapturedAt: captured.toISOString(),
    status: "PAPER_ONLY",
    realMoneyEligible: false,
  };
}

function warningSet(forecast) {
  const rows = forecast?.details?.data_quality?.warnings;
  return new Set(Array.isArray(rows) ? rows.map(text).filter(Boolean) : []);
}

function scoreForecast(forecast, paperCandidate, market, now) {
  const kickoff = utc(forecast?.kickoff_utc);
  const hoursToKickoff = kickoff ? (kickoff.getTime() - now.getTime()) / HOUR_MS : 168;
  const aggregate = parseAggregate(forecast?.first_leg);
  const secondLeg = isSecondLeg(forecast);
  const data = dataConfidence(forecast);
  const fragility = scenarioFragility(forecast);

  const components = {
    baseline: 10,
    tieImportance:
      (secondLeg ? 7 : 0) +
      (aggregate?.margin === 0 ? 10 : aggregate?.margin === 1 ? 8 : aggregate?.margin >= 3 ? 5 : 0),
    dataActionability: 20 * (data / 100),
    marketEvidence: 18 * (market.score / 100),
    decisionUncertainty: 12 * clamp(1 - Math.abs(fragility - 55) / 55, 0, 1),
    informationGap: 8 * clamp(1 - Math.abs(data - 62) / 62, 0, 1),
    urgency: 12 * clamp(1 - hoursToKickoff / 72, 0, 1),
    paperSignal: paperCandidate ? 18 : 0,
  };
  const total = Object.values(components).reduce((sum, value) => sum + value, 0);
  return {
    score: Math.round(clamp(total) * 10) / 10,
    components: Object.fromEntries(
      Object.entries(components).map(([key, value]) => [key, Math.round(value * 10) / 10]),
    ),
  };
}

function classifyForecast(forecast, score, paperCandidate, market, now) {
  const aggregate = parseAggregate(forecast?.first_leg);
  const kickoff = utc(forecast?.kickoff_utc);
  const hoursToKickoff = kickoff ? (kickoff.getTime() - now.getTime()) / HOUR_MS : Infinity;
  const warnings = warningSet(forecast);
  const lineupGap =
    warnings.has("lineups_unavailable") ||
    warnings.has("absences_unavailable") ||
    warnings.has("lineups_and_absences_unavailable");

  if (paperCandidate) return "PAPER CANDIDATE";
  if (aggregate && aggregate.margin >= 3) return "SCENARIO REVIEW";
  if (market.eligible && score >= 60) return "PRICE REVIEW";
  if (lineupGap && hoursToKickoff <= 48) return "LINEUP WATCH";
  if (score >= 60) return "DEEP REVIEW";
  if (!market.eligible && score >= 45) return "PRICE WATCH";
  return "LOW PRIORITY";
}

function reasonsFor(forecast, bucket, market, aggregate, score) {
  const reasons = [];
  if (aggregate?.tied) reasons.push(`Равный агрегат ${aggregate.label}: высокая чувствительность к сценарию.`);
  else if (aggregate?.margin === 1) reasons.push(`Разница в один мяч (${aggregate.label}).`);
  else if (aggregate?.margin >= 3) {
    reasons.push(`Крупный агрегат ${aggregate.label}: ротация и темп важнее базовой силы.`);
  }
  if (market.eligible) {
    reasons.push(
      `Свежая подтверждённая цена: ${market.pricedRows} строк, ${market.bookmakers} букмекеров.`,
    );
  } else {
    reasons.push(`Котировка не участвует в решении: ${market.reason}.`);
  }
  const warnings = warningSet(forecast);
  if (warnings.has("lineups_unavailable")) reasons.push("Нет подтверждённых стартовых составов.");
  if (warnings.has("absences_unavailable")) reasons.push("Нет подтверждённого списка потерь.");
  if (bucket === "PAPER CANDIDATE") reasons.push("Сигнал прошёл строгий PAPER-фильтр; это не реальная ставка.");
  reasons.push(`Приоритет ${score.score}/100 сформирован из проверяемых компонентов.`);
  return reasons;
}

function rateForecast(forecast, paperRanking, now = new Date()) {
  if (!forecast || typeof forecast !== "object") throw new TypeError("forecast must be an object");
  if (!(now instanceof Date) || !Number.isFinite(now.getTime())) throw new TypeError("now must be a valid Date");
  const market = snapshotEvidence(forecast, now);
  const paperCandidate = findPaperCandidate(forecast, paperRanking, now, market);
  const research = scoreForecast(forecast, paperCandidate, market, now);
  const bucket = classifyForecast(forecast, research.score, paperCandidate, market, now);
  const aggregate = parseAggregate(forecast.first_leg);
  return {
    id: String(forecast.id ?? ""),
    competition: text(forecast.competition) || "Неизвестный турнир",
    stage: text(forecast.stage),
    kickoffUtc: text(forecast.kickoff_utc),
    home: text(forecast.home) || "—",
    away: text(forecast.away) || "—",
    aggregate,
    researchScore: research.score,
    scoreComponents: research.components,
    dataConfidence: dataConfidence(forecast),
    marketEvidence: market,
    scenarioFragility: scenarioFragility(forecast),
    paperCandidate,
    bucket,
    reasons: reasonsFor(forecast, bucket, market, aggregate, research),
  };
}

function buildWeeklyRatings(payload, now = new Date(), options = {}) {
  if (!payload || typeof payload !== "object" || !Array.isArray(payload.forecasts)) {
    throw new TypeError("payload.forecasts must be an array");
  }
  if (!(now instanceof Date) || !Number.isFinite(now.getTime())) throw new TypeError("now must be a valid Date");
  const days = finite(options.days) ?? DEFAULT_WINDOW_DAYS;
  if (days <= 0 || days > 31) throw new RangeError("days must be between 0 and 31");
  const windowEnd = new Date(now.getTime() + days * DAY_MS);
  const input = payload.forecasts
    .filter((forecast) => {
      const kickoff = utc(forecast?.kickoff_utc);
      return kickoff && kickoff > now && kickoff <= windowEnd;
    })
    .map((forecast) => rateForecast(forecast, payload.paper_candidate_ranking, now));

  input.sort((left, right) => {
    const paperEdge =
      (right.paperCandidate?.robustEdge ?? -Infinity) -
      (left.paperCandidate?.robustEdge ?? -Infinity);
    return (
      right.researchScore - left.researchScore ||
      (Number.isFinite(paperEdge) ? paperEdge : 0) ||
      left.kickoffUtc.localeCompare(right.kickoffUtc) ||
      left.id.localeCompare(right.id)
    );
  });
  const ratings = input.map((row, index) => ({ ...row, rank: index + 1 }));
  const counts = ratings.reduce((result, row) => {
    result[row.bucket] = (result[row.bucket] ?? 0) + 1;
    return result;
  }, {});
  return {
    schemaVersion: "weekly-research-radar/1.0",
    generatedAt: now.toISOString(),
    sourceGeneratedAt: text(payload.generated_at) || null,
    window: { from: now.toISOString(), to: windowEnd.toISOString(), days },
    methodology: {
      purpose: "research_triage_not_betting_probability",
      scoreRange: [0, 100],
      quoteMaxAgeHours: MAX_QUOTE_AGE_MS / HOUR_MS,
      realMoneyExecution: false,
    },
    counts,
    ratings,
  };
}

export {
  MAX_QUOTE_AGE_MS,
  buildWeeklyRatings,
  parseAggregate,
  rateForecast,
  snapshotEvidence,
};

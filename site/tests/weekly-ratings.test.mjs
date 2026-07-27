import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  MAX_QUOTE_AGE_MS,
  buildWeeklyRatings,
  parseAggregate,
  rateForecast,
  snapshotEvidence,
} from "../public/weekly-ratings.js";

const NOW = new Date("2026-07-27T12:00:00Z");

function forecast(overrides = {}) {
  const base = {
    id: "fixture-1",
    competition: "UEFA Champions League",
    stage: "2-й квалификационный раунд · ответный матч",
    kickoff_utc: "2026-07-29T12:00:00Z",
    forecast_generated_at: "2026-07-27T08:00:00Z",
    home: "Alpha",
    away: "Beta",
    first_leg: "Агрегат 1:1",
    uncertainty: "средняя",
    details: {
      data_quality: { score: 74, warnings: [] },
      tail_risk: { score: 58 },
      market_snapshot: {
        status: "SHADOW_ONLY",
        captured_at_utc: "2026-07-27T10:00:00Z",
        bookmakers: 4,
      },
      market_candidates: [
        {
          status: "SHADOW_ONLY",
          market: "totals",
          selection: "ТБ 2.5",
          probability: 0.58,
          market_odds: 1.92,
          bookmaker: "Example",
        },
      ],
      expanded_market_candidates: [],
    },
  };
  return {
    ...base,
    ...overrides,
    details: { ...base.details, ...(overrides.details ?? {}) },
  };
}

function validPaper(overrides = {}) {
  return {
    fixture_id: "fixture-1",
    selection: "ТБ 2.5",
    market: "totals",
    odds: 1.92,
    bookmaker: "Example",
    quote_captured_at: "2026-07-27T10:00:00Z",
    point_edge: 0.06,
    robust_edge: 0.035,
    status: "PAPER_ONLY",
    real_money_eligible: false,
    ...overrides,
  };
}

test("aggregate parser accepts common separators and rejects unknown text", () => {
  assert.deepEqual(parseAggregate("Агрегат 3:2"), {
    home: 3,
    away: 2,
    margin: 1,
    tied: false,
    label: "3:2",
  });
  assert.equal(parseAggregate("первый матч не указан"), null);
});

test("market evidence fails closed for rejected and stale snapshots", () => {
  const fresh = snapshotEvidence(forecast(), NOW);
  assert.equal(fresh.eligible, true);
  assert.ok(fresh.score > 0);
  assert.equal(fresh.pricedRows, 1);

  const rejected = snapshotEvidence(
    forecast({ details: { market_snapshot: { status: "REJECTED", reason: "captured_before_forecast" } } }),
    NOW,
  );
  assert.equal(rejected.eligible, false);
  assert.equal(rejected.score, 0);
  assert.equal(rejected.reason, "captured_before_forecast");

  const staleCaptured = new Date(NOW.getTime() - MAX_QUOTE_AGE_MS - 1).toISOString();
  const stale = snapshotEvidence(
    forecast({
      forecast_generated_at: "2026-07-26T00:00:00Z",
      details: { market_snapshot: { status: "SHADOW_ONLY", captured_at_utc: staleCaptured } },
    }),
    NOW,
  );
  assert.equal(stale.eligible, false);
  assert.equal(stale.reason, "quote_older_than_6h");
});

test("PAPER badge requires a fresh strict candidate and never enables real money", () => {
  const ranking = { candidates: [validPaper()] };
  const rated = rateForecast(forecast(), ranking, NOW);
  assert.equal(rated.bucket, "PAPER CANDIDATE");
  assert.equal(rated.paperCandidate?.realMoneyEligible, false);

  const stale = rateForecast(
    forecast(),
    { candidates: [validPaper({ quote_captured_at: "2026-07-26T10:00:00Z" })] },
    NOW,
  );
  assert.equal(stale.paperCandidate, null);

  const unsafe = rateForecast(
    forecast(),
    { candidates: [validPaper({ real_money_eligible: true })] },
    NOW,
  );
  assert.equal(unsafe.paperCandidate, null);
});

test("large aggregate is routed to scenario review", () => {
  const rated = rateForecast(forecast({ first_leg: "Агрегат 4:0" }), { candidates: [] }, NOW);
  assert.equal(rated.bucket, "SCENARIO REVIEW");
  assert.match(rated.reasons.join(" "), /Крупный агрегат 4:0/);
});

test("weekly builder filters the time window, sorts deterministically and does not mutate input", () => {
  const inside = forecast({ id: "inside", home: "Inside" });
  const soon = forecast({
    id: "soon",
    kickoff_utc: "2026-07-27T18:00:00Z",
    first_leg: "Агрегат 4:0",
  });
  const past = forecast({ id: "past", kickoff_utc: "2026-07-27T11:59:59Z" });
  const distant = forecast({ id: "distant", kickoff_utc: "2026-08-04T12:00:01Z" });
  const payload = {
    generated_at: NOW.toISOString(),
    forecasts: [inside, soon, past, distant],
    paper_candidate_ranking: { candidates: [] },
  };
  const before = JSON.stringify(payload);
  const result = buildWeeklyRatings(payload, NOW);
  assert.equal(result.ratings.length, 2);
  assert.deepEqual(result.ratings.map((row) => row.rank), [1, 2]);
  assert.ok(result.ratings[0].researchScore >= result.ratings[1].researchScore);
  assert.equal(JSON.stringify(payload), before);
});

test("representative inputs produce a useful spread instead of one repeated score", () => {
  const forecasts = Array.from({ length: 30 }, (_, index) =>
    forecast({
      id: `spread-${index}`,
      kickoff_utc: new Date(NOW.getTime() + (6 + index * 4) * 60 * 60 * 1000).toISOString(),
      first_leg: index % 5 === 0 ? `Агрегат ${index % 4}:0` : index % 3 === 0 ? "Агрегат 1:1" : null,
      uncertainty: ["низкая", "средняя", "высокая"][index % 3],
      details: {
        data_quality: { score: 25 + index * 2.3, warnings: index % 2 ? ["lineups_unavailable"] : [] },
        tail_risk: { score: 20 + ((index * 17) % 70) },
        market_snapshot:
          index % 4 === 0
            ? { status: "REJECTED", reason: "no_market_match" }
            : {
                status: "SHADOW_ONLY",
                captured_at_utc: new Date(NOW.getTime() - (index % 5) * 45 * 60 * 1000).toISOString(),
                bookmakers: 1 + (index % 5),
              },
      },
    }),
  );
  const result = buildWeeklyRatings(
    { generated_at: NOW.toISOString(), forecasts, paper_candidate_ranking: { candidates: [] } },
    NOW,
  );
  const distinct = new Set(result.ratings.map((row) => row.researchScore));
  assert.ok(distinct.size >= 15, `expected >=15 distinct scores, got ${distinct.size}`);
});

test("current repository payload is accepted and remains fail-closed", async () => {
  const raw = await readFile(new URL("../../reports/live_predictions.json", import.meta.url), "utf8");
  const payload = JSON.parse(raw);
  const sourceNow = new Date(payload.generated_at);
  const result = buildWeeklyRatings(payload, sourceNow);
  assert.equal(result.schemaVersion, "weekly-research-radar/1.0");
  assert.ok(result.ratings.length > 0);
  assert.equal(new Set(result.ratings.map((row) => row.rank)).size, result.ratings.length);
  assert.ok(result.ratings.every((row, index, rows) => index === 0 || rows[index - 1].researchScore >= row.researchScore));
  assert.ok(
    result.ratings.every(
      (row) => !row.paperCandidate || (row.marketEvidence.eligible && row.paperCandidate.realMoneyEligible === false),
    ),
  );
});

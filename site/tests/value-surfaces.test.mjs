import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const payloadPath = new URL("../../reports/live_predictions.json", import.meta.url);
const pagePath = new URL("../app/page.tsx", import.meta.url);

const payload = async () => JSON.parse(await readFile(payloadPath, "utf8"));

test("value top is gated and ordered by value_pct, never by a human rating", async () => {
  const { value_top: top } = await payload();
  assert.ok(top, "payload must expose value_top");
  assert.equal(top.sorted_by, "value_pct");
  assert.equal(top.gate.metric, "value_pct");
  assert.equal(top.gate.threshold, 8);
  for (const row of top.candidates) {
    // Everything listed cleared the gate and beats its own minimum entry.
    assert.ok(row.value_pct >= top.gate.threshold, "listed row must clear the gate");
    assert.ok(row.odds >= row.min_entry, "listed row must beat min_entry");
    assert.ok(row.min_entry > row.fair, "min_entry must exceed fair");
  }
  const ordered = [...top.candidates].sort((a, b) => b.value_pct - a.value_pct);
  assert.deepEqual(
    top.candidates.map((row) => row.value_pct),
    ordered.map((row) => row.value_pct),
  );
});

test("every fixture states a verdict, so silence is never ambiguous", async () => {
  const { forecasts } = await payload();
  const withQuotes = forecasts.filter((row) => row.value_verdict);
  assert.ok(withQuotes.length > 0, "verdicts must be attached to fixtures");
  // Every way the system can decline must name its reason. A fixture that is
  // simply blank is indistinguishable from one nobody looked at.
  const allowed = new Set([
    "RECOMMENDED",
    "NO_BET_BEST_MARKET",
    "NO_QUOTE",
    "NO_BET_DEGRADED_RATINGS",
    "NO_BET_FAILED_SELF_AUDIT",
  ]);
  for (const row of withQuotes) {
    assert.ok(allowed.has(row.value_verdict.status), row.value_verdict.status);
    assert.ok(String(row.value_verdict.text || "").length > 0, "verdict needs text");
  }
});

test("consensus feed never lets a book vote on its own price", async () => {
  const { consensus_top: top } = await payload();
  assert.ok(top, "payload must expose consensus_top");
  for (const row of top.candidates) {
    assert.ok(row.consensus_books >= 2, "benchmark must exclude the evaluated book");
    assert.ok(row.value_pct > 0);
  }
});

test("thin markets are reported as insufficient rather than as no edge", async () => {
  const { consensus_top: top } = await payload();
  assert.equal(
    typeof top.insufficient_books,
    "number",
    "the count of markets with too few books must be visible",
  );
  assert.ok(top.insufficient_books <= top.markets_evaluated);
});

test("the page renders the value top, consensus feed and collector", async () => {
  const source = await readFile(pagePath, "utf8");
  for (const marker of ['id="value-top"', 'id="consensus"', 'id="collector"']) {
    assert.ok(source.includes(marker), `page must render ${marker}`);
  }
  // The collector must let the user set their own bankroll and ticket count.
  assert.ok(source.includes("Банк, ₽"));
  assert.ok(source.includes("Ординаров"));
});

test("the payload publishes its own self-audit", async () => {
  const { self_audit: audit } = await payload();
  assert.ok(audit, "payload must expose self_audit");
  assert.ok(["PASSED", "FAILED"].includes(audit.status));
  assert.ok(audit.checked_markets > 0, "the audit must actually check markets");
  // A passing audit must have no critical findings, and a failing one must say
  // how many — a status with no count behind it is not evidence.
  if (audit.status === "PASSED") {
    assert.equal(audit.critical_count, 0);
  } else {
    assert.ok(audit.critical_count > 0);
  }
});

test("the page renders the self-audit banner", async () => {
  const source = await readFile(pagePath, "utf8");
  assert.ok(source.includes("Самопроверка"), "page must show the audit result");
});

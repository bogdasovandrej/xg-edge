import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const payloadPath = new URL("../../reports/live_predictions.json", import.meta.url);

test("public payload exposes a coherent Research Day workflow", async () => {
  const payload = JSON.parse(await readFile(payloadPath, "utf8"));
  const workflow = payload.research_workflow;
  assert.equal(workflow.schema_version, "uefa-research-workflow/2.0");
  assert.equal(workflow.summary.machine_scanned, workflow.records.length);
  assert.equal(workflow.summary.preline_selected, workflow.selected_fixture_ids.length);
  assert.equal(
    workflow.summary.preline_selected,
    workflow.summary.exploitation_slots + workflow.summary.exploration_slots,
  );
  for (const fixtureId of workflow.selected_fixture_ids) {
    const candidates = workflow.market_sets[fixtureId].candidates;
    assert.ok(candidates.length <= 3);
    assert.equal(new Set(candidates.map((row) => row.market_cluster)).size, candidates.length);
    assert.ok(candidates.every((row) =>
      row.status === "WATCH" && row.trigger_price > row.fair_odds_conservative && row.fair_odds_conservative > 1
    ));
  }
});

test("PRELINE chat packets cover every selected fixture once in batches of five", async () => {
  const payload = JSON.parse(await readFile(payloadPath, "utf8"));
  const ids = payload.preline_chat_batches.flatMap((batch) => {
    assert.equal(batch.schema_version, "chat-research-packet/1.0");
    assert.ok(batch.fixtures.length >= 1 && batch.fixtures.length <= 5);
    return batch.fixtures.map((row) => row.fixture_id);
  });
  assert.deepEqual(ids.sort(), [...payload.research_workflow.selected_fixture_ids].sort());
  assert.equal(new Set(ids).size, ids.length);
});

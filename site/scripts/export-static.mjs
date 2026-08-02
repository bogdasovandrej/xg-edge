import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const repository = fileURLToPath(new URL("../../", import.meta.url));
const output = fileURLToPath(new URL("../out-static/", import.meta.url));
const workerUrl = new URL("../dist/server/index.js", import.meta.url);
workerUrl.searchParams.set("export", Date.now().toString());
const { default: worker } = await import(workerUrl.href);

function newestByFixture(rows, timestampField) {
  const newest = new Map();
  for (const row of rows || []) {
    const key = String(row?.fixture_key || "");
    if (!key) continue;
    const previous = newest.get(key);
    if (!previous || String(row?.[timestampField] || "") >= String(previous?.[timestampField] || "")) {
      newest.set(key, row);
    }
  }
  return newest;
}

export function compactForecastArchive(document) {
  const latestResults = newestByFixture(document?.results, "observed_at");
  const completedKeys = new Set(latestResults.keys());
  const latestFixtures = newestByFixture(
    (document?.fixture_snapshots || []).filter((row) => completedKeys.has(String(row?.fixture_key || ""))),
    "observed_at",
  );
  const latestForecasts = newestByFixture(
    (document?.forecasts || []).filter((row) => completedKeys.has(String(row?.fixture_key || ""))),
    "generated_at",
  );
  return {
    schema_version: document?.schema_version,
    updated_at: document?.updated_at,
    fixture_snapshots: [...latestFixtures.values()].map((row) => ({
      fixture_key: row.fixture_key,
      fixture: Object.fromEntries(
        ["id", "competition", "kickoff_utc", "home", "away"]
          .map((key) => [key, row.fixture?.[key]]),
      ),
    })),
    forecasts: [...latestForecasts.values()].map((row) => Object.fromEntries(
      [
        "forecast_id", "fixture_key", "fixture_id", "kickoff_utc", "generated_at",
        "model", "probability_basis", "probabilities", "expected_goals",
        "model_market_forecasts",
      ].map((key) => [key, row[key]]),
    )),
    results: [...latestResults.values()].map((row) => Object.fromEntries(
      ["fixture_key", "fixture_id", "home_goals_90", "away_goals_90", "outcome"]
        .map((key) => [key, row[key]]),
    )),
  };
}

const response = await worker.fetch(
  new Request("https://example.invalid/", { headers: { accept: "text/html" } }),
  { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
  { waitUntil() {}, passThroughOnException() {} },
);
if (!response.ok) throw new Error(`static render failed: HTTP ${response.status}`);

let html = await response.text();
html = html.replaceAll("/assets/", "/xg-edge/assets/");

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
await cp(`${root}dist/client`, output, { recursive: true });
await mkdir(`${output}data`, { recursive: true });
await cp(`${repository}reports/live_predictions.json`, `${output}data/live_predictions.json`);
await cp(`${repository}reports/live/prospective_clv.json`, `${output}data/prospective_clv.json`);
await cp(`${repository}reports/live/prospective_clv_v2.json`, `${output}data/prospective_clv_v2.json`);
const archive = JSON.parse(await readFile(`${repository}reports/live/forecast_archive.json`, "utf8"));
await writeFile(
  `${output}data/forecast_archive.json`,
  `${JSON.stringify(compactForecastArchive(archive))}\n`,
  "utf8",
);
await writeFile(`${output}index.html`, html, "utf8");
await writeFile(`${output}.nojekyll`, "", "utf8");
console.log(`Static site exported to ${output}`);

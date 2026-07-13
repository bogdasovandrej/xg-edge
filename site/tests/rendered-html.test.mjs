import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the xg-edge live dashboard", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<html lang="ru">/i);
  assert.match(html, /<title>xg-edge — честные футбольные вероятности<\/title>/i);
  assert.match(html, /Вероятности/);
  assert.match(html, /NO BET/);
  assert.match(html, /WAIT/);
  assert.match(html, /Prospective CLV/);
  assert.match(html, /95% CI CLV/);
  assert.match(html, /Независимая выборка/);
  assert.match(html, /0\s*<!-- -->\s*\/\s*<!-- -->\s*100/);
  assert.match(html, /CLV пока не измерен/);
  assert.match(html, /Франция/);
  assert.match(html, /Поиск матча/);
  assert.match(html, /Открыть полный разбор/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton|Your site is taking shape/i);
});

test("uses the public snapshot and contains no disposable starter", async () => {
  const [page, layout, packageJson] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);

  assert.match(page, /reports\/live_predictions\.json/);
  assert.match(page, /market_anchored|market_fair/);
  assert.match(page, /Tail risk/);
  assert.match(page, /Кандидат не является рекомендацией/);
  assert.match(page, /Asia\/Yekaterinburg/);
  assert.match(page, /recommendation/);
  assert.match(page, /payload\.prospective_clv/);
  assert.match(page, /summary\?\.min_independent_matches/);
  assert.match(page, /positive_clv_confirmed/);
  assert.match(page, /summary\?\.cohorts/);
  assert.match(page, /Промежуточный CLV скрыт/);
  assert.match(page, /cohort gate/);
  assert.doesNotMatch(page, /−4\.83%|−8\.00%|0\.9834/);
  assert.match(page, /details\?\.market_snapshot/);
  assert.match(page, /details\?\.market_candidates/);
  assert.match(page, /Снимок рынка/);
  assert.match(page, /captured_at_utc/);
  assert.match(page, /Топ-3 shadow-кандидата/);
  assert.match(page, /snapshot\.status === "SHADOW_ONLY" && capturedAt != null/);
  assert.match(page, /candidate\.status === "SHADOW_ONLY"/);
  assert.match(page, /older_than_ttl/);
  assert.match(page, /Цены и shadow-кандидаты скрыты/);
  assert.match(page, /details\?\.candidate_bets \|\| \[\]/);
  assert.doesNotMatch(page, /live price/i);
  assert.match(layout, /lang="ru"/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
  await assert.rejects(access(new URL("../app/_sites-preview", import.meta.url)));
});

test("static export prefixes every asset URL for GitHub Pages", async () => {
  await import(`../scripts/export-static.mjs?test=${Date.now()}`);
  const html = await readFile(new URL("../out-static/index.html", import.meta.url), "utf8");

  assert.doesNotMatch(html, /["'(]\/assets\//);
  assert.match(html, /import\("\/xg-edge\/assets\//);
});

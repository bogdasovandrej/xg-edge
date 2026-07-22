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
  assert.match(html, /Прошедшие матчи не показываются как будущие/);
  assert.match(html, /Поиск матча/);
  assert.match(html, /PAPER ONLY/);
  assert.match(html, /Турнир PAPER-стратегий|Автоматическая виртуальная лаборатория/);
  assert.match(html, /10 000/);
  assert.match(html, /Только edge от 5 п.п./);
  assert.match(html, /Архив проверенных/);
  assert.match(html, /Загружаем prospective-журнал/);
  assert.match(html, /Mean Brier/);
  assert.match(html, /Mean log loss/);
  assert.match(html, /PAPER ONLY/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton|Your site is taking shape/i);
});

test("uses the public snapshot and contains no disposable starter", async () => {
  const [page, layout, packageJson] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);

  assert.match(page, /SITE_DATA_ROOT = "\/xg-edge\/data"/);
  assert.match(page, /live_predictions\.json/);
  assert.match(page, /market_anchored|market_fair/);
  assert.match(page, /Tail risk/);
  assert.match(page, /PAPER-кандидат не является рекомендацией/);
  assert.match(page, /Asia\/Yekaterinburg/);
  assert.match(page, /recommendation/);
  assert.match(page, /payload\.prospective_clv/);
  assert.match(page, /summary\?\.min_independent_matches/);
  assert.match(page, /positive_clv_confirmed/);
  assert.match(page, /summary\?\.cohorts/);
  assert.match(page, /Промежуточный CLV скрыт/);
  assert.match(page, /cohort gate/);
  assert.match(page, /Сценарии счёта — не точный прогноз/);
  assert.match(page, /Остальные счета/);
  assert.match(page, /Очередь PAPER-кандидатов/);
  assert.match(page, /строгий фильтр не пропустил/i);
  assert.match(page, /PAPER BANKROLL/);
  assert.match(page, /PaperTradingLab/);
  assert.match(page, /payload\.paper_trading/);
  assert.match(page, /После разорения новый цикл снова начинается с 10 000 ₽/);
  assert.match(page, /Экспрессы:/);
  assert.match(page, /speed_to_target_used_for_ranking/);
  assert.match(page, /PROSPECTIVE_URL/);
  assert.match(page, /prospective_clv\.json/);
  assert.match(page, /FORECAST_ARCHIVE_URL/);
  assert.match(page, /forecast_archive\.json/);
  assert.match(page, /match-evidence-archive\/1\.0/);
  assert.match(page, /CompletedForecastArchive/);
  assert.match(page, /forecastArchive/);
  assert.match(page, /predicted === actual/);
  assert.match(page, /Top-1 calib\. gap/);
  assert.match(page, /Метрики показаны как «—», а не как нулевой успех/);
  assert.match(page, /Brier и log loss оценивают качество вероятностей/);
  assert.doesNotMatch(page, /демонстрационные матчи:\s*\[/i);
  assert.match(page, /Пуассоновский хвост не откалиброван/);
  assert.match(page, /forecast\.p_over35/);
  assert.match(page, /forecast\.p_over45/);
  assert.doesNotMatch(page, /Счёт-мода/);
  assert.doesNotMatch(page, /−4\.83%|−8\.00%|0\.9834/);
  assert.match(page, /details\?\.market_snapshot/);
  assert.match(page, /details\?\.market_candidates/);
  assert.match(page, /details\?\.expanded_market_candidates/);
  assert.match(page, /Снимок рынка/);
  assert.match(page, /captured_at_utc/);
  assert.match(page, /Топ-3 shadow-кандидата/);
  assert.match(page, /snapshot\.status === "SHADOW_ONLY" && capturedAt != null/);
  assert.match(page, /candidate\.status === "SHADOW_ONLY"/);
  assert.match(page, /older_than_ttl/);
  assert.match(page, /Цены и shadow-кандидаты скрыты/);
  assert.match(page, /Последние официальные матчи/);
  assert.match(page, /товарищеские исключены/);
  assert.match(page, /npxG убирает пенальти/);
  assert.match(page, /Красные карточки|красные карточки/);
  assert.match(page, /Алгоритмический разбор/);
  assert.match(page, /Тоталы и ОЗ: модель против цены/);
  assert.match(page, /Азиатская фора хозяев/);
  assert.match(page, /Положительный расчётный EV не равен доказанной прибыли/);
  assert.match(page, /details\?\.market_candidates \|\| \[\]/);
  assert.match(page, /details\?\.expanded_market_candidates \|\| \[\]/);
  assert.match(page, /Топ-3 ставок по реальным котировкам/);
  assert.match(page, /finiteNumber\(candidate\.market_odds\)/);
  assert.match(page, /marketSnapshotEligible/);
  assert.match(page, /candidate\.status === "EXPERIMENTAL_SHADOW"/);
  assert.match(page, /не считаются ставками и не попадают в топ-3/);
  assert.doesNotMatch(page, /const candidates = details\?\.candidate_bets/);
  assert.doesNotMatch(page, /live price/i);
  assert.match(layout, /lang="ru"/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
  await assert.rejects(access(new URL("../app/_sites-preview", import.meta.url)));
});

test("static export prefixes every asset URL for GitHub Pages", async () => {
  await import(`../scripts/export-static.mjs?test=${Date.now()}`);
  const [html, livePayload, prospectiveLedger, forecastArchive] = await Promise.all([
    readFile(new URL("../out-static/index.html", import.meta.url), "utf8"),
    readFile(new URL("../out-static/data/live_predictions.json", import.meta.url), "utf8"),
    readFile(new URL("../out-static/data/prospective_clv.json", import.meta.url), "utf8"),
    readFile(new URL("../out-static/data/forecast_archive.json", import.meta.url), "utf8"),
  ]);

  assert.doesNotMatch(html, /["'(]\/assets\//);
  assert.match(html, /import\("\/xg-edge\/assets\//);
  assert.ok(Array.isArray(JSON.parse(livePayload).forecasts));
  assert.match(JSON.parse(prospectiveLedger).schema_version, /^prospective-clv\//);
  assert.equal(JSON.parse(forecastArchive).schema_version, "match-evidence-archive/1.0");
});

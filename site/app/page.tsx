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
};

type LivePayload = {
  generated_at: string;
  status: string;
  forecasts: Forecast[];
};

const DATA_URL =
  "https://raw.githubusercontent.com/bogdasovandrej/xg-edge/main/reports/live_predictions.json";

const FALLBACK: LivePayload = {
  generated_at: "2026-07-13T00:00:00Z",
  status: "official-fixtures-only",
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
    </article>
  );
}

export default function Home() {
  const [payload, setPayload] = useState<LivePayload>(FALLBACK);
  const [filter, setFilter] = useState("all");
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
    () => payload.forecasts.filter((item) =>
      filter === "all" ||
      (filter === "world-cup" ? item.competition.includes("World Cup") : item.competition.includes("Champions"))
    ),
    [payload, filter],
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
        <aside className="truth-panel">
          <span className="panel-label">Текущий вердикт</span>
          <strong>NO BET</strong>
          <p>Holdout 2025/26</p>
          <dl>
            <div><dt>Средний CLV</dt><dd>−7.13%</dd></div>
            <div><dt>Kelly ROI</dt><dd>−9.2%</dd></div>
            <div><dt>Live-доказательство</dt><dd>нет</dd></div>
          </dl>
          <small>Красный статус снимется только если нижняя граница live CLV станет выше нуля.</small>
        </aside>
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
        <div className="forecast-grid">
          {forecasts.map((forecast) => <ForecastCard key={forecast.id} forecast={forecast} />)}
        </div>
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

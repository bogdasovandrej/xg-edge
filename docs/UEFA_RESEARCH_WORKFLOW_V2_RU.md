# UEFA Research Workflow v2

Статус: `PREMATCH / PAPER_ONLY / MODEL_IN_QUARANTINE`.

## Цель

Новый контур не сокращает широкий поиск до нескольких очевидных матчей. Он
автоматизирует механическую часть исследования и оставляет человеку только
кандидатов, где глубокий разбор оправдан:

```text
все официальные fixtures
→ machine scan каждого матча
→ 20 PRELINE: 17 exploitation + 3 exploration
→ до 3 diverse market hypotheses на матч
→ 4 ChatGPT packets по 5 матчей
→ exact execution quote + trigger monitor
→ deep audit / Final XI
→ PAPER portfolio
→ exact settlement + CLV/calibration/post-match audit
```

Trigger никогда не означает автоматическое одобрение, а executable quote не
подменяется расчётным fair.

## Machine scan

Каждый будущий официальный fixture получает `ResearchPriorityRecord` и статус
`MACHINE_SCANNED`. Балл 0–100 означает полезность человеческого исследования,
а не вероятность победы или value. Версия `research-priority/1.0` учитывает:

- качество и полноту данных;
- ясность сценария и tail risk;
- стабильность модели;
- ширину рассчитанных market families;
- ожидаемую ликвидность;
- явные штрафы за отсутствующий состав и ненадёжную identity.

Для каждого балла публикуется decomposition. Матчи вне двадцатки сохраняются
как `NOT_PRELINE_SELECTED` и не исчезают из наблюдения.

## PRELINE-20 и market sets

Default — 20 матчей, из них 3 exploration slots. Exploration выбирается
детерминированно из недопредставленных турниров и более нестандартных сценариев,
чтобы selector не замыкался на одинаковых фаворитах.

На матч разрешено 0–3 кандидата. Diversity задаётся versioned taxonomy:
соседние `Under 2.5`, `Under 3.0`, `Under 3.5` относятся к одному
`TOTAL_UNDER` cluster и не заполняют все три места. Отдельно проверяются totals,
team totals, BTTS, DNB/double chance, Asian handicap и qualification, когда
модель/контракт поддерживает рынок.

На PRELINE-стадии сохраняются central/conservative probabilities, fair,
trigger, тезис, антитезис, failure modes и missing data. Это `WATCH`, а не bet.

## Trigger и quote identity

Execution quote обязан совпасть по `fixture_id + market + selection + line` и
иметь bookmaker/source/timestamp. Post-kickoff quote запрещён invariant-ом.

- ниже trigger более чем на 2% → `WATCH`;
- около trigger → `NEAR_TRIGGER`;
- не ниже trigger → `TRIGGER_HIT`;
- сдвиг reference price минимум на 7% → `LARGE_MOVE_REAUDIT`;
- сильный новый сигнал вне PRELINE-20 → `LATE_WILDCARD`.

Все эти состояния требуют human audit. Реальные ставки код не размещает.

## ChatGPT handoff

Двадцать матчей экспортируются четырьмя JSON/Markdown пакетами по пять.
Пакет содержит identity, официальный контекст, доступные process metrics,
score distribution, market set, fair/trigger и честные `UNKNOWN`.

Import использует `human_preline_audit/1.0`. Проверяются fixture, stage и каждый
candidate ID. Валидный импорт превращается в immutable snapshot; исправление
создаёт новую версию, а не перезаписывает старую.

## PAPER и settlement

Ranking `paper-candidate-ranking/1.1` допускает до трёх разных market clusters
на матч. Ledger `paper-trading-ledger/1.2` ключует позицию по candidate ID,
поэтому рекомендуемый тотал или handicap не превращается обратно в 1X2.

Generic payout engine поддерживает `WIN`, `HALF_WIN`, `PUSH`, `HALF_LOSS`,
`LOSS`, `VOID`. Quarter lines делятся точно:

- `+1.25 = 0.5 × +1.0 + 0.5 × +1.5`;
- `Under 2.75 = 0.5 × Under 2.5 + 0.5 × Under 3.0`.

Fair и trigger для push-aware рынков находятся по expected return всего
settlement distribution, а не через ошибочное `1 / p`.

## Что ещё остаётся следующим этапом

Qualification settlement по официальному `qualified_team_id`, tournament
Monte Carlo, joint same-match markets, deep audit state machine, Final XI gate,
portfolio correlation/exposure и post-match trend engine остаются отдельными
логическими фазами. До них интерфейс не должен обещать APPROVED portfolio.

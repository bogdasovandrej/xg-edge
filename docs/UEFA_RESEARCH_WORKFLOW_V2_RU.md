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

## Deep audit, Final XI и portfolio (добавлено в этой фазе)

Trigger hit, near-trigger, large-move и late-wildcard кандидаты собираются в
deep-audit очередь (`src/xgedge/research/deep_audit.py`) без принудительного
минимума или максимума и пакуются по 4 (не по 5, как PRELINE) для ChatGPT.
Импорт использует уже существующую схему `human_deep_audit/1.0`
(`xgedge.research.handoff`); решение `PASS` из схемы нормализуется в
`REJECTED` состояния кандидата. Machine-вероятность/EV и человеческие
`VALUE`/`ROBUSTNESS`/`ACCA QUALITY` остаются разными полями и не
смешиваются в один скор. Более поздний `LARGE_MOVE_REAUDIT` после импорта
audit переводит уже одобренного кандидата в `STALE_AUDIT`.

`src/xgedge/research/final_xi.py` отдельно проверяет: (1) официальный состав
против допущений audit — потеря допущенного игрока, ключевой роли
(вратарь/центральный защитник/главный форвард) или неожиданная замена схемы
дают `FINAL_CHECK_FAILED`, требующий новой вероятности; (2) цену исполнения —
`final_price` ниже `minimum_entry` даёт `PASS_PRICE` независимо от того,
насколько положительным был deep audit.

`src/xgedge/decision/portfolio.py` — первый модуль конвейера, который
предлагает реальные ставки, и остаётся строго `PAPER_ONLY`. Он принимает
только `APPROVED` + `FINAL_CHECK_PASSED` кандидатов с положительным
conservative EV, обрезает каждый матч до `max_distinct_markets_per_match`
(default 2), считает singles с одной допустимой ставкой 500 RUB в день
(`value >= 8.4`, `robustness >= 8.2`, `data_quality >= 80` как рабочая
интерпретация "B+", conservative EV > 0, нет unresolved warning), строит
кросс-матчевые doubles (default `accumulator_stake_rub = 250`), запрещает
same-match ногу в одном тикете без явно переданной joint-вероятности
(`REJECT_CORRELATED_SAME_MATCH_LEGS`), ограничивает переиспользование одной
точной ставки (`max_ticket_uses_per_exact_leg = 2`, включая её собственный
single), запрещает 4+ ног и предупреждает (или отклоняет, по конфигу) при
превышении доли банка на один архетип (`archetype_exposure_cap = 0.30`) из
`src/xgedge/markets/archetypes.py`. Резерв банка никогда не тратится
принудительно: `unused_rub` — нормальное состояние, а не ошибка.

`src/xgedge/markets/joint.py` даёт точную (не наивное произведение)
same-match joint-вероятность через score matrix — именно её и обязан
передать вызывающий код, чтобы включить same-match тикет в portfolio.

`src/xgedge/markets/qualification.py` settle-ит рынок qualification только по
официально подтверждённому `qualified_team_id`; вероятность по-прежнему
считает существующий двухматчевый Monte Carlo
(`xgedge.experiments.ucl_qualifying.simulate_qualification`, aggregate +
extra time + 50/50 пенальти) — модуль не переизобретает эту симуляцию, а
добавляет недостающее: fail-closed settlement и versioned
`CompetitionAdvanceRules` контракт, документирующий допущения (away goals
отключены с 2021/22, extra time и пенальти включены).

## Что ещё остаётся следующим этапом

Полный game-state engine (раздел 32 воркфлоу), post-match trend engine,
подключение portfolio/deep-audit/Final XI к `build_live_payload.py` и
публичному сайту, ручной ввод российской execution-цены как отдельный UI и
провайдер (`ExecutionQuoteProvider`), а также автоматический combo-optimizer
для triples остаются отдельными логическими фазами. До них интерфейс не
должен обещать конечный portfolio на сайте — сейчас все шесть модулей этой
фазы вызываются программно и покрыты тестами, но не подключены к GitHub
Actions или публичному JSON.

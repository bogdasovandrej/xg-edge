# Состояние глубокой доработки xg-edge

Обновлено: 2026-08-18. Ветка: `codex/archive-storage-fix` от актуального
`origin/main`.

Этот файл — точка восстановления для следующей сессии. Перед продолжением
нужно прочитать его, затем `git status`, `git diff` и запустить указанные ниже
тесты. Не называть этап готовым или задеплоенным, пока все quality gates и
публикация реально не завершены.

## Подтверждённая первопричина старой пустышки

- `decision/ranking.py` завершал каждый матч через `match_rows[0]` и оставлял
  ровно один рынок.
- PAPER ledger был ключован только `fixture_id` и явно запрещал второй рынок
  одного матча.
- settlement умел только `WIN/LOSS/PUSH/VOID`, а линии `.25/.75` отклонялись.
- Из-за этого рекомендация на тотал/ОЗ/AH не могла пройти полный путь
  «кандидат → PAPER bet → официальный результат → баланс → архив».
- В текущих live-данных большинство матчей имеет низкое качество контекста;
  это честная причина не делать автоматическую ставку, но не причина скрывать
  model hypotheses и research queue.

## Уже внесено в рабочее дерево

1. `markets/settlement.py`: единый payout engine с `WIN`, `HALF_WIN`, `PUSH`,
   `HALF_LOSS`, `LOSS`, `VOID`, expected return, fair price и trigger price.
2. `markets/paper_markets.py`: точный split и settlement четвертных Asian
   lines; распределение payout states из score matrix.
3. `markets/taxonomy.py`: versioned market family/cluster taxonomy.
4. `decision/ranking.py`: схема ranking 1.1, до трёх разнообразных кандидатов
   на матч, отдельные global/per-match limits, candidate IDs.
5. `simulation/paper.py`: правильная выплата и отдельные счётчики half-win /
   half-loss.
6. `simulation/ledger.py`: начата миграция ledger 1.0/1.1 → 1.2, несколько
   exact-market candidates одного fixture, settlement по candidate ID.
7. `research/screening.py`: deterministic machine scan, default 20 =
   17 exploitation + 3 exploration, decomposition и причины исключения.
8. `research/preline.py`: до трёх diverse pre-line hypotheses, fair/trigger,
   явный status WATCH без ложного объявления ставки.
9. `research/handoff.py`: 4×5 ChatGPT packets и строгий immutable import
   `human_preline_audit/1.0`.
10. `research/triggers.py`: exact quote identity, WATCH/NEAR/TRIGGER,
    LARGE_MOVE_REAUDIT, LATE_WILDCARD и запрет post-kickoff leakage.
11. `build_live_payload.py`: research workflow, summary и chat batches
    добавлены в live payload.
12. Добавлены/обновлены тесты для multi-market ranking, quarter Asian,
    generic trigger math, 53→20, handoff/import и leakage.

## Текущая проверка

- Исходный baseline до правок: весь Python suite зелёный; site tests 10/10.
- После исправления `data_quality: null` полный Python suite прошёл повторно
  несколько раз без ошибок.
- Site production build и 12 Node tests прошли; ESLint и Python compileall
  прошли; `git diff --check` не нашёл ошибок.
- Добавлен и проходит обязательный end-to-end test для `Over 2.75`:
  recommendation → PAPER → 2:1 → HALF_WIN → 10 050 ₽ → market summary.
- Реальный checked-in PAPER ledger мигрирован с 1.1 на 1.2 без удаления event
  history; live payload пересобран с research workflow.

## Точная очередь продолжения

1. Основная доработка уже в `main` через PR #26; Pages включён и первый
   deployment завершился успешно.
2. Live/data workflow выполнил расчёты и тесты, но публикация обнаружила
   инфраструктурный лимит архива; завершить описанное ниже исправление.
3. Повторить live/data workflow, проверить GitHub Pages и опубликовать тот же
   проверенный source через Sites.
4. Следующая backend-фаза: qualification contract + tournament Monte Carlo +
   official `qualified_team_id` settlement.
5. Затем: joint markets, deep audit/Final XI gate, portfolio exposure и trends.

## Инцидент автоматического обновления 18.08.2026

- Расчёт live-predictions успешно дошёл до публикации, но GitHub отклонил
  `forecast_archive.json`: файл вырос до 102,4 МБ и превысил лимит 100 МБ.
- Причина не в модели и не в API: append-only архив хранит много неизменяемых
  снимков прогнозов и всех рассчитанных рынков.
- Исправление: канонический внутренний архив переносится в детерминированный
  `forecast_archive.json.gz`; CLI читает обычный JSON и gzip, запись атомарна,
  повторный запуск не создаёт ложный diff из-за gzip-заголовка.
- Статический сайт по-прежнему получает компактный публичный
  `data/forecast_archive.json`, поэтому формат клиентского API не меняется.
- GitHub Pages остаётся единым автоматически обновляемым data origin. Интерфейс
  Sites читает тот же CORS-enabled feed по абсолютному URL, поэтому не зависит
  от включённого ноутбука и не застревает на пустом локальном fallback.
- Старая привязка Sites `appgprj_6a67165c69908191bc9d5311f277664f`
  подтверждённо отвечает `404 project_not_found`; для публикации создаётся одна
  новая привязка, а её точный ID сохраняется в `site/.openai/hosting.json`.

## Намеренно не обещать

- Реальные ставки и real-money execution не добавляются.
- Trigger hit не равен APPROVED.
- CLV/edge не подтверждены.
- Не выдумывать отсутствующие составы, травмы, event-level xG или российскую
  executable quote.

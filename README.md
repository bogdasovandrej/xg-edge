# xg-edge — вероятностная модель футбольных исходов

[![CI](https://github.com/bogdasovandrej/xg-edge/actions/workflows/ci.yml/badge.svg)](https://github.com/bogdasovandrej/xg-edge/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Version](https://img.shields.io/badge/version-0.4.1-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Исследовательский проект: калиброванные вероятности 1X2, тоталов, BTTS,
азиатских фор и точного счёта из xG. Ядро — двойной Пуассон с поправкой
Dixon–Coles; проверка — хронологический walk-forward, Brier, log-loss,
reliability и CLV против закрывающей линии.

Главный результат честный и отрицательный: на зафиксированном holdout 2025/26
у модели нет преимущества над рынком. Средний CLV равен −7.13%, кластерный 95%
CI [−8.12%, −6.16%]. Следовательно, текущий вывод системы — не ставить.

## Live v0.4

Сайт прогнозов: **https://bogdasovandrej.github.io/xg-edge/**

Версия 0.4 добавляет отдельный контур будущих матчей и не смешивает его с
ретроспективным backtest:

- result-free fixture contract и обучение строго на данных до kickoff;
- официальный календарь FIFA и UEFA, включая время, стадион, раунд, aggregate
  и назначенного судью;
- отдельная модель сборных для ЧМ-2026: предтурнирный FIFA ranking prior +
  завершённые матчи турнира + Poisson/Dixon–Coles;
- экспериментальная модель квалификации ЛЧ: ClubElo + Poisson, отдельная
  симуляция вероятности прохода с учётом первого матча;
- реестр сезона 2026/27 для EPL, La Liga, Bundesliga, Serie A и Ligue 1;
- GitHub Actions обновляет публичный JSON каждые шесть часов;
- live-сайт читает новый snapshot без ручного перезапуска;
- market anchor уменьшает longshot-мискалибровку, но CLV guard оставляет
  глобальный статус `NO BET`.
- раскрываемый match dossier показывает point-in-time Elo, до десяти последних
  официальных матчей, доступность npxG, составов, отсутствующих, судьи и погоды;
- поиск фильтрует матчи по команде, турниру, судье и стадиону;
- tail-risk score показывает хрупкость оценки и пропуски данных, но не выдаётся
  за способность предсказывать «чёрных лебедей».

Актуальные экспериментальные прогнозы на 90 минут, snapshot 2026-07-13:

| Матч | П1 | X | П2 | Счёт-мода |
| --- | ---: | ---: | ---: | ---: |
| Франция — Испания | 40.0% | 29.6% | 30.4% | 1–1 |
| Англия — Аргентина | 36.5% | 30.8% | 32.7% | 1–1 |

Это market-anchored вероятности по проверенной opening-линии. Для
Англия—Аргентина фундаментальная goals-only модель по-прежнему даёт
32.2% / 25.8% / 42.0%; расхождение явно показывается в карточке матча, а не
скрывается. Все три текущих 1X2-кандидата имеют неположительный point edge,
поэтому рекомендация остаётся `NO BET`.

На locked shadow holdout market-anchor снизил log-loss с 1.0009 у raw model до
0.9834 и оказался чуть лучше opening market 0.9846. Но средний CLV 28 shadow
кандидатов остался −4.83%, 95% CI [−8.00%, −1.82%]. Это улучшение калибровки,
а не доказанный betting edge; для открытия gate нужно минимум 100 независимых
проспективных наблюдений с положительной нижней границей CLV.

Для ближайшей квалификации ЛЧ рассчитаны 12 из 14 матчей. `ML Vitebsk` и
`Atert Bissen` отсутствуют в датированном ClubElo snapshot, поэтому система
возвращает `no_prediction`, а не выдумывает рейтинг. Все live-оценки помечены
experimental и не являются рекомендациями для ставок.

## Протокол без подглядывания

Версия 0.2 разделяет разработку и финальную проверку.

1. Development: данные до 2025-07-01; walk-forward окна 2023/24–2024/25,
   745 прогнозов.
2. На development зафиксированы raw xG, отсутствие opponent-нормировки,
   полураспад 180 дней и GLM как primary model.
3. Locked retrospective holdout: сезон 2025/26, 375 пригодных прогнозов.
4. Результаты holdout не используются для перенастройки версии 0.2.

Это ретроспективный, а не настоящий live-эксперимент: проект собран после
завершения сезона. Поэтому окончательное подтверждение возможно только на
проспективно сохранённых прогнозах 2026/27.

На development GLM выиграл у GBM по log-loss: 0.9554 против 0.9675.
На holdout GBM оказался лучше GLM, но после фиксации протокола менять primary
model по этому результату запрещено.

## Данные

| Источник | Содержимое | Роль |
| --- | --- | --- |
| Understat | xG, npxG, PPDA, deep completions | признаки формы |
| football-data.co.uk | результаты и opening/closing odds | рынок и CLV |
| FIFA API | календарь, результаты, timelines, рейтинг сборных | ЧМ и контроль 90 минут |
| UEFA API | fixtures, aggregate, судьи, lineups/events | ЛЧ и point-in-time контекст |
| ClubElo | датированный рейтинг клубов | baseline квалификации ЛЧ |
| Open-Meteo | почасовой прогноз по координатам/городу стадиона | погодный контекст, не самостоятельный betting signal |

Проверенный исторический охват: АПЛ, пять сезонов 2021/22–2025/26, 1900
матчей. Join источников — 100%. Для топ-5 лиг добавлен воспроизводимый реестр
2026/27 и загрузчики; данные нового сезона появятся только после публикации
источниками и не заменяются placeholder-строками.
Сырые файлы не коммитятся; они воспроизводятся скриптом загрузки.

Pinnacle closing используется как sharp benchmark и для 1X2, и для тотала 2.5.
В поздней части 2025/26 Pinnacle отсутствует в источнике, поэтому честное общее
подмножество holdout содержит 205 матчей; пропуски не заменяются Bet365.

## Архитектура

~~~text
raw football-data + Understat
  -> cleaned canonical matches
  -> causal features (raw xG, 180-day decay, venue blend)
  -> Poisson GLM -> lambda_home / lambda_away
  -> Dixon-Coles score matrix
  -> 1X2 / totals / BTTS / AH / exact score
  -> edge filter / quarter Kelly capped at 2%
  -> Brier / log-loss / reliability / clustered CLV
~~~

Все матчи одной календарной даты обрабатываются одним атомарным батчем.
Результат строки не может изменить признаки другой строки той же даты.
Банкролл также рассчитывает все ставки дня от банка на начало дня.

## Holdout 2025/26

### 1X2

Метрики со звёздочкой считаются на общем подмножестве n = 205, где есть
Pinnacle closing.

| Модель | Brier, n=375 | Log-loss, n=375 | Brier* | Log-loss* |
| --- | ---: | ---: | ---: | ---: |
| glm_dc primary | 0.6157 | 1.0276 | 0.5959 | 1.0006 |
| gbm_dc locked challenger | 0.6109 | 1.0210 | 0.5903 | 0.9933 |
| dc_classic | 0.6184 | 1.0375 | 0.5911 | 1.0068 |
| goals_poisson | 0.6253 | 1.1055 | 0.5907 | 1.1135 |
| uniform | 0.6667 | 1.0986 | 0.6667 | 1.0986 |
| Pinnacle closing | — | — | 0.5874 | 0.9822 |

Primary GLM лучше наивных full-sample бейзлайнов, но хуже closing market на
сопоставимом подмножестве.

### Тотал 2.5 на общем подмножестве

| Модель | Brier* | Log-loss* |
| --- | ---: | ---: |
| glm_dc primary | 0.2453 | 0.6837 |
| goals_poisson | 0.2389 | 0.6749 |
| Pinnacle closing | 0.2428 | 0.6784 |

На этом небольшом срезе наивный goal-Poisson оказался лучше рынка по scoring
rules. Это не считается доказательством торгового эджа: отбор ставок primary
model всё равно дал строго отрицательный CLV.

### Решения и CLV

| Показатель | Значение |
| --- | ---: |
| Ставок по EV-фильтру | 467 |
| Ставок с доступным Pinnacle CLV | 270 |
| Независимых match-кластеров | 188 |
| Средний CLV | −7.13% |
| 95% cluster-bootstrap CI | [−8.12%, −6.16%] |
| Доля CLV > 0 | 15.2% |
| Kelly ROI | −9.2% |
| Финальный банк | 0.492 |
| Max drawdown | 68.3% |

H10 отклонена. ROI здесь только вторичная диагностика; решение определяется
CLV и калибровкой.

![1X2 reliability](reports/reliability_1x2.png)

## Допуск признаков на development

| Вариант | Log-loss | Решение |
| --- | ---: | --- |
| BASE: raw xG, no opponent adjustment, decay 180d | 0.9554 | принят |
| добавить opponent adjustment | 0.9571 | не допущен |
| заменить raw xG на npxG | 0.9568 | не допущен |
| убрать time decay | 0.9705 | H8 подтверждена |
| rho = 0 | 0.9549 | H9 не подтверждена для 1X2 |
| half-life 90d / 365d | 0.9577 / 0.9596 | хуже 180d |

Подробный машинно-воспроизводимый реестр: docs/hypotheses.md и
reports/hypotheses.md.

## Monte Carlo

Production-вероятности считаются точной матрицей, поэтому Monte Carlo не
заменяет Dixon–Coles. Он добавлен как независимый convergence-check и
сценарный инструмент. Для каждой оценки выводится Bernoulli standard error.

~~~bash
python scripts/run_monte_carlo.py --lambda-home 1.55 --lambda-away 1.05 \
  --rho -0.08 --simulations 250000 --seed 17
~~~

Тесты требуют, чтобы симуляционные 1X2, over 2.5 и BTTS сходились к
аналитическим значениям в пределах заявленной sampling uncertainty.

## Воспроизведение

~~~bash
git clone https://github.com/bogdasovandrej/xg-edge.git
cd xg-edge
python -m venv .venv
.venv/Scripts/activate
pip install -e ".[dev]"

pytest
python scripts/download_data.py
python scripts/build_dataset.py
python scripts/run_hypotheses.py
python scripts/run_walkforward.py

# 2026/27 top-5 raw snapshots (пропускает ещё не опубликованные файлы)
python scripts/download_top5.py

# future/live snapshots
python scripts/fetch_current_fixtures.py --output-dir reports/live
python scripts/predict_world_cup.py
python scripts/predict_ucl_qualifying.py --mode live --limit 14 \
  --output-json reports/ucl_qualifying_predictions.json \
  --output-csv reports/ucl_qualifying_predictions.csv
python scripts/build_live_payload.py \
  --world-cup reports/world_cup_2026_semifinals.json \
  --ucl reports/ucl_qualifying_predictions.json \
  --fixtures reports/live/current_fixtures.json \
  --output reports/live_predictions.json
~~~

111 тестов работают без сети. CI проверяет Python 3.10 и 3.12.

## Что добавлено в 0.3.0

- causal future-fixture predictor с 1X2, O/U 2.5, BTTS и точным счётом;
- официальные FIFA/UEFA feeds и экспериментальные модели ЧМ/квалификации ЛЧ;
- топ-5 лиг и сезон 2026/27 в registry/download layer;
- point-in-time контракты для составов, травм, судей и event-level карточек;
- market-aware anchor, longshot shrinkage и обязательный CLV deployment gate;
- автоматическое шестичасовое обновление и публичная live-витрина;
- тесты no-leak, offline network mocks и воспроизводимые live snapshots.

Market anchor улучшил holdout log-loss с 1.0009 до 0.9834, но его shadow CLV
остался отрицательным: −4.83%, 95% CI [−8.00%, −1.82%]. Поэтому `NO BET`
является результатом модели, а не временной надписью интерфейса.

## Что исправлено в 0.2.0

- исключена зависимость признаков от порядка матчей одной даты;
- исключено same-day compounding банка по уже известному результату строки;
- iid bootstrap CLV заменён cluster bootstrap по match_id;
- totals переведены на Pinnacle closing и получили common-subset метрики;
- провалившиеся кандидаты не оставлены в default feature set;
- добавлены проверки допустимости rho, lambda и score matrix;
- flat likelihood fit_rho теперь возвращает нейтральный rho = 0;
- добавлен воспроизводимый Monte Carlo convergence layer;
- development и holdout явно разделены.

## Ограничения

- полноценный multi-league cleaned history ещё не построен: новые 2026/27
  источники откроются по мере старта лиг;
- UEFA даёт судей, events и подтверждённые lineups, но до официальной публикации
  состав имеет статус unavailable; это не трактуется как «все здоровы»;
- injuries/xG production coverage требует лицензированного провайдера. Optional
  Sportmonks adapter не работает без пользовательского API token;
- FBref намеренно не скрапится: его data-use policy запрещает использование
  данных для predictive ML без письменного разрешения;
- Opta/Stats Perform требует коммерческой лицензии; в открытый репозиторий нельзя
  законно добавить их закрытые данные;
- BTTS, AH и exact score реализованы на уровне агрегаторов, но ещё не имеют
  полного betting/evaluation контура;
- 2025/26 — ретроспективный locked holdout; нужен live 2026/27;
- модель не имеет доказанного преимущества и не должна использоваться как
  рекомендация для ставок.

## Лицензия и дисклеймер

MIT. Учебно-исследовательский проект по вероятностному моделированию, не
финансовая и не букмекерская рекомендация.

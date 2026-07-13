# Данные: источники, контракт и защита от утечки

## Слои

~~~text
data/raw/       неизменяемые fd CSV и Understat JSON
data/cleaned/   matches.parquet с каноническими ID
data/features/  зарезервированный версионируемый слой
~~~

Raw-файлы не коммитятся. Загрузчики идемпотентны: существующий raw payload не
перезаписывается. Полный cleaned dataset воспроизводится командами:

~~~bash
python scripts/download_data.py
python scripts/build_dataset.py
~~~

Текущий контракт: 1900 матчей АПЛ, по 380 в сезонах 2021/22–2025/26,
1900 уникальных match_id, join football-data и Understat 100%.

## football-data.co.uk

URL сезона: https://www.football-data.co.uk/mmz4281/{season}/E0.csv.

Используются:

- результаты, даты, красные карточки;
- B365H/D/A и B365 over/under 2.5 как цены принятия решения;
- PSCH/PSCD/PSCA как Pinnacle closing 1X2;
- PC>2.5 и PC<2.5 как Pinnacle closing totals;
- Bet365 closing хранится для аудита, но не подменяет отсутствующий Pinnacle
  benchmark.

Для 2025/26 Pinnacle closing отсутствует у 170 матчей 1X2. Pinnacle closing
totals присутствует в 1719 из 1900 матчей всего. Такие строки остаются в
модельной оценке, но исключаются из market-common subset и CLV.

Даты football-data могут иметь двух- или четырёхзначный год; загрузчик
обрабатывает оба формата day-first.

## Understat

Основной endpoint:

GET https://understat.com/getLeagueData/EPL/{year}

Payload содержит dates и teams. Из dates берутся xG и матчи с isResult. Из
team history — npxG, PPDA и deep completions. Старый parser встроенных
JSON.parse blobs сохранён только как fallback для архивного HTML.

Understat kickoff datetime нормализуется до календарной даты для join с
football-data.

## Канонические команды

data/teams.py хранит явные source-name -> canonical-id mappings. Например,
Wolves и Wolverhampton Wanderers становятся wolves. Неизвестное имя вызывает
KeyError: новая команда добавляется явно, а не угадывается.

Join выполняется по season, date, home и away с validate=one_to_one. При потере
5% или более строк сборка останавливается и показывает несовпавшие матчи.

## Контракт колонок

src/xgedge/contracts.py — единственный словарь имён Col и Feat. В v0.2 в него
добавлены Pinnacle totals:

- p_o25 / p_u25 — pre-closing;
- pc_o25 / pc_u25 — closing.

build_features переносит odds без использования в фундаментальных признаках.

## Правила против утечки будущего

1. Матчи сортируются по дате.
2. Все матчи одной даты сначала получают признаки из состояния на конец
   предыдущей даты.
3. Только после расчёта всего same-date batch его xG добавляется в history.
4. Поэтому перестановка строк внутри даты не меняет текущие или будущие фичи.
5. Walk-forward обучается только на датах строго меньше начала test window.
6. Closing odds не входят в признаки и используются только после прогноза.
7. Все ставки одной даты рассчитываются от банка на начало даты и затем
   погашаются одним батчем.
8. CLV confidence interval resamples match_id clusters, а не коррелированные
   selections как независимые строки.

Эти свойства покрыты regression-тестами, включая order-invariance одной даты,
same-day bankroll и cluster bootstrap.

## Проспективные коэффициенты и CLV

The Odds API используется только через официальный v4 endpoint и секрет
`THE_ODDS_API_KEY`. В репозиторий попадают нормализованные цены и quota headers,
но никогда не API key или полный URL ошибки.

- production market: `h2h`, region `eu`;
- discovery: максимум один запрос на sport key за 24 часа;
- closing window: последние 60 минут до kickoff, poll каждые 15 минут;
- фактическим временем снимка считается `received_at` после HTTP-ответа;
- ответ после kickoff не допускается ни в CLV, ни в публичный prematch market;
- shadow taken price и confirmatory close берутся только у Pinnacle;
- median других букмекеров — diagnostic fallback, не confirmatory evidence;
- CLV фиксируется после kickoff и не зависит от результата матча;
- result settlement отдельно считает log-loss и multiclass Brier;
- каждая комбинация competition/model/probability basis/policy образует
  независимую когорту с одной проверкой на фиксированном horizon n=100.

## StatsBomb Open Data

`statsbomb-open-data` — легальный исторический event-level слой, а не замена
текущей Opta. Adapter сохраняет обязательную атрибуцию, каталог покрытия и
флаг `current_coverage_guaranteed: false`. Для одного явно выбранного матча он
нормализует xG/npxG, пенальти, составы, судью и удаления с минутой и
`score_before`; удары серии пенальти не включаются в match xG.

По умолчанию CLI загружает только компактный `competitions.json`. Events и
lineups скачиваются только с явным `--match-id`, чтобы случайно не копировать
весь открытый архив.

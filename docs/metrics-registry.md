# Реестр метрик

Каждая метрика — отдельная гипотеза и дополнительная степень свободы.
Недоказанный кандидат не входит в default feature set. Процедура допуска и
числа находятся в docs/hypotheses.md.

## Активное ядро v0.2

| Метрика | Статус | Основание |
| --- | --- | --- |
| raw xG за/против | активна | development BASE |
| exponential time decay, half-life 180d | активна | H8: без decay log-loss хуже на 0.0151 |
| home/away venue blend 0.3 | активна | часть зафиксированного BASE |
| red-card match down-weight 0.5 | активна | консервативная защита; H3 ждёт event-level проверки |
| home indicator | активна | коэффициент GLM |
| defence соперника | активна | базовая структура lambda |

GLM использует три регрессионных входа: attack rate, opponent defence rate и
home flag. Бюджет сложности 8–12 признаков не исчерпан намеренно.

## Кандидаты, не допущенные в BASE

| Метрика | Development-результат | Статус |
| --- | --- | --- |
| opponent-strength normalization | log-loss 0.9571 против BASE 0.9554 | не улучшает; default off |
| npxG вместо raw xG | 0.9568 против 0.9554 | не улучшает; default off |
| отсутствие time decay | 0.9705 против 0.9554 | отклонено, H8 подтверждена |
| GBM feature-to-lambda layer | 0.9675 против GLM 0.9554 | challenger отклонён |
| Dixon–Coles rho для агрегированного 1X2 | rho=0 дал 0.9549 | H9 не подтверждена для 1X2 |

Dixon–Coles correction остаётся частью score distribution для exact score и
как объект дальнейшего market-specific теста. Это не считается подтверждением
H9 для 1X2.

## Доступно в cleaned, но не используется моделью

| Метрика | Источник | Следующая гипотеза |
| --- | --- | --- |
| PPDA | Understat | H18: pressing -> totals/BTTS |
| deep completions | Understat | H17 / territorial pressure |
| npxG | Understat | повторная проверка только на новом периоде |
| red cards | football-data | H3 с минутой удаления |
| opening/closing odds | football-data | benchmark; H20 отдельно |

## Требует новых данных

| Метрика | Источник-кандидат | Гипотеза |
| --- | --- | --- |
| xG per shot | Understat shots / event data | H12 |
| set-piece vs open-play xG | StatsBomb/Opta | H13 |
| PSxG−GA вратаря | FBref/Opta | H14 |
| rest days / schedule density | расписание | H15 |
| lineup stability / injuries | API-Football/Transfermarkt | H16 |
| game-state-adjusted xG | event data | H19 |

## Не является признаком

Monte Carlo не добавляет информацию в lambda и не участвует в выборе ставок.
Это проверочный слой: выборки из той же Dixon–Coles score matrix обязаны
сходиться к аналитическим рынкам в пределах sampling standard error.

Closing odds также не входят в фундаментальные признаки. Они используются
только как market baseline и для CLV после формирования прогноза.

## Явно исключено

- владение как самостоятельный признак;
- непараметризованные мотивация, характер и класс;
- серии побед/поражений как нарратив;
- личные встречи глубже двух лет;
- товарищеские матчи;
- признаки, которые не были доступны до kickoff.

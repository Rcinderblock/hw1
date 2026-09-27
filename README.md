# hw1

Учебная банковская система на Python. Проект объединяет семь этапов: от модели счёта до обработки переводов, аудита и отчётов. Данные хранятся в памяти; журнал аудита при необходимости записывается в файл. База данных и внешние сервисы не нужны.

## Что реализовано

| Этап | Содержание |
| --- | --- |
| Day1 | Абстрактный и обычный счёт, операции, статусы, валюты и собственные ошибки. |
| Day2 | Сберегательный, премиальный и инвестиционный счета. |
| Day3 | Клиенты, управление счетами, проверка доступа и ограничения по времени. |
| Day4 | Заявки на перевод, очередь с приоритетом и задержкой, комиссии, конвертация и повторные попытки. |
| Day5 | Журнал событий, оценка риска и отчёты аудита. |
| Day6 | Общая демонстрация с клиентами, счетами и транзакциями. |
| Day7 | Отчёты по клиенту, банку и рискам в текстовом, JSON и CSV форматах; графики. |

Исходный код находится в `src/bank_system/`: `accounts.py` и `advanced_accounts.py` описывают счета, `bank.py` управляет клиентами и счетами, `transactions.py` обрабатывает переводы, `audit.py` ведёт журнал и оценивает риск, `reports.py` формирует отчёты. Демонстрации лежат в `demo*.py`, автоматические проверки — в `tests/`.

## Установка и запуск

Нужен Python 3.10 или новее. Из корня репозитория:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Запустить любой этап можно отдельной командой:

```bash
PYTHONPATH=src .venv/bin/python demo.py
PYTHONPATH=src .venv/bin/python demo_day2.py
PYTHONPATH=src .venv/bin/python demo_day3.py
PYTHONPATH=src .venv/bin/python demo_day4.py
PYTHONPATH=src .venv/bin/python demo_day5.py
PYTHONPATH=src .venv/bin/python demo_day6.py
PYTHONPATH=src .venv/bin/python demo_day7.py
```

`demo_day7.py` сохраняет отчёты и графики в `output/day7/`. Другой каталог можно указать через `--output-dir`.

## Проверка

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

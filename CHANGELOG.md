# Changelog

Все заметные изменения проекта документируются в этом файле.

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версионирование — [семантическое (SemVer)](https://semver.org/lang/ru/).

## Как версионируется проект

- Все изменения фиксируются в секции `[Unreleased]` по ходу коммитов. Секция
  превращается в релиз `[X.Y.Z] - дата` только по явной команде владельца;
  одновременно с этим обновляются версия в `pyproject.toml` и `__init__.py`
  и ставится git-тег `vX.Y.Z`.
- Версия задается в `pyproject.toml` (`[project] version`) и дублируется в
  `src/bitrix_auto_manage/__init__.py` (`__version__`).
- Пока проект младше 1.0: минорная версия (`0.x`) может нести и новые
  функции, и несовместимые изменения формата YAML или CLI; патч — только
  исправления без изменения поведения.
- С 1.0: минорная — обратно совместимые функции, патч — исправления,
  мажорная — несовместимые изменения.

## [Unreleased]

### Добавлено

- CI: workflow GitHub Actions `.github/workflows/tests.yml` (push и
  pull request; матрица Python 3.11–3.14) — клонирует публичный SDK
  `sdk-bitrix-time-manager` в соседний каталог, ставит его и пакет
  editable с dev-инструментами, затем `pytest` с покрытием, `ruff check`
  и `mypy src`.
- README: шапка для GitHub — строка бейджей (лицензия MIT, Python 3.11+,
  версия 0.2.0 со ссылкой на тег, звёзды) и теглайн; раздел «Возможности».
  Прежняя преамбула «О проекте» влита в теглайн и «Возможности», ссылка на SDK
  заменена на публичный адрес репозитория.
- Лицензия MIT: текст в `LICENSE` (тот же правообладатель, что у SDK
  `bitrix-time-manager`), объявление в `pyproject.toml` (`license = "MIT"`,
  `license-files`).

### Изменено

- README: бейджи шапки — добавлены `stars` и `tests` (badge.svg workflow
  `tests.yml`), убраны `code style: ruff` и `typing: mypy strict`; итоговый
  набор: release, python, license, stars, tests.
- README: шапка приведена к стилю SDK-репозитория `sdk-bitrix-time-manager`:
  центрированный блок с теглайном и бейджи shields.io построчно (release v0.2.0
  по тегу, Python >=3.11, лицензия MIT, ruff, mypy strict) вместо прежней
  однострочной строки бейджей; бейджи stars убраны (в SDK их нет), tests
  не добавлен (в репозитории нет workflow GitHub Actions).
- README: уточнено описание SDK в шапке — SDK даёт Python API и CLI
  `bitrix-time`, а не «REST-клиент»; в быстрый старт Docker добавлены шаги
  проверки перед запуском демона (`check` и `preview` через
  `docker compose run --rm bitrix-auto`) и примечание о выполнении
  CLI-команд в контейнере.
- README: нумерованная структура разделов с подсекциями (быстрый старт
  разделен на Docker и локальный запуск) и вложенное оглавление «Содержание»
  с якорями GitHub; раздел «Возможности» перенесен под оглавление, ссылки
  на CHANGELOG.md и docs/setup.md вынесены в раздел «Документация».
- README: быстрые старты (Docker и локальный) разбиты на этапы: подготовка
  окружения, предварительная проверка, запуск, проверка логов.

## [0.2.0] - 2026-10-21

### Добавлено

- Тесты `runner` (прогон окна дат, обработка ошибок SDK, `show_day`), `scheduler`
  (расчёт следующего запуска, коды выхода `--once`, пауза до `apply_time` и пауза
  повтора) и CLI (`check`, `preview`, `apply`, `show`, `daemon`, запуск
  `python -m bitrix_auto_manage`) — на фейках SDK, без сети и реального портала;
  строковое покрытие всех модулей пакета доведено со 60% до 100%.

### Безопасность

- `.gitignore`: добавлен каталог `.state/` (дефолтный путь SQLite-журнала
  host/cron-режима — `.state/operations.sqlite3` относительно каталога
  запуска) — устранена публикация журнала с персональными данными при
  `git add -A`; каталог также внесен в Never-список `AGENTS.md`. README
  и `.env.example` теперь честно документируют хост-дефолт журнала
  и рекомендуют задавать `BITRIX_STATE_PATH` при запуске не из корня репо.

## [0.1.0] - 2026-10-20

Первый пре-релиз: автоматизация Time-менеджера Bitrix24 по YAML-расписанию.

### Добавлено

- CLI `bitrix-auto`: команды `check`, `preview`, `apply`, `show`, `daemon`
  (и `daemon --once` для cron / Планировщика Windows); общие флаги `--config`
  (файл или каталог, можно повторять), `--env-file`, `-v`, `--version`.
- YAML-конфигурация: `timezone`, секция `schedule` (`apply_time`,
  `horizon_days`, `backfill_days`, `retry_minutes`) и список `tasks` с видами
  `remote` и `time_entry`; дни недели на русском и английском, диапазоны через
  дефис; строгая валидация — неизвестные поля запрещены.
- Несколько YAML-файлов в каталоге `config/` объединяются по алфавиту:
  скалярные значения перезаписываются, списки `tasks` дополняются; файлы
  с `.example.` в имени игнорируются.
- Политика `on_existing_entries` для учета времени: `skip` (по умолчанию) не
  добавляет запись, если за дату уже есть любая; `append` — добавляет поверх
  (портал суммирует записи за день).
- Идемпотентность через SDK: стабильные ключи `auto:<дата>:<хэш>`, статусы
  `created`/`existing`, SQLite-журнал операций против дублей при повторных
  прогонах и потерянных ответах сети.
- Docker: Dockerfile (python:3.12-slim) и docker-compose с монтированием
  `config/` (только чтение) и `state/`; подключение соседнего SDK через
  переменную `BITRIX_SDK_PATH`.
- Документация: README с преамбулой и оглавлением; `docs/setup.md` —
  пошаговое создание входящего вебхука Bitrix24 и полный справочник по файлу
  расписания.
- Разработка: 25 offline-тестов (pytest), ruff, mypy strict.

[Unreleased]: https://github.com/theillarion/bitrix-auto-manage/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/theillarion/bitrix-auto-manage/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/theillarion/bitrix-auto-manage/releases/tag/v0.1.0

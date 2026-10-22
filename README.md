<div align="center">

# bitrix-auto-manage

Автозаполнение Тайм-менеджера Bitrix24 из YAML-расписания: демон в Docker
или cron заранее выставляет удалёнку и добавляет учет времени.

[![release](https://img.shields.io/badge/release-v0.3.0-blue)](https://github.com/theillarion/bitrix-auto-manage/tree/v0.3.0)
[![python](https://img.shields.io/badge/python-%E2%88%A53.11-007ec6)](pyproject.toml)
[![License: MIT](https://img.shields.io/github/license/theillarion/bitrix-auto-manage)](LICENSE)
[![stars](https://img.shields.io/github/stars/theillarion/bitrix-auto-manage)](https://github.com/theillarion/bitrix-auto-manage)
[![tests](https://github.com/theillarion/bitrix-auto-manage/actions/workflows/tests.yml/badge.svg)](https://github.com/theillarion/bitrix-auto-manage/actions/workflows/tests.yml)

</div>

---

Автозаполнение Тайм-менеджера Bitrix24 — расписание удалёнки и учёта часов
в одном YAML, применяется демоном в Docker или из cron. Удалёнка выставляется
заранее на будущие даты, учет времени добавляется сам, как только за дату
появился рабочий день — по отметке СКУД или от роботов удалёнки.

Проект — обертка над [sdk-bitrix-time-manager](https://github.com/theillarion/sdk-bitrix-time-manager):
SDK даёт Python API и CLI `bitrix-time` для доступа к Тайм-менеджеру портала
через входящий вебхук, с идемпотентностью и журналом операций; здесь —
YAML-конфигурация, планировщик и команды `bitrix-auto`.

## Содержание

1. [Возможности](#1-возможности)
2. [Быстрый старт](#2-быстрый-старт)
    1. [Docker](#21-docker)
    2. [Локальный запуск без Docker](#22-локальный-запуск-без-docker)
3. [Команды](#3-команды)
4. [Формат YAML](#4-формат-yaml)
5. [Настройки окружения (`.env`)](#5-настройки-окружения-env)
6. [Журнал операций и особые случаи](#6-журнал-операций-и-особые-случаи)
7. [Проверки разработки](#7-проверки-разработки)
8. [Документация](#8-документация)

## 1. Возможности

- **Простая настройка** — один YAML-файл и входящий вебхук.
- **Заведётся где угодно** — Docker или cron/Планировщик без Docker.
- **Сухой прогон перед применением** — `preview` показывает план записей,
  `check` проверяет вебхук и схему портала (read-only).
- **Идемпотентно** — повторные прогоны не создают дубли: существующие записи
  возвращаются как `existing`.
- **Безопасно** — только добавляет записи: существующие не редактирует
  и не удаляет.
- **Локальный журнал** — каждая операция попадает в SQLite-журнал (потерянный
  ответ сети не приводит к задвоению); `show` — просмотр дня.

## 2. Быстрый старт

### 2.1. Docker

**Подготовка окружения**

```bash
cp .env.example .env                  # впишите свой BITRIX_WEBHOOK_URL
cp config/schedule.example.yaml config/schedule.yaml
$EDITOR config/schedule.yaml          # свои дни, время, договоры
```

**Предварительная проверка**

```bash
docker compose run --rm bitrix-auto check      # вебхук и схема портала (read-only)
docker compose run --rm bitrix-auto preview --config config/schedule.yaml \
    --date 2026-10-19 --days 1        # план записей на дату, без записи
```

**Запуск**

```bash
docker compose up -d --build
```

**Проверка логов**

```bash
docker compose logs -f                # смотреть журнал прогонов
```

CLI-команды выполняются в контейнере через
`docker compose run --rm bitrix-auto <команда>`; на хосте они доступны
только после pip-установки (раздел 2.2).

Демон при старте сразу применяет окно дат, затем повторяет каждый день в
`schedule.apply_time` (локальное время из YAML). Перед каждым прогоном демон
перечитывает YAML-расписание: правки (в том числе добавление дат в
`skip_dates`) подхватываются без рестарта, а ошибка конфигурации считается
неудачным прогоном — повтор через `retry_minutes`; после сбоя повторяет
попытку через тот же интервал.

SDK вшит в образ: при сборке клонируется по тегу `BITRIX_SDK_REF` (по умолчанию
`v0.2.0`) из репозитория SDK — соседний клон для Docker-старта не нужен.
Для разработки с живым соседним клоном есть override-файл:
`docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build`
монтирует `../sdk-bitrix-time-manager` в `/opt/sdk` (путь задаётся
переменной `BITRIX_SDK_PATH`).

### 2.2. Локальный запуск без Docker

**Подготовка окружения**

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
git clone https://github.com/theillarion/sdk-bitrix-time-manager \
    ../sdk-bitrix-time-manager
pip install -e '../sdk-bitrix-time-manager' -e .
cp .env.example .env
cp config/schedule.example.yaml config/schedule.yaml
$EDITOR config/schedule.yaml          # свои дни, время, договоры
```

Каталог SDK может быть любым: по умолчанию команды ожидают соседний
`../sdk-bitrix-time-manager`; при другом расположении укажите свой путь
в установке — `pip install -e '<путь-к-SDK>' -e .`.

**Предварительная проверка**

```bash
bitrix-auto check                     # вебхук и схема портала (read-only)
bitrix-auto preview --config config/schedule.example.yaml --date 2026-10-19 --days 1
```

**Запуск**

```bash
bitrix-auto apply   --config config/schedule.yaml
bitrix-auto daemon  --config config/schedule.yaml --once   # для cron/Планировщика Windows
```

**Проверка логов**

Вывод команд — в stdout; каждая операция попадает в SQLite-журнал (раздел 6).
Посмотреть день за дату (read-only):

```bash
bitrix-auto show --date 2026-10-19   # день, удалёнка и учет времени за дату
```

Без Docker демона можно заменить системным планировщиком:
`bitrix-auto daemon --once` выполняет ровно один прогон окна и выходит
(cron: `5 18 * * *`, Планировщик Windows: ежедневное задание).

В host/cron-режиме журнал SQLite по умолчанию создается по дефолту SDK —
`.state/operations.sqlite3` относительно каталога запуска. Из корня репо
это `<репо>/.state/` (каталог игнорируется git'ом). Если `bitrix-auto`
запускается не из корня репо (например, cron с другим рабочим каталогом),
задайте полный путь журнала в `BITRIX_STATE_PATH` — дефолт зависит от CWD,
и журнал окажется не там, где вы ожидаете.

## 3. Команды

| Команда | Действие |
|---|---|
| `check` | Проверить вебхук и схему портала, ничего не создавая |
| `preview` | Показать план для окна дат без записей (`status: preview`) |
| `apply` | Реально создать записи окна; повторный запуск вернет `existing` |
| `show --date 2026-10-19` | Показать день, удалёнку и учет времени за дату |
| `daemon` | Прогон окна при старте, затем ежедневно в `apply_time` |
| `daemon --once` | Один прогон окна и выход |

Общие флаги указываются после подкоманды: `--config PATH` (файл или каталог,
можно повторять; по умолчанию каталог `./config`), `--env-file PATH`,
`-v` (отладочный журнал). Для `preview`/`apply` окно задается `--date`
(начало) и `--days` (количество дней); без них берется окно из `schedule`.

Полный справочник всех команд — аргументы с дефолтами, окно
`--date`/`--days`, коды выхода и примеры —
в [docs/commands.md](docs/commands.md); переменные окружения — раздел 5.

## 4. Формат YAML

Полный справочник всех полей, ограничений валидации и листинг для
восстановления example-файла — в [docs/setup.md](docs/setup.md). Ниже — краткая версия:

```yaml
timezone: Europe/Moscow        # должна совпадать с BITRIX_TIMEZONE

schedule:
  apply_time: "17:45"          # локальное время ежедневного прогона демона
  horizon_days: 2              # выставлять на сегодня и 2 дня вперед
  backfill_days: 4             # догонять до 4 прошедших дней после простоя
  retry_minutes: 60            # повтор после неудачного прогона
  skip_dates: []               # даты, полностью исключаемые из планирования;
                               # пример: ["2026-11-03", "2026-11-10"]

tasks:
  # Рабочий день здесь НЕ задается: он появляется сам —
  # по отметке СКУД или в момент начала удалённого дня (роботы портала).

  - kind: remote               # удалённая работа, выставляется заранее
    days: [wed]                # mon tue wed thu fri sat sun / пн вт ср чт пт сб вс;
                               # регистр не важен; можно диапазон mon-fri — включительно,
                               # даже через границу недели (fri-mon)
    start: "08:00"
    end: "17:00"
    comment: "Удалённая работа"

  - kind: time_entry           # учет времени, когда день уже существует
    days: [mon-fri]            # mon tue wed thu fri sat sun / пн вт ср чт пт сб вс;
                               # регистр не важен; можно диапазон mon-fri — включительно,
                               # даже через границу недели (fri-mon)
    contract:
      entity_type: 1298        # 1048/1052 — карточки договоров, 1298 — шаблоны
      id: 2                    # «Общие задачи подразделения»; замените на свой (см docs/setup.md)
    description: "ВПИШИТЕ ОПИСАНИЕ РАБОТ"
    hours: 8
    minutes: 0                 # только 0 или 30
    on_existing_entries: skip  # skip (по умолчанию): если за день уже есть
                               # любая запись учета — новая не добавляется;
                               # append: добавлять поверх существующих
```

Ручное создание рабочего дня не поддерживается: день появляется сам —
по отметке СКУД или роботами удалённого дня.

Каталог `config/` может содержать несколько файлов `*.yaml` — они применяются
по алфавиту: скалярные значения и секция `schedule` перезаписываются поздними
файлами, а списки `tasks` объединяются. Удобно держать базовое расписание и
отдельный файл с удалёнкой.

## 5. Настройки окружения (`.env`)

| Переменная | Значение |
|---|---|
| `BITRIX_WEBHOOK_URL` | Обязателен: `https://портал/rest/USER_ID/СЕКРЕТ/` |
| `BITRIX_TIMEZONE` | `Europe/Moscow`; должна совпадать с `timezone` в YAML |
| `BITRIX_STATE_PATH` | Путь к SQLite-журналу. В Docker — `/app/state/operations.sqlite3` (монтируется в `state/` репо); без Docker по умолчанию не задан, действует дефолт SDK — `.state/operations.sqlite3` относительно каталога запуска |
| `BITRIX_SDK_PATH` | Только Docker с override-файлом `docker-compose.dev.yml`: путь к живому клону SDK, монтируемому в контейнер (`/opt/sdk`) вместо вшитой версии; по умолчанию — соседний `../sdk-bitrix-time-manager`. В базовом Docker-старте и локальном запуске не используется |
| `BITRIX_SDK_REF` | Только Docker: тег SDK, вшиваемый в образ при сборке (build-arg в `docker-compose.yml`); по умолчанию `v0.2.0`. Изменение действует после пересборки: `docker compose up -d --build` |
| `BITRIX_TIMEOUT`, `BITRIX_READ_RETRIES` | Необязательны, см. документацию SDK |

Разрешение записи включается только на время `apply`/`daemon` внутри
приложения; `preview`, `check` и `show` выполняют только чтение.

## 6. Журнал операций и особые случаи

- Журнал SQLite — общий для всех прогонов; не удаляйте его: SDK блокирует
  повторную отправку потерянных операций. В Docker это
  `state/operations.sqlite3` (монтируется из репо); в host/cron-режиме без
  `BITRIX_STATE_PATH` — `.state/operations.sqlite3` относительно каталога
  запуска (из корня репо — `<репо>/.state/`, игнорируется git'ом).
- `time_entry` **добавляет** запись учета, портал суммирует записи за день.
  Поэтому по умолчанию действует защита `on_existing_entries: skip`: если за
  дату уже есть хоть одна запись учета (вручную, старым ботом, прошлым
  прогоном), автоматика ничего не добавляет и пишет об этом в журнал. Чтобы
  добавлять записи поверх существующих, поставьте `append`.
- `time_entry` требует уже существующего рабочего дня. День появляется сам:
  по отметке СКУД или от роботов удалёнки. Если его еще нет, запись учета
  пропускается и будет выполнена при следующем прогоне (`backfill_days`).
- SDK не умеет удалять и изменять записи: лишнее или неверное правится
  вручную на портале.
- Изменение параметров уже примененной задачи создает новую логическую
  операцию: SDK вернет конфликт для пересечений или `existing` для совпадений.
  Старые записи за прошлые дни нужно править на портале вручную.
- При `uncertain` (потерянный ответ сети) прогон останавливает эту операцию;
  разблокировка — вручную через `bitrix-time resolve` из SDK, см. README SDK.

## 7. Проверки разработки

```bash
python -m pytest -q
python -m ruff check src tests
python -m ruff format --check src tests
python -m mypy src
```

## 8. Документация

- [CHANGELOG.md](CHANGELOG.md) — история версий и правила версионирования
- [docs/setup.md](docs/setup.md) — подробное руководство: как создать вебхук
  Bitrix24 и полный справочник по файлу расписания
- [docs/commands.md](docs/commands.md) — справочник всех команд CLI:
  аргументы с дефолтами, окно `--date`/`--days`, коды выхода, переменные
  окружения

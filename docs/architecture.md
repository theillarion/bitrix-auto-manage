# Architecture — bitrix-auto-manage

> Проверено: 2026-10-21, коммит 2d42dc5.

## Границы системы

- Внутри: YAML-конфигурация, планировщик, оркестрация операций (`src/bitrix_auto_manage/`).
- Снаружи: SDK `bitrix-time-manager` — транспорт, журнал, идемпотентность; портал Bitrix24 (REST CRM); файл журнала SQLite принадлежит SDK, не приложению.
- Прямых HTTP-вызовов в приложении нет: импортируется только SDK (runner.py:9-14).

## Компоненты и ответственность

- `config.py` — контракты YAML: строгие модели `extra="forbid"` (config.py:68,80,89,130), типы договоров `Literal[1048, 1052, 1298]` (config.py:82); слияние нескольких файлов — скаляры перезаписываются, `tasks` дополняется (config.py:137), `*.example.*` пропускаются (config.py:155); нечитаемый файл — ошибка конфигурации, а не traceback (config.py:174).
- `config.py` — окно прогона (config.py:194), план дня в фиксированном порядке `remote → time_entry` (config.py:30,201), дата из `skip_dates` → пустой план (config.py:203), идемпотентный ключ `auto:<дата>:<хэш>` (config.py:227).
- `runner.py` — применение плана: разрешение записи включается только здесь (runner.py:48); изоляция отказов — `uncertain` не прерывает окно (runner.py:60), отсутствующий день → skip (runner.py:70); `on_existing_entries: skip` сверяет существующие записи (runner.py:85); `show` — три параллельных чтения (runner.py:119).
- `scheduler.py` — демон: немедленный прогон, далее ежедневно в случайное время внутри `apply_time` (scheduler.py:29,60); конфиг перечитывается из YAML перед каждым прогоном, ошибка загрузки = неудачный прогон (сон `retry_minutes`, scheduler.py:106), первый старт с битым конфигом — exit 1.
- `cli.py` — команды check/preview/apply/show/daemon (cli.py:45-64).

## Потоки данных

YAML → `load_config` → `date_range` (config.py:194) → `plan_for_date` на дату (config.py:201) → `run_window` (runner.py:45) → SDK `TimeManager` → REST портала → статусы `created`/`existing`/`uncertain` → `RunSummary` в stdout; SQLite-журнал пишет SDK до и после отправки. Чтение (`show`, runner.py:119) — только REST-чтение без журнала.

## Внешние зависимости и контракты

- SDK `bitrix-time-manager`: контракт — Python API `TimeManager`; идемпотентность и ровно одна отправка записи — его сторона (SDK service.py:434, journal.py:63).
- Портал Bitrix24: REST CRM `crm.item.*`; привязка рабочего дня к удалёнке — роботы портала (TODO: в коде приложения не выражено).
- Часовые пояса: база `tzdata`; зона берется из конфига, не с хоста (config.py:187).

## Инварианты

- Параметры операции ↔ ключ `auto:<дата>:<хэш>`: изменение параметров создает новую операцию (config.py:227).
- Запись — только в `apply`/`daemon` (runner.py:48); `preview`, `check`, `show` — только чтение.
- Порядок операций внутри дня фиксирован (config.py:30).
- Дата из `skip_dates` → пустой план: не создаётся ни удалёнка, ни запись учета (config.py:203), во всех командах, включая явные окна `--date`.
- Неизвестное поле YAML — ошибка конфигурации, а не предупреждение (config.py:68).

## Нефункциональные ограничения

- Прогон покрывает `сегодня − backfill_days … сегодня + horizon_days` включительно (config.py:191).
- Пауза после неудачного прогона — `retry_minutes` (scheduler.py:60).
- Ретеншн журнала приложением не ограничен; политика не определена — TODO.

## Публикация образа (GHCR)

- Пуш тега `vX.Y.Z` запускает workflow `.github/workflows/docker.yml`:
  multi-arch сборка (linux/amd64, linux/arm64) и пуш образа
  в `ghcr.io/theillarion/bitrix-auto-manage` с тегами `X.Y.Z`, `X.Y`
  и `latest` (semver-правила metadata-action). Ручной запуск
  `workflow_dispatch` с входом `tag` публикует образ указанного тега;
  тег `X.Y.Z` на ручном пути даёт raw-правило (semver из ветки не матчится).
- Пуш образа выполняется встроенным `GITHUB_TOKEN` с правами
  `packages: write` — секреты и учётные записи внешних реестров не нужны.
- Пин `BITRIX_SDK_REF` из Dockerfile запекается в образ на момент сборки
  тега; конвенция сверки пина с версией SDK, на которой прошли тесты релиза
  (AGENTS.md), сохраняется и для опубликованных образов.
- Конвенция: `docker-compose.yml` (сборка из репозитория)
  и `docker-compose.ghcr.yml` (готовый образ) описывают один и тот же сервис
  `bitrix-auto` — изменения `volumes`/`environment` вносятся синхронно
  в оба файла.

## ADR

ADR не заведены. TODO: создать `docs/adr/` при первом существенном архитектурном решении.

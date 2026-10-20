# Architecture — bitrix-auto-manage

> Проверено: 2026-10-21, коммит 2d42dc5.

## Границы системы

- Внутри: YAML-конфигурация, планировщик, оркестрация операций (`src/bitrix_auto_manage/`).
- Снаружи: SDK `bitrix-time-manager` — транспорт, журнал, идемпотентность; портал Bitrix24 (REST CRM); файл журнала SQLite принадлежит SDK, не приложению.
- Прямых HTTP-вызовов в приложении нет: импортируется только SDK (runner.py:9-14).

## Компоненты и ответственность

- `config.py` — контракты YAML: строгие модели `extra="forbid"` (config.py:68,79,88), типы договоров `Literal[1048, 1052, 1298]` (config.py:81); слияние нескольких файлов — скаляры перезаписываются, `tasks` дополняется (config.py:136), `*.example.*` пропускаются (config.py:154).
- `config.py` — окно прогона (config.py:191), план дня в фиксированном порядке `workday → remote → time_entry` (config.py:30,198), идемпотентный ключ `auto:<дата>:<хэш>` (config.py:227).
- `runner.py` — применение плана: разрешение записи включается только здесь (runner.py:48); изоляция отказов — `uncertain` не прерывает окно (runner.py:57), отсутствующий день → skip (runner.py:67); `on_existing_entries: skip` сверяет существующие записи (runner.py:82-83); `show` — три параллельных чтения (runner.py:118).
- `scheduler.py` — демон: немедленный прогон, далее ежедневно в `apply_time` (scheduler.py:21-22,39), повтор через `retry_minutes` (scheduler.py:60).
- `cli.py` — команды check/preview/apply/show/daemon (cli.py:45-64).

## Потоки данных

YAML → `load_config` → `date_range` (config.py:191) → `plan_for_date` на дату (config.py:198) → `run_window` (runner.py:48) → SDK `TimeManager` → REST портала → статусы `created`/`existing`/`uncertain` → `RunSummary` в stdout; SQLite-журнал пишет SDK до и после отправки. Чтение (`show`, runner.py:118) — только REST-чтение без журнала.

## Внешние зависимости и контракты

- SDK `bitrix-time-manager`: контракт — Python API `TimeManager`; идемпотентность и ровно одна отправка записи — его сторона (SDK service.py:434, journal.py:63).
- Портал Bitrix24: REST CRM `crm.item.*`; привязка рабочего дня к удалёнке — роботы портала (TODO: в коде приложения не выражено).
- Часовые пояса: база `tzdata`; зона берется из конфига, не с хоста (config.py:184).

## Инварианты

- Параметры операции ↔ ключ `auto:<дата>:<хэш>`: изменение параметров создает новую операцию (config.py:227).
- Запись — только в `apply`/`daemon` (runner.py:48); `preview`, `check`, `show` — только чтение.
- Порядок операций внутри дня фиксирован (config.py:30).
- Неизвестное поле YAML — ошибка конфигурации, а не предупреждение (config.py:68).

## Нефункциональные ограничения

- Прогон покрывает `сегодня − backfill_days … сегодня + horizon_days` включительно (config.py:191).
- Пауза после неудачного прогона — `retry_minutes` (scheduler.py:60).
- Ретеншн журнала приложением не ограничен; политика не определена — TODO.

## ADR

ADR не заведены. TODO: создать `docs/adr/` при первом существенном архитектурном решении.

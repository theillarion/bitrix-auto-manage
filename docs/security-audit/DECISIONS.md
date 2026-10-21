# Реестр решений владельца по итогам security-аудита

Дата: 2026-10-21. Прогон: run-1 (см. [REPORT.md](REPORT.md)). Файл фиксирует решения владельца по находкам и рекомендациям аудита. Текст аудита не изменяется; `findings.json` остаётся как есть.

| Finding / рекомендация | Решение | Кратко |
| --- | --- | --- |
| M-1 `service.py/_find_duplicate/unfinished-cross-day-overlap` | **NOT APPLICABLE** | Предпосылка на портале владельца неосуществима |
| M-2 `gitignore:wrapper-.state-uncovered` | **FIX** | Исправляется в этом репо |
| `../backup-pre-rewrite-2026-10-20/` (срочная рекомендация №1, §6 REPORT.md) | **ACCEPTED RISK** | Локальный бэкап на single-user хосте остаётся на месте |

---

## M-1 — NOT APPLICABLE (решение владельца, 2026-10-21)

**Finding**: `service.py/_find_duplicate/unfinished-cross-day-overlap` (medium) — «висящий» интервал удалёнки с пустым `remote_end`, начатый в прошлые сутки, обходит проверку перекрытий в `_find_duplicate` (детали: [FINDINGS-DETAIL.md](FINDINGS-DETAIL.md), M-1).

**Решение**: NOT APPLICABLE — предпосылка находки (data_state: существующая запись удалёнки с `remote_start` строго до целевых суток и пустым `remote_end`) на портале владельца неосуществима.

**Обоснование**:

- На портале нет start/stop-флоу удалёнки: удалённый день создаётся обычной формой смарт-процесса, где начало и конец обязательны — «не заполнить поле нельзя».
- Сторонних писателей через API нет: вебхук используется только этим инструментом.
- Сам инструмент пишет `remote_start` + `remote_end` атомарно одним `crm.item.add`: в SDK (`models.py`) поле `DayInterval.end` обязательно и снабжено валидатором `end > start`, а `client.py` архитектурно запрещает update/delete — единственная write-операция SDK это `crm.item.add`. Оставить пустой `remote_end` из этого инструмента невозможно.

**Неточность в findings.json**: формулировка «started in the UI **or by a prior run**» неточна — анализ кода показывает, что «prior run» (запуск этого инструмента) оставить пустой `remote_end` не может (см. обоснование выше). `findings.json` намеренно не правится.

**Рекомендованный аудитом фикс** (третий запрос «неоконченных, начатых до конца плана» в `_find_duplicate`) — опциональный hardening, вне плана до пре-релиза.

## M-2 — FIX (решение владельца, 2026-10-21)

**Finding**: `gitignore:wrapper-.state-uncovered` (medium) — `.state/`-журнал host/cron-режима не в `.gitignore` обёртки; `git add -A` публикует SQLite с персональными данными о рабочем времени (детали: [FINDINGS-DETAIL.md](FINDINGS-DETAIL.md), M-2).

**Решение**: FIX — исправляется в этом репо: `.gitignore`, AGENTS.md, README, `.env.example`. См. `[Unreleased]` в `CHANGELOG.md`.

## `../backup-pre-rewrite-2026-10-20/` — ACCEPTED RISK (решение владельца, 2026-10-21)

**Источник**: срочная рекомендация №1 в §6 REPORT.md (hardening-заметки, вне репозиториев): защитить или уничтожить каталог бэкапа; при подтверждении plaintext-копии вебхука — ротация.

**Решение**: ACCEPTED RISK — каталог является локальным бэкапом на single-user хосте и остаётся на месте.

# Детали подтверждённых находок (medium)

Полные машиночитаемые записи всех находок — в `findings.json`; здесь — детали для medium-записей: полный путь по исходнику и воспроизведение в нативном интерфейсе таргета.

---

## M-1. Неоконченный интервал удалёнки, начатый в другой день, обходит проверку конфликтов

**Fingerprint**: `service.py/_find_duplicate/unfinished-cross-day-overlap` · severity medium (likelihood medium / impact medium) · confidence high

> Решение владельца 2026-10-21: not applicable — см. [DECISIONS.md](DECISIONS.md).

**Принципал и ресурс**: пишущий путь демона (владелец вебхука) → CRM-записи владельца на проде-портале (smart-process 1250). Атакующий не нужен: достаточно «висящего» интервала удалёнки, начатого в UI и не закрытом за полночь.

**Инвариант**: `service.py:352` (комментарий автора) и интеграционный тест `tests/integration/test_service.py:421-440` — неоконченный интервал является конфликтом и должен блокировать создание через `ConflictError`.

**Полный путь по исходнику** (текущий `531ea79`):
1. `src/bitrix_auto_manage/runner.py:94` — `_apply_spec` → `manager.create_remote_work`; у обёртки нет собственного пре-скана перекрытий, контроль только в SDK.
2. `sdk .../service.py:295` — вход в `_finish` без дополнительного скана.
3. `service.py:322-324` — xmlId-запрос точных дублей не ловит случай: ручная запись не имеет `btm_<hash>`, запись в другой день/с иными параметрами имеет другой operation_id.
4. `service.py:344-351` — фильтр перекрытий `{f"<{remote_start}": plan_end, f">{remote_end}": plan_start}`: второе условие требует непустой `remote_end` — «висящая» запись никогда не проходит `>`-сравнение с пустым полем.
5. `service.py:353` → `:219-223` — ловля неоконченных ограничена `remote_start` целевыми сутками: старт с прошлых суток исключён.
6. `service.py:356-361` — `by_id` пуст → цикл не выполняется → `ConflictError (:359-360)` недостижим → возвращается None.
7. `service.py:421→427→434` — `_finish` проходит гейт, `journal.reserve`, `crm.item.add` создаёт перекрывающуюся запись.

**Ограниченное воспроизведение** (sandbox-exec, stdlib, код скопирован дословно из :322-361 и :219-223; семантика сравнений — из `tests/fakes.py:58-59`; артефакт `agents/verify-03/artifacts/verify_overlap.out.txt`):
- Случай A (старт вчера 20:00, конец None) и A2 (конец `''`) против плана сегодня 09:00-18:00: `in overlapping=False`, `in same_day=False`, `_find_duplicate=None` → «journal.reserve THEN crm.item.add EXECUTES» — контроль побеждён.
- Контроль B: неоконченный в те же сутки → `ConflictError` (контроль работает так, как задуман).
- Контроль C: законченное перекрытие через сутки → `ConflictError`.
- Контроль D: вплотную (конец == началу плана) → разрешено (ложных срабатываний нет).

**Условия**: (data_state) у владельца есть запись 1250 с `remote_start` строго до целевого дня и пустым `remote_end` — обычное «забыл остановить удалёнку»; далее каждая плановая дата добавляет по одной лишней записи. (environmental_dependency) серверные фильтры Bitrix не матчат пустые datetime в `>`/`<` — стандартная SQL-семантика, подтверждена собственным тест-даблом SDK и самим существованием same_day-запроса (иначе он был бы избыточен).

**Минимальный фикс** (третий запрос «неоконченных, начатых до конца плана», влить в `by_id` — существующая ветка `not item.get(remote_end)` даст ConflictError):
```python
# sdk service.py, ветка remote в _find_duplicate
unfinished = await self.client.list_items(
    s.remote_type,
    filters={
        "assignedById": self.settings.user_id,
        f"<{s.remote_start}": plan.fields[s.remote_end],
        s.remote_end: "",
    },
)
by_id = {int(item["id"]): item for item in overlapping + same_day + unfinished}
```
Регрессия: обе «кросс-дневные» стороны (None и `''`) + вплотную остаётся разрешённым + закрытое перекрытие остаётся конфликтом.

---

## M-2. `.state/`-журнал host/cron-режима не игнорируется git'ом обёртки — `git add -A` публикует персональные данные

**Fingerprint**: `gitignore:wrapper-.state-uncovered` · severity medium (likelihood medium / impact medium) · confidence high

**Принципал и ресурс**: оператор (рутина `git add -A` + push) → аудитория публичного репозитория, получающая SQLite с данными о рабочем времени владельца.

**Инвариант**: собственный Boundaries-список репо (AGENTS.md Never: домен портала, USER_ID, ID дней/карточек, названия задач — никогда в коммитах) + правило «все локальные артефакты режимов эксплуатации исключены из VCS» (SDK-репо его соблюдает: `.gitignore:5` = `.state/`).

**Полный путь**:
1. `README.md:54-68` — host/cron-поток запускается из корня репо; `BITRIX_STATE_PATH` нигде для этого режима не задаётся (`.env.example` — только URL/TZ; `docker-compose.yml:10` — только для контейнера).
2. `sdk .../config.py:111` — дефолт `Path('.state/operations.sqlite3')` относительно CWD = корень репо.
3. `A cli.py:127` + `runner.py:47-48` + `scheduler.py:39` — write-режим для apply/daemon; `TimeManager` биндит журнал (`service.py:48`).
4. `sdk .../journal.py:27-28` — первый `bind_key/reserve` (`service.py:420/427`) создаёт `<repo>/.state/operations.sqlite3`. Preview не создаёт (dry_run-выход `:415-416` до bind).
5. `.gitignore:3` — есть только `state/` (docker-путь), `.state/` отсутствует во всех 14 строках → `git check-ignore .state/operations.sqlite3` = exit 1.
6. `service.py:423-476` + `models.py:104-115` — в journal.result пишется полный `OperationResult`: комментарии/описания, `item_url` с доменом портала, `assignedById` = USER_ID, ID записей — ровно классы значений из Never-list.
7. Превентивного слоя нет: хуки/pre-commit/CI/сканеры в обёртке отсутствуют; README:141/149 при этом уверяют оператора, что журнал лежит в `state/operations.sqlite3` (игнорируемый docker-путь) — фактический файл невидим.

**Ограниченное воспроизведение** (read-only git; артефакты `agents/verify-12/artifacts/evidence-state-uncovered.txt`, `agents/hunter-cron-host/artifacts/artifacts/gitignore-checks.txt`, `agents/hunter-hygiene2/artifacts/evidence-1-gitignore-matrix.txt`):
- A: `git check-ignore -v .state/operations.sqlite3` → exit 1 (не игнорируется); `state/operations.sqlite3` → exit 0 (`.gitignore:3`); `.env` → exit 0.
- B (SDK): `.state/operations.sqlite3` → exit 0 (`.gitignore:5`) — асимметрия пары репозиториев.
- Содержимое журнала не читалось (только стат/матрица git); на диске A `.state/` пока нет — он материализуется первым write-запуском, что полностью определяется кодом.

**Условия**: host/cron-режим с незаданным `BITRIX_STATE_PATH` (дефолт) + `git add -A`/`git add .` + push (репо готовится к публичному релизу, remote — github.com). Атакующий не участвует.

**Минимальный фикс**: строка `.state/` в `.gitignore` обёртки (зеркало SDK) + `.state/` в Never-list AGENTS.md + честная документация хост-дефолта в README:141/149. Опционально: дефолтить журнал хост-режима в уже игнорируемый `state/`. Максимальная защита: pre-commit/CI-сканер артефактов.

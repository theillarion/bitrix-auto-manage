# Требуют валидации владельцем (needs_validation, без severity)

Три записи с полным source-следом, у которых решающий факт лежит вне исходников и локальной песочницы. Это приоритетные lead'ы, НЕ подтверждённые уязвимости. Живые проверки развёртывания не выполнялись; шаги ниже — наблюдения владельца.

---

## NV-1. root-контейнер + rw `./state` → журнал root-owned на хосте

**Fingerprint**: `deployment/docker.root-container-rw-state-journal-perms`

**Source-след** (полный): `Dockerfile:13` (ENTRYPOINT, ни одного `USER`) → `docker-compose.yml:14` (`./state:/app/state` rw, без `user:`) → `:10` (`environment:` пинит `BITRIX_STATE_PATH`, у env_file приоритета нет*) → `sdk config.py:111` → `journal.py:27-28` (создание umask-зависимым) → `service.py:424/453/476` (plaintext work-данные в journal.result). Локальный рендер прав выполнен (`agents/verify-10/artifacts/render-perms.txt`): umask 022 → каталог 0755, файл 0644, владелец = uid процесса (root контейнера).

**Возможный результат**: (a) оператор (non-root на хосте) не может удалить/перезаписать собственный `state/` без sudo; (b) документированный смешанный режим (Docker-демон + host-cron `daemon --once`) ломается: root-owned 0644 оставляет оператора в классе «other» (r--), запись SQLite → `OperationalError` → `ConflictError`; (c) leg мировой читаемости дублирует подтверждённую находку journal-0644 — root-владение конфиденциальности не добавляет. На single-user хосте с дефолтным Docker root-в-контейнере ≡ host-root, т.е. пересечения доверенной границы нет — потому needs_validation, а не confirmed.

**Блокеры** (все — факты развёртывания): политика учётных записей хоста (есть ли non-owner читатели); конфиг dockerd (userns-remap/daemon.json); фактический uid/mode файлов после реального прогона (`./state` сейчас пуст); *преимущественность compose `environment:` над `env_file` — семантика compose-spec, локально не проверяема.

**Проверки владельцем**: `docker compose config` (сведение env); `docker info` (userns); запуск стека → `ls -ln state/` и `sudo ls -l` vs `ls -l`; попытка host-режима `daemon --once` оператором против того же журнала (ожидаемый ConflictError); листинг учётных записей.

---

## NV-2. `int(parentId1208)` после доставленной записи — тип echo неизвестен

**Fingerprint**: `service.py:_with_item:parent-id-1208-int-coercion-post-write` (унаследован из прошлого аудита; коррекция: «reconciliation unreachable» опровергнута — повторный прогон чинит `pending` через xmlId-ветку `:421-425` + `ON CONFLICT` в `journal.py:84-88`).

**Source-след**: `runner.py:95` → `service.py:293` → `:427` (reserve→pending) → `:434` (add доставлен; проверяется только id `:437`) → `:450` (`_with_item` ВНЕ except-кортежа `:439-449`) → `:398` (`int(parent_id)` для truthy echo) → ValueError/TypeError до `journal.complete :452`; в обёртке — сырой traceback и обрыв окна.

**Блокер**: фактический JSON-тип `result.item.parentId1208` в ответе `crm.item.add` не наблюдаем: `_check_schema:123` проверяет только метаданные типа поля («crm_entity»); `client.call:81-97` — только конверт; тестовый фейк (`fakes.py:105`) повторяет поля запроса (int, потому что `:286` шлёт int); live-тесты read-only (`test_live_portal.py:27,55` — только LIST-наблюдение int-коэрцибельности). Если компилянтный портал всегда echo'ит int — путь недостижим и кандидат должен быть отклонён.

**Локально уже выполнено**: харнесс с дословными выражениями (`agents/verify-11/artifacts/harness_verify11.py`): `'T1208_42'`→ValueError, dict→TypeError, None/''→fallback; strand pending + повторный прогон через xmlId.

**Проверка владельцем**: одна тестовая запись (`bitrix-time entry … --apply` на тестовом портале) с захватом ответа; зафиксировать тип `parentId1208`: int/цифровая строка → reclassify rejected; `'T1208_<id>'`/float/object → confirmed. Альтернатива без записи — документация вендора по типам полей ответа `crm.item.add` для crm_entity.

---

## NV-3. stdout-логи демона в policy-free стоках развёртывания

**Fingerprint**: `runner.py:operation-result-logging:daemon-stdout-unbounded-persistence`

**Source-след** (эмиссия полностью доказана локально): `cli.py:137-142` (INFO→stdout) → `runner.py:111` (на каждую запись: дата, kind, item_id, `item_url` с реальным доменом портала из `config.py:87-90` через `service.py:78-80`) + `runner.py:85-89`, `scheduler.py:33-35,61-62`, ошибки `:58-74` (редукция только вебхук-сегмента, `errors.py:6-8` — доказано `agents/verify-14/artifacts/redact_scope.txt`); без free-text комментариев; затем стоки: `Dockerfile:13-14` (daemon = ENTRYPOINT) + `docker-compose.yml` без `logging:` (документированный дефолт json-file без ротации — факт Docker) и `README.md:63-68` (cron без редирекции/MAILTO → mail spool). Ретеншн-политики нет нигде (`docs/architecture.md:40` — TODO только по журналу). Копия переживает удаление `state/`.

**Блокеры**: эффективный драйвер/опции логов на хосте; права `/var/lib/docker/containers/*/*-json.log` и состав docker-группы; MAILTO/spool и фактическое использование cron; какой режим развёртывания реально активен; существует ли на хосте хоть один lower-trust принципал (без него — гигиена, не граница).

**Локально уже выполнено**: рендер всех строк лога (`stdout_render.txt`) и области redact.

**Проверки владельцем**: `docker inspect <container> --format '{{.HostConfig.LogConfig}}'`; `sudo ls -l /var/lib/docker/containers/*/*-json.log`; тест `docker logs` из non-owner учётки в docker-группе; `crontab -l` (MAILTO), `ls -l /var/mail/*`; учётные записи хоста.

# bitrix-auto-manage
Автоматизация Time-менеджера Bitrix24: YAML-расписание удалёнки и учета времени применяется демоном в Docker или из cron. Обертка над SDK [sdk-bitrix-time-manager](https://github.com/theillarion/sdk-bitrix-time-manager) в соседнем каталоге `../sdk-bitrix-time-manager`. Инвариант: записи — только через SDK с SQLite-журналом, идемпотентно.

## Commands

| Команда | Зачем |
| --- | --- |
| `python3 -m venv .venv` | создать окружение (один раз) |
| `source .venv/bin/activate` | включить окружение |
| `pip install -e '../sdk-bitrix-time-manager' -e '.[dev]'` | поставить SDK, пакет и dev-инструменты (один раз) |
| `pytest -q --cov=bitrix_auto_manage --cov-report=term-missing` | все тесты + покрытие по модулям |
| `pytest -q tests/test_config.py -k weekdays` | один тест: файл + фильтр по имени |
| `ruff check src tests` | линт |
| `mypy src` | типы (strict) |

## Boundaries

Never:
- реальные данные портала (домен, USER_ID, ID дней и карточек, названия задач, URL вебхуков) — только плейсхолдеры;
- `.env`, `state/`, `.state/`, живые `config/schedule.yaml`, `config/*.local.yaml`;
- `Literal[1048, 1052, 1298]` в `src/bitrix_auto_manage/config.py` — типы смарт-процессов портала: не менять;
- force push, переписывание истории, релизы без явной команды.
Ask first:
- новые зависимости в `pyproject.toml`;
- обращения к реальному порталу, даже read-only `check`/`preview`;
- схема YAML-моделей (`extra="forbid"`); правки смежного SDK: после них прогнать `pytest` и `mypy` этого репо и убедиться, что поведение и CLI не изменились; при поломке предложить два варианта — с сохранением обратной совместимости и без (до 1.0.0 SDK вправе ломать совместимость).

## Agents

Главный агент только делегирует: не читает, не правит, не запускает.

### Pipeline

```
planner → implementer → tester → verifier → PASS
              ↑__________________|
                FAIL (max 3)
```

### Roles

| Роль | Файл | Вход | Выход |
| --- | --- | --- | --- |
| planner | `docs/agents/planner/ROLE.md` | задача, критерии приёмки | `spec.md`, `hypotheses.md` |
| implementer | `docs/agents/implementer/ROLE.md` | `spec.md`, `hypotheses.md` | код, `claims.md` |
| tester | `docs/agents/tester/ROLE.md` | `spec.md`, `hypotheses.md`, diff | проверки |
| verifier | `docs/agents/verifier/ROLE.md` | `spec.md`, `hypotheses.md`, `claims.md`, diff | `verdicts.md`, PASS/FAIL |

### Artifacts

| Файл | Кто пишет | Кто читает |
| --- | --- | --- |
| `.agents-cache/<task>/spec.md` | planner | implementer, tester, verifier |
| `.agents-cache/<task>/hypotheses.md` | planner, verifier | implementer, tester, verifier |
| `.agents-cache/<task>/claims.md` | implementer | verifier |
| `.agents-cache/<task>/verdicts.md` | verifier | implementer, человек |

### Rules

- Полный пайплайн: >1 файла, >~50 строк, критичные пути. Мелочи: `implementer` + `verifier`.
- Нет исполняемых проверок — `tester` не вызывается.
- FAIL → `implementer` с `verdicts.md`; максимум 3 раунда, дальше человек.
- `.agents-cache/` в `.gitignore`.

## Conventions

- Документация — на русском; сообщения логов и ошибок в коде — на английском.
- Коммиты — английские, короткие; новая функциональность — с тестами и покрытием модуля ≥95%.
- Изменения — в `[Unreleased]` `CHANGELOG.md`; релиз — по явной команде.

## Checklist

| Проверка | Команда | Зеленый, когда |
| --- | --- | --- |
| lint | `python -m ruff check src tests` | exit 0 |
| typecheck | `python -m mypy src` | exit 0 |
| tests | `python -m pytest -q --cov=bitrix_auto_manage --cov-report=term-missing` | 0 failed, каждый модуль ≥95% |
| регрессионный тест | pytest на base: `git worktree add /tmp/base <ref>` | падает на base, проходит сейчас |
| verifier | `verdicts.md` | PASS: 0 REFUTED, все CRITICAL CONFIRMED |
| evidence | `.agents-cache/<task>/verdicts.md` | по каждой гипотезе |
| secrets / generated | `git status --porcelain` + grep по `Boundaries` | чисто |
| scope | `git diff` против `spec.md` | diff только из spec |

Любая красная строка — задача не готова.

## Deeper context

- `README.md` — быстрый старт, команды, настройки окружения.
- `docs/setup.md` — создание вебхука и полный справочник YAML-расписания.
- `docs/architecture.md` — границы, компоненты, потоки, инварианты.

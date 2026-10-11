# bitrix-auto-manage

Обертка над [sdk-python-bitrix-time-manager](../sdk-python-bitrix-time-manager):
расписание рабочего дня, удалёнки и учета времени задается YAML-файлами и
применяется автоматически — демоном в Docker или разовым запуском из cron
любой ОС.

Повторные прогоны безопасны: SDK идемпотентен, существующие записи
возвращаются как `existing`, дубли и конфликты не создаются.

## Быстрый старт (Docker)

```bash
cp .env.example .env                  # впишите свой BITRIX_WEBHOOK_URL
cp config/schedule.example.yaml config/schedule.yaml
$EDITOR config/schedule.yaml          # свои дни, время, договоры
docker compose up -d --build
docker compose logs -f                # смотреть журнал прогонов
```

Демон при старте сразу применяет окно дат, затем повторяет каждый день в
`schedule.apply_time` (локальное время из YAML). После сбоя повторяет попытку
через `retry_minutes`.

SDK подключается в контейнер монтированием соседнего каталога
`../sdk-python-bitrix-time-manager` в `/opt/sdk`. Если он лежит в другом месте,
задайте переменную `BITRIX_SDK_PATH` в `.env`.

## Локальный запуск без Docker (macOS, Linux, Windows)

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e '../sdk-python-bitrix-time-manager' -e .
cp .env.example .env
bitrix-auto preview --config config/schedule.example.yaml --date 2026-10-19 --days 1
bitrix-auto apply   --config config/schedule.yaml
bitrix-auto daemon  --config config/schedule.yaml --once   # для cron/Планировщика Windows
```

Без Docker демона можно заменить системным планировщиком:
`bitrix-auto daemon --once` выполняет ровно один прогон окна и выходит
(cron: `5 18 * * *`, Планировщик Windows: ежедневное задание).

## Команды

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

## Формат YAML

```yaml
timezone: Europe/Moscow        # должна совпадать с BITRIX_TIMEZONE

schedule:
  apply_time: "18:05"          # локальное время ежедневного прогона демона
  horizon_days: 2              # выставлять на сегодня и 2 дня вперед
  backfill_days: 1             # догонять вчерашний день после простоя
  retry_minutes: 20            # повтор после неудачного прогона

tasks:
  # Рабочий день здесь НЕ задается: он появляется сам —
  # по отметке СКУД или в момент начала удалённого дня (роботы портала).

  - kind: remote               # удалённая работа, выставляется заранее
    days: [fri]                # mon..sun или пн..вс, диапазоны через дефис
    start: "09:00"
    end: "18:00"
    comment: "Удалённая работа"

  - kind: time_entry           # учет времени, когда день уже существует
    days: [mon-fri]
    contract:
      entity_type: 1298        # 1048/1052 — карточки договоров, 1298 — шаблоны
      id: 2
    description: "Общие задачи"
    hours: 7
    minutes: 30                # только 0 или 30
```

Существует еще `kind: workday` (ручной обычный день, аналог
`bitrix-time workday`). Он не используется и на этом портале не работает:
`crm.item.add` в смарт-процесс рабочих дней возвращает `ACCESS_DENIED`
(права вебхука на запись туда нет), а в типовом сценарии день и не нужен —
он создается сам по отметке СКУД или роботами удалёнки. Не добавляйте
`kind: workday` в расписание.

Каталог `config/` может содержать несколько файлов `*.yaml` — они применяются
по алфавиту: скалярные значения и секция `schedule` перезаписываются поздними
файлами, а списки `tasks` объединяются. Удобно держать базовое расписание и
отдельный файл с удалёнкой.

## Настройки окружения (`.env`)

| Переменная | Значение |
|---|---|
| `BITRIX_WEBHOOK_URL` | Обязателен: `https://портал/rest/USER_ID/СЕКРЕТ/` |
| `BITRIX_TIMEZONE` | `Europe/Moscow`; должна совпадать с `timezone` в YAML |
| `BITRIX_STATE_PATH` | В Docker задан как `/app/state/operations.sqlite3` |
| `BITRIX_TIMEOUT`, `BITRIX_READ_RETRIES` | Необязательны, см. документацию SDK |

Разрешение записи включается только на время `apply`/`daemon` внутри
приложения; `preview`, `check` и `show` выполняют только чтение.

## Журнал операций и особые случаи

- Журнал SQLite (`state/operations.sqlite3`) — общий для всех прогонов;
  не удаляйте его: SDK блокирует повторную отправку потерянных операций.
- `time_entry` требует уже существующего рабочего дня. День появляется сам:
  по отметке СКУД или от роботов удалёнки. Если его еще нет, запись учета
  пропускается и будет выполнена при следующем прогоне (`backfill_days`).
- Изменение параметров уже примененной задачи создает новую логическую
  операцию: SDK вернет конфликт для пересечений или `existing` для совпадений.
  Старые записи за прошлые дни нужно править на портале вручную.
- При `uncertain` (потерянный ответ сети) прогон останавливает эту операцию;
  разблокировка — вручную через `bitrix-time resolve` из SDK, см. README SDK.

## Проверки разработки

```bash
python -m pytest -q
python -m ruff check src tests
python -m ruff format --check src tests
python -m mypy src
```

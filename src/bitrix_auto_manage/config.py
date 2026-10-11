"""YAML configuration models, loading and merging for multiple files."""

import re
from datetime import date, time, timedelta
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from bitrix_time_manager.errors import ConfigurationError
from bitrix_time_manager.models import ContractRef, RemoteWorkCreate, TimeEntryCreate, WorkdayCreate
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator, model_validator

WEEKDAY_NAMES = {
    "mon": 0,
    "пн": 0,
    "tue": 1,
    "вт": 1,
    "wed": 2,
    "ср": 2,
    "thu": 3,
    "чт": 3,
    "fri": 4,
    "пт": 4,
    "sat": 5,
    "сб": 5,
    "sun": 6,
    "вс": 6,
}

KIND_ORDER = {"workday": 0, "remote": 1, "time_entry": 2}


def parse_weekdays(value: Any) -> frozenset[int]:
    """Parse 'mon-fri', ['sat', 'sun', 'пн-чт'] into a set of weekday numbers."""
    items: list[str]
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        items = value
    else:
        raise ValueError("Дни недели задаются строкой или списком строк, например [mon-fri, sat].")
    days: set[int] = set()
    for item in items:
        token = item.strip().lower()
        match = re.fullmatch(r"([a-zа-яё]+)\s*-\s*([a-zа-яё]+)", token)
        if match:
            begin, end = WEEKDAY_NAMES.get(match[1]), WEEKDAY_NAMES.get(match[2])
            if begin is None or end is None:
                raise ValueError(f"Неизвестные дни недели: {item!r}. Допустимо: {', '.join(WEEKDAY_NAMES)}.")
            if begin <= end:
                days.update(range(begin, end + 1))
            else:
                days.update(range(begin, 7))
                days.update(range(0, end + 1))
            continue
        day = WEEKDAY_NAMES.get(token)
        if day is None:
            raise ValueError(f"Неизвестный день недели: {item!r}. Допустимо: {', '.join(WEEKDAY_NAMES)}.")
        days.add(day)
    if not days:
        raise ValueError("Список дней недели пуст.")
    return frozenset(days)


class ScheduleSection(BaseModel):
    """Daemon timing: when to run and which date window to cover."""

    model_config = ConfigDict(extra="forbid")

    apply_time: time = time(18, 5)
    horizon_days: int = Field(default=2, ge=0, le=30)
    backfill_days: int = Field(default=1, ge=0, le=30)
    retry_minutes: int = Field(default=20, ge=1, le=720)


class ContractSection(BaseModel):
    """Reference to a contract template or card for time entries."""

    model_config = ConfigDict(extra="forbid")

    entity_type: Literal[1048, 1052, 1298]
    id: PositiveInt


class TaskSection(BaseModel):
    """One scheduled operation kind with its days of week and parameters."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["workday", "remote", "time_entry"]
    days: frozenset[int]
    start: time | None = None
    end: time | None = None
    comment: str = ""
    contract: ContractSection | None = None
    description: str | None = None
    hours: int = Field(default=0, ge=0, le=24)
    minutes: Literal[0, 30] = 0

    @field_validator("days", mode="before")
    @classmethod
    def parse_days(cls, value: Any) -> Any:
        return parse_weekdays(value)

    @field_validator("comment", "description")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def check_kind_fields(self) -> Self:
        if self.kind in {"workday", "remote"}:
            if self.start is None or self.end is None:
                raise ValueError(f"Для kind: {self.kind} обязательны start и end, например '09:00' и '18:00'.")
            if self.end <= self.start:
                raise ValueError("end должен быть позже start в пределах одного дня.")
            return self
        if self.contract is None or not self.description:
            raise ValueError("Для kind: time_entry обязательны contract (entity_type, id) и description.")
        if self.hours * 60 + self.minutes <= 0:
            raise ValueError("Длительность учета времени должна быть больше нуля, например hours: 7, minutes: 30.")
        return self


class AppConfig(BaseModel):
    """Full automation configuration assembled from one or more YAML files."""

    model_config = ConfigDict(extra="forbid")

    timezone: str = "Europe/Moscow"
    schedule: ScheduleSection = ScheduleSection()
    tasks: list[TaskSection] = Field(default_factory=list)


def _merge_dicts(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in extra.items():
        if key == "tasks" and isinstance(value, list):
            merged[key] = [*merged.get(key, []), *value]
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def collect_config_files(paths: list[Path]) -> list[Path]:
    """Expand directories into their YAML files in sorted order."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            found = sorted([*path.glob("*.yaml"), *path.glob("*.yml")])
            if not found:
                raise ConfigurationError(f"В каталоге {path} нет YAML-файлов с настройками.")
            files.extend(found)
        elif path.is_file():
            files.append(path)
        else:
            raise ConfigurationError(f"Путь настроек не найден: {path}")
    return files


def load_config(paths: list[Path]) -> AppConfig:
    """Load and merge YAML files; later files override scalar values and extend tasks."""
    data: dict[str, Any] = {}
    for path in collect_config_files(paths):
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            raise ConfigurationError(f"Не удалось разобрать YAML-файл {path}: {error}") from None
        if loaded is None:
            continue
        if not isinstance(loaded, dict):
            raise ConfigurationError(f"YAML-файл {path} должен содержать отображение настроек.")
        data = _merge_dicts(data, loaded)
    try:
        return AppConfig.model_validate(data)
    except ValueError as error:
        raise ConfigurationError(str(error)) from None


def date_range(config: AppConfig, today: date) -> list[date]:
    """Dates covered by one automatic run: backfill through horizon, oldest first."""
    start = today - timedelta(days=config.schedule.backfill_days)
    stop = today + timedelta(days=config.schedule.horizon_days)
    return [start + timedelta(days=offset) for offset in range((stop - start).days + 1)]


def plan_for_date(config: AppConfig, day: date) -> list[WorkdayCreate | RemoteWorkCreate | TimeEntryCreate]:
    """Specs scheduled for this calendar day, ordered workday, remote, time entry."""
    specs: list[WorkdayCreate | RemoteWorkCreate | TimeEntryCreate] = []
    for task in sorted(config.tasks, key=lambda item: KIND_ORDER[item.kind]):
        if day.weekday() not in task.days:
            continue
        if task.kind == "workday":
            specs.append(WorkdayCreate(day=day, start=task.start, end=task.end, comment=task.comment))
        elif task.kind == "remote":
            specs.append(RemoteWorkCreate(day=day, start=task.start, end=task.end, comment=task.comment))
        else:
            assert task.contract is not None and task.description is not None
            specs.append(
                TimeEntryCreate(
                    day=day,
                    contract=ContractRef(entity_type_id=task.contract.entity_type, item_id=task.contract.id),
                    description=task.description,
                    hours=task.hours,
                    minutes=task.minutes,
                )
            )
    return specs


def idempotency_key(spec: WorkdayCreate | RemoteWorkCreate | TimeEntryCreate) -> str:
    """Stable per-date, per-parameters key so reruns never duplicate records."""
    import hashlib
    import json

    payload = json.dumps(spec.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:8]
    return f"auto:{spec.day.isoformat()}:{digest}"

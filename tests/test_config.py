"""Tests for YAML config parsing, merging and planning."""

from datetime import date, time, timedelta
from pathlib import Path

import pytest
from bitrix_time_manager.models import RemoteWorkCreate, TimeEntryCreate, WorkdayCreate
from pydantic import ValidationError

from bitrix_auto_manage.config import (
    AppConfig,
    TaskSection,
    date_range,
    idempotency_key,
    load_config,
    parse_weekdays,
    plan_for_date,
)

MINIMAL = """
timezone: Europe/Moscow
tasks:
  - kind: workday
    days: [mon-fri]
    start: "09:00"
    end: "18:00"
"""


class TestParseWeekdays:
    def test_range(self) -> None:
        assert parse_weekdays("mon-fri") == frozenset({0, 1, 2, 3, 4})

    def test_wrapped_range(self) -> None:
        assert parse_weekdays("sat-mon") == frozenset({0, 5, 6})

    def test_russian_names(self) -> None:
        assert parse_weekdays(["пн", "ср-пт"]) == frozenset({0, 2, 3, 4})

    def test_single_string(self) -> None:
        assert parse_weekdays("sun") == frozenset({6})

    def test_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            parse_weekdays([])

    def test_unknown_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown weekday"):
            parse_weekdays(["funday"])


class TestTaskSection:
    def test_workday_requires_interval(self) -> None:
        with pytest.raises(ValidationError, match="requires start and end"):
            TaskSection.model_validate({"kind": "workday", "days": ["mon"], "start": "09:00"})

    def test_end_after_start(self) -> None:
        with pytest.raises(ValidationError, match="later than start"):
            TaskSection.model_validate({"kind": "remote", "days": ["mon"], "start": "18:00", "end": "09:00"})

    def test_time_entry_requires_contract(self) -> None:
        with pytest.raises(ValidationError, match="time_entry"):
            TaskSection.model_validate({"kind": "time_entry", "days": ["mon"], "description": "Работа", "hours": 1})

    def test_unknown_entity_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TaskSection.model_validate(
                {
                    "kind": "time_entry",
                    "days": ["mon"],
                    "contract": {"entity_type": 1234, "id": 1},
                    "description": "Работа",
                    "hours": 1,
                }
            )

    def test_unknown_top_level_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TaskSection.model_validate(
                {"kind": "remote", "days": ["mon"], "start": "09:00", "end": "18:00", "tim": "x"}
            )


class TestLoadConfig:
    def test_minimal(self, tmp_path: Path) -> None:
        path = tmp_path / "schedule.yaml"
        path.write_text(MINIMAL, encoding="utf-8")
        config = load_config([path])
        assert config.timezone == "Europe/Moscow"
        assert config.schedule.apply_time == time(18, 5)
        assert len(config.tasks) == 1

    def test_merge_extends_tasks_and_overrides_scalars(self, tmp_path: Path) -> None:
        first = tmp_path / "10-base.yaml"
        first.write_text(MINIMAL, encoding="utf-8")
        second = tmp_path / "20-extra.yaml"
        second.write_text(
            """
schedule:
  apply_time: "17:00"
tasks:
  - kind: remote
    days: [fri]
    start: "09:00"
    end: "18:00"
""",
            encoding="utf-8",
        )
        config = load_config([first, second])
        assert config.schedule.apply_time == time(17, 0)
        assert [task.kind for task in config.tasks] == ["workday", "remote"]

    def test_directory_is_scanned_in_order(self, tmp_path: Path) -> None:
        (tmp_path / "10-base.yaml").write_text(MINIMAL, encoding="utf-8")
        (tmp_path / "20-extra.yaml").write_text("schedule:\n  horizon_days: 5\n", encoding="utf-8")
        config = load_config([tmp_path])
        assert config.schedule.horizon_days == 5

    def test_directory_skips_example_files(self, tmp_path: Path) -> None:
        (tmp_path / "10-base.yaml").write_text(MINIMAL, encoding="utf-8")
        (tmp_path / "schedule.example.yaml").write_text(
            "tasks:\n  - kind: remote\n    days: [sun]\n    start: '01:00'\n    end: '02:00'\n",
            encoding="utf-8",
        )
        config = load_config([tmp_path])
        assert [task.kind for task in config.tasks] == ["workday"]

    def test_empty_directory_rejected(self, tmp_path: Path) -> None:
        from bitrix_time_manager.errors import ConfigurationError

        with pytest.raises(ConfigurationError, match="No settings YAML"):
            load_config([tmp_path])

    def test_invalid_yaml_rejected(self, tmp_path: Path) -> None:
        from bitrix_time_manager.errors import ConfigurationError

        path = tmp_path / "bad.yaml"
        path.write_text("tasks: [unclosed", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="Failed to parse YAML"):
            load_config([path])

    def test_duplicate_kind_merge_replaces_task_list_entry(self, tmp_path: Path) -> None:
        base = tmp_path / "base.yaml"
        base.write_text(MINIMAL, encoding="utf-8")
        patch = tmp_path / "patch.yaml"
        patch.write_text(
            """
schedule:
  backfill_days: 3
""",
            encoding="utf-8",
        )
        config = load_config([base, patch])
        assert config.schedule.backfill_days == 3
        assert config.schedule.horizon_days == 2


class TestPlanning:
    @pytest.fixture
    def config(self) -> AppConfig:
        return AppConfig.model_validate(
            {
                "tasks": [
                    {"kind": "workday", "days": ["mon-fri"], "start": "09:00", "end": "18:00"},
                    {
                        "kind": "remote",
                        "days": ["fri"],
                        "start": "09:00",
                        "end": "18:00",
                        "comment": "Удалёнка",
                    },
                    {
                        "kind": "time_entry",
                        "days": ["mon-fri"],
                        "contract": {"entity_type": 1298, "id": 2},
                        "description": "Общие задачи",
                        "hours": 7,
                        "minutes": 30,
                    },
                ]
            }
        )

    def test_order_and_filtering(self, config: AppConfig) -> None:
        friday = date(2026, 10, 16)
        assert date(2026, 10, 16).weekday() == 4
        kinds = [type(spec).__name__ for _, spec in plan_for_date(config, friday)]
        assert kinds == ["WorkdayCreate", "RemoteWorkCreate", "TimeEntryCreate"]

    def test_weekend_without_tasks(self, config: AppConfig) -> None:
        saturday = date(2026, 10, 17)
        assert plan_for_date(config, saturday) == []

    def test_two_month_invariant(self, config: AppConfig) -> None:
        for offset in range(60):
            day = date(2026, 10, 11) + timedelta(days=offset)
            weekday = day.weekday()
            for _, spec in plan_for_date(config, day):
                if isinstance(spec, RemoteWorkCreate):
                    assert weekday == 4
                elif isinstance(spec, (TimeEntryCreate, WorkdayCreate)):
                    assert weekday <= 4
                else:
                    pytest.fail(f"Незапланированный вид операции: {spec}")

    def test_time_entry_values(self, config: AppConfig) -> None:
        monday = date(2026, 10, 12)
        plans = plan_for_date(config, monday)
        task, entry = plans[-1]
        assert task.on_existing_entries == "skip"
        assert entry.contract is not None
        assert entry.contract.crm_value == "T512_2"
        assert entry.day == monday

    def test_append_policy_accepted_and_invalid_rejected(self) -> None:
        task = TaskSection.model_validate(
            {
                "kind": "time_entry",
                "days": ["mon"],
                "contract": {"entity_type": 1298, "id": 2},
                "description": "Работа",
                "hours": 1,
                "on_existing_entries": "append",
            }
        )
        assert task.on_existing_entries == "append"
        with pytest.raises(ValidationError):
            TaskSection.model_validate(
                {
                    "kind": "time_entry",
                    "days": ["mon"],
                    "contract": {"entity_type": 1298, "id": 2},
                    "description": "Работа",
                    "hours": 1,
                    "on_existing_entries": "replace",
                }
            )


class TestWindowAndKey:
    def test_date_range(self) -> None:
        config = AppConfig.model_validate({"schedule": {"horizon_days": 2, "backfill_days": 1}})
        days = date_range(config, date(2026, 10, 14))
        assert days == [date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16)]

    def test_key_is_stable_per_date_and_params(self) -> None:
        spec_a = TaskSection.model_validate({"kind": "remote", "days": ["fri"], "start": "09:00", "end": "18:00"})
        spec_b = TaskSection.model_validate({"kind": "remote", "days": ["fri"], "start": "10:00", "end": "18:00"})
        day = date(2026, 10, 16)

        def build(section: TaskSection) -> str:
            from bitrix_auto_manage.config import RemoteWorkCreate

            return idempotency_key(
                RemoteWorkCreate(day=day, start=section.start, end=section.end, comment=section.comment)
            )

        assert build(spec_a) == build(spec_a)
        assert build(spec_a) != build(spec_b)
        assert build(spec_a).startswith("auto:2026-10-16:")

"""Tests for YAML config parsing, merging and planning."""

from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from bitrix_time_manager.errors import ConfigurationError
from bitrix_time_manager.models import RemoteWorkCreate, TimeEntryCreate
from pydantic import ValidationError

from bitrix_auto_manage.config import (
    AppConfig,
    TaskSection,
    date_range,
    idempotency_key,
    load_config,
    parse_weekdays,
    plan_for_date,
    today_in,
)

MINIMAL = """
timezone: Europe/Moscow
tasks:
  - kind: remote
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

    def test_non_string_value_rejected(self) -> None:
        for value in (123, ["mon", 1]):
            with pytest.raises(ValueError, match="string or a list of strings"):
                parse_weekdays(value)

    def test_unknown_range_boundary_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown weekday names"):
            parse_weekdays("mon-funday")


class TestTaskSection:
    def test_workday_kind_is_rejected_fail_closed(self) -> None:
        with pytest.raises(ValidationError, match="literal_error"):
            TaskSection.model_validate({"kind": "workday", "days": ["mon"], "start": "09:00", "end": "18:00"})

    def test_remote_requires_interval(self) -> None:
        with pytest.raises(ValidationError, match="requires start and end"):
            TaskSection.model_validate({"kind": "remote", "days": ["mon"], "start": "09:00"})

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

    def test_time_entry_zero_duration_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duration must be greater than zero"):
            TaskSection.model_validate(
                {
                    "kind": "time_entry",
                    "days": ["mon"],
                    "contract": {"entity_type": 1298, "id": 2},
                    "description": "Работа",
                    "hours": 0,
                    "minutes": 0,
                }
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
        assert [task.kind for task in config.tasks] == ["remote", "remote"]

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
        assert [task.kind for task in config.tasks] == ["remote"]

    def test_empty_directory_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="No settings YAML"):
            load_config([tmp_path])

    def test_invalid_yaml_rejected(self, tmp_path: Path) -> None:
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

    def test_merge_recurses_into_nested_dicts(self, tmp_path: Path) -> None:
        base = tmp_path / "base.yaml"
        base.write_text("schedule:\n  horizon_days: 1\n", encoding="utf-8")
        patch = tmp_path / "patch.yaml"
        patch.write_text("schedule:\n  backfill_days: 3\n", encoding="utf-8")
        config = load_config([base, patch])
        assert config.schedule.horizon_days == 1
        assert config.schedule.backfill_days == 3
        assert config.schedule.apply_time == time(18, 5)

    def test_missing_path_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="Settings path not found"):
            load_config([tmp_path / "missing.yaml"])

    def test_empty_yaml_gives_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        assert load_config([path]) == AppConfig()

    def test_non_mapping_yaml_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "list.yaml"
        path.write_text("- one\n- two\n", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="must contain a settings mapping"):
            load_config([path])

    def test_invalid_model_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "invalid.yaml"
        path.write_text(
            "tasks:\n  - kind: remote\n    days: [funday]\n    start: '09:00'\n    end: '18:00'\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="Unknown weekday"):
            load_config([path])

    def test_workday_kind_rejected_as_configuration_error(self, tmp_path: Path) -> None:
        path = tmp_path / "legacy-workday.yaml"
        path.write_text(
            "tasks:\n  - kind: workday\n    days: [mon]\n    start: '09:00'\n    end: '18:00'\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="literal_error"):
            load_config([path])


class TestTodayIn:
    def test_returns_today_in_configured_timezone(self) -> None:
        config = AppConfig.model_validate({"timezone": "Pacific/Kiritimati"})
        assert today_in(config) == datetime.now(ZoneInfo("Pacific/Kiritimati")).date()


class TestPlanning:
    @pytest.fixture
    def config(self) -> AppConfig:
        return AppConfig.model_validate(
            {
                "tasks": [
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
        assert kinds == ["RemoteWorkCreate", "TimeEntryCreate"]

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
                elif isinstance(spec, TimeEntryCreate):
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

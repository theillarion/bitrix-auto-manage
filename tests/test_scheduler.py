"""Tests for the daemon loop: timing math, exit codes and retry pauses (no waiting, no network)."""

import asyncio
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from bitrix_time_manager.config import Settings
from bitrix_time_manager.errors import ConfigurationError, TimeManagerError
from pydantic import SecretStr

from bitrix_auto_manage import scheduler
from bitrix_auto_manage.config import AppConfig, date_range
from bitrix_auto_manage.runner import RunSummary
from bitrix_auto_manage.scheduler import _format_pause, _next_run, run_daemon

WEBHOOK = "https://example.com/rest/1/testsecret/"
MOSCOW = ZoneInfo("Europe/Moscow")


def make_settings(timezone: str = "Europe/Moscow") -> Settings:
    return Settings(webhook_url=SecretStr(WEBHOOK), timezone=timezone)


def make_config(**schedule: Any) -> AppConfig:
    values: dict[str, Any] = {"apply_time": "18:05", **schedule}
    return AppConfig.model_validate({"timezone": "Europe/Moscow", "schedule": values})


class SleepSpy:
    """Replaces scheduler.asyncio: records the requested pauses, cancels the loop after N sleeps."""

    def __init__(self, cancel_after: int = 1) -> None:
        self.seconds: list[float] = []
        self._cancel_after = cancel_after

    async def sleep(self, seconds: float) -> None:
        self.seconds.append(seconds)
        if len(self.seconds) >= self._cancel_after:
            raise asyncio.CancelledError


class LoaderSpy:
    """Replaces scheduler.load_config: yields queued outcomes, records paths and call order."""

    def __init__(self, outcomes: list[AppConfig | Exception], events: list[str] | None = None) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[list[Path]] = []
        self.events = events if events is not None else []

    def __call__(self, paths: list[Path]) -> AppConfig:
        self.calls.append(list(paths))
        self.events.append("load")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeDateTime:
    """Replaces scheduler.datetime with a fixed clock."""

    @classmethod
    def now(cls, tz: ZoneInfo) -> datetime:
        return datetime(2026, 10, 21, 12, 0, tzinfo=tz)


def _run_window_returning(summary: RunSummary) -> Any:
    """A run_window fake that always returns the given summary."""

    async def fake_run_window(config: AppConfig, settings: Settings, days: list[date], *, dry_run: bool) -> RunSummary:
        return summary

    return fake_run_window


class TestNextRun:
    def test_before_apply_time_runs_today(self) -> None:
        now = datetime(2026, 10, 21, 15, 0, tzinfo=MOSCOW)
        assert _next_run(make_config(), now) == datetime(2026, 10, 21, 18, 5, tzinfo=MOSCOW)

    def test_exactly_at_apply_time_runs_tomorrow(self) -> None:
        now = datetime(2026, 10, 21, 18, 5, 0, tzinfo=MOSCOW)
        assert _next_run(make_config(), now) == datetime(2026, 10, 22, 18, 5, tzinfo=MOSCOW)

    def test_inside_startup_grace_runs_tomorrow(self) -> None:
        now = datetime(2026, 10, 21, 18, 5, 10, tzinfo=MOSCOW)
        assert _next_run(make_config(), now) == datetime(2026, 10, 22, 18, 5, tzinfo=MOSCOW)

    def test_long_after_apply_time_runs_tomorrow(self) -> None:
        now = datetime(2026, 10, 21, 21, 30, tzinfo=MOSCOW)
        assert _next_run(make_config(), now) == datetime(2026, 10, 22, 18, 5, tzinfo=MOSCOW)


class TestFormatPause:
    def test_formats_hours_and_minutes(self) -> None:
        assert _format_pause(timedelta(hours=2, minutes=5)) == "02:05"

    def test_zero_and_negative_pause_are_clamped(self) -> None:
        assert _format_pause(timedelta(0)) == "00:00"
        assert _format_pause(timedelta(seconds=-30)) == "00:00"


class TestRunOnce:
    async def test_runs_configured_window(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        seen: dict[str, Any] = {}

        async def fake_run_window(
            config: AppConfig, settings: Settings, days: list[date], *, dry_run: bool
        ) -> RunSummary:
            seen["days"] = days
            seen["dry_run"] = dry_run
            return RunSummary(created=2)

        monkeypatch.setattr(scheduler, "run_window", fake_run_window)
        monkeypatch.setattr(scheduler, "today_in", lambda config: date(2026, 10, 12))
        config = make_config(horizon_days=2, backfill_days=1)
        with caplog.at_level(logging.INFO, logger="bitrix_auto_manage"):
            summary = await scheduler._run_once(config, make_settings(), dry_run=True)
        assert summary.created == 2
        assert seen["days"] == date_range(config, date(2026, 10, 12))
        assert seen["dry_run"] is True
        assert "Running window 2026-10-11..2026-10-14 (dry_run=True)" in caplog.text


class TestRunDaemon:
    paths = [Path("/etc/bitrix/schedule.yaml")]

    async def test_reloads_config_before_every_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        v1, v2 = make_config(horizon_days=2), make_config(horizon_days=5)
        events: list[str] = []
        loader = LoaderSpy([v1, v2], events)
        monkeypatch.setattr(scheduler, "load_config", loader)
        seen: list[AppConfig] = []

        async def fake_run_window(
            config: AppConfig, settings: Settings, days: list[date], *, dry_run: bool
        ) -> RunSummary:
            seen.append(config)
            events.append("run")
            return RunSummary(created=1)

        monkeypatch.setattr(scheduler, "run_window", fake_run_window)
        monkeypatch.setattr(scheduler, "datetime", FakeDateTime)
        spy = SleepSpy(cancel_after=2)
        monkeypatch.setattr(scheduler, "asyncio", spy)
        with pytest.raises(asyncio.CancelledError):
            await run_daemon(self.paths, make_settings())
        assert loader.calls == [self.paths, self.paths]
        assert seen == [v1, v2]
        assert events == ["load", "run", "load", "run"]

    async def test_failed_reload_is_failed_run_and_old_config_stays_unapplied(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        v1 = make_config(retry_minutes=20)
        loader = LoaderSpy([v1, ConfigurationError("Failed to parse YAML file bad.yaml: boom")])
        monkeypatch.setattr(scheduler, "load_config", loader)
        seen: list[AppConfig] = []

        async def fake_run_window(
            config: AppConfig, settings: Settings, days: list[date], *, dry_run: bool
        ) -> RunSummary:
            seen.append(config)
            return RunSummary(created=1)

        monkeypatch.setattr(scheduler, "run_window", fake_run_window)
        monkeypatch.setattr(scheduler, "datetime", FakeDateTime)
        spy = SleepSpy(cancel_after=2)
        monkeypatch.setattr(scheduler, "asyncio", spy)
        with caplog.at_level(logging.INFO, logger="bitrix_auto_manage"), pytest.raises(asyncio.CancelledError):
            await run_daemon(self.paths, make_settings())
        assert seen == [v1]
        assert "Window run failed: Failed to parse YAML file bad.yaml: boom" in caplog.text
        assert spy.seconds[1] == 20 * 60

    async def test_first_load_failure_with_once_exits_one(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(
            scheduler, "load_config", LoaderSpy([ConfigurationError("Settings path not found: x.yaml")])
        )

        async def fake_run_window(
            config: AppConfig, settings: Settings, days: list[date], *, dry_run: bool
        ) -> RunSummary:
            pytest.fail("run_window must not be called when the first load fails")

        monkeypatch.setattr(scheduler, "run_window", fake_run_window)
        with caplog.at_level(logging.INFO, logger="bitrix_auto_manage"), pytest.raises(SystemExit) as info:
            await run_daemon(self.paths, make_settings(), once=True)
        assert info.value.code == 1
        assert "Window run failed: Settings path not found: x.yaml" in caplog.text

    async def test_first_load_failure_without_once_exits_one(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(
            scheduler, "load_config", LoaderSpy([ConfigurationError("Failed to read YAML file x.yaml: e")])
        )
        monkeypatch.setattr(scheduler, "run_window", _run_window_returning(RunSummary()))
        with caplog.at_level(logging.INFO, logger="bitrix_auto_manage"), pytest.raises(SystemExit) as info:
            await run_daemon(self.paths, make_settings())
        assert info.value.code == 1
        assert "No valid configuration loaded; exiting." in caplog.text

    async def test_once_exits_zero_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(scheduler, "load_config", LoaderSpy([make_config()]))
        monkeypatch.setattr(scheduler, "run_window", _run_window_returning(RunSummary(created=1)))
        with pytest.raises(SystemExit) as info:
            await run_daemon(self.paths, make_settings(), once=True)
        assert info.value.code == 0

    async def test_once_exits_one_when_summary_is_not_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(scheduler, "load_config", LoaderSpy([make_config()]))
        monkeypatch.setattr(scheduler, "run_window", _run_window_returning(RunSummary(failed=1)))
        with pytest.raises(SystemExit) as info:
            await run_daemon(self.paths, make_settings(), once=True)
        assert info.value.code == 1

    async def test_once_exits_one_on_manager_error(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(scheduler, "load_config", LoaderSpy([make_config()]))

        async def failing(config: AppConfig, settings: Settings, days: list[date], *, dry_run: bool) -> RunSummary:
            raise TimeManagerError("portal is unreachable")

        monkeypatch.setattr(scheduler, "run_window", failing)
        with pytest.raises(SystemExit) as info:
            await run_daemon(self.paths, make_settings(), once=True)
        assert info.value.code == 1
        assert "Window run failed: portal is unreachable" in caplog.text

    async def test_timezone_mismatch_is_logged_after_every_reload(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(scheduler, "load_config", LoaderSpy([make_config(), make_config()]))
        monkeypatch.setattr(scheduler, "run_window", _run_window_returning(RunSummary()))
        monkeypatch.setattr(scheduler, "datetime", FakeDateTime)
        spy = SleepSpy(cancel_after=2)
        monkeypatch.setattr(scheduler, "asyncio", spy)
        message = "YAML timezone (Europe/Moscow) differs from BITRIX_TIMEZONE (Asia/Yekaterinburg)"
        with caplog.at_level(logging.INFO, logger="bitrix_auto_manage"), pytest.raises(asyncio.CancelledError):
            await run_daemon(self.paths, make_settings(timezone="Asia/Yekaterinburg"))
        assert caplog.text.count(message) == 2

    async def test_loop_sleeps_until_next_apply_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(scheduler, "load_config", LoaderSpy([make_config()]))
        monkeypatch.setattr(scheduler, "run_window", _run_window_returning(RunSummary(created=1)))
        monkeypatch.setattr(scheduler, "datetime", FakeDateTime)
        spy = SleepSpy()
        monkeypatch.setattr(scheduler, "asyncio", spy)
        with pytest.raises(asyncio.CancelledError):
            await run_daemon(self.paths, make_settings())
        assert spy.seconds == [6 * 3600 + 5 * 60]

    async def test_loop_retries_after_configured_minutes(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(scheduler, "load_config", LoaderSpy([make_config(retry_minutes=20)]))

        async def failing(config: AppConfig, settings: Settings, days: list[date], *, dry_run: bool) -> RunSummary:
            raise TimeManagerError("temporary failure")

        monkeypatch.setattr(scheduler, "run_window", failing)
        spy = SleepSpy()
        monkeypatch.setattr(scheduler, "asyncio", spy)
        with caplog.at_level(logging.INFO, logger="bitrix_auto_manage"), pytest.raises(asyncio.CancelledError):
            await run_daemon(self.paths, make_settings())
        assert spy.seconds == [20 * 60]
        assert "Retrying in 20 minutes." in caplog.text

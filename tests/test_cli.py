"""CLI argument parsing and command flow checks (no network, all IO faked)."""

import argparse
import json
import logging
import runpy
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from bitrix_time_manager.config import Settings
from bitrix_time_manager.errors import TimeManagerError

from bitrix_auto_manage import __version__, cli
from bitrix_auto_manage.cli import _load_settings, _parse_date, _window, build_parser, main
from bitrix_auto_manage.config import AppConfig, date_range
from bitrix_auto_manage.runner import RunSummary

WEBHOOK = "https://example.com/rest/1/testsecret/"
CONFIG_YAML = """
timezone: Europe/Moscow
schedule:
  apply_time: "18:05"
tasks:
  - kind: remote
    days: [mon-fri]
    start: "09:00"
    end: "18:00"
"""


def write_config(tmp_path: Path) -> Path:
    path = tmp_path / "schedule.yaml"
    path.write_text(CONFIG_YAML, encoding="utf-8")
    return path


def test_version_flag_reports_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(["--version"])
    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


class TestParseDate:
    def test_accepts_iso_format(self) -> None:
        assert _parse_date("2026-10-21") == date(2026, 10, 21)

    def test_rejects_national_format(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="Invalid date '21.10.2026'"):
            _parse_date("21.10.2026")


class TestWindow:
    @pytest.fixture
    def config(self) -> AppConfig:
        return AppConfig.model_validate({"schedule": {"horizon_days": 2, "backfill_days": 1}})

    @pytest.fixture
    def fixed_today(self, monkeypatch: pytest.MonkeyPatch) -> date:
        today = date(2026, 10, 14)
        monkeypatch.setattr(cli, "today_in", lambda config: today)
        return today

    @staticmethod
    def make_args(date_value: date | None = None, days: int | None = None) -> argparse.Namespace:
        return argparse.Namespace(date=date_value, days=days)

    def test_explicit_date_without_days_is_one_day(self, config: AppConfig, fixed_today: date) -> None:
        assert _window(config, self.make_args(date(2026, 10, 12))) == [date(2026, 10, 12)]

    def test_explicit_date_with_days(self, config: AppConfig, fixed_today: date) -> None:
        days = _window(config, self.make_args(date(2026, 10, 12), days=3))
        assert days == [date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 14)]

    def test_days_below_one_exits(self, config: AppConfig, fixed_today: date) -> None:
        with pytest.raises(SystemExit) as info:
            _window(config, self.make_args(date(2026, 10, 12), days=0))
        assert info.value.code == "--days must be at least 1."

    def test_days_only_starts_from_today(self, config: AppConfig, fixed_today: date) -> None:
        assert _window(config, self.make_args(days=2)) == [fixed_today, fixed_today + timedelta(days=1)]

    def test_defaults_to_configured_range(self, config: AppConfig, fixed_today: date) -> None:
        assert _window(config, self.make_args()) == date_range(config, fixed_today)


class TestLoadSettings:
    def test_env_file_is_loaded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BITRIX_WEBHOOK_URL", raising=False)
        env_file = tmp_path / "test.env"
        env_file.write_text(f"BITRIX_WEBHOOK_URL={WEBHOOK}\n", encoding="utf-8")
        settings = _load_settings(SimpleNamespace(env_file=env_file))
        assert settings.webhook_url.get_secret_value() == WEBHOOK

    def test_missing_env_file_falls_back_to_environment(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BITRIX_WEBHOOK_URL", WEBHOOK)
        settings = _load_settings(SimpleNamespace(env_file=tmp_path / "missing.env"))
        assert settings.webhook_url.get_secret_value() == WEBHOOK


class TestMainCommands:
    @pytest.fixture
    def config_path(self, tmp_path: Path) -> Path:
        return write_config(tmp_path)

    @pytest.fixture
    def missing_env(self, tmp_path: Path) -> Path:
        return tmp_path / "missing.env"

    @pytest.fixture
    def env_webhook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BITRIX_WEBHOOK_URL", WEBHOOK)

    @staticmethod
    def argv(config_path: Path, missing_env: Path, command: str, *extra: str) -> list[str]:
        return [command, f"--config={config_path}", f"--env-file={missing_env}", *extra]

    @staticmethod
    def fake_show_day(seen: dict[str, Any]) -> Any:
        async def show(settings: Settings, day: date) -> dict[str, Any]:
            seen["day"] = day
            return {"day": day.isoformat(), "workday": None, "remote": [], "time_entries": []}

        return show

    def test_check_prints_connection_report(
        self, monkeypatch: pytest.MonkeyPatch, config_path: Path, missing_env: Path, env_webhook: None, capsys: Any
    ) -> None:
        class FakeCheckClient:
            def __init__(self, settings: Settings) -> None:
                self.settings = settings

            async def __aenter__(self) -> "FakeCheckClient":
                return self

            async def __aexit__(self, *exc_info: object) -> None:
                return None

        class FakeCheckManager:
            def __init__(self, client: FakeCheckClient) -> None:
                self.client = client

            async def check_connection(self) -> dict[str, Any]:
                return {"ok": True, "portal": "https://example.com"}

        monkeypatch.setattr(cli, "BitrixClient", FakeCheckClient)
        monkeypatch.setattr(cli, "TimeManager", FakeCheckManager)
        with pytest.raises(SystemExit) as info:
            main(self.argv(config_path, missing_env, "check"))
        assert info.value.code == 0
        assert json.loads(capsys.readouterr().out) == {"ok": True, "portal": "https://example.com"}

    def test_show_prints_json_report_for_date(
        self, monkeypatch: pytest.MonkeyPatch, config_path: Path, missing_env: Path, env_webhook: None, capsys: Any
    ) -> None:
        seen: dict[str, Any] = {}
        monkeypatch.setattr(cli, "show_day", self.fake_show_day(seen))
        with pytest.raises(SystemExit) as info:
            main(self.argv(config_path, missing_env, "show", "--date", "2026-10-12"))
        assert info.value.code == 0
        assert seen["day"] == date(2026, 10, 12)
        assert json.loads(capsys.readouterr().out)["day"] == "2026-10-12"

    def test_show_defaults_to_today(
        self, monkeypatch: pytest.MonkeyPatch, config_path: Path, missing_env: Path, env_webhook: None
    ) -> None:
        seen: dict[str, Any] = {}
        monkeypatch.setattr(cli, "show_day", self.fake_show_day(seen))
        with pytest.raises(SystemExit) as info:
            main(self.argv(config_path, missing_env, "show"))
        assert info.value.code == 0
        assert seen["day"] == date.today()

    def test_daemon_once_delegates_to_scheduler(
        self, monkeypatch: pytest.MonkeyPatch, config_path: Path, missing_env: Path, env_webhook: None
    ) -> None:
        seen: dict[str, Any] = {}

        async def fake_run_daemon(config: AppConfig, settings: Settings, *, once: bool) -> None:
            seen.update(config=config, settings=settings, once=once)

        monkeypatch.setattr(cli, "run_daemon", fake_run_daemon)
        with pytest.raises(SystemExit) as info:
            main(self.argv(config_path, missing_env, "daemon", "--once"))
        assert info.value.code == 0
        assert seen["once"] is True
        assert isinstance(seen["config"], AppConfig)
        assert seen["settings"].webhook_url.get_secret_value() == WEBHOOK

    def test_preview_passes_dry_run_window(
        self,
        monkeypatch: pytest.MonkeyPatch,
        config_path: Path,
        missing_env: Path,
        env_webhook: None,
        capsys: Any,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        seen: dict[str, Any] = {}

        async def fake_run_window(
            config: AppConfig, settings: Settings, days: list[date], *, dry_run: bool
        ) -> RunSummary:
            seen.update(days=days, dry_run=dry_run)
            return RunSummary(created=2)

        monkeypatch.setattr(cli, "run_window", fake_run_window)
        with caplog.at_level(logging.INFO, logger="bitrix_auto_manage"), pytest.raises(SystemExit) as info:
            main(self.argv(config_path, missing_env, "preview", "--date", "2026-10-12", "--days", "3"))
        assert info.value.code == 0
        assert seen["dry_run"] is True
        assert seen["days"] == [date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 14)]
        assert "created=2" in capsys.readouterr().out
        assert "Preview run, no writes." in caplog.text

    def test_apply_disables_dry_run(
        self, monkeypatch: pytest.MonkeyPatch, config_path: Path, missing_env: Path, env_webhook: None, capsys: Any
    ) -> None:
        seen: dict[str, Any] = {}

        async def fake_run_window(
            config: AppConfig, settings: Settings, days: list[date], *, dry_run: bool
        ) -> RunSummary:
            seen["dry_run"] = dry_run
            return RunSummary(created=1)

        monkeypatch.setattr(cli, "run_window", fake_run_window)
        with pytest.raises(SystemExit) as info:
            main(self.argv(config_path, missing_env, "apply", "--date", "2026-10-12"))
        assert info.value.code == 0
        assert seen["dry_run"] is False
        assert "created=1" in capsys.readouterr().out

    def test_config_defaults_to_local_config_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env_webhook: None, capsys: Any
    ) -> None:
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "schedule.yaml").write_text(CONFIG_YAML, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        seen: dict[str, Any] = {}
        monkeypatch.setattr(cli, "show_day", self.fake_show_day(seen))
        with pytest.raises(SystemExit) as info:
            main(["show"])
        assert info.value.code == 0
        assert json.loads(capsys.readouterr().out)["day"] == date.today().isoformat()

    def test_manager_error_exits_one(
        self, monkeypatch: pytest.MonkeyPatch, config_path: Path, missing_env: Path, env_webhook: None, caplog: Any
    ) -> None:
        async def fake_run_window(
            config: AppConfig, settings: Settings, days: list[date], *, dry_run: bool
        ) -> RunSummary:
            raise TimeManagerError("portal is unreachable")

        monkeypatch.setattr(cli, "run_window", fake_run_window)
        with pytest.raises(SystemExit) as info:
            main(self.argv(config_path, missing_env, "preview", "--date", "2026-10-12"))
        assert info.value.code == 1
        assert "portal is unreachable" in caplog.text

    def test_keyboard_interrupt_exits_130(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class AsyncioShim:
            def run(self, coro: Any) -> int:
                coro.close()
                raise KeyboardInterrupt

        monkeypatch.setattr(cli, "asyncio", AsyncioShim())
        with pytest.raises(SystemExit) as info:
            main(["show"])
        assert info.value.code == 130


class TestModuleRun:
    def test_package_module_runs_cli(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["bitrix-auto", "--version"])
        with pytest.raises(SystemExit) as info:
            runpy.run_module("bitrix_auto_manage", run_name="__main__")
        assert info.value.code == 0
        assert __version__ in capsys.readouterr().out

    @pytest.mark.filterwarnings("ignore:.*found in sys.modules.*:RuntimeWarning")
    def test_cli_module_main_guard(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["bitrix-auto", "--version"])
        with pytest.raises(SystemExit) as info:
            runpy.run_module("bitrix_auto_manage.cli", run_name="__main__")
        assert info.value.code == 0
        assert __version__ in capsys.readouterr().out

"""Tests for run_window and show_day: all SDK IO is replaced with fakes."""

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest
from bitrix_time_manager.config import Settings
from bitrix_time_manager.errors import NotFoundError, TimeManagerError, UncertainWriteError
from bitrix_time_manager.models import (
    OperationResult,
    RemoteWorkCreate,
    TimeEntryCreate,
    WorkdayCreate,
)
from pydantic import SecretStr

from bitrix_auto_manage import runner
from bitrix_auto_manage.config import AppConfig
from bitrix_auto_manage.runner import RunSummary, run_window, show_day

WEBHOOK = "https://example.com/rest/1/testsecret/"
MONDAY = date(2026, 10, 12)
FRIDAY = date(2026, 10, 16)
RESULT_KIND = {"workday": "workday", "remote": "remote_work", "time_entry": "time_entry"}


def make_settings() -> Settings:
    return Settings(webhook_url=SecretStr(WEBHOOK))


def make_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "timezone": "Europe/Moscow",
            "tasks": [
                {"kind": "workday", "days": ["mon-fri"], "start": "09:00", "end": "18:00"},
                {"kind": "remote", "days": ["fri"], "start": "10:00", "end": "18:00"},
                {
                    "kind": "time_entry",
                    "days": ["mon-fri"],
                    "contract": {"entity_type": 1298, "id": 2},
                    "description": "Работа",
                    "hours": 7,
                    "minutes": 30,
                },
            ],
        }
    )


def make_result(kind: str, status: str, *, warnings: list[str] | None = None) -> OperationResult:
    return OperationResult(
        status=status,
        kind=RESULT_KIND[kind],
        operation_id=f"op-{kind}-{status}",
        entity_type_id=1048,
        item_id=42,
        item_url="https://example.com/item/42",
        warnings=warnings or [],
    )


@dataclass
class Call:
    kind: str
    spec: Any
    dry_run: bool
    idempotency_key: str


class FakeClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.closed = False

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.closed = True


class Recorder:
    """Collects manager calls; `handle` decides the outcome of each create operation."""

    def __init__(
        self,
        handle: Any = None,
        *,
        entries: list[dict[str, Any]] | None = None,
        entries_error: TimeManagerError | None = None,
        workday: Any = None,
        remote: list[Any] | None = None,
    ) -> None:
        self.handle = handle or (lambda kind, spec: make_result(kind, "created"))
        self.entries = entries if entries is not None else []
        self.entries_error = entries_error
        self.workday = workday if workday is not None else {"item_id": 42}
        self.remote = remote if remote is not None else []
        self.calls: list[Call] = []
        self.clients: list[FakeClient] = []
        self.listed_days: list[date] = []

    async def create(self, kind: str, spec: Any, *, dry_run: bool, idempotency_key: str) -> OperationResult:
        self.calls.append(Call(kind, spec, dry_run, idempotency_key))
        return self.handle(kind, spec)

    async def list_time_entries(self, day: date) -> list[dict[str, Any]]:
        self.listed_days.append(day)
        if self.entries_error is not None:
            raise self.entries_error
        return self.entries


def install_fake(monkeypatch: pytest.MonkeyPatch, recorder: Recorder) -> Recorder:
    def client_factory(settings: Settings) -> FakeClient:
        client = FakeClient(settings)
        recorder.clients.append(client)
        return client

    class FakeManager:
        def __init__(self, client: FakeClient) -> None:
            self.client = client

        async def create_workday(self, spec: Any, *, dry_run: bool, idempotency_key: str) -> OperationResult:
            return await recorder.create("workday", spec, dry_run=dry_run, idempotency_key=idempotency_key)

        async def create_remote_work(self, spec: Any, *, dry_run: bool, idempotency_key: str) -> OperationResult:
            return await recorder.create("remote", spec, dry_run=dry_run, idempotency_key=idempotency_key)

        async def create_time_entry(self, spec: Any, *, dry_run: bool, idempotency_key: str) -> OperationResult:
            return await recorder.create("time_entry", spec, dry_run=dry_run, idempotency_key=idempotency_key)

        async def list_time_entries(self, day: date) -> list[dict[str, Any]]:
            return await recorder.list_time_entries(day)

        async def get_workday(self, day: date) -> Any:
            return recorder.workday

        async def list_remote_work(self, day: date) -> list[Any]:
            return recorder.remote

    monkeypatch.setattr(runner, "BitrixClient", client_factory)
    monkeypatch.setattr(runner, "TimeManager", FakeManager)
    return recorder


class TestRunSummary:
    def test_ok_requires_no_failed_and_no_uncertain(self) -> None:
        assert RunSummary().ok is True
        assert RunSummary(failed=1).ok is False
        assert RunSummary(uncertain=1).ok is False

    def test_describe_lists_all_counters(self) -> None:
        summary = RunSummary(created=1, existing=2, previewed=3, skipped=4, uncertain=5, failed=6)
        assert summary.describe() == "created=1 existing=2 previewed=3 skipped=4 uncertain=5 failed=6"


class TestRunWindow:
    async def test_apply_enables_writes_on_client_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = install_fake(monkeypatch, Recorder())
        summary = await run_window(make_config(), make_settings(), [MONDAY], dry_run=False)
        assert summary.ok
        assert recorder.clients[0].settings.allow_writes is True

    async def test_dry_run_keeps_writes_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = install_fake(monkeypatch, Recorder(lambda kind, spec: make_result(kind, "preview")))
        summary = await run_window(make_config(), make_settings(), [MONDAY], dry_run=True)
        assert recorder.clients[0].settings.allow_writes is False
        assert summary.previewed == 2
        assert all(call.dry_run for call in recorder.calls)

    async def test_statuses_are_counted_per_kind(self, monkeypatch: pytest.MonkeyPatch) -> None:
        statuses = {"workday": "created", "remote": "existing", "time_entry": "preview"}
        install_fake(monkeypatch, Recorder(lambda kind, spec: make_result(kind, statuses[kind])))
        summary = await run_window(make_config(), make_settings(), [FRIDAY], dry_run=True)
        assert summary.describe() == "created=1 existing=1 previewed=1 skipped=0 uncertain=0 failed=0"

    async def test_recovered_status_counts_as_previewed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_fake(monkeypatch, Recorder(lambda kind, spec: make_result(kind, "recovered")))
        summary = await run_window(make_config(), make_settings(), [FRIDAY], dry_run=True)
        assert summary.previewed == 3

    async def test_warnings_and_item_details_are_logged(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def handle(kind: str, spec: Any) -> OperationResult:
            return make_result(kind, "created", warnings=["check the portal"])

        install_fake(monkeypatch, Recorder(handle))
        with caplog.at_level(logging.INFO, logger="bitrix_auto_manage"):
            await run_window(make_config(), make_settings(), [MONDAY], dry_run=True)
        assert "check the portal" in caplog.text
        assert "item_id=42" in caplog.text
        assert "https://example.com/item/42" in caplog.text

    async def test_uncertain_write_continues_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handle(kind: str, spec: Any) -> OperationResult:
            if kind == "workday":
                raise UncertainWriteError("op-uncertain")
            return make_result(kind, "created")

        install_fake(monkeypatch, Recorder(handle))
        summary = await run_window(make_config(), make_settings(), [MONDAY], dry_run=False)
        assert summary.uncertain == 1
        assert summary.created == 1
        assert summary.errors == ["2026-10-12 WorkdayCreate: uncertain"]

    async def test_not_found_is_skipped_without_error_entry(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def handle(kind: str, spec: Any) -> OperationResult:
            if kind == "workday":
                raise NotFoundError("Workday card not found")
            return make_result(kind, "created")

        install_fake(monkeypatch, Recorder(handle))
        with caplog.at_level(logging.INFO, logger="bitrix_auto_manage"):
            summary = await run_window(make_config(), make_settings(), [MONDAY], dry_run=False)
        assert summary.skipped == 1
        assert summary.created == 1
        assert summary.errors == []
        assert "Workday card not found" in caplog.text

    async def test_other_manager_error_is_skipped_and_recorded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handle(kind: str, spec: Any) -> OperationResult:
            if kind == "workday":
                raise TimeManagerError("portal rejected the request")
            return make_result(kind, "created")

        install_fake(monkeypatch, Recorder(handle))
        summary = await run_window(make_config(), make_settings(), [MONDAY], dry_run=False)
        assert summary.skipped == 1
        assert summary.created == 1
        assert summary.errors == ["2026-10-12 WorkdayCreate: portal rejected the request"]

    async def test_skip_policy_with_existing_entries_skips_create(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        recorder = install_fake(monkeypatch, Recorder(entries=[{"ID": 1}]))
        with caplog.at_level(logging.INFO, logger="bitrix_auto_manage"):
            summary = await run_window(make_config(), make_settings(), [MONDAY], dry_run=False)
        assert [call.kind for call in recorder.calls] == ["workday"]
        assert recorder.listed_days == [MONDAY]
        assert summary.created == 1
        assert summary.skipped == 1
        assert "already has 1 time entries" in caplog.text

    async def test_skip_policy_without_existing_entries_creates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = install_fake(monkeypatch, Recorder(entries=[]))
        summary = await run_window(make_config(), make_settings(), [MONDAY], dry_run=False)
        assert [call.kind for call in recorder.calls] == ["workday", "time_entry"]
        assert summary.created == 2

    async def test_dispatch_passes_spec_dry_run_and_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = install_fake(monkeypatch, Recorder())
        await run_window(make_config(), make_settings(), [FRIDAY], dry_run=True)
        assert [call.kind for call in recorder.calls] == ["workday", "remote", "time_entry"]
        assert [type(call.spec) for call in recorder.calls] == [WorkdayCreate, RemoteWorkCreate, TimeEntryCreate]
        assert all(call.spec.day == FRIDAY for call in recorder.calls)
        assert all(call.dry_run for call in recorder.calls)
        assert all(call.idempotency_key.startswith("auto:2026-10-16:") for call in recorder.calls)


class TestShowDay:
    async def test_returns_day_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_fake(monkeypatch, Recorder(workday={"item_id": 7}, remote=[{"ID": 9}]))
        report = await show_day(make_settings(), FRIDAY)
        assert report == {
            "day": "2026-10-16",
            "workday": {"item_id": 7},
            "remote": [{"ID": 9}],
            "time_entries": [],
        }

    async def test_entries_error_is_reported_as_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        install_fake(monkeypatch, Recorder(entries_error=TimeManagerError("entries unavailable")))
        report = await show_day(make_settings(), FRIDAY)
        assert report["time_entries"] == "entries unavailable"

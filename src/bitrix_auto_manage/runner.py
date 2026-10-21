"""Apply planned operations through the SDK with per-operation error handling."""

import asyncio
import logging
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, TypeAlias

from bitrix_time_manager.client import BitrixClient
from bitrix_time_manager.config import Settings
from bitrix_time_manager.errors import NotFoundError, TimeManagerError, UncertainWriteError
from bitrix_time_manager.models import OperationResult, RemoteWorkCreate, TimeEntryCreate
from bitrix_time_manager.service import TimeManager

from bitrix_auto_manage.config import AppConfig, TaskSection, idempotency_key, plan_for_date

logger = logging.getLogger("bitrix_auto_manage")

Spec: TypeAlias = RemoteWorkCreate | TimeEntryCreate


@dataclass
class RunSummary:
    """Counts and outcomes of one applied date window."""

    created: int = 0
    existing: int = 0
    previewed: int = 0
    skipped: int = 0
    uncertain: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0 and self.uncertain == 0

    def describe(self) -> str:
        return (
            f"created={self.created} existing={self.existing} previewed={self.previewed} "
            f"skipped={self.skipped} uncertain={self.uncertain} failed={self.failed}"
        )


async def run_window(config: AppConfig, settings: Settings, days: list[date], *, dry_run: bool) -> RunSummary:
    """Apply scheduled specs for every date; one failed operation never stops the rest."""
    if not dry_run:
        settings = replace(settings, allow_writes=True)
    summary = RunSummary()
    async with BitrixClient(settings) as client:
        manager = TimeManager(client)
        for day in days:
            for task, spec in plan_for_date(config, day):
                label = f"{day.isoformat()} {type(spec).__name__}"
                try:
                    result = await _apply_spec(manager, task, spec, dry_run=dry_run)
                except UncertainWriteError as error:
                    logger.error(
                        "%s: %s Automatic retry is blocked by the SDK; the window continues. "
                        "Check the portal and run `bitrix-time resolve OPERATION_ID --confirmed-not-created`.",
                        label,
                        error,
                    )
                    summary.uncertain += 1
                    summary.errors.append(f"{label}: uncertain")
                    continue
                except NotFoundError as error:
                    logger.info("Skipped: %s", error)
                    summary.skipped += 1
                    continue
                except TimeManagerError as error:
                    logger.warning("%s: %s", label, error)
                    summary.skipped += 1
                    summary.errors.append(f"{label}: {error}")
                    continue
                _collect(summary, day, spec, result)
    return summary


async def _apply_spec(manager: TimeManager, task: TaskSection, spec: Spec, *, dry_run: bool) -> OperationResult | None:
    key = idempotency_key(spec)
    if isinstance(spec, TimeEntryCreate) and task.on_existing_entries == "skip":
        existing = await manager.list_time_entries(spec.day)
        if existing:
            logger.info(
                "%s: day already has %d time entries; skipping (on_existing_entries: skip).",
                spec.day.isoformat(),
                len(existing),
            )
            return None
    if isinstance(spec, RemoteWorkCreate):
        return await manager.create_remote_work(spec, dry_run=dry_run, idempotency_key=key)
    return await manager.create_time_entry(spec, dry_run=dry_run, idempotency_key=key)


def _collect(summary: RunSummary, day: date, spec: Spec, result: OperationResult | None) -> None:
    label = f"{day.isoformat()} {type(spec).__name__}"
    if result is None:
        summary.skipped += 1
        return
    if result.status == "created":
        summary.created += 1
    elif result.status == "existing":
        summary.existing += 1
    else:
        summary.previewed += 1
    for warning in result.warnings:
        logger.warning("%s: %s", label, warning)
    logger.info("%s: %s (item_id=%s, url=%s)", label, result.status, result.item_id, result.item_url)


async def show_day(settings: Settings, day: date) -> dict[str, Any]:
    """Read workday, remote intervals and time entries for one date."""
    async with BitrixClient(settings) as client:
        manager = TimeManager(client)
        workday, remote, entries = await asyncio.gather(
            manager.get_workday(day),
            manager.list_remote_work(day),
            _safe_entries(manager, day),
        )
        return {"day": day.isoformat(), "workday": workday, "remote": remote, "time_entries": entries}


async def _safe_entries(manager: TimeManager, day: date) -> list[dict[str, Any]] | str:
    try:
        return await manager.list_time_entries(day)
    except TimeManagerError as error:
        return str(error)

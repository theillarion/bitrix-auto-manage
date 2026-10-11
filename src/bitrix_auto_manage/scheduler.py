"""Daemon loop: run the date window at a fixed local time every day."""

import asyncio
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from bitrix_time_manager.config import Settings
from bitrix_time_manager.errors import TimeManagerError

from bitrix_auto_manage.config import AppConfig, date_range
from bitrix_auto_manage.runner import RunSummary, run_window

logger = logging.getLogger("bitrix_auto_manage")

STARTUP_GRACE = timedelta(seconds=30)


def _today(config: AppConfig) -> date:
    return datetime.now(ZoneInfo(config.timezone)).date()


def _next_run(config: AppConfig, now: datetime) -> datetime:
    candidate = now.replace(
        hour=config.schedule.apply_time.hour,
        minute=config.schedule.apply_time.minute,
        second=0,
        microsecond=0,
    )
    if candidate - now <= STARTUP_GRACE:
        candidate += timedelta(days=1)
    return candidate


async def _run_once(config: AppConfig, settings: Settings, *, dry_run: bool) -> RunSummary:
    days = date_range(config, _today(config))
    logger.info("Прогон окна %s..%s (dry_run=%s)", days[0], days[-1], dry_run)
    summary = await run_window(config, settings, days, dry_run=dry_run)
    logger.info("Итог: %s", summary.describe())
    return summary


async def run_daemon(config: AppConfig, settings: Settings, *, once: bool = False, dry_run: bool = False) -> None:
    """Run immediately, then repeat daily at schedule.apply_time local time."""
    if config.timezone != settings.timezone:
        logger.warning(
            "timezone в YAML (%s) не совпадает с BITRIX_TIMEZONE (%s); выровняйте настройки.",
            config.timezone,
            settings.timezone,
        )
    while True:
        try:
            summary = await _run_once(config, settings, dry_run=dry_run)
        except TimeManagerError as error:
            summary = RunSummary(failed=1)
            logger.error("Прогон не удался: %s", error)
        if once:
            raise SystemExit(0 if summary.ok else 1)
        zone = ZoneInfo(config.timezone)
        now = datetime.now(zone)
        if summary.ok:
            pause = _next_run(config, now) - now
        else:
            pause = timedelta(minutes=config.schedule.retry_minutes)
            logger.info("Повторная попытка через %d минут.", config.schedule.retry_minutes)
        logger.info("Следующий прогон через %s.", _format_pause(pause))
        await asyncio.sleep(pause.total_seconds())


def _format_pause(pause: timedelta) -> str:
    total = max(0, int(pause.total_seconds()))
    hours, minutes = divmod(total // 60, 60)
    return f"{hours:02d}:{minutes:02d}"

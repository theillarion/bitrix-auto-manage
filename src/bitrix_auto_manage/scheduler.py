"""Daemon loop: run the date window daily at a random time inside apply_time."""

import asyncio
import logging
import random
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bitrix_time_manager.config import Settings
from bitrix_time_manager.errors import TimeManagerError

from bitrix_auto_manage.config import AppConfig, date_range, load_config, today_in
from bitrix_auto_manage.runner import RunSummary, run_window

logger = logging.getLogger("bitrix_auto_manage")


def _next_run(config: AppConfig, now: datetime, rng: random.Random) -> datetime:
    """Pick a random minute inside the next not-yet-started apply_time window.

    The window repeats daily at wall-clock times, so the target day is today
    while `now` is still before the window start, and tomorrow otherwise.
    The draw always covers the full window [start, end] of that day, so the
    result is strictly in the future and the sleep stays under 48 hours.
    """
    window = config.schedule.apply_time
    start_minute = window.start.hour * 60 + window.start.minute
    end_minute = window.end.hour * 60 + window.end.minute
    day = now.date()
    if now >= datetime.combine(day, window.start, tzinfo=now.tzinfo):
        day += timedelta(days=1)
    minute = rng.randint(start_minute, end_minute)
    return datetime.combine(day, time(minute // 60, minute % 60), tzinfo=now.tzinfo)


async def _run_once(config: AppConfig, settings: Settings, *, dry_run: bool) -> RunSummary:
    days = date_range(config, today_in(config))
    logger.info("Running window %s..%s (dry_run=%s)", days[0], days[-1], dry_run)
    summary = await run_window(config, settings, days, dry_run=dry_run)
    logger.info("Summary: %s", summary.describe())
    return summary


async def run_daemon(paths: list[Path], settings: Settings, *, once: bool = False, dry_run: bool = False) -> None:
    """Run immediately, then repeat daily at a random time inside schedule.apply_time.

    The YAML config is reloaded from `paths` before every run, including the
    first one; a failed reload counts as a failed run (retry_minutes pause)
    and the previously loaded config is never applied again. After a
    successful run the next wake-up is drawn from the full apply_time window
    of the next not-yet-started day: today while the run finished before the
    window start, tomorrow otherwise.
    """
    rng = random.Random()
    config: AppConfig | None = None
    while True:
        try:
            loaded = load_config(paths)
            config = loaded
            if loaded.timezone != settings.timezone:
                logger.warning(
                    "YAML timezone (%s) differs from BITRIX_TIMEZONE (%s); align the settings.",
                    loaded.timezone,
                    settings.timezone,
                )
            summary = await _run_once(loaded, settings, dry_run=dry_run)
        except TimeManagerError as error:
            summary = RunSummary(failed=1)
            logger.error("Window run failed: %s", error)
        if once:
            raise SystemExit(0 if summary.ok else 1)
        if config is None:
            logger.error("No valid configuration loaded; exiting.")
            raise SystemExit(1)
        zone = ZoneInfo(config.timezone)
        now = datetime.now(zone)
        if summary.ok:
            next_run = _next_run(config, now, rng)
            pause = next_run - now
            window = config.schedule.apply_time
            if window.start == window.end:
                logger.info("Next run at %s (in %s).", next_run.strftime("%H:%M"), _format_pause(pause))
            else:
                logger.info(
                    "Next run at %s (in %s, window %s-%s).",
                    next_run.strftime("%H:%M"),
                    _format_pause(pause),
                    window.start.strftime("%H:%M"),
                    window.end.strftime("%H:%M"),
                )
        else:
            pause = timedelta(minutes=config.schedule.retry_minutes)
            logger.info("Retrying in %d minutes.", config.schedule.retry_minutes)
        await asyncio.sleep(pause.total_seconds())


def _format_pause(pause: timedelta) -> str:
    total = max(0, int(pause.total_seconds()))
    hours, minutes = divmod(total // 60, 60)
    return f"{hours:02d}:{minutes:02d}"

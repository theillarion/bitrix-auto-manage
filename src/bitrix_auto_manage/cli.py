"""Command line interface: check, preview, apply, show, daemon."""

import argparse
import asyncio
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from bitrix_time_manager.client import BitrixClient
from bitrix_time_manager.config import Settings
from bitrix_time_manager.errors import TimeManagerError
from bitrix_time_manager.service import TimeManager

from bitrix_auto_manage.config import AppConfig, date_range, load_config
from bitrix_auto_manage.runner import run_window, show_day
from bitrix_auto_manage.scheduler import run_daemon


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bitrix-auto",
        description="Автоматизация Time-менеджера Bitrix по YAML-настройкам.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        action="append",
        type=Path,
        default=[],
        help="YAML-файл или каталог настроек; можно повторять (по умолчанию ./config).",
    )
    common.add_argument("--env-file", type=Path, default=Path(".env"), help="Файл окружения (.env).")
    common.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Подробный журнал отладки.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser(
        "check",
        parents=[common],
        help="Проверить соединение, вебхук и схему портала без записей.",
    )

    preview = commands.add_parser(
        "preview", parents=[common], help="Показать, что будет создано для окна дат, без записей."
    )
    _add_window_args(preview)

    apply_cmd = commands.add_parser(
        "apply", parents=[common], help="Реально создать записи для окна дат (идемпотентно)."
    )
    _add_window_args(apply_cmd)

    show = commands.add_parser("show", parents=[common], help="Показать рабочий день, удалёнку и учет времени за дату.")
    show.add_argument("--date", type=_parse_date, help="Дата YYYY-MM-DD (по умолчанию сегодня).")

    daemon = commands.add_parser(
        "daemon",
        parents=[common],
        help="Постоянный режим: прогон окна каждый день в schedule.apply_time.",
    )
    daemon.add_argument("--once", action="store_true", help="Один прогон окна и выход (для cron/планировщика ОС).")
    return parser


def _add_window_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--date", type=_parse_date, help="Начало окна YYYY-MM-DD (по умолчанию из расписания).")
    parser.add_argument("--days", type=int, help="Сколько дней от начала окна (по умолчанию из расписания).")


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Некорректная дата {value!r}; нужен формат YYYY-MM-DD.") from None


def _default_config_paths() -> list[Path]:
    return [Path("config")]


def _window(config: AppConfig, args: argparse.Namespace) -> list[date]:
    if getattr(args, "date", None) is not None:
        length = args.days if args.days is not None else 1
        if length < 1:
            raise SystemExit("--days должен быть не меньше 1.")
        return [args.date + timedelta(days=offset) for offset in range(length)]
    if args.days is not None:
        today = date.today()
        return [today + timedelta(days=offset) for offset in range(args.days)]
    return date_range(config, date.today())


def _load_settings(args: argparse.Namespace) -> Settings:
    return Settings.from_env(args.env_file if args.env_file.exists() else None)


async def _async_main(args: argparse.Namespace) -> int:
    paths = args.config or _default_config_paths()
    config = load_config(paths)
    settings = _load_settings(args)

    if args.command == "check":
        async with BitrixClient(settings) as client:
            manager = TimeManager(client)
            report = await manager.check_connection()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.command == "show":
        report = await show_day(settings, args.date or date.today())
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0

    if args.command == "daemon":
        await run_daemon(config, settings, once=args.once)
        return 0

    days = _window(config, args)
    dry_run = args.command == "preview"
    if dry_run:
        logging.getLogger("bitrix_auto_manage").info("Предварительный просмотр без записей.")
    summary = await run_window(config, settings, days, dry_run=dry_run)
    print(summary.describe())
    return 0


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    try:
        code = asyncio.run(_async_main(args))
    except TimeManagerError as error:
        logging.getLogger("bitrix_auto_manage").error("%s", error)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    raise SystemExit(code)


if __name__ == "__main__":
    main()

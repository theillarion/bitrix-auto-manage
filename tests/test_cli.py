"""CLI argument parsing checks."""

import pytest

from bitrix_auto_manage import __version__
from bitrix_auto_manage.cli import build_parser


def test_version_flag_reports_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(["--version"])
    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out

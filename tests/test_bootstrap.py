"""入口：`dida` 命令组装出来的东西。"""

import importlib
import tomllib
from pathlib import Path

from dida.bootstrap import build_app
from dida.sync.engine import SyncEngine
from dida.tui.app import DidaApp

ROOT = Path(__file__).resolve().parents[1]


def test_build_app_composes_the_shell_with_a_real_engine():
    app = build_app()

    assert isinstance(app, DidaApp)
    assert isinstance(app.engine, SyncEngine)


def test_console_script_points_at_an_importable_main():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    target = pyproject["project"]["scripts"]["dida"]
    module_name, _, attribute = target.partition(":")

    assert callable(getattr(importlib.import_module(module_name), attribute))

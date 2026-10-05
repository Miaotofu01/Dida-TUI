"""入口：`dida` 命令组装出来的东西。"""

import importlib
import tomllib
from pathlib import Path

from dida.bootstrap import build_app
from dida.config import Config
from dida.sync.engine import SyncEngine
from dida.testing import FakeTransport
from dida.tui.app import DidaApp

ROOT = Path(__file__).resolve().parents[1]


def test_build_app_composes_the_shell_with_a_real_engine(tmp_path):
    """组合根装出来的东西：一个真的 ``DidaApp`` + 一个真的 ``SyncEngine``。

    三样都注入（配置 / 传输 / 库路径）：不带参数的 ``build_app()`` 是**生产**那条路——
    它读 ``~/.config/dida-tui/config.toml``、在那里建 sqlite、还会去建真的 HTTP 客户端。
    测试不该碰这些东西（t21 把组合根接上真的之后，这一条才需要说清楚）。
    """
    app = build_app(
        config=Config(),
        transport=FakeTransport(),
        db_path=tmp_path / "cache.sqlite3",
    )

    assert isinstance(app, DidaApp)
    assert isinstance(app.engine, SyncEngine)


def test_console_script_points_at_an_importable_main():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    target = pyproject["project"]["scripts"]["dida"]
    module_name, _, attribute = target.partition(":")

    assert callable(getattr(importlib.import_module(module_name), attribute))

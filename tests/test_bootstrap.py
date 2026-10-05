"""入口：`dida` 命令组装出来的东西。"""

import importlib
import os
import subprocess
import sys
import tomllib
from pathlib import Path

from dida.bootstrap import PUSH_TICK_SECONDS, build_app
from dida.config import Config
from dida.sync.engine import SyncEngine
from dida.testing import FakeTransport
from dida.tui.app import DidaApp

ROOT = Path(__file__).resolve().parents[1]

KITTY_FLAG = "TEXTUAL_DISABLE_KITTY_KEY"
"""关掉 kitty 键盘协议推送的那个环境变量（ADR-0006、用户故事 115）。"""

READ_FLAG = (
    "import dida.bootstrap; from textual import constants; print(constants.DISABLE_KITTY_KEY)"
)


def kitty_flag_after_importing_the_entry_point(*, env_value: str | None) -> str:
    """**干净子进程**里导入入口之后，Textual 读到的那个值。

    必须是子进程：``DISABLE_KITTY_KEY`` 是 ``Final[bool]``，在 ``textual.constants`` 第一次
    被 import 时读一次就冻住了（``textual/constants.py:116``）。在同一个进程里改环境变量再读
    常量，测的是「冻住了没有」，而不是「启动时设上了没有」——后者才是这条要钉的东西。
    父进程的环境里那个变量一律摘掉，免得外面碰巧设过。

    ⚠ 这里断的是**常量**，不是「输入法打长句正常」。开着这个开关时喂一段 kitty CSI-u
    序列给解析器，解出来反而是乱码——那条断言看起来像验收，其实断的是反面
    （见 ``notes/kitty-flag-import-order.md`` §3）。真正的输入法上屏是手测项。
    """
    env = {key: value for key, value in os.environ.items() if key != KITTY_FLAG}
    if env_value is not None:
        env[KITTY_FLAG] = env_value
    done = subprocess.run(
        [sys.executable, "-c", READ_FLAG], env=env, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


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


def test_the_kitty_keyboard_push_is_off_by_the_time_the_entry_point_is_up():
    """``dida`` 起来时 kitty 键盘协议推送已经是关的（ADR-0006、用户故事 115）。

    开着它，输入法一次上屏超过四个汉字就会被逐字符重发成乱码（``terminal-input-evidence.md``）；
    代价是 ``ctrl+enter`` 收不到，而键位表本来就不依赖它。

    诚实的那条断言只有这一条：**导入入口之后常量是 ``True``**。厂商的守卫不是「Textual 解
    CSI-u 解得更好」，而是「终端从来没被要求发 CSI-u」——驱动那条写 ``\\x1b[>25u`` 的分支
    被这个常量挡掉了。
    """
    assert kitty_flag_after_importing_the_entry_point(env_value=None) == "True"


def test_an_explicit_kitty_setting_still_wins():
    """外面显式设过就听外面的（``setdefault``，不是硬写）。

    这也是「设在 ``main()`` 里太晚了」的另一面：这一段必须在 ``bootstrap`` 的 import 块
    **上面**——``bootstrap.py`` 里 ``from dida.tui.app import …`` 是整个进程第一处拉到
    ``textual`` 的 import，晚一行常量就已经冻成 ``False`` 了。
    """
    assert kitty_flag_after_importing_the_entry_point(env_value="0") == "False"


def test_the_pump_interval_is_the_composition_roots_policy(tmp_path):
    """周期泵的间隔由组合根交给 app（``PUSH_TICK_SECONDS`` 住在这里）。

    这条同时是那道防漏闸：这个常量原本住在 ``dida.tui.app`` 里，组合根从 TUI 拿它。
    它还留在那儿的话，界面重写一动那个文件 ``dida`` 就起不来——所以它现在与
    ``STORE_FILENAME`` 一起住在组合根，装配线看得见地在用它。
    """
    app = build_app(
        config=Config(), transport=FakeTransport(), db_path=tmp_path / "cache.sqlite3"
    )

    assert app._push_tick_seconds == PUSH_TICK_SECONDS, "组合根把周期泵的间隔交给了 app"
    assert PUSH_TICK_SECONDS > 0, "间隔得是个正数，否则泵根本不会被挂上"


def test_console_script_points_at_an_importable_main():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    target = pyproject["project"]["scripts"]["dida"]
    module_name, _, attribute = target.partition(":")

    assert callable(getattr(importlib.import_module(module_name), attribute))

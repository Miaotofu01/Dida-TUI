"""组合根：把七个模块拼成一个 app。

TUI 自己不 import 存储与 API 客户端，只拿 :class:`~dida.sync.engine.SyncEngine`；
接线发生在这一层。``dida`` 命令与 ``python -m dida`` 都走这里。
"""

from __future__ import annotations

from dida.clock import Clock, SystemClock
from dida.sync.engine import SyncEngine
from dida.tui.app import DidaApp


def build_engine(clock: Clock | None = None) -> SyncEngine:
    """组装同步引擎。t07（客户端）/t08（存储）/t09（读路径）落地后在此注入协作者。"""
    return SyncEngine(clock=clock if clock is not None else SystemClock())


def build_app(clock: Clock | None = None) -> DidaApp:
    """组装 app。"""
    return DidaApp(build_engine(clock))


def main() -> None:
    """``dida`` 命令的入口：单进程，按 ``q`` 退出。"""
    build_app().run()

"""测试接缝之一：时钟。

凡是要判断「现在」的地方（逻辑日、重试退避、状态栏时间）都从这里取，
绝不在业务代码里直接调用 ``datetime.now()``。测试注入 ``dida.testing.ManualClock``
就能控制「现在」。
"""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """「现在」的来源。"""

    def now(self) -> datetime:
        """返回当前时刻，带时区（本地时区）。"""
        ...


class SystemClock:
    """真实时钟：本地时区的 ``datetime.now()``。"""

    def now(self) -> datetime:
        return datetime.now().astimezone()

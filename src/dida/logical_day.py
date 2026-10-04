"""逻辑日（第 5 个深模块）：纯函数。t04 填充。

一次「还没睡」的周期所对应的日期；结束时刻可配置，默认 24:00。
「现在」从注入的 ``dida.clock.Clock`` 来，本模块自己不看表。

公开接口：``logical_day(now, day_end) -> date``——参数形状由 t04 定稿
（``day_end`` 允许 ``"24:00"`` 这种写法，怎么解析也归 t04）。
零依赖、无副作用、可直接单测。
"""

from __future__ import annotations

from datetime import date, datetime


def logical_day(now: datetime, day_end: str) -> date:
    """``now`` 落在哪个逻辑日。t04 实现。"""
    raise NotImplementedError("逻辑日由 t04 实现")

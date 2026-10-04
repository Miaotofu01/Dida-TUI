"""同步引擎（第 4 个深模块）。

TUI 读写一切只能走本模块；分组、排序、逾期判定、冲突裁决都发生在这里，不在 TUI 里。
公开接口（本工单只落地 ``status()``）：

- ``status() -> SyncStatus`` —— 读：状态栏所需的全部信息。
- ``view()`` —— 读：分组视图模型（类型由 t05 定义，数据由 t09 填充）。
- ``refresh()`` —— 写：全量刷新（逐清单拉取 → 本地 diff → 只写变化），t09。
- ``complete(task_id)`` —— 写：完成并立即推送（服务端不可逆），t11。
- ``defer(task_id)`` —— 写：顺延到下一个逻辑日，t13。

t08/t09/t10 落地后构造器会扩展为接收存储与 API 客户端；
「现在」永远取自注入的 ``Clock``，绝不在本模块里调用 ``datetime.now()``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from dida.clock import Clock


@dataclass(frozen=True)
class SyncStatus:
    """状态栏快照。"""

    checked_at: datetime
    """生成本状态时的时刻，来自注入的 ``Clock``。"""

    pending_count: int = 0
    """待推送改动数量（t10 填充）。"""

    last_refresh_at: datetime | None = None
    """上次全量刷新完成时刻（t09 填充）。"""

    logical_day: date | None = None
    """当前逻辑日（t04 提供纯函数，t09 接入）。"""


class SyncEngine:
    """今日执行台的数据与写入入口。"""

    def __init__(self, *, clock: Clock) -> None:
        self._clock = clock

    def status(self) -> SyncStatus:
        """读：状态栏要的全部信息。"""
        return SyncStatus(checked_at=self._clock.now())

    def refresh(self) -> None:
        """写：全量刷新（ADR 0001）。t09 实现。"""
        raise NotImplementedError("全量刷新由 t09 实现")

    def complete(self, task_id: str) -> None:
        """写：完成任务并立即推送（ADR 0002，服务端不可逆）。t11 实现。"""
        raise NotImplementedError("完成由 t11 实现")

    def defer(self, task_id: str) -> None:
        """写：顺延到下一个逻辑日。t13 实现。"""
        raise NotImplementedError("顺延由 t13 实现")

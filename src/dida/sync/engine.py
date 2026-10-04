"""同步引擎（第 4 个深模块）。

TUI 读写一切只能走本模块；分组、排序、逾期判定、冲突裁决都发生在这里，不在 TUI 里。
公开接口：

- ``status() -> SyncStatus`` —— 读：状态栏所需的全部信息。
- ``view() -> TodayView`` —— 读：分组视图模型（类型见 :mod:`dida.sync.view`）。
- ``refresh()`` —— 写：全量刷新（逐清单拉取 → 本地 diff → 只写变化），t09。
- ``complete(task_id)`` —— 写：完成并立即推送（服务端不可逆），t11。
- ``defer(task_id)`` —— 写：顺延到下一个逻辑日，t13。

t08/t09/t10 落地后把 ``source`` 换成 t08 的 ``Store``；在那之前读路径照样工作：
``source`` 是任何 :class:`~dida.sync.view.ViewSource`，没有就给一张空视图。

「现在」永远取自注入的 ``Clock``，「一天结束的时刻」是注入的 ``day_end``——
本模块里没有 ``datetime.now()``，也没有自己算的日界（交给 :mod:`dida.logical_day`）。

视图模型与分组纯函数都在 :mod:`dida.sync.view`，这里再导出一份，好让 TUI 只 import
``dida.sync.engine`` 一个东西（``tests/test_architecture.py`` 守着这条）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from dida.clock import Clock
from dida.logical_day import logical_day
from dida.sync.view import (
    NO_DUE_TEXT,
    GroupKind,
    ListSnapshot,
    ListSummary,
    SyncState,
    TaskGroup,
    TaskItem,
    TaskSnapshot,
    TodayView,
    ViewSource,
    format_due,
    group_tasks,
    priority_mark,
    summarize_lists,
)

__all__ = [
    "NO_DUE_TEXT",
    "Engine",
    "GroupKind",
    "ListSnapshot",
    "ListSummary",
    "SyncEngine",
    "SyncState",
    "SyncStatus",
    "TaskGroup",
    "TaskItem",
    "TaskSnapshot",
    "TodayView",
    "ViewSource",
    "format_due",
    "group_tasks",
    "priority_mark",
    "summarize_lists",
]

DEFAULT_DAY_END = "00:00"
"""配置注入之前的默认日界：零偏移，逻辑日等于自然日（规范形式见 ADR 0003）。"""


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


@runtime_checkable
class Engine(Protocol):
    """TUI 眼里的引擎：它只用得到这五个。t11/t13 的实现必须仍然满足它。"""

    def status(self) -> SyncStatus:
        """读：状态栏要的全部信息。"""
        ...

    def view(self) -> TodayView:
        """读：分组后的视图模型。"""
        ...

    def refresh(self) -> None:
        """写：全量刷新。"""
        ...

    def complete(self, task_id: str) -> None:
        """写：完成并立即推送。"""
        ...

    def defer(self, task_id: str) -> None:
        """写：顺延到下一个逻辑日。"""
        ...


class SyncEngine:
    """今日执行台的数据与写入入口。"""

    def __init__(self, *, clock: Clock, day_end: str = DEFAULT_DAY_END, source: ViewSource | None = None) -> None:
        self._clock = clock
        self._day_end = day_end
        self._source = source

    def status(self) -> SyncStatus:
        """读：状态栏要的全部信息。"""
        now = self._clock.now()
        state = self._source.sync_state() if self._source is not None else SyncState()
        return SyncStatus(
            checked_at=now,
            pending_count=state.pending_count,
            last_refresh_at=state.last_refresh_at,
            logical_day=logical_day(now, self._day_end).label,
        )

    def view(self) -> TodayView:
        """读：从本地缓存分组出的视图模型。缓存不在就是空视图，不是错误。"""
        if self._source is None:
            return TodayView(lists=(), groups=())
        lists = tuple(self._source.lists())
        tasks = tuple(self._source.tasks())
        return TodayView(
            lists=summarize_lists(lists, tasks),
            groups=group_tasks(tasks, lists, now=self._clock.now(), day_end=self._day_end),
        )

    def refresh(self) -> None:
        """写：全量刷新（ADR 0001）。t09 实现。"""
        raise NotImplementedError("全量刷新由 t09 实现")

    def complete(self, task_id: str) -> None:
        """写：完成任务并立即推送（ADR 0002，服务端不可逆）。t11 实现。"""
        raise NotImplementedError("完成由 t11 实现")

    def defer(self, task_id: str) -> None:
        """写：顺延到下一个逻辑日。t13 实现。"""
        raise NotImplementedError("顺延由 t13 实现")

"""同步引擎（第 4 个深模块）——公开面与读路径。

TUI 读写一切只能走本模块；分组、排序、逾期判定、冲突裁决都发生在这一层，不在 TUI 里。
公开接口：

- ``status() -> SyncStatus`` —— 读：状态栏所需的全部信息。
- ``view() -> TodayView`` —— 读：分组视图模型（类型见 :mod:`dida.sync.view`）。
- ``refresh() -> RefreshReport`` —— 写：全量刷新，**async**（:mod:`dida.sync.refresh`；清单索引
  翻页翻到底、远端已经没有的清单与任务顺手剪掉，#41）。
- ``write(task_id, changes=, kind=)`` —— 写：乐观写，本地当场生效、立即推送（:mod:`dida.sync.push`）。
- ``complete(task_id)`` / ``delete(task_id)`` —— 写：完成与删除的两个预置（:mod:`dida.sync.push`）。
- ``refresh_completed() -> CompletedReport`` —— 写：已完成流，**async**（:mod:`dida.sync.completed`）。
- ``defer(task_id)`` / ``reschedule(...)`` —— 写：顺延与改期（:mod:`dida.sync.schedule`）。
- ``create(title, ...)`` —— 写：新建，落在收集箱（:mod:`dida.sync.create`）。
- ``cycle_priority(task_id)`` —— 写：优先级推进一档（:mod:`dida.sync.priority`）。
- ``subtasks(task_id)`` / ``toggle_subtask(...)`` —— 读 / 写：子任务（:mod:`dida.sync.subtasks`）。

**这个文件只做两件事：组装，以及读路径。** ``SyncEngine`` 由几片职责各自独立的 mixin 拼成，
每片一个模块、一个变化原因（t32 拆的）：

============================  ==========================================
模块                            负责什么
============================  ==========================================
:mod:`dida.sync.writes`         写词汇（唯一的定义处）与写路径共享的协议 / 错误
:mod:`dida.sync.refresh`        全量刷新怎么取数、怎么落库（t09 / #41）
:mod:`dida.sync.push`           乐观入队、立即推送、退避重试（t10 / t11 / t16）
:mod:`dida.sync.completed`      已完成流的窗口与游标（t12）
:mod:`dida.sync.schedule`       顺延与改期：截止时间怎么挪（t13 / t14 / #40 / #44）
:mod:`dida.sync.create`         新建一条任务要发什么（t15 / #39）
:mod:`dida.sync.priority`       优先级推进一档（t17 / #45）
:mod:`dida.sync.subtasks`       子任务的先读后写（t20）
============================  ==========================================

各片的方法挂在同一个 ``SyncEngine`` 上（它们用 ``self._clock`` / ``self._source`` / 彼此的
公开入口），所以**单独一个 mixin 不完整**——一份完整的引擎就是这里的组装结果。

协作者都是注入的：``source`` 是本地副本（生产是 t08 的 ``Store``，只读的 ``ViewSource``
照样能跑读路径，只是刷新与写入会大声报错），``client`` 是 t07 的 ``DidaApiClient``
（刷新与推送都要它）。组合根 ``dida.bootstrap`` 负责接线，本模块不自己 new 任何东西。

「现在」永远取自注入的 ``Clock``，「一天结束的时刻」是注入的 ``day_end``——
这一层里没有 ``datetime.now()``，也没有自己算的日界（交给 :mod:`dida.logical_day`）。

视图模型与分组纯函数都在 :mod:`dida.sync.view`，这里再导出一份，好让 TUI 只 import
``dida.sync.engine`` 一个东西（``tests/test_architecture.py`` 守着这条）——**TUI 的允许表
只有这一个模块**，所以新增的公开类型都要在这里转出去。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Protocol, Sequence, runtime_checkable

from dida.api.errors import AuthError, DidaError
from dida.clock import Clock
from dida.logical_day import logical_day
from dida.sync.completed import (
    DEFAULT_COMPLETED_WINDOW_HOURS,
    CompletedReader,
    CompletedReport,
    CompletedStreamMixin,
)
from dida.sync.create import CreateMixin
from dida.sync.priority import PriorityMixin
from dida.sync.push import PushMixin, TaskWriter, backoff_delay
from dida.sync.read import (
    ListKind,
    ListRow,
    PayloadReader,
    TaskDetail,
    TaskList,
    ViewReader,
    ViewRow,
    builtin_view_rows,
    container_tasks,
    is_inbox_id,
    list_index,
    resolve_lists,
    task_detail,
)
from dida.sync.refresh import ProjectReader, RefreshMixin, RefreshTarget
from dida.sync.schedule import ScheduleMixin
from dida.sync.subtasks import SubtaskMixin, SubtaskWrite, TaskReader
from dida.sync.view import (
    INBOX_ID,
    NO_DUE_TEXT,
    SUBTASK_COMPLETED_STATUS,
    CompletedItem,
    CompletedSection,
    GroupKind,
    ListSnapshot,
    ListSummary,
    SubtaskItem,
    SyncState,
    TaskGroup,
    TaskItem,
    TaskSnapshot,
    TodayView,
    ViewSource,
    completed_section,
    filter_groups,
    format_due,
    fuzzy_match,
    group_tasks,
    list_names,
    next_priority,
    priority_mark,
    subtask_items,
    summarize_lists,
)
from dida.sync.writes import LocalEffect, UnknownTaskError, WireCall, WriteKind, WriteTarget

if TYPE_CHECKING:  # 只为了标注：storage 反过来 import dida.sync.view，运行时不能在这里 import
    from dida.storage.store import RefreshReport

__all__ = [
    "INBOX_ID",
    "NO_DUE_TEXT",
    "AuthError",
    "CompletedItem",
    "CompletedReader",
    "CompletedReport",
    "CompletedSection",
    "DidaError",
    "Engine",
    "GroupKind",
    "ListKind",
    "ListRow",
    "ListSnapshot",
    "ListSummary",
    "LocalEffect",
    "PayloadReader",
    "ProjectReader",
    "RefreshTarget",
    "SUBTASK_COMPLETED_STATUS",
    "SubtaskItem",
    "SubtaskWrite",
    "SyncEngine",
    "SyncState",
    "SyncStatus",
    "TaskDetail",
    "TaskGroup",
    "TaskItem",
    "TaskList",
    "TaskReader",
    "TaskSnapshot",
    "TodayView",
    "UnknownTaskError",
    "ViewReader",
    "ViewRow",
    "ViewSource",
    "WireCall",
    "WriteKind",
    "WriteTarget",
    "backoff_delay",
    "builtin_view_rows",
    "completed_section",
    "container_tasks",
    "filter_groups",
    "format_due",
    "fuzzy_match",
    "group_tasks",
    "is_inbox_id",
    "list_index",
    "next_priority",
    "priority_mark",
    "resolve_lists",
    "subtask_items",
    "summarize_lists",
    "task_detail",
]

DEFAULT_DAY_END = "00:00"
"""配置注入之前的默认日界：零偏移，逻辑日等于自然日（规范形式见 ADR 0003）。"""


@dataclass(frozen=True)
class SyncStatus:
    """状态栏快照。"""

    checked_at: datetime
    """生成本状态时的时刻，来自注入的 ``Clock``。"""

    pending_count: int = 0
    """待推送改动数量：常驻显示，非零就说明本地比服务端新（ADR-0002 的豁免代价）。"""

    last_refresh_at: datetime | None = None
    """上次全量刷新完成时刻（t09 填充）。"""

    logical_day: date | None = None
    """当前逻辑日（t04 提供纯函数，t09 接入）。"""


@runtime_checkable
class Engine(Protocol):
    """TUI 眼里的引擎：它只用得到这几个。t11/t13/t14 的实现必须仍然满足它。"""

    def status(self) -> SyncStatus:
        """读：状态栏要的全部信息。"""
        ...

    def view(self) -> TodayView:
        """读：分组后的视图模型。"""
        ...

    def list_index(self) -> tuple[ListRow, ...]:
        """读：清单索引（内置视图、自定义视图、真实清单三种行）。"""
        ...

    def tasks_in(self, container_id: str) -> TaskList:
        """读：某个容器的任务列表（一个清单，或一个视图）。"""
        ...

    def task_detail(self, task_id: str) -> TaskDetail | None:
        """读：单条任务的详情；本地没有这条任务时 ``None``。"""
        ...

    async def refresh(self) -> RefreshReport:
        """写：全量刷新。**要 await**：它不是一次纯本地操作。"""
        ...

    async def push_pending(self) -> int:
        """写：推一轮待推送改动（到期的才推），返回推成功的条数。

        手动同步（``r``）与周期泵（t21）都从这里过；等待与定时都不在引擎里，
        引擎只负责说清楚「现在哪些能推」。
        """
        ...

    async def refresh_completed(self) -> CompletedReport:
        """写：拉一次已完成流（按完成时间游标拉窗口）。**要 await**。"""
        ...

    def complete(self, task_id: str) -> None:
        """写：完成并立即推送。"""
        ...

    def defer(self, task_id: str, *, days: int = 1) -> None:
        """写：顺延 ``days`` 个逻辑日（``g`` 是 1 天、``G`` 是 7 天）。"""
        ...

    def delete(self, task_id: str) -> None:
        """写：删除一条任务（服务端没有撤销，t16）。"""
        ...

    def reschedule(self, task_id: str, *, due: datetime, all_day: bool) -> None:
        """写：改期（``e``）——把截止时间换成 ``due``，只动 ``dueDate`` 与 ``isAllDay``。"""
        ...

    def create(
        self,
        title: str,
        *,
        due: datetime | None = None,
        all_day: bool = False,
        priority: int | None = None,
        tags: Sequence[str] = (),
    ) -> str:
        """写：新建一条任务到收集箱（``a``），返回本地那条的 id（t15）。"""
    def cycle_priority(self, task_id: str) -> None:
        """写：优先级推进一档（``p``）——无 → 低 → 中 → 高 → 无。"""
        ...

    def subtasks(self, task_id: str) -> tuple[SubtaskItem, ...]:
        """读：这条任务的子任务行（右栏），标题与完成状态都已经是可以直接画的成品。"""
        ...

    async def toggle_subtask(self, task_id: str, subtask_id: str) -> SubtaskWrite:
        """写：勾选/取消勾选一个子任务——**写回之前先重读该任务**（工单 #20）。

        **要 await**：重读是一次网络调用，而这次写回必须建立在它带回来的底稿上。
        """
        ...


class SyncEngine(
    RefreshMixin,
    PushMixin,
    CompletedStreamMixin,
    ScheduleMixin,
    CreateMixin,
    PriorityMixin,
    SubtaskMixin,
):
    """今日执行台的数据与写入入口（组装各片；读路径在本模块）。"""

    def __init__(
        self,
        *,
        clock: Clock,
        day_end: str = DEFAULT_DAY_END,
        source: ViewSource | None = None,
        client: ProjectReader | TaskWriter | CompletedReader | None = None,
        completed_window_hours: int = DEFAULT_COMPLETED_WINDOW_HOURS,
        push_on_change: bool = True,
    ) -> None:
        self._clock = clock
        self._day_end = day_end
        self._source = source
        self._client = client
        self._completed_window_hours = completed_window_hours
        # 「界面上的改动立即推送」（配置键 push_on_change，spec 的配置 schema）。关掉它只是
        # 不排那一轮**立刻**的推送：改动照样入队、照样在本地生效，等下一次 push_pending
        # （手动同步 r，或 t21 的周期泵）再出去。默认开着，ADR-0002 要的就是立刻推。
        self._push_on_change = push_on_change
        # 推送串行化：一次写会顺手排一轮推送，别让同一批改动被两个协程同时推两遍
        # （完成与删除不是幂等的）。锁本身不绑事件循环，第一次 acquire 时才绑。
        self._push_lock = asyncio.Lock()
        # 已在飞的推送轮次；wait_for_pushes() 等它们。
        self._inflight: set[asyncio.Task[int]] = set()

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
        now = self._clock.now()
        return TodayView(
            lists=summarize_lists(lists, tasks),
            groups=group_tasks(tasks, lists, now=now, day_end=self._day_end),
            completed=completed_section(
                tasks,
                lists,
                now=now,
                day_end=self._day_end,
                window_hours=self._completed_window_hours,
            ),
        )

    def list_index(self) -> tuple[ListRow, ...]:
        """读：清单索引——收集箱置顶，然后内置视图、自定义视图、真实清单（#33）。

        缓存不在就是空索引，不是错误（与 :meth:`view` 空缓存给空视图同一条口径）。
        """
        if self._source is None:
            return ()
        return list_index(
            tuple(self._source.lists()),
            tuple(self._source.tasks()),
            now=self._clock.now(),
            day_end=self._day_end,
            views=self._view_rows(),
        )

    def tasks_in(self, container_id: str) -> TaskList:
        """读：某个容器的任务列表——它的**全部**未完成任务（未来的也在）+ 已完成的那部分。

        认不出来的容器给空列表（清单被删了、光标停在一条已经不在了的行上）。
        """
        if self._source is None:
            return TaskList(container_id=container_id)
        return container_tasks(
            container_id,
            tuple(self._source.lists()),
            tuple(self._source.tasks()),
            now=self._clock.now(),
            day_end=self._day_end,
            window_hours=self._completed_window_hours,
            views=self._view_rows(),
        )

    def task_detail(self, task_id: str) -> TaskDetail | None:
        """读：单条任务的详情（重复规则、提醒、子任务、原文里的未知字段都在这）。"""
        if self._source is None:
            return None
        tasks = tuple(self._source.tasks())
        snapshot = next((item for item in tasks if item.id == task_id), None)
        if snapshot is None:
            return None
        names = list_names(resolve_lists(tuple(self._source.lists()), tasks))
        return task_detail(
            snapshot,
            self._payload_of(task_id),
            names,
            now=self._clock.now(),
            day_end=self._day_end,
        )

    def _view_rows(self) -> tuple[ViewRow, ...]:
        """本地库里的自定义视图行（#36 把视图定义落库、求值）。

        源上没有这个能力就是「没有自定义视图」，不是错误——与写路径上那几个
        ``isinstance`` 门同一条口径。内置视图不走这里（:func:`builtin_view_rows` 自己算）。
        """
        source = self._source
        return tuple(source.views()) if isinstance(source, ViewReader) else ()

    def _write_target(self) -> WriteTarget:
        """写路径要写的那个本地副本。没接上就大声报错——绝不假装写成功了。"""
        if not isinstance(self._source, WriteTarget):
            raise RuntimeError(
                "写路径需要本地存储：SyncEngine(source=Store(...))；"
                "只读的 ViewSource 存不下待推送改动"
            )
        return self._source

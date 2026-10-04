"""同步引擎对外的视图模型与纯函数。

引擎的读路径只暴露这里定义的类型：TUI 拿到的是**已经判断好**的东西——
哪条任务属于哪个区、截止时间读作什么、优先级标记长什么样——它自己不做判断。

两组类型：

- 输入（缓存 → 引擎）：:class:`ListSnapshot` / :class:`TaskSnapshot` / :class:`SyncState`，
  :class:`ViewSource` 是它们的只读入口，t08 的 ``Store`` 实现它。
- 输出（引擎 → TUI）：:class:`TodayView` / :class:`TaskGroup` / :class:`TaskItem` /
  :class:`ListSummary`。

函数都是纯的：「现在」与 ``day_end`` 一律从参数进来，逻辑日判定交给
:mod:`dida.logical_day`，这里不重算任何日界。分区与排序的规矩：

- 逾期区置顶：有截止时间、且早于当前逻辑日的开始时刻。
- 今日区：截止时间落在当前逻辑日区间内 ``[start, end)``。
- 没有截止时间的任务留在今日区（读作「—」），等它们的分诊区落地后再分出去。
- 未来（下一个逻辑日起）的任务不属于这张「今日」视图；已完成的也不属于。
- 区内先按截止时间升序，没有截止时间的排在同区有截止时间的后面（按标题）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Protocol, Sequence

from dida.logical_day import logical_day

NO_DUE_TEXT = "—"
"""没有截止时间的读法；与「今天」一眼可分。"""

INBOX_ID = "inbox"
"""收集箱在 API 里的 projectId 别名。"""

INBOX_NAME = "收集箱"


class GroupKind(Enum):
    """中栏的分区身份。"""

    OVERDUE = "overdue"
    TODAY = "today"


_GROUP_TITLES = {GroupKind.OVERDUE: "逾期", GroupKind.TODAY: "今日"}


@dataclass(frozen=True)
class ListSnapshot:
    """缓存里的一条清单（API 叫 project）。"""

    id: str
    name: str


@dataclass(frozen=True)
class TaskSnapshot:
    """缓存里的一条任务快照：只有事实，没有判断。"""

    id: str
    title: str
    list_id: str
    due: datetime | None = None
    """截止时刻（带时区）；全天任务的时刻是当天 00:00，看 ``all_day`` 分辨。"""

    all_day: bool = False
    priority: int = 0
    """``0`` / ``1`` / ``3`` / ``5``（无 / 低 / 中 / 高），与 API 一致。"""

    completed: bool = False


@dataclass(frozen=True)
class SyncState:
    """缓存里的同步状态。"""

    last_refresh_at: datetime | None = None
    pending_count: int = 0


class ViewSource(Protocol):
    """读路径的数据来源：本地缓存。t08 的 ``Store`` 是生产实现，测试用内存替身。"""

    def lists(self) -> Sequence[ListSnapshot]:
        """全部清单。"""
        ...

    def tasks(self) -> Sequence[TaskSnapshot]:
        """全部任务快照，含已完成。"""
        ...

    def sync_state(self) -> SyncState:
        """同步状态。"""
        ...


@dataclass(frozen=True)
class ListSummary:
    """左栏一行：清单 + 未完成条数徽标。"""

    id: str
    name: str
    unfinished: int


@dataclass(frozen=True)
class TaskItem:
    """中栏一行，字段都已经是可以直接画的成品。"""

    task_id: str
    title: str
    list_id: str
    list_name: str
    priority: int
    priority_mark: str
    due: datetime | None
    all_day: bool
    due_text: str


@dataclass(frozen=True)
class TaskGroup:
    """中栏一个区：身份 + 标题（由身份定）+ 行。"""

    kind: GroupKind
    items: tuple[TaskItem, ...]

    @property
    def title(self) -> str:
        """分区标题，如「逾期」。"""
        return _GROUP_TITLES[self.kind]

    @property
    def count(self) -> int:
        """分区条数，显示在标题上。"""
        return len(self.items)


@dataclass(frozen=True)
class TodayView:
    """整屏要的数据：左栏清单与中栏分区。"""

    lists: tuple[ListSummary, ...]
    groups: tuple[TaskGroup, ...]


def priority_mark(priority: int) -> str:
    """优先级标记：高 ``!``、中 ``~``、低与无 ``·``。"""
    return {5: "!", 3: "~"}.get(priority, "·")


def list_names(lists: Sequence[ListSnapshot]) -> dict[str, str]:
    """清单 id → 显示名；收集箱即使没有清单行也能叫出名字。"""
    names = {item.id: item.name for item in lists}
    names.setdefault(INBOX_ID, INBOX_NAME)
    return names


def task_item(snapshot: TaskSnapshot, names: dict[str, str], *, now: datetime, day_end: str) -> TaskItem:
    """一条任务快照 → 一行成品。"""
    return TaskItem(
        task_id=snapshot.id,
        title=snapshot.title,
        list_id=snapshot.list_id,
        list_name=names.get(snapshot.list_id, snapshot.list_id),
        priority=snapshot.priority,
        priority_mark=priority_mark(snapshot.priority),
        due=snapshot.due,
        all_day=snapshot.all_day,
        due_text=format_due(snapshot.due, all_day=snapshot.all_day, now=now, day_end=day_end),
    )


def summarize_lists(lists: Sequence[ListSnapshot], tasks: Sequence[TaskSnapshot]) -> tuple[ListSummary, ...]:
    """左栏：每条清单一个未完成条数徽标，顺序照缓存给的来。"""
    unfinished: dict[str, int] = {}
    for snapshot in tasks:
        if not snapshot.completed:
            unfinished[snapshot.list_id] = unfinished.get(snapshot.list_id, 0) + 1
    return tuple(
        ListSummary(id=item.id, name=item.name, unfinished=unfinished.get(item.id, 0)) for item in lists
    )


def group_tasks(
    tasks: Sequence[TaskSnapshot],
    lists: Sequence[ListSnapshot],
    *,
    now: datetime,
    day_end: str,
) -> tuple[TaskGroup, ...]:
    """未完成任务 → 分区（逾期在前）。空区不出现在结果里。"""
    label = logical_day(now, day_end).label
    names = list_names(lists)
    buckets: dict[GroupKind, list[TaskItem]] = {GroupKind.OVERDUE: [], GroupKind.TODAY: []}
    for snapshot in tasks:
        if snapshot.completed:
            continue
        if snapshot.due is None:
            kind = GroupKind.TODAY
        else:
            day = due_day(snapshot.due, all_day=snapshot.all_day, day_end=day_end)
            if day < label:
                kind = GroupKind.OVERDUE
            elif day == label:
                kind = GroupKind.TODAY
            else:
                continue  # 未来的任务不属于「今日」
        buckets[kind].append(task_item(snapshot, names, now=now, day_end=day_end))

    return tuple(
        TaskGroup(kind=kind, items=tuple(_by_due(items)))
        for kind, items in buckets.items()
        if items
    )


def due_day(due: datetime, *, all_day: bool, day_end: str) -> date:
    """这条任务的截止属于哪个逻辑日。

    有具体时刻的截止：按逻辑日偏移算（``day_end = "04:00"`` 时，昨天 23:00 属于今天）。
    全天任务的「截止」是**日期标记**（服务端写的是当天 00:00），它属于它写的那一天——
    按 00:00 这个时刻去套偏移会把它整天挪到前一个逻辑日。
    """
    return due.date() if all_day else logical_day(due, day_end).label


def format_due(due: datetime | None, *, all_day: bool, now: datetime, day_end: str) -> str:
    """截止时间的人类读法：「今天 18:00」「昨天 09:00」「3 天前」；没有截止时间给「—」。

    全天任务只给日词（「今天」），绝不给「今天 00:00」。哪一天用**逻辑日**判定：
    ``day_end = "04:00"`` 时，凌晨两点看到的昨天 23:00 截止读作「今天 23:00」。
    """
    if due is None:
        return NO_DUE_TEXT
    delta = (logical_day(now, day_end).label - due_day(due, all_day=all_day, day_end=day_end)).days
    if delta == 0:
        word = "今天"
    elif delta == 1:
        word = "昨天"
    elif delta == -1:
        word = "明天"
    elif delta > 1:
        word = f"{delta} 天前"
    else:
        word = f"{-delta} 天后"
    if all_day or abs(delta) > 1:
        return word
    return f"{word} {due.strftime('%H:%M')}"


def _by_due(items: list[TaskItem]) -> list[TaskItem]:
    """有截止时间的按时间升序在前，没有的按标题排在后面。"""
    dated = sorted((item for item in items if item.due is not None), key=lambda item: (item.due, item.title))
    undated = sorted((item for item in items if item.due is None), key=lambda item: (item.title, item.task_id))
    return dated + undated

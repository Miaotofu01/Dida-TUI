"""视图求值（#35）：`视图定义 + 全量任务缓存 + 当前逻辑日` → 一份排好序的任务列表。

这里只有**纯函数**：不碰网络、不碰存储、不 import Textual。所以它可以被直接测（不需要 app，
也不需要假后端），而「内置视图与自定义视图共用同一条求值路径」这句话才有内容：

- :class:`ViewDefinition` 是一个视图的**过滤条件 + 名字**（spec 的「视图定义」一节）。
  三个内置视图就是三个写死的定义（:func:`builtin_view_definitions`），**和自定义视图走
  同一个** :func:`evaluate_view`——没有 ``if builtin`` 这种分支。#36 把用户建的视图从本地库
  读出来、组成同样的定义，就从这里接进来。
- :func:`evaluate_view` 是求值：按定义筛成员，再按客户端统一的那份顺序排好
  （:func:`order_key`）。返回的 :class:`ViewTask` 除了任务本身还带着求值才知道的东西
  （``overdue``：这条逾期了没有），TUI 因此不必自己判日期。

「今天」不是一段干净的截止时间区间，而是**逾期 ∪ 截止于当前逻辑日**：写成
``[今天, 明天)`` 会静默丢掉每一条逾期任务。定义里它表达成 ``DueWindow(last=0)``
——上界是今天、下界不设，逾期自然落进来；置顶与标红的依据是求值给的 ``overdue``。

日期判断一律走**当前逻辑日**（:func:`dida.logical_day.logical_day` 与
:func:`dida.sync.view.due_day`），不走自然日；全天任务的截止是**日期标记**（当天 00:00），
不参与逻辑日偏移——``due_day`` 里已经这么分了，这里不重写第二份日期比较。

「现在」与 ``day_end`` 一律从参数进来（与 :mod:`dida.sync.view` 同一条规矩），这一层不读时钟。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Sequence

from dida.logical_day import logical_day
from dida.sync.view import TaskSnapshot, due_day, is_overdue

__all__ = [
    "BUILTIN_VIEW_DAYS",
    "Completion",
    "DueWindow",
    "ViewDefinition",
    "ViewTask",
    "builtin_view_definitions",
    "evaluate_view",
    "order_key",
]

BUILTIN_VIEW_DAYS = 7
"""「最近七天」的窗口：从当前逻辑日起的**七个**逻辑日（spec：今天算第一天）。"""


class Completion(Enum):
    """完成状态那一维（spec 的过滤维度之一）：未完成 / 已完成 / 不限。

    「所有」是**全部未完成**任务，不是「全部任务」——已完成的不进任何内置视图。
    """

    UNFINISHED = "unfinished"
    COMPLETED = "completed"
    ANY = "any"


@dataclass(frozen=True)
class DueWindow:
    """截止时间落在哪一段，**相对当前逻辑日**表达（spec 的过滤维度之一）。

    ``first`` / ``last`` 是相对「今天」的**天数偏移**（``0`` = 今天，``-1`` = 昨天，
    ``6`` = 六天后），``None`` 表示那一侧没有边界。没有截止时间的任务由 ``undated``
    单独说了算——它是「无日期」这个独立的区间词，不是任何一侧的边界。

    于是三个内置视图的截止条件各是一句话：「今天」= ``DueWindow(last=0)``（上界今天、
    下界不设 ⇒ 逾期全在）；「最近七天」= ``DueWindow(first=0, last=6)``；「所有」= 不设
    （``None``），所以没有日期的任务也在。
    """

    first: int | None = 0
    last: int | None = 0
    undated: bool = False

    def covers(self, day: date | None, *, today: date) -> bool:
        """这个逻辑日（``None`` = 没有截止时间）落不落在这一段里。"""
        if day is None:
            return self.undated
        if self.first is not None and day < today + timedelta(days=self.first):
            return False
        if self.last is not None and day > today + timedelta(days=self.last):
            return False
        return True


@dataclass(frozen=True)
class ViewDefinition:
    """一个视图的**过滤条件 + 名字**（内置与自定义是同一个类型）。

    过滤维度里这一票只长了内置视图用得着的两维——**截止时间**（``due``）与**完成状态**
    （``completion``）。#36 的清单范围 / 优先级 / 标签是往这里加字段、往
    :func:`_matches` 里加一条判据，不是另写一条求值路径。
    """

    id: str
    name: str
    due: DueWindow | None = None
    """截止时间的区间；``None`` = 不限（没有日期的任务因此也在）。"""

    completion: Completion = Completion.UNFINISHED


TODAY_VIEW = ViewDefinition(id="today", name="今天", due=DueWindow(first=None, last=0))
"""「今天」= 逾期 ∪ 截止于当前逻辑日（用户故事 25）。

``first=None``（下界不设）是**承重**的：它就是「逾期也在里面」的那一半。写成
``DueWindow(first=0, last=0)`` 会静默丢掉每一条逾期任务。
"""

NEXT7_VIEW = ViewDefinition(
    id="next7", name="最近七天", due=DueWindow(first=0, last=BUILTIN_VIEW_DAYS - 1)
)
"""「最近七天」= 截止时间落在从今天起的七个逻辑日内（用户故事 26）。逾期的不算。"""

ALL_VIEW = ViewDefinition(id="all", name="所有")
"""「所有」= 全部未完成任务（用户故事 27）：未来截止的、没有日期的都在。"""


def builtin_view_definitions() -> tuple[ViewDefinition, ...]:
    """三个内置视图的定义，按清单索引里的顺序（今天 / 最近七天 / 所有）。"""
    return (TODAY_VIEW, NEXT7_VIEW, ALL_VIEW)


@dataclass(frozen=True)
class ViewTask:
    """求值结果里的一条：任务本身 + 求值才知道的那点东西。

    ``overdue`` 是「逾期」这个判断（逻辑日判定，已完成的不算），TUI 拿它标红；它不是
    TUI 自己算的——架构规则里日期判断全在引擎这一层。
    """

    snapshot: TaskSnapshot
    overdue: bool = False


def evaluate_view(
    definition: ViewDefinition,
    tasks: Sequence[TaskSnapshot],
    *,
    now: datetime,
    day_end: str,
) -> tuple[ViewTask, ...]:
    """求值：``定义 + 缓存 + 逻辑日`` → 排好序的成员。

    纯函数、确定性：同一份输入永远给同一个结果（排序键在 :func:`order_key`，所有分量都是
    任务自己的事实，没有随机、没有时钟、没有「和谁比过」的状态）。
    """
    today = logical_day(now, day_end).label
    members = [
        ViewTask(
            snapshot=snapshot,
            overdue=is_overdue(snapshot, today=today, day_end=day_end),
        )
        for snapshot in tasks
        if _matches(definition, snapshot, today=today, day_end=day_end)
    ]
    return tuple(sorted(members, key=lambda item: order_key(item.snapshot, today=today, day_end=day_end)))


def order_key(snapshot: TaskSnapshot, *, today: date, day_end: str) -> tuple:
    """客户端统一重排的那份顺序（spec 的「排序」一节）。

    逾期置顶 → 截止时间升序 → 优先级降序 → 没有截止时间的靠后 → 已完成的沉底；
    末尾再按标题与 id 断开剩下的平局，所以结果是**确定的**（同一份输入不会两次不同）。

    ``due_day``（逻辑日）而不是裸的时刻，是「逾期置顶」与「截止时间升序」真正分开的地方：
    ``day_end = "04:00"`` 时当天 03:00 属于**昨天**，它逾期、要置顶，而它的时刻又比当天
    00:00 那个全天标记更晚——只按时刻升序会把它排到那条全天任务后面。
    """
    if snapshot.completed:
        return (3, 0.0, -snapshot.priority, snapshot.title, snapshot.id)
    if snapshot.due is None:
        return (2, 0.0, -snapshot.priority, snapshot.title, snapshot.id)
    rank = 0 if is_overdue(snapshot, today=today, day_end=day_end) else 1
    return (
        rank,
        snapshot.due.timestamp(),
        -snapshot.priority,
        snapshot.title,
        snapshot.id,
    )


def _matches(
    definition: ViewDefinition, snapshot: TaskSnapshot, *, today: date, day_end: str
) -> bool:
    """这条任务在不在这个定义里：逐条判据，各自一句话。

    日期判据走 :func:`dida.sync.view.due_day`（它认全天任务的日期标记），所以这里不做
    第二份日期比较——两处日期比较漂移才是这类 bug 的来源。
    """
    if not _matches_completion(definition.completion, snapshot):
        return False
    if definition.due is None:
        return True
    day = (
        None
        if snapshot.due is None
        else due_day(snapshot.due, all_day=snapshot.all_day, day_end=day_end)
    )
    return definition.due.covers(day, today=today)


def _matches_completion(completion: Completion, snapshot: TaskSnapshot) -> bool:
    if completion is Completion.ANY:
        return True
    return snapshot.completed if completion is Completion.COMPLETED else not snapshot.completed

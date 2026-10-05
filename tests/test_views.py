"""视图求值（#35）：视图定义 + 全量任务缓存 + 当前逻辑日 → 确定的、排好序的任务列表。

**无接缝（纯函数）**：这一组直接调 :func:`dida.sync.views.evaluate_view`——「求值是纯函数、
不碰网络不碰存储」本身就是验收标准的一条，所以它不需要 app 也不需要假后端。期望值来自
spec（issue #30 的「视图定义」与「排序」两节）与 :mod:`dida.logical_day`，不是照抄实现。

三个内置视图就是三个写死的 :class:`~dida.sync.views.ViewDefinition`，走的是**和自定义视图
同一条**求值路径：文件末尾那条测试用一个不属于内置三个的定义证明这条路不是为内置写死的
（#36 的自定义视图从这里接）。

逻辑日是这一组的另一半：所有日期判断都按**当前逻辑日**算，不按自然日。``day_end = "04:00"``
时凌晨两点看到的昨天 23:00 属于「今天」，而当天 03:00 属于**昨天**——全天任务的截止是
**日期标记**（当天 00:00），它不参与这个偏移。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dida.sync.view import TaskSnapshot
from dida.sync.views import (
    Completion,
    DueWindow,
    ViewDefinition,
    builtin_view_definitions,
    evaluate_view,
)

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
"""测试里的「现在」：2026-03-14 中午（逻辑日就是 03-14）。"""


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


def task(
    title: str,
    *,
    due: datetime | None = None,
    all_day: bool = False,
    priority: int = 0,
    completed: bool = False,
    list_id: str = "work",
) -> TaskSnapshot:
    """一条任务快照；``id`` 就用标题，读起来方便（快照只要 id 唯一）。"""
    return TaskSnapshot(
        id=title,
        title=title,
        list_id=list_id,
        due=due,
        all_day=all_day,
        priority=priority,
        completed=completed,
        completed_at=T0 if completed else None,
    )


def titles(evaluated) -> list[str]:
    return [item.snapshot.title for item in evaluated]


def view(view_id: str) -> ViewDefinition:
    """三个内置定义里的一个（今天 / 最近七天 / 所有）。"""
    return next(item for item in builtin_view_definitions() if item.id == view_id)


def evaluate(view_id: str, tasks, *, now: datetime = T0, day_end: str = "24:00"):
    return evaluate_view(view(view_id), tasks, now=now, day_end=day_end)


# ---------------------------------------------------------------- 「所有」


def test_all_holds_every_unfinished_task():
    """「所有」= 全部未完成任务（用户故事 27）：未来截止的、没有日期的都在，已完成的不在。

    这正是 v1 让用户看不到的那一个视图（v1 只显示与今天有关的任务），所以「未来的也在」
    是这条测试的重点，不是顺带。
    """
    tasks = (
        task("昨天到期", due=T0 - timedelta(days=1)),
        task("今天到期", due=T0.replace(hour=18)),
        task("下个月", due=T0 + timedelta(days=30)),
        task("没日期"),
        task("做完了", completed=True),
    )

    assert titles(evaluate("all", tasks)) == ["昨天到期", "今天到期", "下个月", "没日期"]

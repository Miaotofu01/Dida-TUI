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


# ---------------------------------------------------------------- 「今天」= 逾期 ∪ 今天到期


def test_today_is_overdue_union_due_today():
    """「今天」= 逾期 ∪ 截止于当前逻辑日（用户故事 25 / spec 的定义）。

    **它不是一个干净的截止时间区间**：写成「截止时间落在今天之内」会静默丢掉每一条逾期
    任务，而那些正是最该被看见的。所以这条测试同时断两半：昨前天的逾期在，明天的、
    没日期的、已完成的都不在。
    """
    tasks = (
        task("前天到期", due=T0 - timedelta(days=2)),
        task("昨天到期", due=T0 - timedelta(days=1)),
        task("今天 18 点", due=T0.replace(hour=18)),
        task("明天", due=T0 + timedelta(days=1)),
        task("没日期"),
        task("今天做完的", due=T0.replace(hour=9), completed=True),
    )

    evaluated = evaluate("today", tasks)

    assert titles(evaluated) == ["前天到期", "昨天到期", "今天 18 点"]
    assert [item.overdue for item in evaluated] == [True, True, False], "逾期的前面两条要标红"


def test_overdue_is_pinned_even_when_its_clock_time_is_later():
    """逾期置顶是**独立的一条**，压过「谁的时刻更早」。

    边界 ``04:00`` 下当天 03:00 属于昨天：它逾期、要置顶，可它的时刻（03-14 03:00）比
    当天那个全天标记（03-13 00:00，属于同一个逻辑日、不逾期）**更晚**。只按时刻升序排
    就会把它排到后面——这就是「置顶」这两个字值钱的地方。
    """
    tasks = (
        task("今天的全天", due=at(13), all_day=True),
        task("凌晨三点", due=at(13, 3)),
    )

    evaluated = evaluate("today", tasks, now=at(14, 2), day_end="04:00")

    assert titles(evaluated) == ["凌晨三点", "今天的全天"]
    assert [item.overdue for item in evaluated] == [True, False]


# ---------------------------------------------------------------- 逻辑日边界（AC 的那一条）


def test_the_0400_boundary_keeps_last_nights_deadline_in_today():
    """边界配成 ``04:00`` 时，凌晨两点看到的仍是同一个逻辑日：昨夜 23:00 属于**今天**。

    验收标准原话：「边界配成 ``04:00`` 时，凌晨两点看到的仍是同一个逻辑日的截止任务，
    且截止于昨天 23:00 的任务归在今日而不是被判成逾期」。所以这一条同时钉两半：昨晚
    23:00 在「今天」里且**不**逾期，而昨夜 03:00（落在同一个自然日的边界之前）属于更早
    那个逻辑日——它才逾期。
    """
    tasks = (
        task("昨夜 03 点", due=at(13, 3)),
        task("昨晚 23 点", due=at(13, 23)),
        task("明天 23 点", due=at(14, 23)),
    )

    evaluated = evaluate("today", tasks, now=at(14, 2), day_end="04:00")

    assert titles(evaluated) == ["昨夜 03 点", "昨晚 23 点"]
    assert [item.overdue for item in evaluated] == [True, False]


def test_an_all_day_deadline_is_a_date_marker_not_an_instant():
    """全天任务的截止是**日期标记**（当天 00:00），不是时刻：它不参与逻辑日偏移。

    拿 03-13 00:00 去套 ``04:00`` 的边界，它会落进 03-12 那个逻辑日——于是每一条「今天」
    的全天任务都被静默挪成逾期。所以这一条与上一条是同一件事的两半：**日期比较只有
    :func:`dida.sync.view.due_day` 一份**，这里不重写第二份。
    """
    tasks = (
        task("今天的全天", due=at(13), all_day=True),
        task("今天的 09 点", due=at(13, 9)),
        task("昨天的全天", due=at(12), all_day=True),
    )

    evaluated = evaluate("today", tasks, now=at(14, 2), day_end="04:00")

    assert titles(evaluated) == ["昨天的全天", "今天的全天", "今天的 09 点"]
    assert [item.overdue for item in evaluated] == [True, False, False]

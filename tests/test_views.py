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

from dida.testing import FakeBackend, ManualClock

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


# ---------------------------------------------------------------- 「最近七天」


def test_next_seven_days_covers_seven_logical_days_from_today():
    """「最近七天」= 截止时间落在从今天起的**七个**逻辑日内（用户故事 26）。

    今天算第一天，所以窗口是 ``[今天, 今天+6]``（``BUILTIN_VIEW_DAYS = 7``）；第八天不在
    里面。逾期的不在——「最近七天」不是「最近七天加上以前欠的」，逾期的属于「今天」。
    没有日期的不在（``undated`` 那一维默认关着）。
    """
    tasks = (
        task("昨天到期", due=T0 - timedelta(days=1)),
        task("今天到期", due=T0.replace(hour=20)),
        task("第六天后", due=T0 + timedelta(days=6)),
        task("第七天后", due=T0 + timedelta(days=7)),
        task("没日期"),
    )

    assert titles(evaluate("next7", tasks)) == ["今天到期", "第六天后"]


def test_next_seven_days_walks_the_logical_day_at_the_boundary():
    """同一个边界下「七天」也跟着逻辑日走：凌晨两点时今天其实是 13 号。

    03-14 05:00 那一刻属于**下一个**逻辑日（它是第二天），而 03-19 那个逻辑日仍是窗口里
    的最后一天——按自然日算，这两条都会各错一天。
    """
    tasks = (
        task("昨晚 23 点", due=at(13, 23)),
        task("明天 05 点", due=at(14, 5)),
        task("第七个逻辑日", due=at(19, 23)),
        task("第八个逻辑日", due=at(20, 5)),
    )

    assert titles(evaluate("next7", tasks, now=at(14, 2), day_end="04:00")) == [
        "昨晚 23 点",
        "明天 05 点",
        "第七个逻辑日",
    ]


# ---------------------------------------------------------------- 排序与确定性


def test_ties_on_the_same_deadline_break_by_priority_high_first():
    """同一条截止时间上按优先级**降序**（spec 的「排序」一节），四个档位都排一遍。

    平局必须被断开到确定为止：终端里列表的顺序就是用户看到的顺序，两次刷新换个先后
    看起来像丢了一条又重新出现。
    """
    tasks = (
        task("低", due=at(14, 10), priority=1),
        task("高", due=at(14, 10), priority=5),
        task("无", due=at(14, 10)),
        task("中", due=at(14, 10), priority=3),
    )

    assert titles(evaluate("today", tasks)) == ["高", "中", "低", "无"]


def test_evaluation_is_deterministic_for_a_fixed_definition_cache_and_logical_day():
    """给定（定义、缓存、逻辑日），求值给**确定**的排好序的列表——与输入顺序无关。

    缓存是从 sqlite 里读出来的，行的先后不是承诺；求值不能因此给两份不同的列表
    （验收标准：「输出确定的、排好序的任务列表」）。所以这里把同一份缓存倒过来再求一次。
    """
    tasks = (
        task("同样的截止 A", due=at(14, 10), priority=3),
        task("同样的截止 B", due=at(14, 10), priority=3),
        task("没日期的 A"),
        task("没日期的 B"),
        task("逾期", due=at(13, 10)),
    )

    forwards = titles(evaluate("all", tasks))
    backwards = titles(evaluate("all", list(reversed(tasks))))

    assert forwards == backwards == ["逾期", "同样的截止 A", "同样的截止 B", "没日期的 A", "没日期的 B"]


# ---------------------------------------------------------------- 一条求值路径


def test_a_definition_that_is_not_one_of_the_builtins_evaluates_on_the_same_path():
    """内置视图与自定义视图共用同一条求值路径（验收标准最后一条）。

    这条测试用的是**不属于内置三个**的定义：「无日期的那些」。求值器认的是定义本身
    （截止区间 + 完成状态），不是 ``today``/``next7``/``all`` 这三个 id——所以 #36 把用户
    建的视图读出来、组成同样的 :class:`ViewDefinition` 就从这里接进来，不必另写一条路。
    """
    undated = ViewDefinition(
        id="undated", name="无日期", due=DueWindow(dated=False, undated=True)
    )
    tasks = (
        task("今天到期", due=T0.replace(hour=20)),
        task("没日期 A"),
        task("没日期 B"),
        task("没日期但做完了", completed=True),
    )

    assert titles(evaluate_view(undated, tasks, now=T0, day_end="24:00")) == ["没日期 A", "没日期 B"]
    assert titles(evaluate("today", tasks)) == ["今天到期"], "内置那三个不受影响"


def test_the_completion_dimension_decides_who_is_a_member():
    """完成状态是定义里的一维（spec 的过滤维度之一）：「最近完成」那种视图由它表达。

    内置三个都只收未完成的，所以这一维默认是 :attr:`Completion.UNFINISHED`；换成
    :attr:`Completion.COMPLETED` 收的就是做完的那些——同一个求值器，只换定义。
    """
    done = ViewDefinition(id="done", name="最近完成", completion=Completion.COMPLETED)
    tasks = (
        task("没做完", due=T0.replace(hour=20)),
        task("做完了 A", due=T0.replace(hour=20), completed=True),
        task("做完了 B", completed=True),
    )

    evaluated = evaluate_view(done, tasks, now=T0, day_end="24:00")

    assert titles(evaluated) == ["做完了 A", "做完了 B"]
    assert [item.overdue for item in evaluated] == [False, False], "做完的不算逾期"


# ---------------------------------------------------------------- 接缝一：引擎的读路径


def backend(*, now: datetime = T0, day_end: str = "24:00") -> FakeBackend:
    """内存缓存 + **真引擎的读路径**（``FakeBackend`` 的读委托给生产那一份实现）。"""
    return FakeBackend(clock=ManualClock(now), day_end=day_end)


def test_the_engine_keeps_the_evaluated_order_and_marks_the_overdue_rows():
    """进视图看到的那份列表：顺序是求值给的，逾期的那些带着 ``overdue`` 位（供标红）。

    ``TaskItem`` 上没有这个位的话，TUI 要标红就只能自己判日期——那是架构规则不许的
    （日期判断全在引擎这一层）。所以位在这里给，画在 #37。
    """
    fake = backend()
    fake.add_task("前天到期", list_name="work", due=T0 - timedelta(days=2))
    fake.add_task("今天 18 点", list_name="work", due=T0.replace(hour=18))
    fake.add_task("明天", list_name="work", due=T0 + timedelta(days=1))

    items = fake.tasks_in("today").items

    assert [item.title for item in items] == ["前天到期", "今天 18 点"]
    assert [item.overdue for item in items] == [True, False]


def test_the_engine_pins_overdue_rows_at_the_logical_boundary():
    """边界 ``04:00``、凌晨两点：昨夜 23:00 是「今天」，当天 03:00 反而逾期、置顶。"""
    fake = backend(now=at(14, 2), day_end="04:00")
    fake.add_task("昨夜 03 点", list_name="work", due=at(13, 3))
    fake.add_task("今天的全天", list_name="work", due=at(13), all_day=True)
    fake.add_task("昨晚 23 点", list_name="work", due=at(13, 23))

    items = fake.tasks_in("today").items

    assert [item.title for item in items] == ["昨夜 03 点", "今天的全天", "昨晚 23 点"]
    assert [item.overdue for item in items] == [True, False, False]


def test_a_view_shows_the_list_name_while_a_real_list_does_not():
    """视图不是容器：同一个清单名在视图里重复显示是必要信息，在清单里是噪音。

    判断归读模型（``TaskList.shows_list_name``），画归 TUI——这也正是「清单名重复一百遍」
    那种噪音该在哪一层被裁掉的问题：页面不该自己去猜这个容器是不是视图。
    """
    fake = backend()
    fake.add_list("工作", id="work")
    fake.add_task("交报告", list_name="work", due=T0.replace(hour=18))

    assert fake.tasks_in("work").shows_list_name is False
    assert fake.tasks_in("today").shows_list_name is True
    assert fake.tasks_in("today").items[0].list_name == "工作"


def test_the_index_row_count_and_the_view_list_come_from_one_evaluation():
    """索引里的条数与进去看到的列表来自**同一次求值**（#33 的契约，#35 不许破坏它）。

    逾期那条同时在「今天」与「所有」里，但不在「最近七天」里——索引上的数字与容器里的
    条数因此必须逐行对上。
    """
    fake = backend()
    fake.add_task("昨天到期", list_name="work", due=T0 - timedelta(days=1))
    fake.add_task("今天到期", list_name="work", due=T0.replace(hour=18))
    fake.add_task("第八天后", list_name="work", due=T0 + timedelta(days=8))

    rows = {row.id: row for row in fake.list_index()}

    assert rows["today"].unfinished == len(fake.tasks_in("today").items) == 2
    assert rows["next7"].unfinished == len(fake.tasks_in("next7").items) == 1
    assert rows["all"].unfinished == len(fake.tasks_in("all").items) == 3

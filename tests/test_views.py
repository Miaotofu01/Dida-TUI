"""视图求值（#35）：视图定义 + 全量任务缓存 + 当前逻辑日 → 确定的、排好序的任务列表。

**接缝一（纯函数）**：这一组的大半直接调 :func:`dida.sync.views.evaluate_view`——「求值是
纯函数、不碰网络不碰存储」本身就是验收标准的一条，所以它不需要 app 也不需要假后端。
期望值来自 spec（issue #30 的「视图定义」与「排序」两节）与 :mod:`dida.logical_day`，
不是照抄实现。

**接缝二（引擎 / 界面）**：同一批判断在 ``FakeBackend``（内存缓存 + 真引擎的读路径）与
真 ``DidaApp`` + Pilot 上再断一次——「我按了这个键，屏幕上出现了什么」。跨层的那几条
（三个内置视图是索引里的行、进去看到过滤后的任务、视图里的行写着所属清单名、逾期标红）
只有在真 app 上才有意义。

三个内置视图就是三个写死的 :class:`~dida.sync.views.ViewDefinition`，走的是**和自定义视图
同一条**求值路径：中间那条测试用一个不属于内置三个的定义证明这条路不是为内置写死的
（#36 的自定义视图从这里接）。

逻辑日是这一组的另一半：所有日期判断都按**当前逻辑日**算，不按自然日。``day_end = "04:00"``
时凌晨两点看到的昨天 23:00 属于「今天」，而当天 03:00 属于**昨天**——全天任务的截止是
**日期标记**（当天 00:00），它不参与这个偏移。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp
from dida.tui.pages.index import BUILTIN_MARK, CUSTOM_MARK, INBOX_MARK, LIST_MARK
from dida.sync.view import TaskSnapshot
from dida.sync.views import (
    Completion,
    DueWindow,
    ViewDefinition,
    builtin_view_definitions,
    evaluate_view,
)
from support import screen_styled_text, screen_text

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


# ---------------------------------------------------------------- 接缝一（界面）：真 app + Pilot

WIDE = (100, 30)

SGR = re.compile(r"\x1b\[([0-9;]*)m")


@pytest.fixture(autouse=True)
def a_colour_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """摘掉 shell 的 ``NO_COLOR``：否则 ``App`` 会挂一层 Monochrome，颜色断言全部假绿。"""
    monkeypatch.delenv("NO_COLOR", raising=False)


def sgr_parameters(line: str) -> set[int]:
    """这一行里出现过的每一个 SGR 参数（``\\x1b[31;49m`` → ``{31, 49}``）。

    断的是**参数**不是整串：Textual 会把前景与背景并进同一条序列，按整串写会假红。
    """
    out: set[int] = set()
    for group in SGR.findall(line):
        out.update(int(part) for part in group.split(";") if part)
    return out


def line_with(text: str, needle: str) -> str:
    """屏幕上写着 ``needle`` 的那一行（没有就是测试写错了）。"""
    for line in text.splitlines():
        if needle in line:
            return line
    raise AssertionError(f"屏幕上没有「{needle}」这一行：\n{text}")


async def enter_view(pilot, app: DidaApp, view_id: str) -> None:
    """从清单列表页走进一个视图：光标从收集箱往下走到那一行，再 ``enter``。"""
    for _ in range(10):
        if app.index_page().selected_id == view_id:
            break
        await pilot.press("j")
    await pilot.press("enter")
    await pilot.pause()


def app_with(*, now: datetime = T0, day_end: str = "24:00") -> DidaApp:
    fake = FakeBackend(clock=ManualClock(now), day_end=day_end)
    fake.add_list("工作", id="work")
    fake.add_list("生活", id="life")
    fake.add_task("交报告", list_name="work", due=T0.replace(hour=18))
    fake.add_task("遛狗", list_name="life", due=T0 - timedelta(days=1))
    fake.add_task("下个月", list_name="work", due=T0 + timedelta(days=30))
    fake.add_task("没日期", list_name="life")
    return DidaApp(fake)


async def test_the_three_builtins_are_index_rows_marked_by_prefix_character():
    """三个内置视图是清单列表页里的行，**用前缀字符**与真实清单一眼分开（用户故事 10）。

    前缀而不是颜色：色弱、``NO_COLOR``、只有 8 色的终端上颜色都不算数，所以「一眼可分」
    这件事必须落在字符上。三个视图与真实清单、收集箱的记号两两不同。
    """
    app = app_with()
    async with app.run_test(size=WIDE) as pilot:
        text = screen_text(app)

        for name in ("今天", "最近七天", "所有"):
            assert f"{BUILTIN_MARK} {name}" in line_with(text, name), (
                f"「{name}」这一行没有内置视图的前缀"
            )
        assert f"{LIST_MARK} 工作" in line_with(text, "工作"), "真实清单是另一个记号"
        assert len({BUILTIN_MARK, LIST_MARK, INBOX_MARK, CUSTOM_MARK}) == 4, "记号本身要互不相同"
        assert app.index_page().selected_id is not None


async def test_entering_today_shows_overdue_and_then_todays_task():
    """进「今天」看到的是逾期 ∪ 今天到期，**逾期在上**（用户故事 25）。

    这是那一层最外部的行为：我没有按任何「排序」键，屏幕上的先后就是求值给的先后。
    明天的、没日期的都不在里面——「今天」不是「所有」。
    """
    app = app_with()
    async with app.run_test(size=WIDE) as pilot:
        await enter_view(pilot, app, "today")
        text = screen_text(app)

        assert "遛狗" in text and "交报告" in text
        assert text.index("遛狗") < text.index("交报告"), "逾期置顶"
        assert "下个月" not in text, "未来截止的不算今天"
        assert "没日期" not in text, "没有截止时间的不算今天"


async def test_entering_all_shows_what_no_list_would_show():
    """进「所有」看到全部未完成任务（用户故事 27）：未来的、没日期的都在。

    v1 让用户看不到的正是这一个视图，所以这条断的是「它真的把那些行放出来了」。
    """
    app = app_with()
    async with app.run_test(size=WIDE) as pilot:
        await enter_view(pilot, app, "all")
        text = screen_text(app)

        for title in ("交报告", "遛狗", "下个月", "没日期"):
            assert title in text, f"「所有」里少了「{title}」"


async def test_rows_in_a_view_show_their_list_name_and_rows_in_a_list_do_not():
    """视图里的每条任务显示所属清单名；真实清单里不重复显示（用户故事 34 / 58）。

    断的是**任务行**而不是整屏：清单那一屏的抬头本来就叫「工作」，所以「屏幕上没有工作
    两个字」是个错的断言。要看的是「交报告」这一行。
    """
    app = app_with()
    async with app.run_test(size=WIDE) as pilot:
        await enter_view(pilot, app, "all")
        in_view = line_with(screen_text(app), "交报告")
        assert "工作" in in_view, "视图不是容器：这一行要写出它属于哪个清单"

        await pilot.press("escape")
        await pilot.pause()
        for _ in range(10):
            if app.index_page().selected_id == "work":
                break
            await pilot.press("j")
        await pilot.press("enter")
        await pilot.pause()
        in_list = line_with(screen_text(app), "交报告")
        assert "工作" not in in_list, "清单里的行不重复写清单名（抬头已经写着它了）"


async def test_the_overdue_row_is_red_and_the_todays_row_is_not():
    """逾期标红（用户故事 25 / 93）：断 SGR **参数**里的 ``31``（红），不写死整串。

    光标那一行整体会被换成强调色（``CursorPage._redraw`` 的 ``SELECTED``），所以这条必须
    把光标从逾期那条上移开——不然测到的是光标，不是逾期。这是真行为，不是测试的将就：
    用户在「今天」里看到的第一条（逾期的）正是光标停着的那条，它不红是因为光标压着它。
    """
    app = app_with()
    async with app.run_test(size=WIDE) as pilot:
        await enter_view(pilot, app, "today")
        await pilot.press("j")  # 光标从逾期那条移到今天到期那条
        await pilot.pause()
        styled = screen_styled_text(app)

        assert 31 in sgr_parameters(line_with(styled, "遛狗")), "逾期那条没有标红"
        assert 31 not in sgr_parameters(line_with(styled, "交报告")), "今天到期那条不该标红"
        assert "38;2;" not in styled, "红是真彩色：终端主题被顶掉了"

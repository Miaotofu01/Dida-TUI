"""逾期**行**标红：故事 14「逾期任务置顶并标红」（评审修 A2）。

分区标题标红已经有测试了（``tests/test_app_view.py::test_the_overdue_group_header_is_red_and_today_is_not``），
它只盯标题，所以「行本身是不是红的」一直没有东西守着。

分组、排序、逾期判定全是引擎的判断（:class:`~dida.sync.view.GroupKind`）：栏位只是把
「这一行在逾期区」画成红色，自己不碰日期、不比重。断言因此分两层：纯行层钉住「红」这个
事实，屏上层钉住「这个事实真的从分区传到了每一行」。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from dida.sync.view import GroupKind, TaskItem
from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp
from dida.tui.panes import TaskPane, task_line
from support import screen_styled_text, screen_text

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)

SGR = re.compile(r"\x1b\[[0-9;]*m")


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


def make_backend() -> FakeBackend:
    """逾期两条 + 今日两条：光标落在逾期区第一条上，两条被比的行都不是光标行。"""
    backend = FakeBackend(clock=ManualClock(T0))
    backend.add_task("交水费", list_name="生活", id="t1", due=at(12, 9, 0))
    backend.add_task("还信用卡", list_name="生活", id="t2", due=at(13), all_day=True)
    backend.add_task("修水龙头", list_name="生活", id="t3", due=at(14), all_day=True)
    backend.add_task("写周报", list_name="工作", id="t4", due=at(14, 18, 0))
    return backend


def item_of(kind: GroupKind) -> TaskItem:
    """引擎分好的某一区里的第一条任务（行的身份由引擎给，测试不自己判）。"""
    view = make_backend().view()
    group = next(group for group in view.groups if group.kind is kind)
    return group.items[0]


def test_an_overdue_row_is_red_and_a_today_row_is_not():
    """行层：逾期那一行整行带红，今日那一行不带。"""
    overdue = item_of(GroupKind.OVERDUE)
    today = item_of(GroupKind.TODAY)

    assert "red" in str(task_line(overdue, selected=False, overdue=True).style)
    assert "red" not in str(task_line(today, selected=False, overdue=False).style)


def test_the_overdue_row_keeps_its_plain_text():
    """标红是样式，不是往行里塞字符：文字一个字都不变。"""
    overdue = item_of(GroupKind.OVERDUE)

    plain = task_line(overdue, selected=False, overdue=True)
    unstyled = task_line(overdue, selected=False, overdue=False)

    assert plain.plain == unstyled.plain


def row_styles(app: DidaApp, row: str) -> str:
    """屏幕上某一**任务行**里、``row`` 之前的 SGR 序列（不含文字）。

    与 ``tests/test_complete.py`` 的 ``row_style`` 同一个手法：不写死颜色，比的是
    「这一行的样式和别人不一样」。``row`` 要带上清单名（如 ``还信用卡  生活``），
    因为同一句标题在右栏详情里也会出现一次——那一条不是任务行。
    """
    for line in screen_styled_text(app).splitlines():
        plain = SGR.sub("", line)
        if row in plain:
            return "".join(SGR.findall(line[: _cut_before(line, plain.index(row))]))
    raise AssertionError(f"屏幕上没有「{row}」这一行")


def _cut_before(line: str, visible: int) -> int:
    """带 SGR 的原文里，第 ``visible`` 个可见字符的下标。"""
    cut = seen = 0
    while cut < len(line) and seen < visible:
        match = SGR.match(line, cut)
        if match:
            cut = match.end()
            continue
        cut += 1
        seen += 1
    return cut


async def test_the_overdue_row_on_screen_is_styled_differently_from_a_today_row():
    """屏上层：光标行之外，逾期行的样式与今日行不同（红线真的从分区接到了行上）。

    光标停在逾期区第一条（``交水费``）上，比的是另外两条**都不是光标行**的任务：
    逾期一条（``还信用卡``）、今日一条（``修水龙头``）。两条的优先级标记与清单名都一样，
    唯一的差别就是它们所属的分区。
    """
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        assert app.query_one(TaskPane).selected_task_id == "t1", "光标默认落在逾期区第一条上"

        text = screen_text(app)
        assert "❯ · 交水费  生活" in text, "光标行是另一条，不参与这次比较"
        assert "  · 还信用卡  生活" in text and "  · 修水龙头  生活" in text
        assert row_styles(app, "还信用卡  生活") != row_styles(app, "修水龙头  生活"), (
            "逾期行与今日行在屏幕上长得一模一样"
        )

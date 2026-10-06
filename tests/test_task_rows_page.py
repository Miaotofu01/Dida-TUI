"""任务行的样子与顺序（工单 #37）：真按键、真看屏幕的那一半。

**接缝一**：真 ``DidaApp`` + ``FakeBackend`` + Pilot。断的是外部行为——「进了这个清单，
屏幕上这一行读成什么」、「窗口只有 30 列时先丢的是哪一样」、「刷新之后光标还在不在原来
那条任务上」。

样式那一半只比 **SGR 参数**（``\\x1b[31;49m`` → 看 ``31`` 在不在），不比整串：Textual 把
前景与背景并进同一条序列，按整串比会假红（ADR-0007 四）。纯函数那一半在
``tests/test_task_row_model.py``。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from rich.cells import cell_len

from dida.sync.engine import Completion
from dida.testing import FakeBackend, ManualClock
from dida.tui import theme
from dida.tui.app import DidaApp
from support import screen_styled_text, screen_text

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
WIDE = (100, 30)

SGR = re.compile(r"\x1b\[[0-9;]*m")
"""ANSI 的 SGR 序列：样式在屏幕文本里就长这样。"""

RED = 31
"""ANSI 槽 1（红）。:data:`dida.tui.theme.OVERDUE` 就是它。"""

STRIKE = 9
"""删除线（``theme.DONE`` 里的 ``strike``）。"""


@pytest.fixture(autouse=True)
def a_colour_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """摘掉 shell 的 ``NO_COLOR``：否则 ``App`` 会挂一层 Monochrome，颜色断言全部假绿。"""
    monkeypatch.delenv("NO_COLOR", raising=False)


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


def backend() -> FakeBackend:
    """一份够用的缓存：一条逾期、一条今天到期、一条没日期，外加一条做完了的。"""
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task("昨天就该做的", list_name="work", id="t1", due=at(13, 9, 0))
    fake.add_task("今天要做的", list_name="work", id="t2", due=at(14, 18, 0))
    fake.add_task("没日期的", list_name="work", id="t3")
    fake.add_task("已经做完的", list_name="work", id="t4", completed=True, completed_at=at(14, 11, 0))
    return fake


def lines(text: str) -> list[str]:
    return text.splitlines()


def line_with(text: str, needle: str) -> str:
    """屏幕上写着 ``needle`` 的那一行（没有就是测试写错了）。"""
    for line in lines(text):
        if needle in line:
            return line
    raise AssertionError(f"屏幕上没有「{needle}」这一行：\n{text}")


def sgr_parameters(emitted: str) -> set[int]:
    """这一段字节里出现过的每一个 SGR 参数（``\\x1b[36;49m`` → ``{36, 49}``）。"""
    out: set[int] = set()
    for group in re.findall(r"\x1b\[([0-9;]*)m", emitted):
        out.update(int(part) for part in group.split(";") if part)
    return out


def style_before(line: str, needle: str) -> str:
    """屏幕上这一行里、``needle`` 之前那些 SGR 序列（原样拼起来）。

    不解读它是哪个颜色，只把它交给 :func:`sgr_parameters`——颜色随主题变，参数不随。
    """
    plain = SGR.sub("", line)
    cut = plain.index(needle)
    seen = 0
    for match in SGR.finditer(line):
        if len(SGR.sub("", line[: match.start()])) >= cut:
            break
        seen = match.end()
    return line[:seen]


async def enter_work(pilot, app: DidaApp) -> None:
    """走到「工作」这个清单里（光标停在它的第一条任务上）。"""
    for _ in range(20):
        if app.index_page().selected_id == "work":
            break
        await pilot.press("j")
    await pilot.press("enter")
    await pilot.pause()


# ------------------------------------------------------------------ 行的样子


async def test_the_overdue_row_is_red_and_the_others_are_not():
    """逾期标红（用户故事 25）：红的是**这一行**，不是旁边那一条。

    比的是 SGR 参数：逾期行里有 ``31``，今天到期的那一行里没有。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_work(pilot, app)
        # 光标走到第三条上：被比的那两行都不是光标行——光标行整行是强调色 + 字重
        # （ADR-0007 三：选中要靠颜色与字重，终端画不出半格），红在那里让位给「选中」。
        await pilot.press("j", "j")
        assert app.tasks_page().selected_id == "t3"
        styled = screen_styled_text(app)

    overdue = style_before(line_with(styled, "昨天就该做的"), "昨天就该做的")
    today = style_before(line_with(styled, "今天要做的"), "今天要做的")

    assert RED in sgr_parameters(overdue), f"逾期那一行没有标红：{overdue!r}"
    assert RED not in sgr_parameters(today), f"今天到期的被标红了：{today!r}"


async def test_a_row_reads_its_priority_mark_its_due_and_its_tags_on_screen():
    """屏幕上真读得到：优先级标记、标题、人类可读的截止时间、标签（验收标准 1）。"""
    fake = backend()
    fake.add_task("交季度报告", list_name="work", id="t9", due=at(14, 20, 0), priority=5, tags=("周报",))
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_work(pilot, app)
        text = screen_text(app)

    row = line_with(text, "交季度报告")
    assert row.lstrip().startswith(f"{theme.CURSOR_MARK} ! 交季度报告") or "! 交季度报告" in row
    assert row.rstrip().endswith("今天 20:00"), f"截止时间要读成人类可读的样子：{row!r}"
    assert "#周报" in row


async def test_a_row_without_a_due_date_does_not_look_like_one_due_today():
    """「没有截止时间」与「今天截止」一眼可分（验收标准 3、用户故事 39）。"""
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_work(pilot, app)
        text = screen_text(app)

    undated = line_with(text, "没日期的")
    today = line_with(text, "今天要做的")

    assert undated.rstrip().endswith("-"), f"没有日期读作一道短横：{undated!r}"
    assert "今天" not in undated
    assert today.rstrip().endswith("今天 18:00")


async def test_an_all_day_task_reads_as_today_and_never_with_a_time():
    """全天任务读作「今天」，绝不读作「今天 00:00」（验收标准 2）。"""
    fake = backend()
    fake.add_task("今天全天的", list_name="work", id="t9", due=at(14, 0, 0), all_day=True)
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_work(pilot, app)
        text = screen_text(app)

    row = line_with(text, "今天全天的")
    assert row.rstrip().endswith("今天"), f"全天任务只给日词：{row!r}"
    assert "00:00" not in row


# ------------------------------------------------------------------ 清单名


def two_lists() -> FakeBackend:
    """两个清单各一条今天到期的任务：进视图看得到清单名，进清单看不到。"""
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task("写周报", list_name="work", id="t1", due=at(14, 18, 0))
    fake.add_list("生活", id="life")
    fake.add_task("交水费", list_name="life", id="t2", due=at(14, 20, 0))
    return fake


async def enter_container(pilot, app: DidaApp, container_id: str) -> None:
    """走到清单列表页的某一行上，按 ``enter`` 进去。"""
    for _ in range(20):
        if app.index_page().selected_id == container_id:
            break
        await pilot.press("j")
    assert app.index_page().selected_id == container_id
    await pilot.press("enter")
    await pilot.pause()


async def test_a_view_row_says_which_list_the_task_belongs_to():
    """视图不是容器：行里必须写清这条任务属于哪个清单（验收标准：视图里显示）。"""
    app = DidaApp(two_lists())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_container(pilot, app, "today")
        text = screen_text(app)

    assert "工作" in line_with(text, "写周报")
    assert "生活" in line_with(text, "交水费")


async def test_a_list_row_does_not_repeat_the_list_name():
    """清单是容器：行里再写一遍清单名，就是把同一个名字重复一百遍（验收标准：清单里不重复）。"""
    app = DidaApp(two_lists())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_container(pilot, app, "work")
        text = screen_text(app)

    row = line_with(text, "写周报")
    assert "工作" not in row, f"清单视图里的行不许再写清单名：{row!r}"
    assert row.rstrip().endswith("今天 18:00")


# ------------------------------------------------------------------ 窄终端


async def test_at_thirty_columns_the_title_survives_and_the_due_time_is_dropped():
    """窄终端先丢注解、最后才截标题——用户报的那个「标题只剩四个字」的正是反面。

    原型 30 列实测是「``~ 回邮件给 … 3 天前``」：标题被砍到四个字，截止时间一格不少。
    现在反过来：标题一个字都不少，截止时间先走。
    """
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task("回邮件给产品经理", list_name="work", id="t1", due=at(13, 18, 0))
    app = DidaApp(fake)

    async with app.run_test(size=(30, 12)) as pilot:
        await pilot.pause()
        await enter_work(pilot, app)
        text = screen_text(app)

    row = line_with(text, "回邮件给产品经理")
    assert "回邮件给产品经理" in row, f"标题被截了：{row!r}"
    assert theme.ELLIPSIS not in row, f"标题一个字都没丢，不该有省略号：{row!r}"
    assert "18:00" not in row and "昨天" not in row, f"截止时间该先走：{row!r}"
    assert cell_len(row) <= 30, "这一行不许超出终端宽度"
    assert len([line for line in lines(text) if "回邮件" in line]) == 1, "一行仍然只放一条任务"


# ------------------------------------------------------------------ 已完成


async def test_completed_rows_are_struck_through_and_sunk_below_the_unfinished_ones():
    """已完成的划掉显示、沉到列表最底（验收标准：划掉 + 沉底，用户故事 54/55）。"""
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task("没做完的", list_name="work", id="t1", due=at(14, 18, 0))
    fake.add_task("做完了的", list_name="work", id="t2", completed=True, completed_at=at(14, 11, 0))
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_work(pilot, app)
        styled = screen_styled_text(app)
        rows = lines(screen_text(app))

    done = line_with(styled, "做完了的")
    assert theme.DONE_MARK in done, f"已完成的行有自己的前缀记号：{done!r}"
    assert STRIKE in sgr_parameters(style_before(done, "做完了的")), f"没划掉：{done!r}"

    assert next(i for i, line in enumerate(rows) if "没做完的" in line) < next(
        i for i, line in enumerate(rows) if "做完了的" in line
    ), "已完成的要沉在未完成下面"


def recently_completed_view() -> FakeBackend:
    """一份缓存 + 一个自建视图：视图里**含已完成成员**（#36 的「最近完成」那个例子）。

    视图不是容器，它的成员由求值给：已完成的那条与未完成的落在**同一份成员**里，而真实清单
    的已完成那几条来自读模型的已完成区（窗口 + ``CompletedItem``）。两种容器于是走的是两段
    不同的数据，这一条摆的正是前一种。
    """
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task("没做完的", list_name="work", id="t1", due=at(14, 18, 0))
    fake.add_task("做完了的", list_name="work", id="t2", completed=True, completed_at=at(14, 11, 0))
    fake.add_view("最近完成", id="recent", completion=Completion.COMPLETED, completed_days=7)
    return fake


async def test_a_completed_row_in_a_view_is_struck_through_like_one_in_a_list():
    """视图里的已完成行照样划掉显示（用户故事 54）：算不算已完成跟着**行**走，不跟着它从哪一段出来走。

    同一句话在真实清单里是对的（那一条测试在上面），在视图里必须一样对——「已完成」是这一
    **行**的事实，不是「它恰好从哪一段取出来」的推论。
    """
    app = DidaApp(recently_completed_view())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_container(pilot, app, "recent")
        styled = screen_styled_text(app)

    done = line_with(styled, "做完了的")
    assert theme.DONE_MARK in done, f"已完成的行有自己的前缀记号：{done!r}"
    assert STRIKE in sgr_parameters(style_before(done, "做完了的")), f"视图里没划掉：{done!r}"


async def test_a_completed_row_in_a_view_sinks_below_the_unfinished_ones():
    """已完成沉底在视图里也成立（用户故事 55）：顺序归读模型的求值，页面只照画。

    清单里那一条（上面）沉底靠的是「未完成一段 + 已完成区」这个拼接；视图里两段是**同一
    份成员**，沉底只能靠求值那份顺序（``order_key`` 的已完成那一档）。同一句话，两处来源，
    所以两处各断一次。
    """
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task("没做完的", list_name="work", id="t1", due=at(14, 18, 0))
    fake.add_task("做完了的", list_name="work", id="t2", completed=True, completed_at=at(14, 11, 0))
    fake.add_view("全部任务", id="everything", completion=Completion.ANY)
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_container(pilot, app, "everything")
        rows = lines(screen_text(app))

    assert next(i for i, line in enumerate(rows) if "没做完的" in line) < next(
        i for i, line in enumerate(rows) if "做完了的" in line
    ), "视图里已完成的也要沉在未完成下面"


async def test_a_task_completed_more_than_seven_days_ago_does_not_take_the_screen():
    """只显示最近 7 天完成的：更早的不占屏幕（验收标准：窗口默认值已改成 7 天）。"""
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task("六天前做完的", list_name="work", id="t1", completed=True, completed_at=at(8, 12, 0))
    fake.add_task("八天前做完的", list_name="work", id="t2", completed=True, completed_at=at(6, 12, 0))
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_work(pilot, app)
        text = screen_text(app)

    assert "六天前做完的" in text
    assert "八天前做完的" not in text


async def test_the_completed_rows_on_screen_follow_the_normal_sort_key():
    """真实清单的已完成段在屏幕上也按正常排序键排（工单 #64 验收标准 1、3）。

    两条的完成时刻与截止时间是**反的**：「晚截止的」刚做完，「早截止的」更早完成。按完成
    时刻倒序（旧行为）屏幕上是 晚截止的 / 早截止的；按正常排序键是 早截止的 / 晚截止的。
    两条都仍要沉在未完成那条下面。
    """
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task("没做完的", list_name="work", id="t1", due=at(14, 18, 0))
    fake.add_task(
        "晚截止的", list_name="work", id="t2", due=at(20, 9, 0),
        completed=True, completed_at=at(14, 12, 0),
    )
    fake.add_task(
        "早截止的", list_name="work", id="t3", due=at(16, 9, 0),
        completed=True, completed_at=at(14, 10, 0),
    )
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_work(pilot, app)
        rows = lines(screen_text(app))

    def row_of(needle: str) -> int:
        return next(i for i, line in enumerate(rows) if needle in line)

    assert row_of("早截止的") < row_of("晚截止的"), "已完成段要按截止时间升序，不是完成时刻倒序"
    assert row_of("没做完的") < row_of("早截止的"), "已完成的仍要沉在未完成下面"


# ------------------------------------------------------------------ 光标跨刷新


async def test_a_background_refresh_keeps_the_cursor_on_the_same_task():
    """后台刷新之后光标仍在原来那一条上（验收标准；用户故事 21/57）。

    刷新会重排整份行（新任务可能排到光标前面、已完成的那条会冒出来），所以「光标还在
    原来那条」只能按**行 id** 认回来，不能按行号。
    """
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task("写周报", list_name="work", id="t1", due=at(14, 18, 0))
    fake.add_task("交水费", list_name="work", id="t2", due=at(14, 20, 0))
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_work(pilot, app)
        await pilot.press("j")  # 光标走到第二条（交水费）
        assert app.tasks_page().selected_id == "t2"

        # 后台刷新落地：一条更早的排到了光标前面，另一条在别处完成了。
        fake.add_task("更早的一条", list_name="work", id="t3", due=at(14, 1, 0))
        fake.add_task("刚做完的", list_name="work", id="t4", completed=True, completed_at=at(14, 12, 0))
        app.refresh_view()
        await pilot.pause()  # 让这一帧重排完（行的条数变了，正文的高度跟着变）
        text = screen_text(app)
        selected = app.tasks_page().selected_id

    assert selected == "t2", "光标被刷新踢到别的任务上了"
    row = line_with(text, "交水费")
    assert row.lstrip().startswith(theme.CURSOR_MARK), f"光标记号不在原来那一条上：{row!r}"
    assert "更早的一条" in text, "新来的那条排到了前面，但光标不许跟着走"


async def test_resizing_the_window_keeps_the_cursor_and_re_lays_the_row():
    """窗口变窄：行按新宽度重排（注解块贴右边缘），光标仍停在同一条任务上。

    行是**按格**排出来的，所以宽度一变就得重排——重排按行 id 认光标，不按行号。
    """
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task("写周报", list_name="work", id="t1", due=at(14, 18, 0), tags=("周报",))
    fake.add_task("交水费", list_name="work", id="t2", due=at(14, 20, 0))
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_work(pilot, app)
        await pilot.press("j")
        assert app.tasks_page().selected_id == "t2"

        await pilot.resize_terminal(30, 24)
        await pilot.pause()
        narrow = screen_text(app)
        selected = app.tasks_page().selected_id

    assert selected == "t2", "缩放把光标踢走了"
    row = line_with(narrow, "交水费")
    assert row.lstrip().startswith(theme.CURSOR_MARK), f"光标记号不在原来那一条上：{row!r}"
    assert row.rstrip().endswith("今天 20:00"), f"注解块按新宽度重新贴到右边缘：{row!r}"
    assert cell_len(row) == 30, f"这一行要正好铺满 30 列（现在是 {cell_len(row)} 格）：{row!r}"

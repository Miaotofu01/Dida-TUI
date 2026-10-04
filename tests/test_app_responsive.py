"""接缝一：窄屏降级、详情浮层与键位帮助（工单 #18）。

用 Textual 自带的 Pilot 驱动真实 app：断言只落在「屏幕上有什么」与「按键之后变了什么」，
不做整屏快照、不碰私有属性。
"""

import re
from datetime import datetime, timedelta, timezone

import pytest
from rich.text import Text

from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp
from dida.tui.panes import HelpScreen, ListsScreen, TaskPane
from support import screen_text

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)

WIDE = 110
"""三档的分界：≥110 三栏常驻；80–109 收起右栏；<80 再收起左栏。"""


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


def make_backend() -> FakeBackend:
    """三条任务：一条逾期、一条今日、一条没日期（详情栏要铺开的字段都在里面）。"""
    backend = FakeBackend(clock=ManualClock(T0), day_end="24:00")
    backend.add_task("交季度报告", list_name="工作", due=at(11, 9, 0), priority=5)
    backend.add_task("写周报", list_name="工作", due=at(14, 18, 0))
    backend.add_task("买牛奶", list_name="生活")
    backend.set_sync_state(last_refresh_at=at(14, 12, 0), pending_count=0)
    return backend


def visible_panes(app: DidaApp) -> tuple[bool, bool, bool]:
    """三栏各在不在屏上（左 / 中 / 右）。"""
    return tuple(
        app.query_one(f"#{name}").display for name in ("list-pane", "task-pane", "detail-pane")
    )  # type: ignore[return-value]


def overlay_text(app: DidaApp) -> str:
    """当前浮层盒子里的正文。

    浮层是叠在下面那一屏上的，``screen_text`` 里两屏的字混在同样的行上，分不出哪半句是谁
    的（下面那一屏的任务行还在两边露着）。断「浮层里写的是哪一条」要看浮层自己拿到的正文：
    :attr:`~dida.tui.panes.OverlayBox.body`，那是 app 交给它的那一份，原样。
    """
    body = app.screen.body
    return body.plain if isinstance(body, Text) else str(body)


@pytest.mark.parametrize(
    ("width", "expected"),
    [
        (120, (True, True, True)),
        (WIDE, (True, True, True)),
        (109, (True, True, False)),
        (80, (True, True, False)),
        (79, (False, True, False)),
        (60, (False, True, False)),
    ],
)
async def test_the_width_tier_decides_which_panes_are_on_screen(width, expected):
    """宽度分档：≥110 三栏；80–109 收起右栏；<80 连左栏一起收起。

    分界线上两边都测（109/110、79/80）：档位差一列就换一套布局，边界正是最容易写错的
    那一列。
    """
    app = DidaApp(make_backend())

    async with app.run_test(size=(width, 30)) as pilot:
        await pilot.pause()
        assert visible_panes(app) == expected, f"{width} 列时三栏的收放不对"


async def test_resizing_re_tiers_without_a_restart():
    """宽度变了就重新分档（验收标准 #7）：一路变窄再变回来，三栏自己回来。

    不重启是这一条的全部意思：用户拖一次窗口就得重开一个 app 的话，窄屏降级等于没有。
    """
    app = DidaApp(make_backend())

    async with app.run_test(size=(WIDE, 30)) as pilot:
        await pilot.pause()
        assert visible_panes(app) == (True, True, True)

        await pilot.resize_terminal(95, 30)
        await pilot.pause()
        assert visible_panes(app) == (True, True, False), "窄了要收起右栏"

        await pilot.resize_terminal(70, 30)
        await pilot.pause()
        assert visible_panes(app) == (False, True, False), "更窄连左栏也收起"

        await pilot.resize_terminal(120, 30)
        await pilot.pause()
        assert visible_panes(app) == (True, True, True), "宽回来三栏自己回来"


async def test_enter_opens_the_detail_overlay_where_the_right_pane_is_dropped():
    """80–109 列：右栏不在屏上，``Enter`` 把它作为浮层打开（验收标准 #2、#4）。

    浮层里必须是**光标下那一条**：右栏被收起只是看不见，不是换了一条任务。
    """
    app = DidaApp(make_backend())

    async with app.run_test(size=(95, 30)) as pilot:
        await pilot.pause()
        assert "截止" not in screen_text(app), "右栏收起了，详情不在屏上"

        await pilot.press("enter")
        await pilot.pause()
        assert "截止" in screen_text(app), "Enter 该把详情弹到屏幕上"
        body = overlay_text(app)
        assert "交季度报告" in body, "浮层里是光标下那一条"
        assert "清单  工作" in body
        assert "优先级  !" in body

        await pilot.press("escape")
        await pilot.pause()
        assert "截止" not in screen_text(app), "Esc 收起浮层"


async def test_enter_again_closes_the_detail_overlay():
    """``Enter`` 是**开合**右栏详情：浮层开着的时候再按一次就收起来（验收标准 #4）。"""
    app = DidaApp(make_backend())

    async with app.run_test(size=(95, 30)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert "截止" in screen_text(app)

        await pilot.press("enter")
        await pilot.pause()
        assert "截止" not in screen_text(app), "再按一次 Enter 该收起来"


async def test_the_wide_tier_keeps_the_right_pane_and_enter_toggles_it():
    """≥110 列三栏常驻；``Enter`` 的开合在这里就是右栏在不在屏上（验收标准 #1、#4）。"""
    app = DidaApp(make_backend())

    async with app.run_test(size=(WIDE, 30)) as pilot:
        await pilot.pause()
        assert visible_panes(app) == (True, True, True)

        await pilot.press("enter")
        await pilot.pause()
        assert visible_panes(app) == (True, True, False), "Enter 收起右栏"

        await pilot.press("enter")
        await pilot.pause()
        assert visible_panes(app) == (True, True, True), "再按一次 Enter 右栏回来"


async def test_the_detail_pane_shows_the_fields_of_the_selected_task():
    """验收标准 #5：右栏铺开当前任务的字段，光标一动就换成下一条。

    断的是**视图模型里的成品**（清单名、优先级标记、人类可读的截止时间）——右栏不重算
    这几样，也不该重算；字段对得上就说明它老实照着引擎给的画。
    """
    app = DidaApp(make_backend())

    async with app.run_test(size=(WIDE, 30)) as pilot:
        await pilot.pause()
        pane = app.query_one(TaskPane)
        first = pane.selected_item
        assert first is not None

        text = screen_text(app)
        assert first.title in text
        assert f"清单  {first.list_name}" in text
        assert f"优先级  {first.priority_mark}" in text
        assert f"截止  {first.due_text}" in text, "逾期那一条的读法由引擎给，右栏照抄"

        await pilot.press("j")
        await pilot.pause()
        second = pane.selected_item
        assert second is not None and second.title != first.title

        text = screen_text(app)
        assert second.title in text
        assert f"清单  {second.list_name}" in text
        assert f"截止  {second.due_text}" in text, "右栏跟着光标换成下一条"


async def test_the_detail_pane_says_so_when_nothing_is_selected():
    """一条都没选中时右栏说实话（过滤到空）：占位不能是「（未接入）」那种假话。"""
    app = DidaApp(make_backend())

    async with app.run_test(size=(WIDE, 30)) as pilot:
        await pilot.pause()
        await pilot.press("/")
        await pilot.press(*"zzz")
        await pilot.pause()
        assert "（没有选中任务）" in screen_text(app)


async def test_the_narrow_tier_puts_the_lists_behind_an_overlay():
    """<80 列：左栏也收起，清单通过浮层看（验收标准 #3）。

    断条数是有意的：清单名（工作/生活）本来就在任务行里跟着任务一起出现，只有「名字 +
    未完成条数」这一种写法出现在清单浮层上。
    """
    app = DidaApp(make_backend())

    async with app.run_test(size=(70, 30)) as pilot:
        await pilot.pause()
        assert visible_panes(app) == (False, True, False)
        assert not isinstance(app.screen, ListsScreen), "没按 l 之前没有浮层"

        await pilot.press("l")
        await pilot.pause()
        assert isinstance(app.screen, ListsScreen), "l 该把清单弹出来"
        rows = overlay_text(app)
        assert "工作  2" in rows, "浮层里是清单与未完成条数"
        assert "生活  1" in rows

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ListsScreen), "Esc 收起浮层"
        assert visible_panes(app) == (False, True, False), "收起后还是窄档那一屏"


async def test_the_narrow_tier_still_opens_the_detail_overlay():
    """<80 列：中栏还在，``Enter`` 照旧弹详情——降级的是布局，不是这条语义。"""
    app = DidaApp(make_backend())

    async with app.run_test(size=(70, 30)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        text = screen_text(app)
        assert "截止" in text and "交季度报告" in text, "窄档下 Enter 还是打开详情"


SIBLING_KEYS = ("x", "g", "G", "e", "p", "a", "d", "/", "c", "o", "q")
"""兄弟工单已经绑上的键：帮助里一个都不能漏（漏了就等于那个功能不存在）。"""


async def test_question_mark_opens_the_key_help_and_escape_closes_it():
    """验收标准 #6：``?`` 打开键位帮助浮层，``Esc`` 关闭。

    键位表是这些键**唯一**被写下来的地方：footer 只显示得下头几个，而且它只显示
    ``DidaApp`` 自己绑的键——``c``（已完成区）是任务列绑的，footer 根本不提它。
    """
    app = DidaApp(make_backend())

    async with app.run_test(size=(WIDE, 30)) as pilot:
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen), "没按 ? 之前没有帮助浮层"

        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen), "? 该打开键位帮助"
        rows = overlay_text(app)
        for key in SIBLING_KEYS:
            assert re.search(rf"^{re.escape(key)}\s+\S", rows, re.M), f"帮助里没有 {key} 这一行"
        assert re.search(r"^q\s+退出", rows, re.M), "q 那一行说的是它干什么"

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen), "Esc 该关掉帮助"
        assert visible_panes(app) == (True, True, True), "关掉帮助后还是原来那一屏"


async def test_the_help_overlay_opens_in_the_narrow_tier_too():
    """窄屏里更要有键位表：footer 在 70 列下连一半键都显示不出来。"""
    app = DidaApp(make_backend())

    async with app.run_test(size=(70, 30)) as pilot:
        await pilot.pause()
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        assert re.search(r"^x\s+\S", overlay_text(app), re.M)

        await pilot.press("escape")
        await pilot.pause()
        assert visible_panes(app) == (False, True, False), "关掉帮助还是窄档那一屏"


async def test_the_overlay_opens_on_the_task_the_cursor_is_on():
    """验收标准 #5 在收起右栏的档位里：光标先动、浮层再开，开的是当前那一条。

    右栏收起只是看不见，不是停止跟着光标——不然 Enter 弹出来的会是别的任务。
    """
    app = DidaApp(make_backend())

    async with app.run_test(size=(95, 30)) as pilot:
        await pilot.pause()
        await pilot.press("j")
        await pilot.pause()
        selected = app.query_one(TaskPane).selected_title
        assert selected is not None and selected != "交季度报告"

        await pilot.press("enter")
        await pilot.pause()
        body = overlay_text(app)
        assert selected in body
        assert "交季度报告" not in body, "浮层里是光标下那一条，不是第一条"

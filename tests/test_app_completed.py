"""接缝一：已完成区的展开收起（t12）。

用 Textual 自带的 ``Pilot`` 驱动真 app + 内存假后端，断言只落在「屏幕文本」与
「假后端收到的调用」上：不碰私有属性，不做整屏快照。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dida.sync.engine import CompletedItem, CompletedSection
from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp
from dida.tui.panes import TaskPane, completed_header, completed_line
from support import screen_text

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


def make_backend() -> FakeBackend:
    """一条未完成任务 + 两条窗口内完成的任务（其中一条是「手机上做完的」）。"""
    backend = FakeBackend(clock=ManualClock(T0))
    backend.add_task("写周报", list_name="工作", due=at(14, 18, 0))
    backend.add_task(
        "手机上做完的", list_name="工作", completed=True, completed_at=at(14, 11, 30)
    )
    backend.add_task("昨晚做完的", list_name="生活", completed=True, completed_at=at(13, 20, 0))
    return backend


async def test_the_completed_section_starts_collapsed_with_its_count():
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        text = screen_text(app)

    assert "已完成 2 项" in text
    assert "手机上做完的" not in text, "默认收起：标题在，行不在"


async def test_c_expands_and_collapses_the_completed_section():
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        expanded = screen_text(app)
        await pilot.press("c")
        await pilot.pause()
        collapsed = screen_text(app)

    assert "手机上做完的" in expanded
    assert "昨晚做完的" in expanded
    assert "已完成 2 项" in expanded
    assert "手机上做完的" not in collapsed, "再按一次该收起来"
    assert "已完成 2 项" in collapsed


async def test_the_completed_section_sits_below_the_today_groups():
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        text = screen_text(app)

    assert text.index("── 今日") < text.index("已完成 2 项") < text.index("手机上做完的")


async def test_a_window_without_completed_tasks_shows_no_section_at_all():
    backend = FakeBackend(clock=ManualClock(T0))
    backend.add_task("写周报", list_name="工作", due=at(14, 18, 0))
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        text = screen_text(app)

    assert "已完成 0 项" not in text, "窗口里没有完成的任务就别摆一个空区"
    assert "▸ 已完成" not in text


async def test_completing_everything_still_says_there_is_nothing_left():
    """只剩已完成区时，空状态那句话不能说没了：这一屏是「今天该做什么」。"""
    backend = FakeBackend(clock=ManualClock(T0))
    backend.add_task(
        "手机上做完的", list_name="工作", completed=True, completed_at=at(14, 11, 30)
    )
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        text = screen_text(app)

    assert "（今天没有未完成的任务）" in text
    assert "已完成 1 项" in text


def test_completed_rows_are_dim_and_struck_through():
    """已完成行：暗灰 + 删除线（用 rich 的样式名，不写死颜色）。"""
    line = completed_line(
        CompletedItem(
            task_id="t2",
            title="手机上做完的",
            list_name="工作",
            completed_at=at(14, 11, 30),
            completed_text="今天 11:30",
        )
    )

    assert "dim" in str(line.style)
    assert "strike" in str(line.style)
    assert line.plain.endswith("手机上做完的  工作  今天 11:30")


def test_the_completed_header_shows_the_count_and_the_folded_state():
    section = CompletedSection(
        items=(
            CompletedItem(
                task_id="t2",
                title="手机上做完的",
                list_name="工作",
                completed_at=at(14, 11, 30),
                completed_text="今天 11:30",
            ),
        )
    )

    folded = completed_header(section, expanded=False)
    unfolded = completed_header(section, expanded=True)

    assert "已完成 1 项" in folded.plain
    assert "已完成 1 项" in unfolded.plain
    assert folded.plain != unfolded.plain, "展开与收起的标记要能看出区别"


async def test_the_toggle_keeps_its_state_when_the_view_is_redrawn():
    """重画（t09 的全量刷新之后）不该把用户摊开的那一区又收回去。"""
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        app.refresh_view()
        await pilot.pause()
        text = screen_text(app)
        selected = app.query_one(TaskPane).selected_task_id

    assert "手机上做完的" in text
    assert selected == "t1", "已完成区不参与光标移动"

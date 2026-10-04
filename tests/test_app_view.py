"""接缝一：TUI 应用 + 内存假后端，用 Textual 自带的 Pilot 驱动。

断言只落在「屏幕文本」和「假后端收到的调用」上，不做整屏快照，不碰私有属性。
"""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dida.sync.engine import GroupKind, TaskGroup
from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp
from dida.tui.panes import ListPane, TaskPane, group_header
from support import screen_text

ROOT = Path(__file__).resolve().parents[1]

HEX_COLOUR = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8}|[0-9a-fA-F]{3})(?![0-9a-fA-F])")

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


def make_backend() -> FakeBackend:
    """一屏有代表性的缓存：逾期两条、今日三条（含全天与无日期）、未来的与已完成的不该出现。"""
    backend = FakeBackend(clock=ManualClock(T0), day_end="24:00")
    backend.add_task("交季度报告", list_name="工作", due=at(11, 9, 0), priority=5)
    backend.add_task("还信用卡", list_name="生活", due=at(13), all_day=True)
    backend.add_task("修水龙头", list_name="生活", due=at(14), all_day=True)
    backend.add_task("写周报", list_name="工作", due=at(14, 18, 0))
    backend.add_task("买牛奶", list_name="生活")
    backend.add_task("下周再审", list_name="工作", due=at(20, 9, 0))
    backend.add_task("已做完", list_name="工作", due=at(14, 9, 0), completed=True)
    backend.set_sync_state(last_refresh_at=at(14, 12, 0), pending_count=2)
    return backend


async def test_task_pane_pins_overdue_on_top_with_counts_in_the_headers():
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        text = screen_text(app)

    assert "── 逾期 · 2 项" in text
    assert "── 今日 · 3 项" in text
    assert text.index("── 逾期") < text.index("── 今日")


async def test_left_pane_shows_every_list_with_its_unfinished_badge():
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        text = screen_text(app)

    # 工作 3 条未完成里，下周那条不在今日区，但徽标统计的是清单的未完成总数
    assert "工作  3" in text
    assert "生活  3" in text


async def test_task_rows_read_human_due_times_and_never_an_iso_string():
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        text = screen_text(app)

    assert "交季度报告" in text and "3 天前" in text
    assert "还信用卡" in text and "昨天" in text
    assert "写周报" in text and "今天 18:00" in text
    assert re.search(r"\d{4}-\d{2}-\d{2}", text) is None, "任务行不该出现 ISO 日期串"


async def test_all_day_task_reads_as_today_and_never_as_midnight():
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        text = screen_text(app)

    assert "修水龙头" in text
    assert "00:00" not in text


async def test_no_due_date_looks_different_from_due_today():
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        text = screen_text(app)

    assert "买牛奶  生活  —" in text
    assert "修水龙头  生活  今天" in text


async def test_rows_show_the_priority_mark_the_title_and_the_list_name():
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        text = screen_text(app)

    assert "! 交季度报告  工作" in text
    assert "· 买牛奶  生活" in text


async def test_status_bar_shows_the_last_refresh_time_and_the_logical_day():
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        text = screen_text(app)

    assert "已同步 12:00 · 待推送 2 · 逻辑日 03-14" in text


async def test_empty_cache_renders_an_empty_state_instead_of_failing():
    app = DidaApp(FakeBackend(clock=ManualClock(T0)))

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        text = screen_text(app)

    assert "（还没有清单）" in text
    assert "（今天没有未完成的任务）" in text


async def test_tab_switches_the_focus_between_the_list_pane_and_the_task_pane():
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        assert isinstance(app.focused, TaskPane), "启动就该落在任务列上，j/k 立刻能用"

        await pilot.press("tab")
        await pilot.pause()
        assert isinstance(app.focused, ListPane)

        await pilot.press("tab")
        await pilot.pause()
        assert isinstance(app.focused, TaskPane)


async def test_j_k_and_the_arrow_keys_move_the_cursor_and_only_the_focused_pane():
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        task_pane = app.query_one(TaskPane)
        list_pane = app.query_one(ListPane)
        assert task_pane.selected_task_id == "t1"
        assert "❯ ! 交季度报告  工作  3 天前" in screen_text(app)

        await pilot.press("j")
        await pilot.pause()
        assert task_pane.selected_task_id == "t2"
        assert "❯ · 还信用卡  生活  昨天" in screen_text(app)
        assert "❯ ! 交季度报告" not in screen_text(app)

        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("up")
        await pilot.pause()
        assert task_pane.selected_task_id == "t3"

        await pilot.press("tab")
        await pilot.press("j")
        await pilot.pause()
        assert list_pane.selected_list_id == "生活"
        assert task_pane.selected_task_id == "t3", "焦点在左栏时任务光标不该动"


async def test_the_cursor_stops_at_both_ends():
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        task_pane = app.query_one(TaskPane)

        await pilot.press("k")
        await pilot.pause()
        assert task_pane.selected_task_id == "t1"

        for _ in range(10):
            await pilot.press("j")
        await pilot.pause()
        assert task_pane.selected_task_id == "t5", "今日区最后一行是那条没有截止时间的任务"


def test_the_overdue_group_header_is_red_and_today_is_not():
    overdue = group_header(TaskGroup(kind=GroupKind.OVERDUE, items=()))
    today = group_header(TaskGroup(kind=GroupKind.TODAY, items=()))

    assert overdue.plain == "── 逾期 · 0 项"
    assert "red" in str(overdue.style)
    assert "red" not in str(today.style)


def test_the_tui_never_hardcodes_a_hex_colour():
    """只用终端 16 色：源码里不许出现 #rrggbb 之类的颜色字面量。"""
    offenders = [
        str(path.relative_to(ROOT))
        for path in sorted((ROOT / "src" / "dida" / "tui").rglob("*.py"))
        if HEX_COLOUR.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


async def test_the_cursor_stays_visible_in_a_list_longer_than_the_pane():
    backend = FakeBackend(clock=ManualClock(T0))
    for index in range(20):
        backend.add_task(f"任务{index}", list_name="工作", due=at(14, 9, 0))
    backend.add_task("目标行", list_name="工作", due=at(14, 20, 0))
    app = DidaApp(backend)

    async with app.run_test(size=(110, 12)) as pilot:
        await pilot.pause()
        for _ in range(20):
            await pilot.press("j")
        await pilot.pause()

        assert app.query_one(TaskPane).selected_task_id == "t21"
        assert "❯ · 目标行" in screen_text(app), "光标走到看不见的地方了"

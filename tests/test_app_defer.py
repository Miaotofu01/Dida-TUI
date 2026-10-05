"""接缝一：``g`` / ``G`` 两个键，用 Textual 自带的 Pilot 驱动。

断言只落在「假后端收到的调用」上：顺延的落点怎么算是引擎的事（``tests/test_sync_defer.py``
钉的就是那一份），TUI 这一层只把光标下那条任务交出去，不重算任何日界。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


def make_backend() -> FakeBackend:
    """凌晨两点的屏：边界 04:00，一条有截止时间、一条没有。"""
    backend = FakeBackend(clock=ManualClock(at(15, 2, 0)), day_end="04:00")
    backend.add_task("写周报", list_name="工作", due=at(14, 23, 0))
    backend.add_task("买牛奶", list_name="生活")
    return backend


async def test_g_defers_the_task_under_the_cursor_to_the_next_logical_day():
    backend = make_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("g")
        await pilot.pause()

    assert backend.deferred == ["t1"]
    assert backend.deferred_days == [1]


async def test_shift_g_defers_to_the_same_weekday_next_week():
    backend = make_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("j")  # 光标移到第二条
        await pilot.press("G")
        await pilot.pause()

    assert backend.deferred == ["t2"], "顺延的是光标下那条，不是第一条"
    assert backend.deferred_days == [7]


async def test_the_defer_keys_do_nothing_when_there_is_no_task_under_the_cursor():
    backend = FakeBackend(clock=ManualClock(at(15, 2, 0)), day_end="04:00")
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("g")
        await pilot.press("G")
        await pilot.pause()

    assert backend.deferred == [], "空屏上按键不许报错，也不许凭空顺延点什么"

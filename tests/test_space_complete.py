"""``Space`` 与 ``x`` 是同一个「完成」（规范键位表；评审修 A1）。

规范键位表写的是一格两键：``| `x` / `Space` | 完成（立即推送） |``。两个键走**同一个
动作**，因此也吃同一份防误按约束——完成的补偿只有「别按错」与「按下去看得见」
（ADR-0002），多绑一个键不许把这两样弄松。``tests/test_complete.py`` 里那两条
「完成键不在导航键旁边 / 不在光标键位组里」的测试因此自动把 ``space`` 也罩住了。

接缝一（主）：``DidaApp`` + ``FakeBackend``，Pilot 按 ``space`` → 断言假后端收到
``complete`` 调用。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


def make_backend() -> FakeBackend:
    """两条今天的任务：光标停在第一条上。"""
    backend = FakeBackend(clock=ManualClock(T0))
    backend.add_task("交季度报告", list_name="工作", id="t1", due=at(14, 9, 0))
    backend.add_task("写周报", list_name="工作", id="t2", due=at(14, 18, 0))
    return backend


async def test_pressing_space_completes_the_task_under_the_cursor():
    """接缝一：按 ``space`` → 假后端收到 complete 调用（与按 ``x`` 同一条路）。"""
    backend = make_backend()
    app = DidaApp(backend)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()

    assert backend.completed == ["t1"], "Space 与 x 都完成光标下那一条"


def test_space_is_bound_to_the_same_action_as_x():
    """键位表那一格的两个键都真的绑在 ``complete`` 上。"""
    keys = {binding.key for binding in DidaApp.BINDINGS if binding.action == "complete"}

    assert {"x", "space"} <= keys, f"完成只绑了 {sorted(keys)}"

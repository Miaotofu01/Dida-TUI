"""接缝一：三栏外壳 + 状态栏，用 Textual 的 Pilot 驱动真实 app。"""

from datetime import datetime, timedelta, timezone

from dida.sync.engine import SyncEngine
from dida.testing import ManualClock
from dida.tui.app import DidaApp
from support import screen_text

T0 = datetime(2026, 3, 14, 12, 3, tzinfo=timezone(timedelta(hours=8)))


def make_app() -> DidaApp:
    return DidaApp(SyncEngine(clock=ManualClock(T0)))


async def test_shell_renders_three_panes_and_the_status_bar():
    app = make_app()

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        text = screen_text(app)

    assert "清单" in text
    assert "今日" in text
    assert "详情" in text
    assert "已同步 — · 待推送 0 · 逻辑日 —" in text


async def test_q_quits_the_app():
    app = make_app()

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("q")
        await pilot.pause()
        assert app.is_running is False

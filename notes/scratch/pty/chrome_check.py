"""Independent check: chrome is exactly two rows (top bar + status bar), no Footer."""
import asyncio, sys
sys.path.insert(0, "/home/tofu/dida-v2-worktrees/integration/src")
sys.path.insert(0, "/home/tofu/dida-v2-worktrees/integration/tests")
from datetime import datetime, timedelta, timezone
from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp
from dida.tui import theme
from support import screen_text

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)

def backend():
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work", group_id="g1")
    fake.add_task("写周报", list_name="work", id="t1")
    fake.set_sync_state(last_refresh_at=T0, pending_count=0)
    return fake

async def main():
    app = DidaApp(backend())
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        text = screen_text(app)
        rows = text.splitlines()
        print("screen rows:", len(rows))
        print("row0 :", repr(rows[0]))
        print("row-1:", repr(rows[-1]))
        print("row-2:", repr(rows[-2]))
        print("Footer widgets:", len(app.query("Footer")))
        print("#top-bar:", len(app.query("#top-bar")), "size:", app.query_one("#top-bar").size)
        print("#status-bar:", len(app.query("#status-bar")), "size:", app.query_one("#status-bar").size)
        print("#page-body rows occupied: stage height:", app.query_one("#stage").size)
        # chrome rows = rows that are neither page body nor blank separators
        print("wordmark accent in top bar:", theme.CSS_ACCENT)
        print("page body bg:", theme.CSS_PAGE, "overlay surface:", theme.CSS_SURFACE)
        # confirm the top bar carries breadcrumb 'dida' wordmark and the last row the status trio
        print("top bar non-empty:", bool(rows[0].strip()))
        print("status row has 已同步:", "已同步" in rows[-1], "| 待推送:", "待推送" in rows[-1], "| 逻辑日:", "逻辑日" in rows[-1])

asyncio.run(main())

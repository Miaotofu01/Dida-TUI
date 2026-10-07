import asyncio, sys
sys.path.insert(0, "/home/tofu/dida-v2-worktrees/integration/src")
sys.path.insert(0, "/home/tofu/dida-v2-worktrees/integration/tests")
from datetime import datetime, timedelta, timezone
from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp

TZ = timezone(timedelta(hours=8)); T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
fake = FakeBackend(clock=ManualClock(T0)); fake.add_list("工作", id="work"); fake.add_task("写周报", list_name="work", id="t1")
app = DidaApp(fake)
async def main():
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        print("type(app._animate) =", type(app._animate).__name__, "| value =", app._animate)
        print("callable(app._animate) =", callable(app._animate))
        bar = app.query_one("#status-bar")
        try:
            app.animate("animation_level", 1.0, duration=0.1)
            print("app.animate(...) OK")
        except Exception as exc:
            print("app.animate(...) RAISED:", type(exc).__name__, exc)
asyncio.run(main())

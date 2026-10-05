"""任务列表页上的删除与顺延（工单 #40）：``d`` 先问一句，``g`` / ``G`` 顺延。

两个接缝：

- **接缝一**（内存 ``FakeBackend`` + ``run_test()`` pilot）：真按键、真看屏幕——``d`` 弹出来
  的那一句话、``y`` 与 ``n`` 各走到哪里、``g`` / ``G`` 交给引擎的是几个逻辑日。
- **接缝二**（可注入的 HTTP 传输层）：真 ``Store`` + 真 ``SyncEngine`` + 假传输，钉住删除的
  请求形状（``DELETE .../task/{taskId}``、没有请求体）与顺延「只改截止时间那一项」。

**顺延的算术不在这里**：落点由 ``sync/schedule.py`` 算，``tests/test_sync_defer.py`` 钉着它
（含 ``04:00`` 边界上凌晨两点那一条）。这一层只把 ``g`` / ``G`` 接上去，所以这里的断言是
「请求体里只多了截止时间那一项」——不是自己再把日期算一遍（那样两边一起错就一起绿）。

删除的确认文案有两条是文档级事实（``api-shapes.md`` §A6）：整份官方文档里**没有**回收站、
没有 undelete、也没有「已删除」列表，所以那一句话不许承诺任何恢复手段。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp
from support import screen_text

TZ = timezone(timedelta(hours=8))
WIDE = (100, 30)
"""一栏三层不需要降级，一个普通的终端尺寸就够。"""


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻（带时区）。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 3)


def backend() -> FakeBackend:
    """一份够用的缓存：「工作」里一条有截止时间的、一条没有的。"""
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task("写周报", list_name="work", id="t1", due=at(14, 18, 0))
    fake.add_task("交水费", list_name="work", id="t2")
    return fake


async def open_work(app: DidaApp, pilot) -> None:
    """把光标从收集箱走到「工作」上，``enter`` 进层二（任务列表页）。

    不按行数走：光标走到哪一行是清单列表页自己的事（收集箱在它前面），这里只认 id。
    """
    for _ in range(20):
        if app.index_page().selected_id == "work":
            break
        await pilot.press("j")
    assert app.index_page().selected_id == "work", "光标没能走到「工作」那一行"
    await pilot.press("enter")
    await pilot.pause()


# ------------------------------------------------------------------ d：删任务


async def test_d_asks_once_and_only_y_deletes_the_task():
    """``d`` 弹一次 ``y/n`` 确认；``y`` 才删那一条，``n`` 一条都不删（验收标准 1、3）。

    接缝一只能断到「引擎被叫去删哪一条」：``FakeBackend`` 与 ``complete`` / ``defer`` 同一
    口径，只记录、不动缓存，所以「删完那一行没了」在接缝二上断（真 ``Store``）。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_work(app, pilot)
        assert app.tasks_page().selected_id == "t1", "光标在第一条任务上"

        await pilot.press("d")
        await pilot.pause()
        asked = screen_text(app)

        await pilot.press("n")
        await pilot.pause()
        after_cancel = screen_text(app)

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        after_yes = screen_text(app)

    assert "删除「写周报」？" in asked, "问的是哪一条，写在最上面"
    assert "写周报" in after_cancel, "``n`` 之后什么都没发生"
    assert "删掉就找不回来了" not in after_yes, "``y`` 之后确认框自己收掉，回到任务列表页"
    assert fake.deleted == ["t1"], "只有 y 那一次真的删了"


# ------------------------------------------------------------------ g / G：顺延


async def test_g_defers_by_one_logical_day_and_G_by_a_week():
    """``g`` 顺延一个逻辑日、``G`` 顺延一周（验收标准 4、5）。

    接缝一断的是「哪一条任务、几个**逻辑日**」：落点怎么算是引擎的算术，
    ``tests/test_sync_defer.py`` 钉着它（含 ``04:00`` 边界上凌晨两点那一档）。这一页只说
    「往后几个逻辑日」，不自己算日期——它连时钟都没有。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_work(app, pilot)
        assert app.tasks_page().selected_id == "t1", "光标在第一条任务上"

        await pilot.press("g")
        await pilot.pause()
        await pilot.press("G")
        await pilot.pause()

    assert fake.deferred == ["t1", "t1"], "两次都落在光标那条任务上"
    assert fake.deferred_days == [1, 7], "g 是一个逻辑日、G 是一周"


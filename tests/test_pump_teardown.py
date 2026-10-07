"""关窗与「回来晚了」的那一次（#41 观察、#34 属地）——**这两半都要留着**。

缺陷的现场：周期泵的定时器句柄被丢掉、没有 ``on_unmount``，而 ``push_tick`` /
``refresh_view`` 在 ``await`` 之后直接 ``query_one(StatusBar)``；关窗那一刻正在飞的那一次
回来时 widget 已经拆了，于是 ``NoMatches`` 冒出来——生产里泵是每秒一跳，所以这是真 bug，
不是测试的洁癖。``Timer._tick`` 会把回调里的异常路由给 app 的 handler，于是它总是以**拆屏
期**的报错现身，离现场很远。

修法两半（#34 重写界面时必须保住）：

1. ``on_unmount`` 里把泵的定时器停掉（句柄留着，不丢）；
2. 每个 ``await`` 之后动 DOM 的地方先问 ``self.is_running``——**只**放过「屏幕已经不在跑」
   这一种，app 还在跑时状态栏不见了仍旧是 bug，照旧让它冒出来。

外加**第三件同样重要的事**：写要照旧落地。界面没了不代表用户那一下不算数——v1 用一次
子任务勾选钉的这句话（``test_a_toggle_that_lands_after_the_ui_is_gone_does_not_raise``），
v2 里子任务是只读的，所以这里改用**周期泵**这条活得下来的路重新表达。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from textual.css.query import NoMatches

from dida.api.client import DidaApiClient
from dida.api.errors import NetworkError
from dida.storage.store import Store
from dida.sync.engine import SyncEngine
from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp, StatusBar
from support import screen_text

TZ = timezone(timedelta(hours=8))
WIDE = (100, 30)


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 3)


def inbox() -> dict:
    return {"id": "inbox", "name": "收集箱", "sortOrder": 0}


def task(id: str = "t1", title: str = "写周报", **extra: object) -> dict:
    return {"id": id, "projectId": "inbox", "title": title, "status": 0, **extra}


class Server:
    """假服务端：可以摆一道闸门（慢慢答），也可以先断网后恢复。"""

    def __init__(self, *, error: Exception | None = None, gate: asyncio.Event | None = None) -> None:
        self.error = error
        self.gate = gate
        self.requests: list[httpx.Request] = []

    async def send(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        if request.url.path == "/open/v1/project":
            return httpx.Response(200, json=[inbox()])
        if request.url.path.endswith("/data"):
            return httpx.Response(200, json={"project": inbox(), "tasks": []})
        if request.url.path == "/open/v1/task/completed":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={})


def open_store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "dida.sqlite3")
    store.apply_refresh(lists=[inbox()], tasks=[task()])
    return store


def make_app(store: Store, server: Server, *, clock: ManualClock | None = None) -> DidaApp:
    engine = SyncEngine(
        clock=clock if clock is not None else ManualClock(T0),
        source=store,
        client=DidaApiClient(token="tok", transport=server),
    )
    return DidaApp(engine)


# ------------------------------------------------------------------ 泵的两半


async def test_the_pump_timer_is_stopped_when_the_app_unmounts():
    """关窗时把周期泵停掉：不留一个还在跳的定时器（``on_unmount``）。

    **这条故意是白盒的**（断的是我们自己的 ``_push_timer``）：没有任何纯外部断言能分清
    「定时器停了」与「定时器还在跳但恰好没触发」。它钉的是这一层的生命周期契约，不是实现
    细节——Textual 收尾时也会停 app 身上那批定时器，但那是框架的兜底，不是我们的契约。
    """
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_task("写周报", list_name="收集箱")
    app = DidaApp(fake, push_tick_seconds=0.05)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert app._push_timer is not None, "开着的时候泵挂在定时器上"

    assert app._push_timer is None, "关窗时自己把泵停掉"


async def test_the_periodic_timer_pumps_the_queue_without_any_keypress():
    """周期泵真的挂在定时器上：不按键，时间到了它自己推（用户故事 99）。

    间隔调成 0.05 秒，然后让出真实时间——不 sleep 赌一个固定时长，而是**等到它跳满两次**
    为止（到点就退出循环），所以这条不会因为机器慢而偶发红。
    """
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_task("写周报", list_name="收集箱")
    app = DidaApp(fake, push_tick_seconds=0.05)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        for _ in range(40):
            if fake.pushes >= 2:
                break
            await pilot.pause(0.05)

    assert fake.pushes >= 2, "没人按键，周期泵自己跳了不止一次"


async def test_a_push_in_flight_when_the_screen_tears_down_does_not_raise(tmp_path):
    """拆屏那一刻正在飞的那次推送回来时，屏幕已经没了——它不能再往状态栏写。

    这条把它摆成**确定**的：假服务端挂一道闸门，推送在屏上时被挡住（明明白白没跑完），
    拆完屏才开闸——不用 sleep 去赌交错。
    """
    gate = asyncio.Event()
    store = open_store(tmp_path)
    server = Server(error=NetworkError("连不上"))
    clock = ManualClock(T0)
    app = make_app(store, server, clock=clock)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        app.engine.write("t1", changes={"priority": 1})  # 本地生效 + 立刻推一次（断网，失败）
        await app.engine.wait_for_pushes()
        await pilot.pause()
        assert store.pending_count() == 1, "推不动就留在队列里"

        server.error = None  # 网络回来了
        server.gate = gate  # 但服务端现在慢慢答
        clock.advance(timedelta(seconds=5))  # 走过退避点，这一推真的会发出去
        in_flight = asyncio.create_task(app.push_tick())
        await pilot.pause()
        assert not in_flight.done(), "这一次推送还挂在网络上——下一句就是拆屏"

    assert app.is_running is False, "出来时屏幕已经拆了"

    gate.set()  # 屏幕拆完，服务端才答
    await in_flight  # 关窗时正在飞的那一次，回来时就是这样：不许抛


async def test_the_push_that_lands_after_the_screen_is_gone_still_reaches_the_server(tmp_path):
    """**写要照旧落地**：屏幕没了不等于用户那一下不算数。

    这是 v1 那条 ``test_a_toggle_that_lands_after_the_ui_is_gone_does_not_raise`` 里最要紧
    的一半（子任务勾选在 v2 变成只读，所以换成周期泵这条活得下来的路来钉）：队列里的那一笔
    在拆屏之后照样发出去、照样出队。只看「没抛异常」是不够的——静默丢掉一笔写也满足那句话。
    """
    gate = asyncio.Event()
    store = open_store(tmp_path)
    server = Server(error=NetworkError("连不上"))
    clock = ManualClock(T0)
    app = make_app(store, server, clock=clock)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        app.engine.write("t1", changes={"priority": 1})
        await app.engine.wait_for_pushes()
        await pilot.pause()
        assert store.pending_count() == 1

        server.error = None
        server.gate = gate
        clock.advance(timedelta(seconds=5))
        in_flight = asyncio.create_task(app.push_tick())
        await pilot.pause()
        assert not in_flight.done()

    gate.set()
    await in_flight

    assert store.pending_count() == 0, "屏幕没了，这一笔照旧落地、照旧出队"
    assert server.requests[-1].method == "POST", "服务端真的收到了这一笔（推不出去就会留在队列里）"


async def test_a_redraw_that_lands_after_the_ui_is_gone_does_not_raise(tmp_path):
    """一轮同步回来得比拆屏晚时，重画那一屏也得是空操作——三层已经不在了。"""
    store = open_store(tmp_path)
    app = make_app(store, Server())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()

    assert app.is_running is False, "出来时屏幕已经拆了"

    app.refresh_view()  # 回来晚了的那次同步就是这样收尾的
    app.update_status()


async def test_a_missing_status_bar_while_the_app_runs_is_still_an_error(tmp_path):
    """守卫只放过关窗那一种：app 还在跑时状态栏不见了，照旧是 bug，不许被吞掉。

    一个「什么 ``NoMatches`` 都咽下去」的守卫，会把「状态栏根本没组出来」这种真 bug 变成
    一片安静。两条路各来一次：状态栏那一句、与重画那一屏。
    """
    store = open_store(tmp_path)
    app = make_app(store, Server())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert "待推送" in screen_text(app), "先确认它本来是在的"
        await app.query_one(StatusBar).remove()
        await pilot.pause()

        with pytest.raises(NoMatches):
            app.update_status()
        with pytest.raises(NoMatches):
            app.refresh_view()  # 重画最后也要刷状态栏，同一个洞

"""一轮同步看得见的那几件事：``r`` 发什么、覆盖告知、断网、启动刷新。

**真引擎那一条**（工单 #21 的接缝）：真 ``DidaApp`` + 真 ``SyncEngine`` + 真 ``Store`` +
假传输，用 Textual 的 Pilot 驱动；断言的是屏幕文本与假服务端收到的请求。「现在」注入
``ManualClock``，所以退避到没到点是测试说了算。

这些结论来自 v1 的 ``tests/test_sync_session.py``（那个文件随三栏界面一起作废）：**引擎级**
的那一半 #32 已经搬进不依赖界面的文件，这里留下的是「在界面上看得见」的那一半——启动刷新
不挡第一屏、三件事各报各的失败、覆盖必须说出来、断网照样能读能写。
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from dida.api.client import DidaApiClient
from dida.api.errors import NetworkError
from dida.config import Config
from dida.storage.store import Store
from dida.sync.engine import SyncEngine
from dida.testing import ManualClock
from dida.tui.app import DidaApp
from support import screen_text

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻（带时区）。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 3)
WIDE = (100, 30)


def inbox() -> dict:
    return {"id": "inbox", "name": "收集箱", "sortOrder": 0}


def project(id: str = "work", name: str = "工作") -> dict:
    return {"id": id, "name": name, "sortOrder": 1}


def task(id: str = "t1", title: str = "写周报", project_id: str = "work", **extra: object) -> dict:
    return {"id": id, "projectId": project_id, "title": title, "status": 0, **extra}


class Server:
    """假服务端：按路径应答，并记下每一个请求。"""

    def __init__(
        self,
        *,
        lists: list[dict] | None = None,
        tasks: dict[str, list[dict]] | None = None,
        completed: list[dict] | None = None,
        error: Exception | None = None,
        completed_error: Exception | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.lists = list(lists if lists is not None else [inbox(), project()])
        self.tasks = dict(tasks or {})
        """清单 id → 该清单的未完成任务原文。"""
        self.completed = list(completed or [])
        self.error = error
        """摆了异常就每个请求都抛它（断网的那一条路）；清成 ``None`` 就是网络回来了。"""
        self.completed_error = completed_error
        """只让已完成流那一个请求失败（试「三件事各报各的失败」）。"""
        self.gate = gate
        """摆了闸门就每个请求都先等它（慢服务端）：开屏那一屏因此可以当场断言。"""
        self.requests: list[httpx.Request] = []

    @property
    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    async def send(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        path = request.url.path
        if path == "/open/v1/project":
            return httpx.Response(200, json=self.lists)
        if path.endswith("/data"):
            list_id = path.split("/")[4]
            return httpx.Response(
                200,
                json={"project": self._list(list_id), "tasks": self.tasks.get(list_id, [])},
            )
        if path == "/open/v1/task/completed":
            if self.completed_error is not None:
                raise self.completed_error
            return httpx.Response(200, json=self.completed)
        if request.method in ("POST", "DELETE"):
            return httpx.Response(200, json={})
        raise AssertionError(f"假服务端没有准备这条路径：{path}")

    def _list(self, list_id: str) -> dict:
        return next((item for item in self.lists if item["id"] == list_id), inbox())


def open_store(tmp_path: Path) -> Store:
    """指向临时文件的库。"""
    return Store(tmp_path / "dida.sqlite3")


def seed(store: Store, *tasks: dict, lists: list[dict] | None = None) -> None:
    """直接把一份缓存摆进库里（不必先跑一遍刷新）。"""
    store.apply_refresh(lists=lists if lists is not None else [inbox(), project()], tasks=list(tasks))


def make_app(
    store: Store,
    server: Server,
    *,
    clock: ManualClock | None = None,
    refresh_on_start: bool = False,
    push_on_change: bool = True,
) -> DidaApp:
    """真引擎 + 真库 + 打给假服务端的真客户端。"""
    engine = SyncEngine(
        clock=clock if clock is not None else ManualClock(T0),
        source=store,
        client=DidaApiClient(token="tok", transport=server),
        push_on_change=push_on_change,
    )
    return DidaApp(engine, refresh_on_start=refresh_on_start)


async def settle(app: DidaApp, pilot) -> None:
    """等这一轮同步跑完再断言：worker 跑完 + 推送轮次跑完 + 屏幕重画。"""
    await app.workers.wait_for_complete()
    pushes = getattr(app.engine, "wait_for_pushes", None)
    if pushes is not None:
        await pushes()
    await pilot.pause()


def open_work_list(app: DidaApp) -> None:
    """把光标从收集箱移到「工作」上（第二个可停的行）。

    「工作」在缓存里不一定存在（刷新之前本地是空的），所以这里不按行数走：走到名字对上为止。
    """
    for _ in range(10):
        if app.index_page().selected_id == "work":
            return
        app.index_page().action_cursor_down()
    raise AssertionError("光标没能走到「工作」那一行")


# ------------------------------------------------------------------ 启动刷新


async def test_a_background_refresh_never_blocks_the_first_screen(tmp_path):
    """启动就刷一次，但第一屏是缓存给的（用户故事 4 + 5 + 94）。

    服务端卡在闸门上，于是「网络还没回来时屏幕上有什么」可以当场断言：缓存那一份已经在，
    服务端那一份还没有。闸门放开之后它才补进来——**网络从来不是第一屏的前置条件**。
    """
    gate = asyncio.Event()
    store = open_store(tmp_path)
    seed(store, task(id="t0", title="缓存里的旧任务", project_id="inbox"), lists=[inbox()])
    server = Server(tasks={"inbox": [task(id="t1", title="服务端的任务", project_id="inbox")]}, gate=gate)
    app = make_app(store, server, refresh_on_start=True)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        first = screen_text(app)
        assert "收集箱" in first, "开屏那一刻屏幕上就是缓存里的清单"
        assert re.search(r"收集箱\D+1\b", first), "缓存里那条未完成任务数在行上"

        gate.set()
        await settle(app, pilot)
        text = screen_text(app)

    assert re.search(r"收集箱\D+1\b", text)
    assert any(request.url.path == "/open/v1/project" for request in server.requests), "启动刷了"


async def test_no_startup_request_when_the_config_turns_the_refresh_off(tmp_path):
    """``refresh_on_start=false``：启动一个请求都不发（配置说了算）。"""
    server = Server()
    app = make_app(open_store(tmp_path), server, refresh_on_start=False)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.pause()

    assert server.requests == []


# ------------------------------------------------------------------ r：一次做三件事


async def test_r_refreshes_pushes_and_pulls_the_completed_stream(tmp_path):
    """``r`` 是手动同步：全量刷新 + 推待推送改动 + 拉已完成流，一件都不能少（用户故事 50）。"""
    store = open_store(tmp_path)
    server = Server(tasks={"work": [task()], "inbox": []})
    app = make_app(store, server)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("r")
        await settle(app, pilot)
        text = screen_text(app)

        open_work_list(app)
        await pilot.press("enter")  # 刷新带回来的那条任务，进清单就能看见
        await pilot.pause()
        inside = screen_text(app)

    assert server.paths == [
        "/open/v1/project",
        "/open/v1/project/inbox/data",
        "/open/v1/project/work/data",
        "/open/v1/task/completed",
    ], "r 要全量刷新（逐清单）+ 推一轮 + 拉已完成流"
    assert "已同步 12:03 · 待推送 0 · 逻辑日 03-14" in text, "状态栏报的是这次刷新的结果"
    assert "写周报" in inside, "刷新把服务端的任务带回了缓存与界面"


async def test_a_completed_stream_failure_does_not_claim_the_whole_sync_failed(tmp_path):
    """已完成流拉不到时只说这一件事：未完成任务那一份是新的（用户故事 50）。"""
    store = open_store(tmp_path)
    server = Server(
        tasks={"inbox": [task(id="t1", title="写周报", project_id="inbox")]},
        completed_error=NetworkError("连不上"),
    )
    app = make_app(store, server)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("r")
        await settle(app, pilot)
        text = screen_text(app)

    assert "已完成流没拉到" in text, "是哪一件事失败就说哪一件"
    assert "同步失败" not in text, "未完成任务那一份已经落地了，不许说整次都失败"


async def test_offline_still_reads_the_cache_and_says_the_sync_failed(tmp_path):
    """断网：缓存照读，``r`` 如实说一句「同步失败」（用户故事 104 + 105）。"""
    store = open_store(tmp_path)
    seed(store, task(id="t1", title="写周报", project_id="inbox"), lists=[inbox()])
    server = Server(error=NetworkError("连不上"))
    app = make_app(store, server)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert "收集箱" in screen_text(app), "缓存开屏就在（网络不是第一屏的前置条件）"

        await pilot.press("r")
        await settle(app, pilot)
        text = screen_text(app)

    assert "同步失败" in text, "刷新失败要如实说，不许装成刷过了"
    assert "收集箱" in text, "缓存那一份还在"


# ------------------------------------------------------------------ 覆盖告知


async def test_a_refresh_that_overwrites_local_changes_says_how_many(tmp_path):
    """服务端盖掉本地改动时必须说出来：「N 处本地改动被覆盖」（验收标准 12、用户故事 102）。

    场景是「推上去了，服务端那一份却是另一个值」——推成功就不在队列里了，于是没有豁免，
    服务端权威生效、本地那个值被盖回去。**静默**盖回去正是 ADR-0002 要挡的那件事。
    """
    store = open_store(tmp_path)
    seed(store, task(id="t1", title="写周报", project_id="inbox"), lists=[inbox()])
    server = Server(
        tasks={"inbox": [task(id="t1", title="写周报", project_id="inbox", priority=0)]}
    )
    app = make_app(store, server)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        # 一次本地改动，推得上去（服务端收下，但刷新回来的那一份还是旧值）。
        app.engine.write("t1", changes={"priority": 5})
        await app.engine.wait_for_pushes()
        await app.push_tick()  # 生产里就是每秒那一跳：推完把状态栏刷成 0
        await pilot.pause()
        assert "待推送 0" in screen_text(app), "推上去了：队列是空的，所以没有豁免"

        await pilot.press("r")
        await settle(app, pilot)
        text = screen_text(app)

    assert "1 处本地改动被覆盖" in text, "覆盖必须被看见，而且要说清几处"


async def test_an_unpushed_change_is_not_counted_as_overwritten(tmp_path):
    """队列里那处改动不算被覆盖：它豁免于服务端权威，本地值留着（用户故事 103）。"""
    store = open_store(tmp_path)
    seed(store, task(id="t1", title="写周报", project_id="inbox"), lists=[inbox()])
    server = Server(
        tasks={"inbox": [task(id="t1", title="写周报", project_id="inbox", priority=0)]}
    )
    app = make_app(store, server, push_on_change=False)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        app.engine.write("t1", changes={"priority": 5})
        await pilot.pause()
        assert store.pending_count() == 1, "改动在队列里（还没推）"
        assert server.requests == [], "push_on_change=False：一个请求都还没发"

        await pilot.press("r")
        await settle(app, pilot)
        text = screen_text(app)

    assert "被覆盖" not in text, "没有任何东西被盖掉，就不该说被覆盖了"
    assert "待推送 0" in text, "r 顺手把队列推上去了"
    assert json.loads(
        next(r for r in server.requests if r.method == "POST").content
    )["priority"] == 5, "推的是本地那个值"


async def test_the_composition_root_wires_the_config_into_the_app(tmp_path):
    """组合根真的接线：config → store → client → engine → app（工单 #21）。

    ``day_end="04:00"`` 从配置一路走到状态栏那一格：凌晨两点还是前一个逻辑日。
    """
    from dida.bootstrap import build_app

    db = tmp_path / "cache.sqlite3"
    app = build_app(
        clock=ManualClock(at(14, 2, 0)),  # 凌晨两点：day_end=04:00 时还是 03-13
        config=Config(token="tok-1234", day_end="04:00", refresh_on_start=False),
        transport=Server(tasks={"inbox": [], "work": []}),
        db_path=db,
    )

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        text = screen_text(app)

    assert "逻辑日 03-13" in text, "day_end 从配置来：凌晨两点还是前一个逻辑日"
    assert db.exists(), "存储落在组合根给的那条路径上"
    assert "tok-1234" not in text, "token 绝不许出现在屏幕上"

"""工单 #21：手动同步 ``r``、只写变化、断网读缓存、重试泵、退出拦截、覆盖告知。

**接缝一**：真 ``DidaApp`` + 真 ``SyncEngine`` + 真 ``Store`` + 假传输，用 Textual 的 Pilot
驱动；断言的是屏幕文本与假服务端收到的请求。「现在」注入 ``ManualClock``，所以退避到没到点
是测试说了算——定时器只决定「什么时候看一眼队列」，不参与判断。

**接缝二**：传输可注入，假服务端按 URL 应答（不是按队列回放）——队列形式要求测试替引擎算清
取数顺序，写错就变成「测试和实现一起错」；按路径应答则多一个请求、少一个请求都看得见。
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from textual.css.query import NoMatches

from dida.api.client import DidaApiClient
from dida.api.errors import NetworkError
from dida.api.transport import Transport
from dida.bootstrap import build_app, default_transport, paste_token
from dida.config import Config, CredentialsError, load_config
from dida.storage.store import Store
from dida.sync.engine import SyncEngine
from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp
from dida.tui.panes import ConfirmScreen, StatusBar, TaskPane
from support import screen_text

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻（带时区）。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 3)

WIDE = (110, 30)
"""三栏常驻的终端尺寸：这些测试考的不是降级，让三栏都在屏上。"""


def inbox() -> dict:
    return {"id": "inbox", "name": "收集箱", "sortOrder": 0}


def project(id: str = "work", name: str = "工作") -> dict:
    return {"id": id, "name": name, "sortOrder": 1}


def task(id: str = "t1", title: str = "写周报", project_id: str = "work", **extra: object) -> dict:
    return {"id": id, "projectId": project_id, "title": title, "status": 0, **extra}


def inbox_task(id: str = "t1", title: str = "写周报", **extra: object) -> dict:
    """收集箱里的一条任务原文：这些测试的落点大多是收集箱（新建也落在那里）。"""
    return task(id=id, title=title, project_id="inbox", **extra)


class Server:
    """接缝二的假服务端：按路径应答，并记下每一个请求。"""

    def __init__(
        self,
        *,
        lists: list[dict] | None = None,
        tasks: dict[str, list[dict]] | None = None,
        completed: list[dict] | None = None,
        error: Exception | None = None,
        completed_error: Exception | None = None,
        gate: asyncio.Event | None = None,
        status: int | None = None,
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
        self.status = status
        """摆了状态码就一律回它（试 401 那条路：凭据被拒）。"""
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
        if self.status is not None:
            return httpx.Response(self.status)
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
    push_tick_seconds: float | None = None,
) -> DidaApp:
    """接缝一的 app：真引擎 + 真库 + 打给假服务端的真客户端。

    ``refresh_on_start=False`` 是默认值：除专门考启动刷新的那一条，别的测试都要自己按 ``r``
    才发请求，否则「按了什么、发了什么」就对不上了。``push_tick_seconds=None`` 同理——
    周期泵归专门考它的那一条，别的测试直接 ``await app.push_tick()``。
    """
    engine = SyncEngine(
        clock=clock if clock is not None else ManualClock(T0),
        source=store,
        client=DidaApiClient(token="tok", transport=server),
        push_on_change=push_on_change,
    )
    return DidaApp(
        engine,
        refresh_on_start=refresh_on_start,
        push_tick_seconds=push_tick_seconds,
    )


async def settle(app: DidaApp, pilot) -> None:
    """等这一轮同步跑完再断言：worker 跑完 + 推送轮次跑完 + 屏幕重画。

    ``r`` 的同步跑在事件循环的 worker 里（网络等待不阻塞界面），所以「按完 r 立刻断言」
    是在赌它跑完了没有。这里几个 await 各管一段，缺一个都会变成偶发红。
    """
    await app.workers.wait_for_complete()
    pushes = getattr(app.engine, "wait_for_pushes", None)
    if pushes is not None:
        await pushes()
    await pilot.pause()


# ------------------------------------------------------------------ r：一次做三件事


async def test_r_refreshes_pushes_and_pulls_the_completed_stream(tmp_path):
    """``r`` 是手动同步：全量刷新 + 推待推送改动 + 拉已完成流，一件都不能少（用户故事 53）。

    这一条钉的是**三件事都发生了**：刷新把服务端的任务带回屏幕，推一轮走队列（这里队列是空的，
    所以没有写请求），已完成流照拉一次。少做任何一件，请求序列立刻不一样。
    """
    store = open_store(tmp_path)
    server = Server(tasks={"work": [task()], "inbox": []})
    app = make_app(store, server)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert "写周报" not in screen_text(app), "还没按 r，缓存是空的，屏幕上不该有它"

        await pilot.press("r")
        await settle(app, pilot)
        text = screen_text(app)

    assert "写周报" in text, "全量刷新把服务端的任务带上了屏幕"
    assert "已同步 12:03 · 待推送 0 · 逻辑日 03-14" in text, "状态栏报的是这次刷新的结果"
    assert server.paths == [
        "/open/v1/project",
        "/open/v1/project/inbox/data",
        "/open/v1/project/work/data",
        "/open/v1/task/completed",
    ], "r 要全量刷新（逐清单）+ 推一轮 + 拉已完成流"


async def test_r_pushes_what_is_waiting_in_the_queue(tmp_path):
    """待推送改动也是 ``r`` 的一部分：按一下就把队列里那条推上去（用户故事 53 + 56）。

    ``push_on_change=False`` 摆出「队列里已经有东西」的状态：写操作只入队、不立刻推，
    所以按 ``r`` 之前服务端一个请求都没收到——那一次写请求只可能是 ``r`` 推的。
    """
    store = open_store(tmp_path)
    seed(store, task(id="t1", title="写周报", project_id="work"))
    server = Server(tasks={"work": [task()], "inbox": []})
    app = make_app(store, server, push_on_change=False)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("p")  # 优先级推进一档：本地当场生效 + 入队
        await pilot.pause()
        assert "待推送 1" in screen_text(app), "改动进了队列，状态栏那个数就该是 1"
        assert server.paths == [], "push_on_change=False：写操作不立刻推"

        await pilot.press("r")
        await settle(app, pilot)
        text = screen_text(app)

    posted = [request for request in server.requests if request.url.path == "/open/v1/task/t1"]
    assert len(posted) == 1, "r 把队列里那条推上去了，而且只推一次"
    assert posted[0].method == "POST", "更新任务走 POST /open/v1/task/{taskId}"
    assert json.loads(posted[0].content)["priority"] == 1, "推的是本地那一档（低=1）"
    assert "待推送 0" in text, "推成功就出队"


async def type_text(pilot, text: str) -> None:
    """逐字把一段话打进当前焦点（Pilot 只认单个字符或键名，中文也一样）。"""
    for char in text:
        await pilot.press("space" if char == " " else char)
    await pilot.pause()


# ------------------------------------------------------------------ 只写变化：不闪、光标不丢


async def test_a_second_refresh_of_the_same_data_leaves_the_screen_and_cursor_alone(tmp_path):
    """全量刷新只写变化：同一份数据拉第二次，行不动、光标不跳（用户故事 61）。

    网络层永远是全量拉（ADR-0001），「不闪」不是靠少拉，而是靠本地 diff 之后**没有变化就
    不写**。用户看得见的结果有两条，这一条同时钉住：屏幕文本一模一样，光标还停在刚才那一行
    ——被重画推回第一行的话，正在连着分诊的人每刷一次就要重新找位置。
    """
    tasks = [
        inbox_task(id="t1", title="写周报"),
        inbox_task(id="t2", title="买牛奶"),
        inbox_task(id="t3", title="修水龙头"),
    ]
    store = open_store(tmp_path)
    server = Server(tasks={"inbox": tasks, "work": []})
    app = make_app(store, server)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("r")
        await settle(app, pilot)
        pane = app.query_one(TaskPane)
        first_id = pane.selected_task_id

        await pilot.press("j")
        await pilot.pause()
        moved_id = pane.selected_task_id
        assert moved_id != first_id, "光标真的移开了第一行（否则下一条断言什么都没证明）"
        before = screen_text(app)

        await pilot.press("r")
        await settle(app, pilot)
        after = screen_text(app)

    assert pane.selected_task_id == moved_id, "同一份数据再刷一次，光标不许跳"
    assert after == before, "没有变化就不该重画：屏幕文本一个字都不该变"


# ------------------------------------------------------------------ 断网：读缓存、写排队


async def test_offline_still_reads_the_cache_and_queues_writes(tmp_path):
    """断网：缓存照读、照样分诊，写操作排队等重试（用户故事 62 + 56）。

    ``r`` 只是如实说一句「同步失败」，屏幕上的任务一条都不少；接着新建一条照样当场出现在
    分区里，只是它进的是待推送队列——状态栏那个数就是给用户看的「还没上去」。
    """
    store = open_store(tmp_path)
    seed(store, task(id="t1", title="写周报", project_id="inbox"), lists=[inbox()])
    server = Server(error=NetworkError("连不上"))
    app = make_app(store, server)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert "写周报" in screen_text(app), "缓存开屏就在（网络不是第一屏的前置条件）"

        await pilot.press("r")
        await settle(app, pilot)
        assert "同步失败" in screen_text(app), "刷新失败要如实说，不许装成刷过了"

        await pilot.press("a")
        await type_text(pilot, "买牛奶")
        await pilot.press("enter")
        await pilot.pause()
        await app.engine.wait_for_pushes()
        await pilot.pause()
        text = screen_text(app)

    assert "买牛奶" in text, "断网也照样能加任务（乐观写：本地先动）"
    assert "写周报" in text, "缓存里的任务还在，断网不改变这一屏"
    assert "待推送 1" in text, "推不出去就留在队列里，这个数就是「还没上去」"


# ------------------------------------------------------------------ 重试泵：谁来定期推


async def test_the_push_tick_retries_only_once_the_clock_reaches_the_backoff(tmp_path):
    """重试泵：退避到点了才推得出去，到点之前一次都不许多发（用户故事 56）。

    这是 t10 留给 t21 的那件事——退避与 ``next_retry_at`` 都做好了，缺的是「谁定期来问一句
    到点了没有」。这一条同时钉住两半：**定时器只负责问，钟负责答**。所以这里把钟摆着不动按
    多少次泵都推不出去，钟一走过退避点，同样的一次 ``push_tick()`` 就把它推上去了。
    """
    store = open_store(tmp_path)
    seed(store, task(id="t1", title="写周报", project_id="inbox"), lists=[inbox()])
    server = Server(error=NetworkError("连不上"))
    clock = ManualClock(T0)
    app = make_app(store, server, clock=clock)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("p")  # 本地当场生效 + 立刻推一次（断网，失败）
        await app.engine.wait_for_pushes()
        await pilot.pause()
        assert "待推送 1" in screen_text(app), "推不动就留在队列里"
        attempts = len(server.requests)

        await app.push_tick()
        assert len(server.requests) == attempts, "钟没走到退避点，泵一次都不许发"

        server.error = None  # 网络回来了
        clock.advance(timedelta(seconds=2))
        await app.push_tick()
        await pilot.pause()
        text = screen_text(app)

    assert len(server.requests) == attempts + 1, "到点重试，而且只重试一次"
    assert "待推送 0" in text, "推成功就出队，状态栏那个数回到 0"


async def test_the_periodic_timer_pumps_the_queue_without_any_keypress(tmp_path):
    """周期泵真的挂在定时器上：不按键、不手动调，时间到了它自己推（用户故事 56）。

    上一条证明「泵推得动」，这一条证明「泵真的会自己动」——间隔调成 0.05 秒，钟走过退避点
    之后只让出真实时间，队列就该自己空掉。间隔只是「多久看一眼」，到没到点仍然由注入的钟答。
    """
    store = open_store(tmp_path)
    seed(store, task(id="t1", title="写周报", project_id="inbox"), lists=[inbox()])
    server = Server(error=NetworkError("连不上"))
    clock = ManualClock(T0)
    app = make_app(store, server, clock=clock, push_tick_seconds=0.05)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("p")
        await app.engine.wait_for_pushes()
        await pilot.pause()
        assert "待推送 1" in screen_text(app), "先失败一次，改动留在队列里"

        server.error = None
        clock.advance(timedelta(seconds=2))
        await pilot.pause(0.4)  # 让出真实时间，够定时器跳好几下
        text = screen_text(app)

    assert "待推送 0" in text, "没人按键，周期泵自己把队列推空了"
    assert len(server.requests) >= 2, "真的又发了一次请求"


# --------------------------------------- 关窗与「回来晚了」的那一次（#41 观察、#34 属地）


async def test_a_push_in_flight_when_the_screen_tears_down_does_not_raise(tmp_path):
    """拆屏那一刻正在飞的那次推送回来时，屏幕已经没了——它不能再往状态栏写。

    偶发红的现场就长这样：定时器跳了一下，``await self.engine.push_pending()`` 还挂在
    网络上，用户这时关了窗（``run_test`` 收尾拆 widget），那一次才回来。这条把它摆成
    **确定**的：假服务端挂一道闸门，推送在屏上时被挡住（``in_flight`` 明明白白没跑完），
    拆完屏才开闸——不用 sleep 去赌交错。修好之后它什么都不做，也不许抛。

    没有这道闸门时，同样的一次「回来晚了」是直接 ``await app.push_tick()``；两者走的
    是同一条路，这里是更严的那一种（await 真的跨过了拆屏那一刻）。
    """
    store = open_store(tmp_path)
    seed(store, task(id="t1", title="写周报", project_id="inbox"), lists=[inbox()])
    gate = asyncio.Event()
    server = Server(error=NetworkError("连不上"))
    clock = ManualClock(T0)
    app = make_app(store, server, clock=clock)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("p")  # 本地当场生效 + 立刻推一次（断网，失败）
        await app.engine.wait_for_pushes()
        await pilot.pause()
        assert "待推送 1" in screen_text(app), "先失败一次，改动留在队列里"

        server.error = None  # 网络回来了
        server.gate = gate  # 但服务端现在慢慢答
        clock.advance(timedelta(seconds=2))  # 走过退避点，这一推真的会发出去
        in_flight = asyncio.create_task(app.push_tick())
        await pilot.pause()
        assert not in_flight.done(), "这一次推送还挂在网络上——下一句就是拆屏"

    assert app.is_running is False, "出来时屏幕已经拆了"

    gate.set()  # 屏幕拆完，服务端才答
    await in_flight  # 关窗时正在飞的那一次，回来时就是这样


async def test_a_redraw_that_lands_after_the_ui_is_gone_does_not_raise(tmp_path):
    """一轮同步回来得比拆屏晚时，重画那一屏也得是空操作——三栏已经不在了。

    这是同一个洞的另一条路：``_sync`` 在 ``await`` 之后调 ``refresh_view()``，而 ``r`` 与
    启动刷新都排得出这条路。窗口比周期泵那条窄，但错的是同一件事。
    """
    store = open_store(tmp_path)
    seed(store, task(id="t1", title="写周报", project_id="inbox"), lists=[inbox()])
    server = Server()
    app = make_app(store, server, clock=ManualClock(T0))

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()

    assert app.is_running is False, "出来时屏幕已经拆了"

    app.refresh_view()  # 回来晚了的那次同步就是这样收尾的


async def test_a_missing_status_bar_while_the_app_runs_is_still_an_error(tmp_path):
    """守卫只放过关窗那一种：app 还在跑时状态栏不见了，照旧是 bug，不许被吞掉。

    别的测试摆错东西时也要看得见错——一个「什么 NoMatches 都咽下去」的守卫，会把「状态栏
    根本没组出来」这种真 bug 变成一片安静。两条路各来一次：状态栏那一句、与重画那一屏。
    """
    store = open_store(tmp_path)
    seed(store, task(id="t1", title="写周报", project_id="inbox"), lists=[inbox()])
    server = Server()
    app = make_app(store, server, clock=ManualClock(T0))

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await app.query_one(StatusBar).remove()
        await pilot.pause()

        with pytest.raises(NoMatches):
            app.update_status()
        with pytest.raises(NoMatches):
            app.refresh_view()  # 重画最后也要刷状态栏，同一个洞


async def test_the_pump_timer_is_stopped_when_the_app_unmounts(tmp_path):
    """关窗时把周期泵停掉：不留一个还在跳的定时器（``on_unmount``）。

    Textual 收尾时也会停 app 身上那批定时器，所以这里断的不是「它会一直跳到进程结束」——
    断的是**这一层自己**留着泵的句柄，并在 ``on_unmount`` 里把它关掉，不靠框架兜。
    """
    store = open_store(tmp_path)
    seed(store, task(id="t1", title="写周报", project_id="inbox"), lists=[inbox()])
    server = Server()
    app = make_app(store, server, clock=ManualClock(T0), push_tick_seconds=0.05)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert app._push_timer is not None, "开着的时候泵挂在定时器上"

    assert app._push_timer is None, "关窗时自己把泵停掉"


# ------------------------------------------------------------------ 退出拦截


async def test_q_is_interrupted_once_and_says_how_many_are_pending(tmp_path):
    """待推送改动还在时 ``q`` 被拦一下，并且说清有几处（用户故事 58）。

    一次拦截 = 一次浮层：它必须说出那个数（用户不知道是刚按的那一下，还是攒了一整天），
    并且给一个「仍然退出」的出口。``n`` / ``Esc`` 回到原样——取消必须一点痕迹都不留。
    没有待推送改动时 ``q`` 照旧直接退，那是 ``test_app_shell`` 钉的。
    """
    store = open_store(tmp_path)
    seed(store, task(id="t1", title="写周报", project_id="inbox"), lists=[inbox()])
    server = Server(error=NetworkError("连不上"))
    app = make_app(store, server)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("p")  # 本地生效 + 推送失败 → 队列里一处
        await app.engine.wait_for_pushes()
        await pilot.pause()
        assert "待推送 1" in screen_text(app)

        await pilot.press("q")
        await pilot.pause()
        interrupted = screen_text(app)
        assert app.is_running, "还有没推上去的改动时，q 不该直接退"
        assert isinstance(app.screen, ConfirmScreen), "拦一下 = 一次确认浮层"
        assert "1 处" in interrupted, "要说清有几处没推上去"
        assert "退出" in interrupted, "浮层里要给出「仍然退出」这条路"

        await pilot.press("n")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen), "n 关掉浮层"
        assert app.is_running, "取消不是退出"
        assert "待推送 1" in screen_text(app), "取消一点痕迹都不留：改动还在队列里"

        await pilot.press("q")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen), "再按一次还是拦"
        assert sum(1 for screen in app.screen_stack if isinstance(screen, ConfirmScreen)) == 1, (
            "连按 q 不许叠出一摞确认框"
        )

        await pilot.press("y")
        await pilot.pause()
        assert app.is_running is False, "确认了才真的退"


async def test_the_quit_prompt_does_not_claim_the_queue_is_lost(tmp_path):
    """那句话不许说「就丢了」：待推送改动落在本地库里，下次启动接着补推（t21 的周期泵）。

    屏幕上的话必须与实现一致。实测过一次——断网写一笔、退出、重开同一个缓存文件，
    ``push_pending()`` 把它推了出去；写路径那一侧的
    ``test_a_restart_still_has_the_queue_and_pushes_it`` 钉着这件事。吓唬用户说丢了，
    是拿一句不真的话换他一次犹豫。
    """
    store = open_store(tmp_path)
    seed(store, task(id="t1", title="写周报", project_id="inbox"), lists=[inbox()])
    app = make_app(store, Server(error=NetworkError("连不上")))

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("p")  # 本地生效 + 推送失败 → 队列里一处
        await app.engine.wait_for_pushes()
        await pilot.pause()

        await pilot.press("q")
        await pilot.pause()
        prompt = screen_text(app)

        assert "1 处" in prompt, "还是得说清有几处没推上去"
        assert "下次打开" in prompt, "要说清它们去哪儿了：留在本地，下次接着补推"
        assert "丢了" not in prompt, "它们没丢——本地库里有，下次启动会接着推"


# ------------------------------------------------------------------ 覆盖告知


async def test_a_refresh_that_overwrites_local_changes_says_how_many(tmp_path):
    """服务端盖掉本地改动时必须说出来：「N 处本地改动被覆盖」（用户故事 59）。

    场景是「推上去了，服务端那一份却是另一个值」——推成功就不在队列里了，于是没有豁免，
    服务端权威生效、本地那个值被盖回去。**静默**盖回去正是 ADR-0002 要挡的那件事：用户刚按
    的那两下在屏幕上变回了原样，却没有任何解释。
    """
    store = open_store(tmp_path)
    seed(store, inbox_task(id="t1", title="写周报"), lists=[inbox()])
    server = Server(tasks={"inbox": [inbox_task(id="t1", title="写周报", priority=0)]})
    app = make_app(store, server)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.press("p")  # 无 → 低 → 中：屏幕上显示 ~
        await app.engine.wait_for_pushes()
        # 推送是异步落地的，状态栏那个数要有人再刷一次：生产里就是每秒那一跳的周期泵
        # （见 test_the_periodic_timer_pumps_the_queue_without_any_keypress）。这里直接调它，
        # 免得为了「等一秒」把这条测试变成看运气。
        await app.push_tick()
        await pilot.pause()
        assert "~ 写周报" in screen_text(app), "本地那两下生效了"
        assert "待推送 0" in screen_text(app), "而且推上去了：队列是空的，所以没有豁免"

        await pilot.press("r")  # 服务端那一份还是 priority 0
        await settle(app, pilot)
        text = screen_text(app)

    assert "1 处本地改动被覆盖" in text, "覆盖必须被看见，而且要说清几处"
    assert "· 写周报" in text, "服务端权威：本地那个值确实被盖回去了"


async def test_an_unpushed_change_is_not_counted_as_overwritten(tmp_path):
    """队列里那处改动不算被覆盖：它豁免于服务端权威，本地值留着（用户故事 60）。

    与上一条同一处本地改动、同一份服务端数据，唯一的区别是这次**还没推上去**。结果必须是
    「本地值还在 + 一句覆盖的话都没有」——ADR-0002：否则一次失败推送加上一次刷新，用户的
    操作就悄悄没了。豁免是逐字段的，被挡回去的那些进的是 ``suppressed``，不进 ``overwritten``。
    """
    store = open_store(tmp_path)
    seed(store, inbox_task(id="t1", title="写周报"), lists=[inbox()])
    server = Server(tasks={"inbox": [inbox_task(id="t1", title="写周报", priority=0)]})
    app = make_app(store, server, push_on_change=False)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.press("p")
        await pilot.pause()
        assert "待推送 2" in screen_text(app), "两处改动都在队列里（还没推）"
        assert server.requests == [], "push_on_change=False：一个请求都还没发"

        await pilot.press("r")
        await settle(app, pilot)
        text = screen_text(app)

    assert "~ 写周报" in text, "待推送改动豁免于服务端权威：本地值没被盖回去"
    assert "被覆盖" not in text, "没有任何东西被盖掉，就不该说被覆盖了"
    assert "待推送 0" in text, "r 顺手把队列推上去了"


# ------------------------------------------------------------------ 启动刷新（配置项）


async def test_the_startup_refresh_never_blocks_the_first_screen(tmp_path):
    """启动就刷一次，但第一屏是缓存给的（用户故事 3 + 4 + 51）。

    ``refresh_on_start`` 是配置项，由组合根交给 app。这里把服务端卡住（假服务端的闸门），
    于是「网络还没回来时屏幕上有什么」可以当场断言：缓存那一份已经在，服务端那一份还没有。
    闸门放开之后它才补进来——**网络从来不是第一屏的前置条件**。
    """
    gate = asyncio.Event()
    store = open_store(tmp_path)
    seed(store, inbox_task(id="t0", title="缓存里的旧任务"), lists=[inbox()])
    server = Server(tasks={"inbox": [inbox_task(id="t1", title="服务端的任务")]}, gate=gate)
    app = make_app(store, server, refresh_on_start=True)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        first = screen_text(app)
        assert "缓存里的旧任务" in first, "开屏那一刻屏幕上就是缓存"
        assert "服务端的任务" not in first, "网络还卡着，它还没到"

        gate.set()
        await settle(app, pilot)
        text = screen_text(app)

    assert "服务端的任务" in text, "启动那次全量刷新把服务端的任务补了进来"


async def test_no_startup_request_when_the_config_turns_it_off(tmp_path):
    """``refresh_on_start=false``：启动一个请求都不发（配置说了算）。

    这是组合根接线的那一半：app 自己不认识配置，是 ``build_app`` 把这一项传进来的。
    """
    db = tmp_path / "cache.sqlite3"
    server = Server(tasks={"inbox": [inbox_task(id="t1", title="写周报")]})
    app = build_app(
        clock=ManualClock(T0),
        config=Config(token="tok", refresh_on_start=False),
        transport=server,
        db_path=db,
    )

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.pause()

    assert server.requests == [], "关掉启动刷新就一个请求都不该发"


# ------------------------------------------------------------------ 组合根接线


async def test_the_composition_root_wires_config_into_the_engine_and_the_app(tmp_path):
    """组合根真的接线：config → store → client → engine → app（工单 #21）。

    三个配置项都要看得见地生效，而且各走各的路：

    - ``day_end="04:00"``：凌晨两点的逻辑日还是前一天（状态栏那一格）；
    - ``completed_window_hours=48``：30 小时前完成的那条落在窗口里（默认 24 会把它挡在外面）；
    - ``token``：请求头带 Bearer——但**屏幕上一个字都不许出现**。

    存储落在给的路径上，所以这条测试不碰 ``~/.config``。
    """
    db = tmp_path / "cache.sqlite3"
    with Store(db) as seeded:
        seeded.apply_refresh(
            lists=[inbox()],
            tasks=[
                inbox_task(id="t1", title="写周报"),
                inbox_task(
                    id="done1",
                    title="手机上做完的",
                    status=2,
                    completedTime="2026-03-13T06:03:00+0800",
                )
            ],
        )
    server = Server(tasks={"inbox": [], "work": []})
    app = build_app(
        clock=ManualClock(at(14, 2, 0)),  # 凌晨两点：day_end=04:00 时还是 03-13
        config=Config(
            token="tok-1234",
            day_end="04:00",
            completed_window_hours=48,
            refresh_on_start=False,
            push_on_change=False,
        ),
        transport=server,
        db_path=db,
    )

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        text = screen_text(app)
        assert "逻辑日 03-13" in text, "day_end 从配置来：凌晨两点还是前一个逻辑日"
        assert "已完成 1 项" in text, "completed_window_hours=48：30 小时前那条在窗口里"
        await pilot.press("c")  # 展开已完成区：标题在里面
        await pilot.pause()
        text = screen_text(app)
        assert "手机上做完的" in text

        await pilot.press("p")  # push_on_change=False：只入队，不推
        await pilot.pause()
        assert server.requests == [], "配置关掉了「改动立即推送」"
        assert "待推送 1" in screen_text(app), "改动入了队，状态栏那个数就变了"

        await pilot.press("r")
        await settle(app, pilot)
        text = screen_text(app)

    assert db.exists(), "存储落在组合根给的那条路径上"
    assert "tok-1234" not in text, "token 绝不许出现在屏幕上"
    assert server.requests[0].headers["Authorization"] == "Bearer tok-1234", "token 用在请求头里"
    assert any(request.url.path == "/open/v1/task/t1" for request in server.requests), "r 把队列推了"


# ------------------------------------------------------------------ 首次运行的凭据


async def test_the_first_run_verifies_the_token_before_storing_it(tmp_path):
    """首次运行：粘贴 → 一次「列清单」验证 → 通过才落盘（用户故事 1 + 2 + 7）。

    token 只在这条路上进磁盘：先拿它真的列一次清单，服务端认了才写文件（0600）。
    """
    path = tmp_path / "config.toml"
    server = Server(lists=[inbox()])

    config = await paste_token(
        transport=server, prompt=lambda _: "  tok-1234  ", path=path
    )

    assert config.token == "tok-1234", "粘贴来的空格要去掉"
    assert load_config(path).token == "tok-1234", "验证通过才落盘"
    assert (path.stat().st_mode & 0o777) == 0o600, "配置文件权限 0600"
    assert server.paths == ["/open/v1/project"], "验证就是一次「列清单」"
    assert server.requests[0].headers["Authorization"] == "Bearer tok-1234"


async def test_a_rejected_token_is_not_stored_and_never_printed(tmp_path):
    """服务端不认这个 token：什么都不落盘，而且它一个字都不许出现在错误里（用户故事 6）。

    401/403 与网络失败要分得开：前者说「凭据失效，重新粘贴」，后者原样是网络错误——
    把网络失败说成凭据失效，用户会去换一把本来好好的 token。
    """
    path = tmp_path / "config.toml"
    server = Server(error=NetworkError("连不上"))

    with pytest.raises(Exception) as caught:
        await paste_token(transport=server, prompt=lambda _: "tok-secret", path=path)

    assert "tok-secret" not in str(caught.value), "token 不许进错误消息（日志、回滚都看得到）"
    assert not path.exists(), "验证没过就一个文件都不该写"


async def test_an_auth_rejection_tells_the_user_to_paste_again(tmp_path):
    """401/403：明确说「凭据失效，重新粘贴」，而不是笼统的网络错误（用户故事 6）。"""
    path = tmp_path / "config.toml"
    server = Server(status=401)

    with pytest.raises(CredentialsError) as caught:
        await paste_token(transport=server, prompt=lambda _: "tok-secret", path=path)

    assert "重新粘贴" in str(caught.value)
    assert "tok-secret" not in str(caught.value)
    assert not path.exists()


# ------------------------------------------------------------------ 键位表


def overlay_text(app: DidaApp) -> str:
    """当前浮层盒子里的正文：浮层叠在下面那一屏上，屏幕文本里两屏的字混在同样的行上。"""
    body = getattr(app.screen, "body", "")
    return body.plain if hasattr(body, "plain") else str(body)


async def test_the_help_lists_the_manual_sync_key():
    """``r`` 要出现在键位表里（t18 的规矩：新绑一个键就加一行）。

    那张表是这些键唯一被写下来的地方——footer 只显示得下头几个。漏一行的后果不是「少个
    说明」，是那个功能没人找得到。
    """
    app = DidaApp(SyncEngine(clock=ManualClock(T0)))

    async with app.run_test(size=WIDE) as pilot:
        await pilot.press("question_mark")
        await pilot.pause()
        rows = overlay_text(app)

    assert re.search(r"^r\s+\S", rows, re.M), "帮助里没有 r 这一行"
    assert re.search(r"^r\s+同步", rows, re.M), "r 那一行说的是它干什么"


# ------------------------------------------------------------------ 组合根的兜底


async def test_a_transport_that_cannot_be_built_does_not_stop_the_app(tmp_path):
    """建不出真传输也不该把 app 挡在门外：第一屏读的是本地缓存（用户故事 3 + 62）。

    真实场景就是这台机器：``all_proxy=socks5://…`` 而没装 ``socksio`` 时，
    ``httpx.AsyncClient()`` 当场抛 ``ImportError``。那不是「启动失败」，是「现在连不上」：
    用户照样该看到自己的任务，第一次同步时按网络失败报出来。
    """
    def boom() -> Transport:
        raise ImportError("Using SOCKS proxy, but the 'socksio' package is not installed.")

    db = tmp_path / "cache.sqlite3"
    with Store(db) as seeded:
        seeded.apply_refresh(lists=[inbox()], tasks=[inbox_task(id="t1", title="写周报")])
    app = build_app(
        clock=ManualClock(T0),
        config=Config(token="tok", refresh_on_start=False),
        transport=default_transport(factory=boom),
        db_path=db,
    )

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert "写周报" in screen_text(app), "建不出客户端不影响读缓存"

        await pilot.press("r")
        await settle(app, pilot)
        text = screen_text(app)

    assert "同步失败" in text, "真要发请求时按网络失败报出来，界面不崩"
    assert "写周报" in text, "缓存还在：这一屏不因为连不上就不能用"


async def test_r_calls_all_three_engine_entry_points():
    """``r`` 在引擎**接口**上就是三件事：刷新 + 推一轮 + 拉已完成流（用户故事 53）。

    上面那条用真引擎钉的是「服务端收到了哪些请求」；这一条钉的是接口本身——``Engine``
    协议上少了哪一个，TUI 就没法把三件事都做全（t21 给协议补了后两个：``push_pending``
    与 ``refresh_completed``）。
    """
    backend = FakeBackend(clock=ManualClock(T0))
    backend.add_task("写周报", list_name="收集箱")
    app = DidaApp(backend)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("r")
        await app.workers.wait_for_complete()
        await pilot.pause()

    assert (backend.refreshes, backend.pushes, backend.completed_pulls) == (1, 1, 1)


async def test_a_completed_stream_failure_does_not_claim_the_whole_sync_failed(tmp_path):
    """已完成流拉不到时只说这一件事：未完成任务那一份是新的（工单 #21）。

    三件事各报各的失败。把「已完成流没拉到」说成整次同步失败，用户会以为屏幕上那些任务
    也是旧的——而它们刚刚才落地。
    """
    store = open_store(tmp_path)
    server = Server(
        tasks={"inbox": [inbox_task(id="t1", title="写周报")]},
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
    assert "写周报" in text, "刷新带回来的任务照旧在屏幕上"

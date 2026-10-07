"""认领换 id 之后，界面手里的旧 id 要继续认（工单 #75 / ADR-0009）。

**接缝**：真 ``DidaApp`` + 真 ``SyncEngine`` + 真 ``Store`` + 假传输 + Pilot（``tests/test_app_sync.py``
的样式）。**不是接缝一**——``FakeBackend.create`` 直接发 ``t1`` 这种真 id，认领在替身里不存在，
所以这个 bug 在接缝一上根本复现不出来；那正是它在近千条测试里全绿的原因。

断的是用户报的那句话：「新建的任务在一段时间内会显示该任务不在本地缓存中，无法编辑」。
明细的根因与实测输出在 ``notes/probe-stale-id.py`` 与工单 #75 里。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from dida.api.client import DidaApiClient
from dida.storage.store import Store
from dida.sync.engine import SyncEngine
from dida.testing import ManualClock
from dida.tui import messages
from dida.tui.app import DidaApp
from dida.tui.pages.detail import DetailPage
from support import screen_text

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
WIDE = (100, 30)


def inbox() -> dict:
    return {"id": "inbox", "name": "收集箱", "sortOrder": 0}


def project(id: str = "work", name: str = "工作") -> dict:
    return {"id": id, "name": name, "sortOrder": 1}


class Server:
    """假服务端：新建真的回一个带服务端 id 的原文（认领就发生在这里），其余照常应答。

    ``create_gate`` 把新建那一个请求拦在闸门上，好让「人已经站在详细页上、推送才落地」这一
    瞬间可以被断言（工单 #75 判据 3）。
    """

    def __init__(self, *, create_gate: asyncio.Event | None = None) -> None:
        self.create_gate = create_gate
        self.requests: list[httpx.Request] = []
        self.next_id = 1

    @property
    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    async def send(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/open/v1/task" and request.method == "POST":
            if self.create_gate is not None:
                await self.create_gate.wait()
            body = json.loads(request.content)
            created = {
                "id": f"srv{self.next_id}",
                "projectId": body.get("projectId"),
                "title": body.get("title", ""),
                "status": 0,
            }
            self.next_id += 1
            return httpx.Response(200, json=created)
        if path == "/open/v1/project" and request.method == "POST":
            body = json.loads(request.content)
            return httpx.Response(
                200, json={"id": "srvlist1", "name": body.get("name", ""), "sortOrder": 5}
            )
        if path == "/open/v1/project":
            return httpx.Response(200, json=[inbox(), project()])
        if path.endswith("/data"):
            list_id = path.split("/")[4]
            return httpx.Response(
                200,
                json={"project": project() if list_id == "work" else inbox(), "tasks": []},
            )
        if path == "/open/v1/task/completed":
            return httpx.Response(200, json=[])
        if request.method in ("POST", "DELETE"):
            return httpx.Response(200, json={})
        raise AssertionError(f"这张测试的假服务端没准备这条路径：{path}")


def open_store(tmp_path: Path) -> Store:
    return Store(tmp_path / "dida.sqlite3")


def seed(store: Store) -> None:
    """摆一份缓存：两个真实清单（不摆的话清单列表页只有三个内置视图，建出来的任务会落进收集箱）。"""
    store.apply_refresh(lists=[inbox(), project()], tasks=[])


def make_engine(store: Store, server: Server) -> SyncEngine:
    """真引擎 + 真库 + 打给假服务端的真客户端。"""
    return SyncEngine(
        clock=ManualClock(T0),
        source=store,
        client=DidaApiClient(token="tok", transport=server),
    )


def make_app(store: Store, server: Server) -> DidaApp:
    return DidaApp(make_engine(store, server))


async def enter_work(app: DidaApp, pilot) -> None:
    """走进「工作」清单（任务列表页）。走不到就是这一份缓存没摆对，当场说清楚。"""
    for _ in range(10):
        if app.index_page().selected_id == "work":
            break
        app.index_page().action_cursor_down()
    else:
        raise AssertionError(f"光标没能走到「工作」上，停在 {app.index_page().selected_id}")
    await pilot.press("right")
    await pilot.pause()
    assert app._container_id == "work", "要站在真实清单里，不是某个视图里"


async def create_task(app: DidaApp, pilot, title: str = "买牛奶") -> None:
    """``n`` → 只填标题 → 回车。"""
    await pilot.press("n")
    await pilot.pause()
    await pilot.press(*title)
    await pilot.press("enter")
    await pilot.pause()


# ---------------------------------------------------------------- 存储 / 引擎：旧 id 还认

async def test_the_old_id_still_finds_the_task_after_the_claim(tmp_path):
    """新建推成功、认领把那一行挪到服务端 id 上之后，旧 id 照样读得到这条任务。"""
    store = open_store(tmp_path)
    server = Server()
    engine = make_engine(store, server)

    local = engine.create("买牛奶", "work")
    await engine.wait_for_pushes()

    assert [task.id for task in store.tasks()] == ["srv1"], "认领把那一行挪到服务端 id 上了"
    assert store.task_payload(local) is not None, "旧 id 还读得到那份底稿"
    detail = engine.task_detail(local)
    assert detail is not None and detail.title == "买牛奶", "详细页拿旧 id 也找得到这条任务"
    store.close()


async def test_a_write_on_the_old_id_lands_on_the_real_row(tmp_path):
    """拿旧 id 写一笔：落到真 id 那一行上，而且**排进队列的也是真 id**（不留推不出去的改动）。"""
    store = open_store(tmp_path)
    server = Server()
    engine = make_engine(store, server)

    local = engine.create("买牛奶", "work")
    await engine.wait_for_pushes()
    engine.write(local, changes={"title": "买牛奶（改）"})
    await engine.wait_for_pushes()

    assert "/open/v1/task/srv1" in server.paths, "请求打在服务端认的那个 id 上"
    assert not any("local-task" in path for path in server.paths), "不许拿临时 id 去打服务端"
    assert store.pending() == (), "队列里不许留下一条永远推不出去的改动"
    assert store.task_payload("srv1")["title"] == "买牛奶（改）"
    store.close()


async def test_the_alias_does_not_outlive_the_process(tmp_path):
    """别名只在本次进程里有效：重开这个库，旧 id 就没人认了（ADR-0009 二）。"""
    path = tmp_path / "dida.sqlite3"
    store = Store(path)
    engine = make_engine(store, Server())
    local = engine.create("买牛奶", "work")
    await engine.wait_for_pushes()
    assert store.task_payload(local) is not None
    store.close()

    again = Store(path)
    assert [task.id for task in again.tasks()] == ["srv1"], "那一行还是服务端 id 那一行"
    assert again.task_payload(local) is None, "新进程里没有界面握着那个旧 id，别名清掉了"
    again.close()


async def test_the_old_id_of_a_just_created_list_still_works(tmp_path):
    """清单那条路同一个形状（工单 #75 判据 4）：建完清单拿旧 id 改得动。"""
    store = open_store(tmp_path)
    server = Server()
    engine = make_engine(store, server)

    local = engine.create_list("新清单")
    await engine.wait_for_pushes()
    assert [row.id for row in store.lists() if row.name == "新清单"] == ["srvlist1"]

    engine.update_list(local, name="改名")
    await engine.wait_for_pushes()

    assert "/open/v1/project/srvlist1" in server.paths, "改名打在服务端认的那个清单 id 上"
    assert any(row.name == "改名" for row in store.lists())
    store.close()


# ---------------------------------------------------------------- 界面：新建之后立刻用它

async def test_a_just_created_task_opens_its_detail_page(tmp_path):
    """新建 → 推送落地（认领发生）→ 按 ``→``：详细页显示的是这条任务，不是「不在本地缓存里了」。"""
    store = open_store(tmp_path)
    seed(store)
    server = Server()
    app = make_app(store, server)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_work(app, pilot)
        await create_task(app, pilot)
        await app.engine.wait_for_pushes()
        await pilot.pause()

        await pilot.press("right")
        await pilot.pause()
        shown = screen_text(app)

        assert messages.EMPTY_DETAIL_MESSAGE not in shown, "详细页不许说它不在本地缓存里了"
        assert "买牛奶" in shown, "详细页显示的是这条任务"

    store.close()


async def test_a_just_created_task_can_be_deleted_and_completed(tmp_path):
    """同一行上 ``d`` 要弹确认框（不许静默），``space`` 要真的把它标成完成。"""
    store = open_store(tmp_path)
    seed(store)
    server = Server()
    app = make_app(store, server)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_work(app, pilot)
        await create_task(app, pilot)
        await app.engine.wait_for_pushes()
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        assert "删除" in screen_text(app), "按 d 要弹出删除确认框"
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("space")
        await pilot.pause()
        await app.engine.wait_for_pushes()
        await pilot.pause()
        shown = screen_text(app)

        assert messages.UNKNOWN_TASK_MESSAGE not in shown, "不许说这条任务不在本地缓存里了"
        assert (
            "/open/v1/project/work/task/srv1/complete" in server.paths
        ), "完成落在服务端认的那条任务上"
        assert "☑ 买牛奶" in shown, "它已经带上完成记号、沉到已完成那一段里（工单 #74）"

    store.close()


async def test_editing_from_the_detail_page_survives_the_claim(tmp_path):
    """人**已经站在详细页上**时认领才发生：改一个字段照样写得出去，页面也不被清空。"""
    store = open_store(tmp_path)
    seed(store)
    gate = asyncio.Event()
    server = Server(create_gate=gate)
    app = make_app(store, server)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await enter_work(app, pilot)
        await create_task(app, pilot)

        doing = app.tasks_page().selected_id
        assert doing is not None and doing.startswith("local-task"), "推送还挂在闸门上，行上是临时 id"
        await pilot.press("right")
        await pilot.pause()
        assert "买牛奶" in screen_text(app), "闸门没开的时候这一页是好的"

        gate.set()
        await app.engine.wait_for_pushes()
        await pilot.pause()

        await app.on_detail_page_field_edited(DetailPage.FieldEdited(doing, "content", "改一下"))
        await pilot.pause()
        shown = screen_text(app)

        assert messages.UNKNOWN_TASK_MESSAGE not in shown, "不许说这条任务不在本地缓存里了"
        assert messages.EMPTY_DETAIL_MESSAGE not in shown, "页面不许被清空"
        assert "/open/v1/task/srv1" in server.paths, "这一笔写落在服务端认的那个 id 上"

    store.close()

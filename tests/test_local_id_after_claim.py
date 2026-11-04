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
from dida.api.errors import NetworkError
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

    ``create_gate`` 把**任务**新建那一个请求拦在闸门上，好让「人已经站在详细页上、推送才落地」
    这一瞬间可以被断言（工单 #75 判据 3）。

    ``no_id_create`` 是 #54 那一格：建清单回了 ``201`` 空体（服务端建好了，但没告诉我们 id），
    于是认领要等到下一次全量刷新按名字对上（``ListMixin._identify_created_lists``）。
    """

    def __init__(
        self,
        *,
        create_gate: asyncio.Event | None = None,
        list_gate: asyncio.Event | None = None,
        no_id_create: bool = False,
        task_error: Exception | None = None,
    ) -> None:
        self.create_gate = create_gate
        self.list_gate = list_gate
        self.no_id_create = no_id_create
        self.task_error = task_error
        """摆了异常就让**改任务**那一个请求抛它：那一笔改动会留在队列里，好直接读它的 ``task_id``。"""
        self.requests: list[httpx.Request] = []
        self.created: list[dict] = []
        """``POST /open/v1/task`` 收到的那些请求体（断落点用）。"""
        self.lists = [inbox(), project()]
        """清单索引（``GET /open/v1/project`` 给的就是它）；测试可以直接往里加一行。"""
        self.tasks: dict[str, list[dict]] = {}
        self.next_id = 1
        self.next_list_id = 1

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
            self.created.append(body)
            created = {
                "id": f"srv{self.next_id}",
                "projectId": body.get("projectId"),
                "title": body.get("title", ""),
                "status": 0,
            }
            self.next_id += 1
            return httpx.Response(200, json=created)
        if path == "/open/v1/project" and request.method == "POST":
            if self.list_gate is not None:
                await self.list_gate.wait()
            if self.no_id_create:
                return httpx.Response(201)
            body = json.loads(request.content)
            created = {
                "id": f"srvlist{self.next_list_id}",
                "name": body.get("name", ""),
                "sortOrder": 5,
            }
            self.next_list_id += 1
            self.lists.append(created)
            return httpx.Response(200, json=created)
        if path == "/open/v1/project":
            return httpx.Response(200, json=self.lists)
        if path.endswith("/data"):
            list_id = path.split("/")[4]
            row = next((item for item in self.lists if item["id"] == list_id), inbox())
            return httpx.Response(
                200, json={"project": row, "tasks": self.tasks.get(list_id, [])}
            )
        if path == "/open/v1/task/completed":
            return httpx.Response(200, json=[])
        if path.startswith("/open/v1/task/") and request.method == "POST":
            # 改一条任务（``POST /open/v1/task/{taskId}``）：摆了 ``task_error`` 就抛，
            # 那一笔改动会留在队列里等重试——好直接读队列行上的 ``task_id``。
            if self.task_error is not None:
                raise self.task_error
            return httpx.Response(200, json={})
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
    """别名只在**这一次打开**期间有效：重开这个库，旧 id 就没人认了（ADR-0009 二）。"""
    path = tmp_path / "dida.sqlite3"
    store = Store(path)
    engine = make_engine(store, Server())
    local = engine.create("买牛奶", "work")
    await engine.wait_for_pushes()
    assert store.task_payload(local) is not None
    store.close()

    again = Store(path)
    assert [task.id for task in again.tasks()] == ["srv1"], "那一行还是服务端 id 那一行"
    assert again.task_payload(local) is None, "重开之后没有界面还握着那个旧 id，别名清掉了"
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


async def test_a_reissued_local_list_id_does_not_point_at_the_old_list(tmp_path):
    """别名记着那个号，那个号就**不许再发出去**（#57 的规矩不能因为别名松掉）。

    清单的临时 id 是发号器给的（任务那边是 uuid，撞不上），所以认领之后那个号会被重新发出去
    ——而别名还记着「local-list-1 现在指 srvlist1」。真发出去的话，第二条清单拿自己的 id 改名会
    落到**第一条**上：那正是 #57 那条「拿另一条清单的名字去改服务端上的一行」。
    """
    store = open_store(tmp_path)
    server = Server()
    engine = make_engine(store, server)

    first = engine.create_list("第一")
    await engine.wait_for_pushes()
    assert first == "local-list-1", "第一个本地清单 id 是 1 号"

    second = engine.create_list("第二")
    assert second != first, "1 号还被别名占着（它现在指 srvlist1），不许再发一次"

    engine.update_list(second, name="第二（改）")
    await engine.wait_for_pushes()

    assert [row.name for row in store.lists() if row.id == "srvlist1"] == ["第一"], "改的是第二条"
    assert any(row.name == "第二（改）" for row in store.lists())
    store.close()


async def test_a_task_created_in_a_just_claimed_list_lands_in_it(tmp_path):
    """在一条**刚被认领**的清单里新建任务：落点是服务端认的那个清单 id。

    界面交进来的落点就是它手里的 ``_container_id``（``tui/app.py`` 的 ``_finish_new_task``），
    而那个值在认领之后还是旧的——不过这一道的话，新建会被 :class:`UnclaimedListError` 拒掉，
    屏幕上说的是「这个清单还没同步完」，而它明明已经同步完了。
    """
    store = open_store(tmp_path)
    server = Server()
    engine = make_engine(store, server)

    local = engine.create_list("工作")
    await engine.wait_for_pushes()
    assert [row.id for row in store.lists() if row.name == "工作"] == ["srvlist1"]

    engine.create("买牛奶", local)
    await engine.wait_for_pushes()

    assert server.created[0]["projectId"] == "srvlist1", "落点是服务端认的那个清单"
    assert [task.list_id for task in store.tasks() if task.title == "买牛奶"] == ["srvlist1"]
    store.close()


async def test_a_list_claimed_at_refresh_time_still_answers_its_old_id(tmp_path):
    """#54 那一格：建清单时服务端没回 id，认领发生在**下一次全量刷新按名字对上**。

    那条认领走的是 ``Store.identify_list``（不是 ``adopt_created_list``），所以别名也得在那里
    落账——不然「旧 id 继续认」这条只覆盖推送那一条路。
    """
    store = open_store(tmp_path)
    server = Server(no_id_create=True)
    engine = make_engine(store, server)

    local = engine.create_list("新清单")
    await engine.wait_for_pushes()
    assert store.list_payload(local) is not None, "没回 id，所以认领还没发生——本地那一行照旧在"
    assert store.resolve_id(local) == local

    server.lists.append({"id": "srvlist9", "name": "新清单", "sortOrder": 5})
    await engine.refresh()
    assert store.list_payload("srvlist9") is not None, "刷新按名字把这一条认回来了"

    assert store.list_payload(local) is not None, "认领之后旧 id 仍然读得到这一行"
    engine.update_list(local, name="改名")
    await engine.wait_for_pushes()
    assert "/open/v1/project/srvlist9" in server.paths, "改名打在服务端认的那个清单 id 上"
    store.close()


async def test_standing_in_a_just_created_list_shows_its_name_not_the_local_id(tmp_path):
    """人**已经站在**那个刚建好的清单里、认领才发生：抬头写的是它的名字，不是 ``local-list-1``。

    这一层手里只有一份 ``_container_id``，它撑起抬头与新建的落点；光标那一行的行 id 来自读模型，
    认领之后是新的。两处对同一个容器各说一个名字的话，抬头那一段会把原始 id 画给用户看
    （``_container_name`` 认不出来时照原样写 id）。
    """
    store = open_store(tmp_path)
    gate = asyncio.Event()
    server = Server(list_gate=gate)
    app = make_app(store, server)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await app._finish_list_form({"name": "新清单", "color": ""})
        await pilot.pause()

        index = app.index_page()
        for _ in range(12):
            if (index.selected_id or "").startswith("local-list"):
                break
            index.action_cursor_down()
        else:
            raise AssertionError(f"光标没能走到刚建的那一行上，停在 {index.selected_id}")
        await pilot.press("right")
        await pilot.pause()
        assert (app._container_id or "").startswith("local-list"), "推送还挂在闸门上，进的是临时 id"

        gate.set()
        await app.engine.wait_for_pushes()
        await pilot.pause()
        app.refresh_view()  # 认领之后第一次重画（真实使用时是用户的下一个动作带上来的）
        await pilot.pause()
        shown = screen_text(app)

        assert "local-list" not in shown, "抬头不许把本地临时 id 画给用户看"
        assert "新清单" in shown, "抬头写的是这个清单的名字"

    store.close()


async def test_the_change_queued_under_the_old_id_carries_the_real_task_id(tmp_path):
    """拿旧 id 写的那一笔，**排进队列的是真 id**——直读队列行，不看「推完队列空了」这种效果。

    队列里带临时 id 的改动永远推不出去（服务端没有那个任务，#53），所以这一条是这一票最要紧的
    不变量；用「推不动」的服务端把它留在队列里，是为了能直接读那一行的 ``task_id``。
    """
    store = open_store(tmp_path)
    server = Server(task_error=NetworkError("连不上"))
    engine = make_engine(store, server)

    local = engine.create("买牛奶", "work")
    await engine.wait_for_pushes()  # 新建推成功 → 认领换名
    engine.write(local, changes={"title": "买牛奶（改）"})
    await engine.wait_for_pushes()  # 这一笔推不动，留在队列里

    queued = store.pending()
    assert len(queued) == 1, "那一笔还在队列里等重试"
    assert queued[0].task_id == "srv1", "队列行上是服务端认的那个 id，不是临时 id"
    assert queued[0].list_id == "work"
    store.close()


async def test_editing_a_just_created_list_from_the_index_page(tmp_path):
    """工单判据 4 的界面那一半：建完清单、推送落地之后，在那一行上按 ``e`` 能改。

    **这一条能分辨**：``_finish_list_form`` 的重画也排在推送之前，所以清单列表页那一行的行 id
    认领之后仍然是 ``local-list-1``——按 ``e`` 交回引擎的正是那个旧 id。（清单那一行的 id 不
    刷新就不会变，与「光标那一行的 id 每次重画都新读」的错觉相反。）

    全程走键（``e`` → 打字 → ``enter``，与 ``tests/test_list_overlay.py`` 那条同一套）：
    名字那一格是**全选**的，直接打就是整个换掉。
    """
    store = open_store(tmp_path)
    server = Server()
    app = make_app(store, server)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await app._finish_list_form({"name": "新清单", "color": ""})
        await pilot.pause()
        await app.engine.wait_for_pushes()
        await pilot.pause()

        index = app.index_page()
        for _ in range(12):
            if (index.selected_id or "").startswith("local-list"):
                break
            index.action_cursor_down()
        else:
            raise AssertionError(f"光标没能走到刚建的那一行上，停在 {index.selected_id}")

        await pilot.press("e")
        await pilot.pause()
        await pilot.press(*"改名")
        await pilot.press("enter")
        await pilot.pause()
        await app.engine.wait_for_pushes()
        await pilot.pause()

        assert "/open/v1/project/srvlist1" in server.paths, "改名打在服务端认的那个清单 id 上"
        assert any(row.name == "改名" for row in store.lists()), "改的是这一行"

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

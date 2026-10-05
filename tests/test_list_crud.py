"""清单的建 / 改 / 删（工单 #42）：请求形状与推送行为。

两个接缝：

- **接缝二**（可注入的 HTTP 传输层）：钉死三条端点的方法、URL、请求体字段——尤其是
  文档那两个坑：``201 Created → No Content``（成功但**没有**响应体），以及更新时
  ``sortOrder`` 要原样 echo 回去，否则改名可能把用户的清单顺序重置成 0。
- **引擎公开入口 + 真存储 + 真客户端**（与 ``tests/test_sync_refresh.py`` 同一条）：
  断言「乐观写立刻看得见」「推不动就留在重试队列」「剪枝/重放不会把用户刚删的清单带回来」。

不断言私有方法、不断言内部状态对象；「队列里有没有」读的是引擎的公开状态
（``status().pending_count``）与 ``push_pending()`` 的行为。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dida.api.client import DidaApiClient
from dida.api.errors import NetworkError, ServerRejectionError
from dida.storage.store import Store
from dida.sync.engine import ListKind, SyncEngine
from dida.testing import FakeTransport, ManualClock

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 3)


@pytest.fixture
def store(tmp_path):
    """指向临时文件的库；关掉时不留句柄。"""
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


def make_engine(store, transport, *, clock: ManualClock | None = None) -> SyncEngine:
    """接上真存储与真客户端（网络走假传输）。"""
    return SyncEngine(
        clock=clock if clock is not None else ManualClock(T0),
        source=store,
        client=DidaApiClient(token="tok", transport=transport),
    )


def names_of(engine: SyncEngine) -> list[str]:
    """清单列表页上**真实清单**那一列的名字，按屏幕顺序（内置视图不算）。"""
    return [row.name for row in engine.list_index() if row.kind is ListKind.LIST]


# ------------------------------------------------------------------ 接缝二：三条端点的请求形状


async def test_create_project_pins_method_url_auth_and_body():
    """``POST /open/v1/project``：名字与颜色进请求体（文档 :1175–1224）。"""
    transport = FakeTransport(json={"id": "p1", "name": "购物", "color": "#F18181"})
    client = DidaApiClient(token="tok-123", transport=transport)

    created = await client.create_project({"name": "购物", "color": "#F18181"})

    request = transport.last_request
    assert request.method == "POST"
    assert str(request.url) == "https://api.dida365.com/open/v1/project"
    assert request.headers["Authorization"] == "Bearer tok-123"
    assert request.headers["Content-Type"] == "application/json"
    assert transport.last_json == {"name": "购物", "color": "#F18181"}
    assert created == {"id": "p1", "name": "购物", "color": "#F18181"}


async def test_a_created_project_with_no_body_is_a_success_not_a_malformed_response():
    """文档说建清单有两种成功形状：``200 → Project`` 与 ``201 → No Content``（:1193–1194）。

    无脑解析响应体的实现会在**成功**那条路上抛 ``MalformedResponseError``——只有真的
    连一次服务端才会撞上，所以这里按状态码钉住：201 没有响应体是成功，不是坏数据。
    """
    transport = FakeTransport(status_code=201)
    client = DidaApiClient(token="tok", transport=transport)

    created = await client.create_project({"name": "购物"})

    assert created is None, "201 Created 没有响应体：建好了，只是服务端没回那一份"


async def test_update_project_echoes_the_sort_order_back_and_never_sends_a_group():
    """``POST /open/v1/project/{projectId}``（文档 :1228–1245）。

    两件事必须同时成立：

    - ``sortOrder`` 在文档里写着 "default 0"，而省略字段是替换还是合并**文档没说**——
      改名时把它 echo 回去，用户那份清单顺序才不会被顺手重置（api-shapes §B10）。
    - ``groupId`` 任何请求体都不接受（只在 Project **响应**里出现），所以不许写它，
      也不许假装能改归属——连带 ``closed`` / ``permission`` / ``id`` 一起不进请求体。
    """
    transport = FakeTransport(json={"id": "p1", "name": "买买买", "sortOrder": 7})
    client = DidaApiClient(token="tok", transport=transport)

    await client.update_project(
        "p1",
        {"name": "买买买"},
        snapshot={
            "id": "p1",
            "name": "购物",
            "color": "#F18181",
            "sortOrder": 7,
            "groupId": "g1",
            "kind": "TASK",
            "closed": False,
            "permission": "write",
        },
    )

    request = transport.last_request
    assert request.method == "POST"
    assert str(request.url) == "https://api.dida365.com/open/v1/project/p1"
    assert transport.last_json == {
        "name": "买买买",
        "color": "#F18181",
        "sortOrder": 7,
        "kind": "TASK",
    }


async def test_update_project_is_a_success_when_the_server_answers_201():
    """更新那条也有 ``201 No Content`` 的成功形状（:1245）。"""
    transport = FakeTransport(status_code=201)
    client = DidaApiClient(token="tok", transport=transport)

    updated = await client.update_project("p1", {"name": "买买买"})

    assert updated is None


async def test_delete_project_pins_method_and_path():
    """``DELETE /open/v1/project/{projectId}``：没有请求体，响应体不可信（文档 :1278–1302）。"""
    transport = FakeTransport(status_code=200)
    client = DidaApiClient(token="tok", transport=transport)

    await client.delete_project("p1")

    request = transport.last_request
    assert request.method == "DELETE"
    assert str(request.url) == "https://api.dida365.com/open/v1/project/p1"
    assert not request.content


async def test_a_rejected_project_write_is_a_structured_error():
    """服务端拒绝照旧是 ``DidaError``：删除失败要能进重试队列，不能是裸异常。"""
    transport = FakeTransport(status_code=500, json={"error": "boom"})
    client = DidaApiClient(token="tok", transport=transport)

    with pytest.raises(ServerRejectionError):
        await client.delete_project("p1")


# ------------------------------------------------------------------ 引擎：乐观写、立即推送、重试队列


async def test_a_new_list_shows_up_at_once_and_is_pushed_in_the_same_breath(store):
    """「建完出现在清单列表页」+「立即推送」（验收标准 6、7；ADR-0002）。

    本地先动（不等网络），请求体只有名字与颜色——``groupId`` 不在里面（验收标准 9）。
    推送成功之后服务端的 id 认领回来，所以列表上**只有一条**，不是「临时的那条 + 真的那条」。
    """
    transport = FakeTransport(json={"id": "p1", "name": "购物", "color": "#F18181"})
    engine = make_engine(store, transport)

    engine.create_list("购物", color="#F18181")
    assert "购物" in names_of(engine), "本地先动：不等网络就该看得见"

    await engine.wait_for_pushes()

    assert transport.last_request.method == "POST"
    assert str(transport.last_request.url) == "https://api.dida365.com/open/v1/project"
    assert transport.last_json == {"name": "购物", "color": "#F18181"}, "不写 groupId"
    assert names_of(engine) == ["收集箱", "购物"], "认领服务端 id 之后仍然只有一条"
    assert engine.status().pending_count == 0


async def test_a_new_list_survives_a_created_with_no_body(store):
    """``201 No Content``：推送算成功（出队），本地那一行留到下一次刷新认领（验收标准 6）。"""
    transport = FakeTransport(status_code=201)
    engine = make_engine(store, transport)

    engine.create_list("购物")

    await engine.wait_for_pushes()

    assert engine.status().pending_count == 0, "201 是成功，不该留在重试队列里"
    assert "购物" in names_of(engine)


async def test_a_rename_shows_at_once_and_echoes_the_sort_order_back(store):
    """改名字立刻看得见；请求体带上服务端给过的 ``sortOrder``（陷阱 2）。"""
    transport = FakeTransport(json={"id": "p1", "name": "购物", "color": "#F18181", "sortOrder": 7})
    engine = make_engine(store, transport)
    engine.create_list("购物", color="#F18181")
    await engine.wait_for_pushes()

    engine.update_list("p1", name="买买买", color="#F18181")
    assert "买买买" in names_of(engine), "本地先动"

    await engine.wait_for_pushes()

    assert str(transport.last_request.url).endswith("/open/v1/project/p1")
    assert transport.last_json["name"] == "买买买"
    assert transport.last_json["sortOrder"] == 7, "echo 回去，别把顺序重置成 0"
    assert "groupId" not in transport.last_json
    assert engine.status().pending_count == 0


async def test_a_rename_that_cannot_be_pushed_stays_queued_and_is_retried(store):
    """推不动就进重试队列（验收标准 6）：本地那一份照旧生效，到点了再推一次。"""
    clock = ManualClock(T0)
    transport = FakeTransport(json={"id": "p1", "name": "购物"})
    engine = make_engine(store, transport, clock=clock)
    engine.create_list("购物")
    await engine.wait_for_pushes()

    transport.enqueue(httpx.ConnectError("断网了"))
    engine.update_list("p1", name="买买买")
    await engine.wait_for_pushes()

    assert engine.status().pending_count == 1, "推不动的那一笔留在队列里，状态栏那个数看得见"
    assert "买买买" in names_of(engine), "本地那份改动不许被撤销"

    clock.advance(timedelta(seconds=30))
    await engine.push_pending()

    assert engine.status().pending_count == 0
    assert transport.last_json["name"] == "买买买"


async def test_a_deleted_list_is_gone_at_once_and_the_delete_goes_out(store):
    """删掉立刻看不见；推送走 ``DELETE .../project/{id}``。"""
    transport = FakeTransport(json={"id": "p1", "name": "购物"})
    engine = make_engine(store, transport)
    engine.create_list("购物")
    await engine.wait_for_pushes()

    engine.delete_list("p1")

    assert "购物" not in names_of(engine), "本地先动：不等网络就该消失"
    await engine.wait_for_pushes()
    assert transport.last_request.method == "DELETE"
    assert str(transport.last_request.url) == "https://api.dida365.com/open/v1/project/p1"
    assert engine.status().pending_count == 0


async def test_a_delete_that_cannot_be_pushed_keeps_the_list_off_the_page_and_queued(store):
    """删不掉也**不许**把那一行放回来（验收标准 7 的另一半）。"""
    transport = FakeTransport(json={"id": "p1", "name": "购物"})
    engine = make_engine(store, transport)
    engine.create_list("购物")
    await engine.wait_for_pushes()

    transport.enqueue(httpx.ConnectError("断网了"))
    engine.delete_list("p1")
    await engine.wait_for_pushes()

    assert "购物" not in names_of(engine)
    assert engine.status().pending_count == 1, "那一笔还在重试队列里"


async def test_a_remotely_deleted_list_does_not_come_back_after_a_refresh(store):
    """**工单的阻塞理由**（验收标准 7）：删掉的清单刷新之后也不回来。

    这条走的是 #41 的剪枝：推送真的把清单删掉了，下一次全量刷新拿回来的索引里没有它，
    本地那一行必须跟着消失——不然「删掉」只在这一屏成立。
    """
    clock = ManualClock(T0)
    transport = FakeTransport(json={"id": "p1", "name": "购物"})
    engine = make_engine(store, transport, clock=clock)
    engine.create_list("购物")
    await engine.wait_for_pushes()
    engine.delete_list("p1")
    await engine.wait_for_pushes()
    assert "购物" not in names_of(engine)

    # 下一轮全量刷新：服务端的清单索引里已经没有它了（只剩别的清单）。
    transport.enqueue(httpx.Response(200, json=[{"id": "other", "name": "工作"}]))
    transport.enqueue(httpx.Response(200, json={"tasks": []}))
    transport.enqueue(httpx.Response(200, json={"tasks": []}))
    await engine.refresh()

    assert "购物" not in names_of(engine), "刷新之后也不许回来"
    assert "工作" in names_of(engine)


async def test_a_list_created_offline_survives_a_refresh(store):
    """新建的清单还没推上去时，刷新**不许**把它当成「远端已删」剪掉（#41 的剪枝豁免）。

    它不在服务端的索引里只有一个原因：那一笔改动还没出去。剪掉它的表现是用户刚建的清单
    刷新一次就没了——与「删掉的清单会回来」是同一个 bug 的两个方向（ADR-0002 的豁免）。
    """
    clock = ManualClock(T0)
    transport = FakeTransport(json={"id": "p1", "name": "购物"})
    engine = make_engine(store, transport, clock=clock)
    transport.enqueue(httpx.ConnectError("断网了"))
    engine.create_list("购物")
    await engine.wait_for_pushes()
    assert engine.status().pending_count == 1

    transport.enqueue(httpx.Response(200, json=[{"id": "other", "name": "工作"}]))
    transport.enqueue(httpx.Response(200, json={"tasks": []}))
    transport.enqueue(httpx.Response(200, json={"tasks": []}))
    await engine.refresh()

    assert "购物" in names_of(engine), "还没推上去的那一条不是「远端已删」"


async def test_a_rename_that_cannot_be_pushed_is_not_overwritten_by_the_next_refresh(store):
    """本地还没推上去的改名，服务端那份不许盖回来（ADR-0002 的豁免）。"""
    transport = FakeTransport(json={"id": "p1", "name": "购物"})
    engine = make_engine(store, transport)
    engine.create_list("购物")
    await engine.wait_for_pushes()
    transport.enqueue(httpx.ConnectError("断网了"))
    engine.update_list("p1", name="买买买")
    await engine.wait_for_pushes()

    transport.enqueue(httpx.Response(200, json=[{"id": "p1", "name": "购物", "sortOrder": 7}]))
    transport.enqueue(httpx.Response(200, json={"tasks": []}))
    transport.enqueue(httpx.Response(200, json={"tasks": []}))
    await engine.refresh()

    assert "买买买" in names_of(engine), "用户的改动还在，没有被服务端盖掉"


async def test_deleting_a_list_does_not_delete_its_tasks_from_the_local_cache(store):
    """删清单**只**动清单那一行：它里面的任务会怎样服务端没写，客户端不替它猜。

    这条钉的是「孤儿任务」那个决定的**客户端一侧**：本地库里那些任务照旧在（视图里还看得见
    它们，清单名回退成清单 id）。真正的取舍写在 ``dida/sync/lists.py`` 的模块文档里。
    """
    transport = FakeTransport(json={"id": "p1", "name": "购物"})
    engine = make_engine(store, transport)
    engine.create_list("购物")
    await engine.wait_for_pushes()
    store.apply_refresh(
        lists=[{"id": "p1", "name": "购物"}],
        tasks=[{"id": "t1", "projectId": "p1", "title": "买牛奶", "status": 0}],
        prune_lists=True,
        prune_unfinished_tasks=True,
    )
    assert engine.tasks_in("p1").items, "先确认这条任务真的在"

    engine.delete_list("p1")

    assert engine.task_detail("t1") is not None, "任务不是清单的一部分：客户端不跟着删"
    assert "买牛奶" in [item.title for item in engine.tasks_in("all").items], "「所有」里仍然看得见它"


async def test_a_list_created_offline_and_then_renamed_still_gets_both_changes_out(store):
    """断网时「先建、再改名」：两笔都要推得出去，第二笔打的必须是**服务端给的 id**。

    本地新建用的是一个临时 id（服务端建好之后才给真 id）。第二笔改名如果一直打在临时 id
    上，服务端上没有那个清单——它**永远**推不出去，状态栏那个数一直非零，用户读到的是
    「等一下就好」。所以认领真 id 的时候，这条清单后面排着的改动跟着一起挪。
    """
    clock = ManualClock(T0)
    transport = FakeTransport(json={"id": "p1", "name": "购物"})
    engine = make_engine(store, transport, clock=clock)

    transport.enqueue(httpx.ConnectError("断网了"))
    local_id = engine.create_list("购物")
    await engine.wait_for_pushes()
    transport.enqueue(httpx.ConnectError("还没好"))
    engine.update_list(local_id, name="买买买")
    await engine.wait_for_pushes()
    assert engine.status().pending_count == 2, "两笔都还没出去"

    clock.advance(timedelta(seconds=30))
    await engine.push_pending()

    assert engine.status().pending_count == 0, "两笔都要出去"
    assert str(transport.requests[-1].url).endswith("/open/v1/project/p1"), "改名打的是服务端的 id"
    assert transport.last_json == {"name": "买买买"}
    assert names_of(engine) == ["收集箱", "买买买"], "本地那一行与刚发出去的一致（不是服务端那句旧名字）"

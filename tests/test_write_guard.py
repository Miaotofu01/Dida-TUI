"""写路径的守卫（t25）：对不在本地缓存里的任务写入，不许留下永远推不出去的队列项。

接缝是 ``SyncEngine`` 的公开写入口（``write()`` / ``complete()``）与它的 ``status()`` /
``view()``：接真的 ``Store``（t08）与真的 ``DidaApiClient``（t07），网络钉在**接缝二**
（传输层可注入）上。断言的是「用户看到什么、队列里剩什么、往网上发了什么」，
不碰任何私有方法，也不 mock 我们自己的模块。

缺陷的形状（实测）：``write()`` 无条件入队 → ``Store._list_of()`` 用收集箱兜底、
``_apply_locally()`` 凭空造出一行没有 ``projectId`` 的快照 → 请求带着**猜来的**
``projectId`` 发出去 → 那条改动永远推不出去，状态栏那个数一直非零。
「等一下就好」的假象正是规范「如实呈现」一节要消灭的东西。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dida.api.client import DidaApiClient
from dida.api.errors import DidaError
from dida.storage.store import Store
from dida.sync.engine import SyncEngine, UnknownTaskError, WriteKind
from dida.testing import FakeTransport, ManualClock

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻（带时区）。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 3)


@pytest.fixture
def store(tmp_path):
    """指向临时文件的库；关掉时不留句柄。"""
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


def project(id: str = "work", name: str = "工作") -> dict:
    """一份 ``GET /open/v1/project`` 那样的清单原文。"""
    return {"id": id, "name": name, "sortOrder": 1}


def task(id: str = "t1", title: str = "写周报", project_id: str = "work", **extra: object) -> dict:
    """一份 ``GET /open/v1/project/{id}/data`` 那样的任务原文。"""
    return {"id": id, "projectId": project_id, "title": title, "status": 0, **extra}


def inbox() -> dict:
    """收集箱：API 里用字面量 ``"inbox"`` 这个 projectId。"""
    return {"id": "inbox", "name": "收集箱", "sortOrder": 0}


def seed(store: Store, *tasks: dict, lists: list[dict] | None = None) -> None:
    """直接把一份缓存摆进库里（写路径的测试不必先跑一遍刷新）。"""
    store.apply_refresh(
        lists=lists if lists is not None else [inbox(), project()],
        tasks=list(tasks),
    )


def make_engine(
    store: Store,
    transport: FakeTransport | None = None,
    *,
    now: datetime = T0,
) -> SyncEngine:
    """接上真存储；给了传输就同时接上真客户端。"""
    return SyncEngine(
        clock=ManualClock(now),
        source=store,
        client=None if transport is None else DidaApiClient(token="tok", transport=transport),
    )


def titles(engine: SyncEngine) -> list[str]:
    """视图里看得见的任务标题（用户真正看到的那一份）。"""
    return [item.title for group in engine.view().groups for item in group.items]


def test_writing_a_task_that_is_not_in_the_cache_is_refused(store):
    """缓存里没有这条任务：拒绝写，并且本地一个字都不写、一条都不入队。

    没有底稿就拼不出请求必需的 ``projectId``（``POST /open/v1/task/{taskId}`` 要求
    ``id`` + ``projectId``；完成与删除把它写在路径里），所以这条写**永远**推不出去。
    如实拒绝，不是把一条死队列项塞进状态栏。
    """
    engine = make_engine(store)

    with pytest.raises(UnknownTaskError) as caught:
        engine.write("ghost", changes={"title": "幽灵"})

    assert isinstance(caught.value, DidaError), "UI 只认结构化错误"
    assert caught.value.task_id == "ghost"
    assert store.pending() == (), "不许入队"
    assert engine.status().pending_count == 0, "状态栏那个数不许卡在非零"
    assert store.task_payload("ghost") is None, "不许凭空造一行没有 projectId 的快照"
    assert titles(engine) == [], "视图里也不许冒出一条幽灵任务"


@pytest.mark.parametrize("kind", [WriteKind.UPDATE, WriteKind.COMPLETE, WriteKind.DELETE])
async def test_a_refused_write_never_reaches_the_queue_or_the_wire(store, kind):
    """拒绝之后什么都推不出去：队列空、状态栏 0，推一轮也发不出任何一个请求。

    三种改动一个都不许漏——更新、完成、删除要的都是任务真实的清单 id，缺了它全都没法发。
    """
    transport = FakeTransport()
    engine = make_engine(store, transport)

    with pytest.raises(UnknownTaskError):
        engine.write("ghost", kind=kind)

    assert await engine.push_pending() == 0
    await engine.wait_for_pushes()

    assert transport.requests == [], "被拒绝的写一个字节都不许上网"
    assert store.pending() == ()
    assert engine.status().pending_count == 0, "待推送计数不许卡在非零"


def test_rescheduling_a_task_outside_the_cache_is_refused_not_queued(store):
    """缓存里没有这条任务：``reschedule()`` 当场拒绝（结构化错误），本地一个字都不写。

    与 ``defer()`` 的静默 no-op 不同——用户在输入框里写了日期、按了 Enter，悄悄什么都不做
    正是「如实呈现」要消灭的那种安静。界面那一侧把 ``UnknownTaskError`` 翻成一句提示
    （那条测试随界面作废）；这里留下的是引擎那一半：拒绝就得是拒绝，一条永远推不出去的
    改动都不许入队，没有底稿连请求都不该发。原本钉在 ``test_reschedule.py`` 里（#32 搬出）。
    """
    transport = FakeTransport(json={"id": "t2"})
    engine = make_engine(store, transport)

    with pytest.raises(UnknownTaskError) as caught:
        engine.reschedule("t2", due=at(20, 14, 0), all_day=False)

    assert caught.value.task_id == "t2"
    assert store.pending() == (), "拒绝就得是拒绝：不许留一条永远推不出去的改动"
    assert transport.requests == [], "没有底稿就连请求都不该发"


def test_a_snapshot_without_a_list_id_is_refused_too(store):
    """底稿没有 ``projectId`` 也不许写：否则存储层会拿收集箱兜底，发一个**猜来的**清单。

    这是同一条缺陷的另一张脸（工单 #25 的「拿不到底稿 → 缺 projectId」）：一行没有
    ``projectId`` 的快照照样拼不出必填字段，推出去只会 404 到天荒地老。
    """
    store.apply_refresh(lists=[inbox()], tasks=[{"id": "half", "title": "半条快照"}])
    engine = make_engine(store)

    with pytest.raises(UnknownTaskError) as caught:
        engine.write("half", changes={"title": "半条快照（我改的）"})

    assert caught.value.task_id == "half"
    assert store.pending() == ()
    assert engine.status().pending_count == 0
    assert store.task_payload("half")["title"] == "半条快照", "拒绝的写不许改到本地那份"


async def test_a_cached_task_is_still_written_and_pushed_unchanged(store):
    """合法输入照旧：缓存里有这条任务，就写、就入队、就推送，请求形状一个字不变。

    守卫只挡「拼不出请求」的那种输入；它不许把正常的乐观写一起挡掉（那会是比缺陷更糟的
    回归）。请求体照旧带着任务**真实的**清单 id 与那份含未知字段的完整底稿。
    """
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200, json={}))
    transport.enqueue(httpx.Response(200, json={}))
    seed(store, task(id="t1", title="写周报", project_id="work", kind="TEXT"))
    engine = make_engine(store, transport)

    engine.write("t1", changes={"title": "写周报（我改的）"})
    engine.write("t1", changes={"priority": 5})  # 同一条任务再写一次也不许被挡

    assert engine.status().pending_count == 2
    assert store.task_payload("t1")["title"] == "写周报（我改的）", "本地当场生效"

    await engine.wait_for_pushes()

    assert [str(request.url) for request in transport.requests] == [
        "https://api.dida365.com/open/v1/task/t1",
        "https://api.dida365.com/open/v1/task/t1",
    ]
    body = transport.last_json
    assert body["id"] == "t1"
    assert body["projectId"] == "work", "清单 id 来自本地底稿，不是猜的"
    assert body["kind"] == "TEXT", "不认识的服务端字段照旧原样回写"
    assert store.pending() == ()
    assert engine.status().pending_count == 0

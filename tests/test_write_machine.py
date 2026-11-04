"""一台写入机器（工单 #84）：任务与清单两条推送流水线合成一台之后，外部行为与拼装顺序无关。

接缝是 ``SyncEngine`` 的公开面（``write`` / ``update_list`` / ``push_pending`` / ``refresh``）
+ 真 ``Store`` + 假传输层：断的是**发出去的请求**与**本地那一份清单**，不碰私有属性。

守卫的形状：同一段场景跑在两台引擎上——正常拼装的那一台，以及**把写路径那几片的基类顺序
倒过来**的一台（:data:`REORDERED`，它由 ``SyncEngine.__bases__`` 重新拼出来）。#84 之前那台
倒着拼的引擎会**静默失效**：``push_pending`` 只推任务、``refresh`` 不认领新建的清单——而
全量测试看不见这件事。这两条断言就是把那段默契钉成行为。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dida.api.client import DidaApiClient
from dida.storage.store import Store
from dida.sync.engine import SyncEngine, WriteKind
from dida.sync.lists import ListMixin
from dida.sync.push import PushMixin
from dida.sync.refresh import RefreshMixin
from dida.testing import FakeTransport, ManualClock

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 12, 3)


def project(id: str = "work", name: str = "工作", sort_order: int = 1) -> dict:
    """一份 ``Project``（服务端清单索引里的那一行）。"""
    return {"id": id, "name": name, "sortOrder": sort_order}


def inbox() -> dict:
    """收集箱：服务端的清单索引里没有它，引擎自己补那一行。"""
    return {"id": "inbox", "name": "收集箱", "sortOrder": 0}


def task(id: str = "t1", title: str = "写周报", project_id: str = "work", **extra: object) -> dict:
    """一份任务原文（``GET /open/v1/project/{id}/data`` 里那一条）。"""
    return {"id": id, "projectId": project_id, "title": title, "status": 0, **extra}


def _reordered_engine() -> type:
    """把写路径那几片（``push`` / ``refresh`` / ``lists``）的基类顺序倒过来的一台引擎。

    正常拼装是 ``ListMixin`` 在最前、``PushMixin`` 在后；这里把 ``PushMixin`` 与
    ``RefreshMixin`` 提到 ``ListMixin`` 前面——#84 之前「谁先生效」正是由这个顺序决定的，
    所以这一台就是那次静默失效的形状。其余各片照 ``SyncEngine.__bases__`` 原样带上，好让
    这台引擎除了顺序之外与真货没有别的差别。
    """
    others = tuple(
        base for base in SyncEngine.__bases__ if base not in (ListMixin, PushMixin, RefreshMixin)
    )
    # ``SyncEngine`` 自己的那几件（``_write_target`` / 读形状……）原样带过来：这台引擎除了
    # 基类的排列顺序之外，与真货没有别的差别。
    body = {name: value for name, value in vars(SyncEngine).items() if not name.startswith("__")}
    body["__init__"] = SyncEngine.__init__
    body["__doc__"] = "写路径那几片的基类顺序倒过来的一台引擎（#84 的拼装顺序守卫）。"
    return type("ReorderedSyncEngine", (PushMixin, RefreshMixin, ListMixin) + others, body)


REORDERED = _reordered_engine()


def make_engine(engine_class: type, store: Store, transport: FakeTransport) -> SyncEngine:
    """这台引擎不自动排推送（``push_on_change=False``），好让每一轮由测试自己发起。"""
    return engine_class(
        clock=ManualClock(T0),
        source=store,
        client=DidaApiClient(token="tok", transport=transport),
        push_on_change=False,
    )


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


def urls(transport: FakeTransport) -> list[str]:
    return [str(request.url) for request in transport.requests]


@pytest.mark.parametrize(
    "engine_class", [SyncEngine, REORDERED], ids=["正常拼装", "倒过来拼"]
)
async def test_one_round_pushes_a_task_change_and_a_list_change(engine_class, store):
    """一轮推送里，任务改动与清单改动**都被推出去**——不管写路径那几片怎么拼。

    #84 之前这一条在「倒过来拼」那一台上是红的：``push_pending`` 只认任务那一份，清单
    那一条改动安静地留在队列里，状态栏那个数一直非零而没有任何测试盯着。
    """
    transport = FakeTransport(json={"id": "x"})
    engine = make_engine(engine_class, store, transport)
    store.apply_refresh(lists=[inbox(), project()], tasks=[task()])

    assert engine.update_list("work", name="工作（改）") is True
    assert engine.write("t1", changes={"title": "写周报（改）"}) is True
    assert store.pending_count() == 2, "两边各欠一笔"

    assert await engine.push_pending() == 2, "一轮里两台队列都推了"

    assert "https://api.dida365.com/open/v1/project/work" in urls(transport), "清单那一笔"
    assert "https://api.dida365.com/open/v1/task/t1" in urls(transport), "任务那一笔"
    assert store.pending_count() == 0, "两边都推成功了"


@pytest.mark.parametrize(
    "engine_class", [SyncEngine, REORDERED], ids=["正常拼装", "倒过来拼"]
)
async def test_a_refresh_adopts_a_newly_created_list(engine_class, store):
    """新建的清单（201 空 body：建好了但没回 id）在下一次刷新时被**认领**。

    断的是**那条认领记录出队了**（``pending_lists()`` 空）：认领那一步的证据在队列里，不在
    本地那一行在不在——刷新自己的「写服务端索引 + 剪枝」也会把 ``local-list-1`` 换成 ``p1``
    （服务端索引里没有临时那一行），所以只看本地行的话，把认领整个抽掉这条测试照样绿。
    也**不能**看 ``pending_count()``：``AWAIT_ID`` 那一种本来就不算待推送（#54），它一直是 0。

    #84 之前这一条在「倒过来拼」那一台上是红的：``refresh`` 只跑取数落库那一份，认领那
    一步没接上——那一行永远停在本地临时 id 上，之后每一次改名与删除都打到一个服务端没
    见过的 id 上（#54 记的就是这一类安静错误）。
    """
    transport = FakeTransport(status_code=201)
    engine = make_engine(engine_class, store, transport)

    local_id = engine.create_list("购物")
    assert local_id.startswith("local-list-")
    assert await engine.push_pending() == 1, "新建那一笔发出去了"
    assert local_id in {row.id for row in store.lists()}, "201 空 body：认领还没发生"

    # 下一次全量刷新：服务端索引里有它了（名字还是建出去时的那个）。
    transport.enqueue(httpx.Response(200, json=[project("p1", "购物")]))
    transport.enqueue(httpx.Response(200, json={"project": project("p1", "购物"), "tasks": []}))
    transport.enqueue(httpx.Response(200, json={"tasks": []}))
    await engine.refresh()

    assert store.pending_lists() == (), "认领那一步真的跑了：等 id 的记录出队了"
    ids = {row.id for row in store.lists()}
    assert "p1" in ids, "认到服务端给的 id 上了"
    assert local_id not in ids, "本地临时那一行让位给真 id"


@pytest.mark.parametrize(
    "engine_class", [SyncEngine, REORDERED], ids=["正常拼装", "倒过来拼"]
)
async def test_a_write_schedules_its_own_push_without_the_caller_asking(engine_class, store):
    """写完之后**引擎自己**按配置排一轮推送——#83 那几笔不 await 的界面写靠的就是这条契约。

    引擎内部的「写完立刻推」入口是 ``_push_now``（配置默认开着），而合成一台泵只动了泵的
    内部形状、没有动它排的那一轮推谁：两种改动写完之后各自都会自己出去。这台引擎用默认配置
    （``push_on_change=True``），测试不自己发起任何一轮推送，只等已经排下的那几轮跑完。
    """
    transport = FakeTransport(json={"id": "x"})
    engine = engine_class(
        clock=ManualClock(T0),
        source=store,
        client=DidaApiClient(token="tok", transport=transport),
    )
    store.apply_refresh(lists=[inbox(), project()], tasks=[task()])

    assert engine.write("t1", changes={"title": "写周报（改）"}) is True
    assert engine.update_list("work", name="工作（改）") is True
    await engine.wait_for_pushes()

    assert "https://api.dida365.com/open/v1/task/t1" in urls(transport), "任务那一笔自己出去了"
    assert "https://api.dida365.com/open/v1/project/work" in urls(transport), "清单那一笔也是"


async def test_an_unaddressable_change_in_the_task_queue_is_not_a_failed_attempt(store):
    """任务队列里一笔**打在一个服务端没见过的 id 上**的改动：泵跳过它，不算一次失败。

    公开写路径已经拒绝入队这种改动（#53 的守卫），所以这一格只能由旧库遗留或者直接改库造出来
    ——用 ``Store.enqueue`` 摆出那笔改动就是这件事。它是 #84 报备的那处**语义对齐**：旧代码在
    ``_send`` 里抛 ``Unaddressable…``、由泵记一次失败（``attempts+1``、写下 ``last_error``、排
    下次重试），新泵与清单那边一致地在挑候选时就跳过它、等认领。

    用户看得见的差别只有一处：那行 ``last_error`` 会经 ``pending_error`` →
    ``status().last_error`` 在状态栏说一句「保存失败」。所以这里既断「不发请求、不算失败」，
    也断那句话说不出来。
    """
    transport = FakeTransport(json={})
    engine = make_engine(SyncEngine, store, transport)
    store.enqueue(
        task_id="t1",
        kind=WriteKind.UPDATE,
        payload={"title": "写周报（改）"},
        now=T0,
        list_id="local-list-1",
    )

    assert await engine.push_pending() == 0
    assert transport.requests == [], "发不出去的改动一个请求都不许发"

    queued = store.pending()
    assert len(queued) == 1, "它留在队列里等认领，不许被丢掉"
    assert (queued[0].attempts, queued[0].next_retry_at, queued[0].last_error) == (0, None, None), (
        "发不出去不是一次失败：不记尝试次数、不排下次重试、不写错误"
    )
    assert engine.status().last_error is None, "状态栏不许为它说一句「保存失败」"


def test_push_pending_and_refresh_have_exactly_one_implementation_each():
    """``push_pending`` 与 ``refresh`` 各自**只有一个实现**（#84 的验收标准）。

    「谁先生效」曾经由 ``SyncEngine`` 那串基类的排列顺序决定：两个方法各有两个实现、靠
    ``super()`` 串起来，把顺序换一下清单改动就悄悄不再推送、新建的清单也不再被认领。认领
    之后这两个名字在整条 MRO 上只许出现一次——再有人加第二份，这一条当场红。
    """
    for name in ("push_pending", "refresh"):
        defining = [base.__name__ for base in SyncEngine.__mro__ if name in vars(base)]
        assert len(defining) == 1, f"{name} 有不止一个实现：{defining}"

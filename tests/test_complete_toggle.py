"""完成 ↔ 取消完成（工单 #38）：两个方向各打哪个端点、本地怎么变、按键走得到哪一步。

三个接缝各测它该测的那一半：

- **接缝二（HTTP 传输层可注入）** 钉请求形状：「取消完成打到批量更新端点且带 ``status: 0``」
  与「批量更新只发 id、projectId、status」是两条验收标准，期望值来自 spec 的实测口径
  （``已实测的 API 事实`` 第 1 条）与 openapi 的 §A3——不是从这里再算一遍。
- **引擎 + 真存储 + 真客户端**（网络钉在接缝二上）钉本地效果：取消完成之后本地立刻不再把
  它算作已完成，**即使完成时间戳还在**；一次刷新也得经得起。
- **接缝一（内存假后端 + ``run_test()`` pilot）** 钉按键那一半：任务列表页按 ``space`` 在
  两个方向之间翻，屏幕上有一句短暂的反馈；输入框有焦点时 ``space`` 只是一个空格。

``id2error`` 那一半单独说一句：批量更新的失败**塞在 200 OK 里**（openapi :567），只按状态码
分类的实现会把整批全失败报成成功——用户看到的就是一句假的「已完成」。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dida.api.client import DidaApiClient
from dida.api.errors import BatchRejectedError, DidaError
from dida.storage.store import COMPLETED_STATUS, ChangeKind, Store
from dida.sync.engine import SyncEngine
from dida.testing import FakeTransport, ManualClock

TASK_ID = "t1"
PROJECT_ID = "work"
UNCOMPLETED = 0
"""未完成的 ``status``（spec：2 是完成、0 是正常、-1 是已放弃）。"""

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
COMPLETED_AT = "2026-03-14T11:00:00+0800"
"""服务端给过的完成时间戳：取消完成**不会**把它清掉（spec 的实测事实第 1 条）。"""


@pytest.fixture
def store(tmp_path):
    """指向临时文件的库；关掉时不留句柄。"""
    opened = Store(tmp_path / "dida.sqlite3")
    yield opened
    opened.close()


def seed(store: Store, *tasks: dict) -> None:
    """直接把一份缓存摆进库里（写路径的测试不必先跑一遍刷新）。"""
    store.apply_refresh(
        lists=[{"id": PROJECT_ID, "name": "工作", "sortOrder": 1}],
        tasks=list(tasks),
    )


def completed_task(**extra: object) -> dict:
    """一条**服务端说已完成**的任务原文（``completedTime`` 还在、``status`` 是 2）。"""
    return {
        "id": TASK_ID,
        "projectId": PROJECT_ID,
        "title": "写周报",
        "desc": "本地那份描述",
        "priority": 5,
        "status": COMPLETED_STATUS,
        "completedTime": COMPLETED_AT,
        **extra,
    }


def make_engine(store: Store, transport: FakeTransport | None = None) -> SyncEngine:
    """接上真存储与真客户端；网络钉在假传输上（接缝二）。"""
    return SyncEngine(
        clock=ManualClock(T0),
        day_end="24:00",
        source=store,
        client=None if transport is None else DidaApiClient(token="tok", transport=transport),
    )


def completed_titles(engine: SyncEngine) -> list[str]:
    """屏幕上「已完成」那一段里的任务（用户真正看得见的那一份）。"""
    return [item.title for item in engine.tasks_in(PROJECT_ID).completed.items]


def open_titles(engine: SyncEngine) -> list[str]:
    """屏幕上未完成那一段里的任务。"""
    return [item.title for item in engine.tasks_in(PROJECT_ID).items]


async def test_cancelling_completion_posts_to_the_batch_endpoint_with_status_zero():
    """取消完成打到批量更新端点且带 ``status: 0``（验收标准 3、9）。

    ``POST /open/v1/task/batch``，请求体是 ``{"update": [...]}``（openapi §A3：数组最多 50 条）。
    这条用法官方文档一字未提，是实测确认的（spec 的实测事实第 1 条）——所以它是一份
    **实测口径**的断言，不是从文档抄下来的。
    """
    transport = FakeTransport(json={"id2etag": {TASK_ID: "etag-1"}, "id2error": {}})
    client = DidaApiClient(token="tok-123", transport=transport)

    await client.batch_update([{"id": TASK_ID, "projectId": PROJECT_ID, "status": UNCOMPLETED}])

    request = transport.last_request
    assert request.method == "POST"
    assert str(request.url) == "https://api.dida365.com/open/v1/task/batch"
    assert request.headers["Authorization"] == "Bearer tok-123"
    assert transport.last_json == {
        "update": [{"id": TASK_ID, "projectId": PROJECT_ID, "status": UNCOMPLETED}]
    }


async def test_the_batch_body_carries_nothing_but_id_project_id_and_status():
    """批量更新只发 id、projectId、status（验收标准 4）。

    多给的字段**不发**：批量更新是合并语义，带上标题 / 描述 / 优先级就是拿本地那一份去
    覆盖服务端——「其余字段不因这次取消完成而改变」这句话得由请求体来兑现。
    """
    transport = FakeTransport(json={"id2etag": {}, "id2error": {}})
    client = DidaApiClient(token="tok-123", transport=transport)

    await client.batch_update(
        [
            {
                "id": TASK_ID,
                "projectId": PROJECT_ID,
                "status": UNCOMPLETED,
                "title": "本地那份标题",
                "desc": "本地那份描述",
                "priority": 5,
            }
        ]
    )

    assert transport.last_json == {
        "update": [{"id": TASK_ID, "projectId": PROJECT_ID, "status": UNCOMPLETED}]
    }


async def test_a_per_task_failure_inside_a_two_hundred_is_not_a_success():
    """批量更新把每个任务的失败塞在 ``200 OK`` 里（``id2error``，openapi :567）。

    整批全失败也是 200，所以「没抛异常」不等于「改成了」：只按状态码分类的实现会把
    「一条都没改成」报成成功，而用户看到的就是一句假的「已完成」——ADR-0002 要消灭的
    正是这种安静的错误。``id2error`` 里的码是文档给的那一张（``NOT_EXISTED``、
    ``DELETED``、``EXCEED_QUOTA``……），原样带给上层。
    """
    transport = FakeTransport(json={"id2etag": {}, "id2error": {TASK_ID: "NOT_EXISTED"}})
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(BatchRejectedError) as caught:
        await client.batch_update(
            [{"id": TASK_ID, "projectId": PROJECT_ID, "status": UNCOMPLETED}]
        )

    assert isinstance(caught.value, DidaError), "UI 只认结构化错误"
    assert caught.value.errors == {TASK_ID: "NOT_EXISTED"}
    assert TASK_ID in str(caught.value) and "NOT_EXISTED" in str(caught.value)


# ---------------------------------------------------------------- 引擎：本地效果与队列


def test_cancelling_completion_leaves_it_unfinished_even_with_the_timestamp_still_there(store):
    """取消完成后本地立刻不再把它算作已完成，**即使完成时间戳还在**（验收标准 6）。

    本地判定「已完成」一律看 ``status``（spec：2 是完成、0 是正常、-1 是已放弃），不看有没有
    ``completedTime``——实测取消完成不会清掉那个时间戳，只按时间戳判会把这条又捞回已完成区。
    所以这一条同时钉两件事：屏幕上它回到未完成那一段，而**原文里时间戳一个字没动**、
    别的字段（标题、描述、优先级）也一个都没动。
    """
    seed(store, completed_task())
    engine = make_engine(store)
    assert completed_titles(engine) == ["写周报"], "摆进去的是一条已完成的任务"

    engine.uncomplete(TASK_ID)

    assert completed_titles(engine) == [], "取消完成之后本地立刻不再算它已完成"
    assert open_titles(engine) == ["写周报"], "它回到未完成那一段里"
    payload = store.task_payload(TASK_ID)
    assert payload["completedTime"] == COMPLETED_AT, "完成时间戳不许被这次取消完成清掉"
    assert payload["status"] == UNCOMPLETED
    assert (payload["title"], payload["desc"], payload["priority"]) == (
        "写周报",
        "本地那份描述",
        5,
    ), "其余字段不因这次取消完成而改变"
    assert [(change.task_id, change.kind) for change in store.pending()] == [
        (TASK_ID, ChangeKind.UNCOMPLETE)
    ], "一次取消完成只留一条 UNCOMPLETE 改动"
    assert engine.status().pending_count == 1, "状态栏那个数立刻顶上（本地比服务端新）"


async def test_the_cancel_write_reaches_the_batch_endpoint_and_leaves_the_queue(store):
    """一次取消完成推出去的就是那一个请求，推成功就出队（验收标准 2、3、9）。

    端到端那一条：引擎的写入口 → 队列 → 客户端 → 传输层。路径与请求体是实测口径
    （spec 的实测事实第 1 条），不是从文档抄的。
    """
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200, json={"id2etag": {TASK_ID: "etag-1"}, "id2error": {}}))
    seed(store, completed_task())
    engine = make_engine(store, transport)

    engine.uncomplete(TASK_ID)
    await engine.wait_for_pushes()

    request = transport.last_request
    assert request.method == "POST"
    assert str(request.url) == "https://api.dida365.com/open/v1/task/batch"
    assert transport.last_json == {
        "update": [{"id": TASK_ID, "projectId": PROJECT_ID, "status": UNCOMPLETED}]
    }
    assert store.pending() == (), "推成功了就出队，状态栏那个数跟着落回去"


async def test_a_batch_that_failed_the_task_in_a_two_hundred_is_not_reported_as_pushed(store):
    """逐条失败藏在 ``200 OK`` 里：这一笔**没推成功**，不许当成推过了。

    取消完成是「本地先动、服务端随后到」的写（ADR-0002），所以失败的表现是那条改动还在
    队列里、状态栏那个数一直非零，而不是屏幕上悄悄变成成功。这一条同时是「整批全失败也
    是 200」那张脸的守卫：只按状态码分类的实现会在这里返回 1。
    """
    transport = FakeTransport()
    transport.enqueue(
        httpx.Response(200, json={"id2etag": {}, "id2error": {TASK_ID: "NOT_EXISTED"}})
    )
    seed(store, completed_task())
    engine = make_engine(store, transport)

    engine.uncomplete(TASK_ID)
    await engine.wait_for_pushes()

    assert await engine.push_pending() == 0, "服务端说这条没改成，就不算推成功"
    (change,) = store.pending()
    assert change.task_id == TASK_ID and change.attempts == 1
    assert "NOT_EXISTED" in (change.last_error or ""), "失败的原因要留在队列里（状态栏读得到）"
    assert engine.status().pending_count == 1


def test_a_refresh_does_not_make_a_cancelled_completion_completed_again(store):
    """取消完成的本地效果经得起一次刷新（验收标准 6 的补充）。

    判据只有 ``status``：服务端**可能仍然把 ``completedTime`` 带回来**（实测取消完成不会
    清掉那个时间戳），只看时间戳就会把刚取消完成的任务又塞回已完成区。两半都钉：

    - 服务端照做了（``status: 0``、时间戳还在）→ 屏幕上它就是未完成的；
    - 服务端还没认过这一笔（``status`` 仍然是 2）→ 本地那条待推送改动豁免于服务端权威
      （ADR-0002），这一屏不会自己变回去。
    """
    seed(store, completed_task())
    engine = make_engine(store)
    engine.uncomplete(TASK_ID)

    store.apply_refresh(
        lists=[{"id": PROJECT_ID, "name": "工作", "sortOrder": 1}],
        tasks=[completed_task(status=UNCOMPLETED)],
    )

    assert completed_titles(engine) == [], "时间戳还在，但它已经不是已完成了"
    assert open_titles(engine) == ["写周报"]

    store.apply_refresh(
        lists=[{"id": PROJECT_ID, "name": "工作", "sortOrder": 1}],
        tasks=[completed_task()],
    )

    assert completed_titles(engine) == [], "那条改动还没推上去，服务端那份不许盖回来"
    assert open_titles(engine) == ["写周报"]

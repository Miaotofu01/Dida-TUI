"""挑选型字段（工单 #45）：所属清单 / 优先级 / 标签。

两个既定接缝都用：

- **接缝二**（真引擎 + 真库 + 可注入的 HTTP 传输）钉**搬运的请求形状**与「搬完之后
  读路径上这条任务在哪」——那两件事是网络与本地库这一侧的事实，替身说了不算。
- **接缝一**（内存 ``FakeBackend`` + ``run_test()`` pilot）钉三个挑选浮层的**外部行为**：
  按了什么键、屏幕上出现了什么、写出去的是哪一笔。

只断外部行为：不断控件树、不断内部状态对象、不断渲染字符串里的颜色码。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dida.api.client import DidaApiClient
from dida.api.errors import MalformedResponseError
from dida.storage.store import Store
from dida.sync.engine import SyncEngine
from dida.testing import FakeBackend, FakeTransport, ManualClock

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
WIDE = (100, 30)

TITLE = "交季度报告"


# ------------------------------------------------------------------ 接缝二：搬运的请求形状


async def test_the_move_request_is_a_json_array_and_the_response_is_an_id_etag_array():
    """``POST /open/v1/task/move`` 的两个形状陷阱（工单 #45 补的那两条）。

    请求体**顶层是数组**、每项三个字段都必填（``openapi-dida365.md:504``、``:508–510``）；
    响应是 ``{id, etag}`` 的数组（``:516``），**不是**被搬的那条 Task。
    """
    transport = FakeTransport(json=[{"id": "t1", "etag": "43p2zso1"}])
    client = DidaApiClient(token="tok-123", transport=transport)

    results = await client.move_task("work", "life", "t1")

    request = transport.last_request
    assert request.method == "POST"
    assert str(request.url) == "https://api.dida365.com/open/v1/task/move"
    assert request.headers["Authorization"] == "Bearer tok-123"
    assert transport.last_json == [
        {"fromProjectId": "work", "toProjectId": "life", "taskId": "t1"}
    ], "顶层必须是数组，不是对象"
    assert results == [{"id": "t1", "etag": "43p2zso1"}]


async def test_a_move_response_that_is_a_task_object_is_rejected_as_bad_shape():
    """响应按 ``{id, etag}`` 的**数组**解析：给一条 Task 对象是坏形状，不是「搬好了」。"""
    transport = FakeTransport(json={"id": "t1", "title": TITLE})
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(MalformedResponseError):
        await client.move_task("work", "life", "t1")


async def test_a_move_that_answers_201_with_no_body_is_a_success():
    """``201 → No Content``（``:517``）是成功形状：空响应体不是坏数据。"""
    transport = FakeTransport()
    transport.enqueue(httpx.Response(201))
    client = DidaApiClient(token="tok-123", transport=transport)

    assert await client.move_task("work", "life", "t1") == []


# ------------------------------------------------------------------ 接缝二：搬完人真的换了清单


def real_engine(tmp_path, transport, *, lists=None, tasks=None) -> SyncEngine:
    """接缝二：真引擎 + 真库 + 打给假服务端的真客户端（``test_detail_page.py`` 那一套）。

    搬运的**请求形状**与「搬完之后读路径上这条任务在哪」都是网络与本地库这一侧的事实，
    替身说了不算——``FakeBackend`` 的写只记录，读路径上的搬家效果在它那里根本不存在。
    """
    store = Store(tmp_path / "dida.sqlite3")
    store.apply_refresh(
        lists=lists
        if lists is not None
        else [
            {"id": "work", "name": "工作", "sortOrder": 0},
            {"id": "life", "name": "生活", "sortOrder": 1},
        ],
        tasks=tasks
        if tasks is not None
        else [{"id": "t1", "projectId": "work", "title": TITLE, "status": 0}],
    )
    return SyncEngine(
        clock=ManualClock(T0),
        source=store,
        client=DidaApiClient(token="tok", transport=transport),
    )


async def test_moving_a_task_goes_to_the_move_endpoint_not_an_ordinary_field_update(tmp_path):
    """搬运打的是搬运端点，**不是**把它当成一次普通的字段更新（验收标准 2）。

    两个形状一起钉：顶层是数组、每项三个字段（``:504``、``:508–510``）；而且这次推送里
    **一个** ``POST /open/v1/task/{taskId}`` 都没有——那正是「当成字段更新」的样子。
    """
    transport = FakeTransport(json=[{"id": "t1", "etag": "43p2zso1"}])
    engine = real_engine(tmp_path, transport)

    engine.move_task("t1", to_list_id="life")
    await engine.push_pending()

    urls = [str(request.url) for request in transport.requests]
    assert urls == ["https://api.dida365.com/open/v1/task/move"], f"搬运走错了端点：{urls}"
    assert transport.last_json == [
        {"fromProjectId": "work", "toProjectId": "life", "taskId": "t1"}
    ]


async def test_after_the_move_the_target_list_has_the_task_and_the_source_list_does_not(tmp_path):
    """搬完**在读路径上**断言：目标清单里有它、原清单里没有（验收标准 8）。

    断的不是本地某个集合，而是两层页面真正读的那两个口子（``tasks_in`` 与详情页的清单名）
    ——本地集合对了而读路径没变，屏幕上就还是原来的样子。
    """
    transport = FakeTransport(json=[{"id": "t1", "etag": "43p2zso1"}])
    engine = real_engine(tmp_path, transport)

    engine.move_task("t1", to_list_id="life")
    await engine.push_pending()

    assert [item.task_id for item in engine.tasks_in("life").items] == ["t1"], "目标清单里没有这条任务"
    assert [item.task_id for item in engine.tasks_in("work").items] == [], "原清单里还留着这条任务"
    detail = engine.task_detail("t1")
    assert detail is not None and detail.list_name == "生活", "详情页那一格还写着原清单"


async def test_a_task_moves_both_ways_between_the_inbox_and_a_real_list(tmp_path):
    """收集箱与真实清单之间**双向**可搬（验收标准 3）。

    文档对这一节里的收集箱一个字都没提（``:497–548``），所以这里不替它写一条「文档说」；
    能钉的是**形状**：收集箱那一行带的是什么 id，搬过去就发什么 id（本测试用的就是
    ``move_targets()`` 真正会给挑选器的那个 id），回来时再搬一次。
    """
    transport = FakeTransport(json=[{"id": "t1", "etag": "e1"}])
    engine = real_engine(
        tmp_path,
        transport,
        tasks=[{"id": "t1", "projectId": "inbox", "title": TITLE, "status": 0}],
    )
    inbox_id = next(row.id for row in engine.move_targets() if row.is_inbox)

    engine.move_task("t1", to_list_id="work")
    await engine.push_pending()
    assert [item.task_id for item in engine.tasks_in("work").items] == ["t1"], "搬出收集箱没成"
    assert engine.task_detail("t1").list_name == "工作"

    engine.move_task("t1", to_list_id=inbox_id)
    await engine.push_pending()
    assert [item.task_id for item in engine.tasks_in(inbox_id).items] == ["t1"], "搬回收集箱没成"
    assert [item.task_id for item in engine.tasks_in("work").items] == []

    assert [json.loads(request.content) for request in transport.requests] == [
        [{"fromProjectId": "inbox", "toProjectId": "work", "taskId": "t1"}],
        [{"fromProjectId": "work", "toProjectId": inbox_id, "taskId": "t1"}],
    ], "两次搬运的请求体不是文档那个数组形状"


async def test_a_list_the_server_has_not_seen_is_never_offered_as_a_move_target(tmp_path):
    """本地刚建、还没推上去的清单**不**当搬运目标（#53/#54 是同一类）。

    它的 id 是本地临时的（服务端没见过），拿它当 ``toProjectId`` 会 404，而那条改动
    **永远推不出去**——状态栏那个数从此一直非零，读起来像「等一下就好」。判据是队列里
    还有没有这一行的 ``CREATE``：推成功、认领了服务端的 id 之后它就该出现在可选里。
    """
    transport = FakeTransport(json={"id": "srv-1", "name": "新清单", "sortOrder": 0})
    engine = real_engine(tmp_path, transport)

    local_id = engine.create_list("新清单")

    assert local_id in {row.id for row in engine.list_index()}, "本地那一行本来就该在清单索引里"
    assert local_id not in {row.id for row in engine.move_targets()}, (
        "服务端还没见过的清单被当成了搬运目标"
    )

    await engine.push_pending()  # 新建推成功：本地那一行认领服务端的 id

    offered = {row.id for row in engine.move_targets()}
    assert local_id not in offered
    assert "srv-1" in offered, "服务端认过的清单该能当搬运目标"

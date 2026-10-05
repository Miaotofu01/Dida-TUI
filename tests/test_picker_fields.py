"""挑选型字段（工单 #45）：所属清单 / 优先级 / 标签。

两个既定接缝都用：

- **接缝二**（真引擎 + 真库 + 可注入的 HTTP 传输）钉**搬运的请求形状**与「搬完之后
  读路径上这条任务在哪」——那两件事是网络与本地库这一侧的事实，替身说了不算。
- **接缝一**（内存 ``FakeBackend`` + ``run_test()`` pilot）钉三个挑选浮层的**外部行为**：
  按了什么键、屏幕上出现了什么、写出去的是哪一笔。

只断外部行为：不断控件树、不断内部状态对象、不断渲染字符串里的颜色码。
"""

from __future__ import annotations

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

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

import httpx
import pytest

from dida.api.client import DidaApiClient
from dida.api.errors import BatchRejectedError, DidaError
from dida.testing import FakeTransport

TASK_ID = "t1"
PROJECT_ID = "work"
UNCOMPLETED = 0
"""未完成的 ``status``（spec：2 是完成、0 是正常、-1 是已放弃）。"""


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

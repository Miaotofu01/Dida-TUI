"""接缝二：HTTP 传输层可注入。

只钉请求形状（方法、URL、认证头、请求体字段）与失败的结构化表达，
不在这里测任何业务逻辑。
"""

from dida.api.client import DidaApiClient
from dida.api.errors import (
    AuthError,
    DatelessRepeatError,
    DidaError,
    FieldIgnoredError,
    InvalidDateError,
    MalformedResponseError,
    NetworkError,
    ServerRejectionError,
)
from dida.testing import FakeTransport
from datetime import datetime, timedelta, timezone
from typing import Any
import httpx
import pytest


async def test_get_project_data_pins_method_url_and_auth_header():
    transport = FakeTransport(json={"project": {"id": "inbox"}, "tasks": []})
    client = DidaApiClient(token="tok-123", transport=transport)

    await client.get_project_data("inbox")

    request = transport.last_request
    assert request.method == "GET"
    assert str(request.url) == "https://api.dida365.com/open/v1/project/inbox/data"
    assert request.headers["Authorization"] == "Bearer tok-123"


async def test_get_project_data_returns_the_server_payload():
    transport = FakeTransport(json={"project": {"id": "inbox"}, "tasks": [{"id": "t-1"}]})
    client = DidaApiClient(token="tok-123", transport=transport)

    payload = await client.get_project_data("inbox")

    assert payload == {"project": {"id": "inbox"}, "tasks": [{"id": "t-1"}]}


async def test_create_task_pins_body_fields():
    transport = FakeTransport(json={"id": "t-1"})
    client = DidaApiClient(token="tok-123", transport=transport)

    created = await client.create_task({"title": "写周报", "projectId": "inbox"})

    request = transport.last_request
    assert request.method == "POST"
    assert str(request.url) == "https://api.dida365.com/open/v1/task"
    assert request.headers["Authorization"] == "Bearer tok-123"
    assert request.headers["Content-Type"] == "application/json"
    assert transport.last_json == {"title": "写周报", "projectId": "inbox"}
    assert created == {"id": "t-1"}


async def test_auth_failure_is_a_structured_error():
    transport = FakeTransport(status_code=401, json={"error": "invalid_token"})
    client = DidaApiClient(token="bad-token", transport=transport)

    with pytest.raises(AuthError) as caught:
        await client.get_project_data("inbox")

    assert caught.value.status_code == 401


async def test_network_failure_is_a_structured_error():
    transport = FakeTransport()
    transport.enqueue(httpx.ConnectError("连不上"))
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(NetworkError) as caught:
        await client.get_project_data("inbox")

    assert isinstance(caught.value.__cause__, httpx.ConnectError)


async def test_server_rejection_keeps_the_status_code():
    transport = FakeTransport(status_code=500, json={})
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(ServerRejectionError) as caught:
        await client.create_task({"title": "写周报"})

    assert caught.value.status_code == 500


async def test_list_projects_pins_method_url_and_auth_header():
    transport = FakeTransport(json=[{"id": "inbox", "name": "收集箱"}])
    client = DidaApiClient(token="tok-123", transport=transport)

    projects = await client.list_projects()

    request = transport.last_request
    assert request.method == "GET"
    assert str(request.url) == "https://api.dida365.com/open/v1/project"
    assert request.headers["Authorization"] == "Bearer tok-123"
    assert projects == [{"id": "inbox", "name": "收集箱"}]


# --- 请求形状：t07 补的端点 -------------------------------------------------


async def test_get_task_pins_method_url_and_auth_header():
    task = {"id": "t-1", "projectId": "inbox", "title": "写周报"}
    transport = FakeTransport(json=task)
    client = DidaApiClient(token="tok-123", transport=transport)

    fetched = await client.get_task("inbox", "t-1")

    request = transport.last_request
    assert request.method == "GET"
    assert str(request.url) == "https://api.dida365.com/open/v1/project/inbox/task/t-1"
    assert request.headers["Authorization"] == "Bearer tok-123"
    assert fetched == task


async def test_list_tags_pins_method_url_and_auth_header():
    tags = [{"name": "工作", "label": "工作", "sortOrder": 1, "color": "#ff0000", "type": 0}]
    transport = FakeTransport(json=tags)
    client = DidaApiClient(token="tok-123", transport=transport)

    listed = await client.list_tags()

    request = transport.last_request
    assert request.method == "GET"
    assert str(request.url) == "https://api.dida365.com/open/v1/tag"
    assert request.headers["Authorization"] == "Bearer tok-123"
    assert listed == tags


async def test_complete_task_posts_with_no_body():
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200))
    client = DidaApiClient(token="tok-123", transport=transport)

    result = await client.complete_task("inbox", "t-1")

    request = transport.last_request
    assert request.method == "POST"
    assert str(request.url) == "https://api.dida365.com/open/v1/project/inbox/task/t-1/complete"
    assert request.headers["Authorization"] == "Bearer tok-123"
    assert request.content == b""
    assert result is None


async def test_delete_task_uses_the_delete_verb():
    transport = FakeTransport(json={})
    client = DidaApiClient(token="tok-123", transport=transport)

    result = await client.delete_task("inbox", "t-1")

    request = transport.last_request
    assert request.method == "DELETE"
    assert str(request.url) == "https://api.dida365.com/open/v1/project/inbox/task/t-1"
    assert request.headers["Authorization"] == "Bearer tok-123"
    assert result is None


async def test_update_task_posts_to_the_task_id_with_id_and_project_id():
    transport = FakeTransport(json={"id": "t-1", "title": "新标题"})
    client = DidaApiClient(token="tok-123", transport=transport)

    updated = await client.update_task("inbox", "t-1", {"title": "新标题"})

    request = transport.last_request
    assert request.method == "POST"
    assert str(request.url) == "https://api.dida365.com/open/v1/task/t-1"
    assert request.headers["Authorization"] == "Bearer tok-123"
    assert transport.last_json == {"id": "t-1", "projectId": "inbox", "title": "新标题"}
    assert updated == {"id": "t-1", "title": "新标题"}


async def test_list_completed_posts_the_window_and_project_ids():
    completed = [{"id": "t-9", "status": 2}]
    transport = FakeTransport(json=completed)
    client = DidaApiClient(token="tok-123", transport=transport)

    tasks = await client.list_completed(
        project_ids=["inbox", "p-2"],
        start_date="2026-03-01T00:00:00+0800",
        end_date="2026-03-05T00:00:00+0800",
    )

    request = transport.last_request
    assert request.method == "POST"
    assert str(request.url) == "https://api.dida365.com/open/v1/task/completed"
    assert transport.last_json == {
        "projectIds": ["inbox", "p-2"],
        "startDate": "2026-03-01T00:00:00+0800",
        "endDate": "2026-03-05T00:00:00+0800",
    }
    assert tasks == completed


async def test_list_completed_omits_the_fields_it_was_not_given():
    transport = FakeTransport(json=[])
    client = DidaApiClient(token="tok-123", transport=transport)

    await client.list_completed()

    assert transport.last_json == {}


async def test_list_projects_can_page_with_offset_and_limit():
    transport = FakeTransport(json=[])
    client = DidaApiClient(token="tok-123", transport=transport)

    await client.list_projects(offset=200, limit=200)

    assert str(transport.last_request.url) == (
        "https://api.dida365.com/open/v1/project?offset=200&limit=200"
    )


# --- 四个本地守卫 -----------------------------------------------------------
#
# 服务端对这四件事一声不吭，只在几天后表现为「任务怎么不对」。守卫必须在**发出请求之前**
# 抛结构化错误：断言 transport.requests == [] 就是「没发出去」。


async def test_illegal_due_date_is_rejected_before_the_request_is_sent():
    transport = FakeTransport(json={"id": "t-1"})
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(InvalidDateError) as caught:
        await client.create_task(
            {"title": "写周报", "projectId": "inbox", "dueDate": "2026/03/05 09:00"}
        )

    assert caught.value.field == "dueDate"
    assert transport.requests == []


async def test_update_rejects_an_illegal_date_before_sending():
    transport = FakeTransport(json={"id": "t-1"})
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(InvalidDateError) as caught:
        await client.update_task("inbox", "t-1", {"dueDate": "2026-03-05 09:00"})

    assert caught.value.field == "dueDate"
    assert transport.requests == []


async def test_an_illegal_checklist_item_date_is_rejected_too():
    transport = FakeTransport(json={"id": "t-1"})
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(InvalidDateError) as caught:
        await client.update_task(
            "inbox",
            "t-1",
            {"items": [{"id": "i-1", "title": "子任务", "startDate": "明天"}]},
        )

    assert caught.value.field == "items[0].startDate"
    assert transport.requests == []


async def test_the_completed_window_is_a_date_field_too():
    transport = FakeTransport(json=[])
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(InvalidDateError) as caught:
        await client.list_completed(start_date="上周")

    assert caught.value.field == "startDate"
    assert transport.requests == []


async def test_a_datetime_end_date_is_rejected_as_a_structured_error_not_a_bare_type_error():
    """窗口的另一端（``endDate``）和 ``startDate`` 是同一类字段（工单 #26）。

    漏掉它的时候，``datetime`` 会一路走到 json 编码器，漏出裸 ``TypeError``——
    spec 说这一层不把裸异常抛给 UI，所以这里断言的是**结构化**的那一个。
    """
    transport = FakeTransport(json=[])
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(InvalidDateError) as caught:
        await client.list_completed(end_date=datetime(2026, 3, 5, 9, 30))

    assert caught.value.field == "endDate"
    assert transport.requests == []


#: 已完成流窗口的两端：同一套守卫，两个不同的字段名（工单 #26）。
COMPLETED_WINDOW_BOUNDS = [("start_date", "startDate"), ("end_date", "endDate")]


@pytest.mark.parametrize(("bound", "field"), COMPLETED_WINDOW_BOUNDS)
@pytest.mark.parametrize("rejected", [datetime(2026, 3, 5, 9, 30), "2026-03-05", "上周"])
async def test_each_end_of_the_completed_window_rejects_the_same_illegal_forms(
    bound, field, rejected
):
    """窗口两端行为一致：naive ``datetime``、裸日期、非日期字符串都不许发出去。

    裸日期（``2026-03-05``）是最阴的一种：日期正则不认它，但它是**合法 JSON**，
    漏过去服务端只会静默忽略这个窗口。
    """
    transport = FakeTransport(json=[])
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(InvalidDateError) as caught:
        await client.list_completed(**{bound: rejected})

    assert caught.value.field == field
    assert transport.requests == []


@pytest.mark.parametrize(("bound", "field"), COMPLETED_WINDOW_BOUNDS)
async def test_each_end_of_the_completed_window_serializes_a_datetime_the_same_way(bound, field):
    """合法输入照旧：字符串原样回写，带时区的 ``datetime`` 按自己的 offset 序列化。"""
    transport = FakeTransport(json=[])
    client = DidaApiClient(token="tok-123", transport=transport)
    await client.list_completed(**{bound: "2026-03-05T09:00:00+0800"})
    assert transport.last_json[field] == "2026-03-05T09:00:00+0800"  # 不是 +08:00

    transport = FakeTransport(json=[])
    client = DidaApiClient(token="tok-123", transport=transport)
    await client.list_completed(
        **{bound: datetime(2026, 3, 5, 9, 30, tzinfo=timezone(timedelta(hours=8)))}
    )
    assert transport.last_json[field] == "2026-03-05T09:30:00+0800"  # 不是 01:30:00+0000


async def test_a_checklist_items_completed_time_is_a_guarded_date_field_too():
    """子任务的 ``completedTime`` 与任务上那个同名同姓：也是服务端给的、原样带回的值。

    漏掉它和漏掉 ``endDate`` 是同一类（``ChecklistItem`` 的字段表里就有它）：
    ``datetime`` 会漏成裸 ``TypeError``，非法字符串会被静默丢掉。
    """
    transport = FakeTransport(json={"id": "t-1"})
    client = DidaApiClient(token="tok-123", transport=transport)
    await client.update_task(
        "inbox",
        "t-1",
        {"items": [{"id": "i-1", "completedTime": "2026-03-05T09:00:00+0800"}]},
    )
    assert transport.last_json["items"][0]["completedTime"] == "2026-03-05T09:00:00+0800"

    utc_noon = datetime(2026, 3, 5, 9, 30, tzinfo=timezone.utc)
    transport = FakeTransport(json={"id": "t-1"})
    client = DidaApiClient(token="tok-123", transport=transport)
    await client.update_task(
        "inbox",
        "t-1",
        {"items": [{"id": "i-1", "completedTime": utc_noon}]},
    )
    assert transport.last_json["items"][0]["completedTime"] == "2026-03-05T09:30:00+0000"

    transport = FakeTransport(json={"id": "t-1"})
    client = DidaApiClient(token="tok-123", transport=transport)
    with pytest.raises(InvalidDateError) as caught:
        await client.update_task(
            "inbox", "t-1", {"items": [{"id": "i-1", "completedTime": "昨天"}]}
        )
    assert caught.value.field == "items[0].completedTime"
    assert transport.requests == []


def _body_field(body: Any, path: str) -> Any:
    """按 ``items[0].completedTime`` 这种路径（api-contracts.md 的写法）从请求体里取值。"""
    for part in path.split("."):
        name, _, index = part.partition("[")
        body = body[name]
        if index:
            body = body[int(index.rstrip("]"))]
    return body


#: 请求体里**所有**带日期的字段，一次列全：字段路径（与 api-contracts.md 的 ``Task`` /
#: ``ChecklistItem`` 字段表一致）+ 怎么把一个值放进那次请求 + 那次调用的响应形状。
#: 这张表是独立抄的，不从守卫的常量里生成——守卫漏字段时它才会红。
DATE_FIELD_CASES = [
    (
        "create_task.startDate",
        "startDate",
        lambda client, value: client.create_task(
            {"title": "写周报", "projectId": "inbox", "startDate": value}
        ),
        {"id": "t-1"},
    ),
    (
        "create_task.dueDate",
        "dueDate",
        lambda client, value: client.create_task(
            {"title": "写周报", "projectId": "inbox", "dueDate": value}
        ),
        {"id": "t-1"},
    ),
    (
        "create_task.completedTime",
        "completedTime",
        lambda client, value: client.create_task(
            {"title": "写周报", "projectId": "inbox", "completedTime": value}
        ),
        {"id": "t-1"},
    ),
    (
        "update_task.startDate",
        "startDate",
        lambda client, value: client.update_task("inbox", "t-1", {"startDate": value}),
        {"id": "t-1"},
    ),
    (
        "update_task.dueDate",
        "dueDate",
        lambda client, value: client.update_task("inbox", "t-1", {"dueDate": value}),
        {"id": "t-1"},
    ),
    (
        "update_task.items[0].startDate",
        "items[0].startDate",
        lambda client, value: client.update_task(
            "inbox", "t-1", {"items": [{"id": "i-1", "startDate": value}]}
        ),
        {"id": "t-1"},
    ),
    (
        "update_task.items[0].completedTime",
        "items[0].completedTime",
        lambda client, value: client.update_task(
            "inbox", "t-1", {"items": [{"id": "i-1", "completedTime": value}]}
        ),
        {"id": "t-1"},
    ),
    (
        "list_completed.startDate",
        "startDate",
        lambda client, value: client.list_completed(start_date=value),
        [],
    ),
    (
        "list_completed.endDate",
        "endDate",
        lambda client, value: client.list_completed(end_date=value),
        [],
    ),
]


async def test_every_date_field_in_every_request_body_is_guarded_before_it_is_sent():
    """横着扫一遍日期字段：**任何**一个字段收到非法值，都在发请求之前结构化报错。

    ``endDate`` 曾经不在任何清单里（工单 #26）：拿它当字段名逐个端点数一遍，
    才是「同类漏网还有没有」的答案，而不是只看被报告的那一个。
    新端点带日期字段时，往 :data:`DATE_FIELD_CASES` 里加一行——表在，漏网就在。
    """
    rejected = "上周"  # 服务端会静默忽略的写法：请求成功、窗口没生效
    for name, path, call, payload in DATE_FIELD_CASES:
        transport = FakeTransport(json=payload)
        client = DidaApiClient(token="tok-123", transport=transport)

        with pytest.raises(InvalidDateError) as caught:
            await call(client, rejected)

        assert caught.value.field == path, f"{name} 报的字段名不对"
        assert transport.requests == [], f"{name} 把非法日期发出去了"


async def test_no_date_field_in_any_request_body_leaves_a_datetime_object_behind():
    """另一半：合法输入必须是**字符串**才出门，否则 json 编码器会漏裸 ``TypeError``。

    带时区的 ``datetime`` 是合法输入，所以这里断言它被序列化成了文档形式，
    而不是「没抛异常就算过」——裸 ``TypeError`` 恰恰是工单 #26 报出来的那一个。
    """
    aware = datetime(2026, 3, 5, 9, 30, tzinfo=timezone(timedelta(hours=8)))
    for name, path, call, payload in DATE_FIELD_CASES:
        transport = FakeTransport(json=payload)
        client = DidaApiClient(token="tok-123", transport=transport)

        await call(client, aware)

        assert _body_field(transport.last_json, path) == "2026-03-05T09:30:00+0800", name


async def test_a_repeat_rule_on_a_dateless_task_never_leaves_the_client():
    transport = FakeTransport(json={"id": "t-1"})
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(DatelessRepeatError) as caught:
        await client.create_task(
            {
                "title": "每天倒垃圾",
                "projectId": "inbox",
                "repeatFlag": "RRULE:FREQ=DAILY;INTERVAL=1",
            }
        )

    assert caught.value.field == "repeatFlag"
    assert transport.requests == []


async def test_a_repeat_rule_with_a_due_date_is_fine():
    transport = FakeTransport(json={"id": "t-1"})
    client = DidaApiClient(token="tok-123", transport=transport)

    await client.create_task(
        {
            "title": "每天倒垃圾",
            "projectId": "inbox",
            "dueDate": "2026-03-05T21:00:00+0800",
            "repeatFlag": "RRULE:FREQ=DAILY;INTERVAL=1",
        }
    )

    assert transport.last_json["repeatFlag"] == "RRULE:FREQ=DAILY;INTERVAL=1"


async def test_a_repeat_rule_with_only_a_start_date_is_fine_too():
    transport = FakeTransport(json={"id": "t-1"})
    client = DidaApiClient(token="tok-123", transport=transport)

    await client.create_task(
        {
            "title": "每天倒垃圾",
            "projectId": "inbox",
            "startDate": "2026-03-05T21:00:00+0800",
            "repeatFlag": "RRULE:FREQ=DAILY;INTERVAL=1",
        }
    )

    assert transport.last_json["repeatFlag"] == "RRULE:FREQ=DAILY;INTERVAL=1"


async def test_clearing_the_repeat_rule_on_a_dateless_task_is_allowed():
    transport = FakeTransport(json={"id": "t-1"})
    client = DidaApiClient(token="tok-123", transport=transport)

    await client.update_task("inbox", "t-1", {"repeatFlag": None})

    assert transport.last_json["repeatFlag"] is None


async def test_update_rejects_a_repeat_rule_on_a_dateless_task():
    transport = FakeTransport(json={"id": "t-1"})
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(DatelessRepeatError):
        await client.update_task("inbox", "t-1", {"repeatFlag": "RRULE:FREQ=WEEKLY"})

    assert transport.requests == []


#: 服务端返回的一份任务：里面有我们不认识的字段（手机端设置的），有 Asia/Shanghai，
#: 截止时间带 ``+0800``。写回时这三样都必须逐字节还在。
SERVER_TASK = {
    "id": "t-1",
    "projectId": "inbox",
    "title": "写周报",
    "dueDate": "2026-03-05T09:00:00+0800",
    "timeZone": "Asia/Shanghai",
    "focusSummaries": [{"focusId": "f-1"}],
    "kind": "TEXT",
    "status": 0,
}


async def test_writing_back_carries_unknown_fields_timezone_and_the_exact_due_date():
    transport = FakeTransport(json=dict(SERVER_TASK))
    client = DidaApiClient(token="tok-123", transport=transport)
    await client.get_task("inbox", "t-1")

    await client.update_task("inbox", "t-1", {"title": "写月报"})

    body = transport.last_json
    assert body["title"] == "写月报"
    assert body["focusSummaries"] == [{"focusId": "f-1"}]
    assert body["kind"] == "TEXT"
    assert body["timeZone"] == "Asia/Shanghai"
    assert body["dueDate"] == "2026-03-05T09:00:00+0800"  # 不是 01:00:00+0000
    assert body["id"] == "t-1"
    assert body["projectId"] == "inbox"


async def test_a_full_refresh_arms_the_carry_back():
    transport = FakeTransport(json={"project": {"id": "inbox"}, "tasks": [dict(SERVER_TASK)]})
    client = DidaApiClient(token="tok-123", transport=transport)

    await client.get_project_data("inbox")  # ADR 0001：刷新的那一次调用
    await client.update_task("inbox", "t-1", {"title": "写月报"})

    body = transport.last_json
    assert body["title"] == "写月报"
    assert body["focusSummaries"] == [{"focusId": "f-1"}]
    assert body["timeZone"] == "Asia/Shanghai"


async def test_an_explicit_snapshot_is_used_when_the_client_has_not_seen_the_task():
    transport = FakeTransport(json=dict(SERVER_TASK))
    client = DidaApiClient(token="tok-123", transport=transport)

    await client.update_task("inbox", "t-1", {"title": "写月报"}, snapshot=dict(SERVER_TASK))

    body = transport.last_json
    assert body["title"] == "写月报"
    assert body["focusSummaries"] == [{"focusId": "f-1"}]
    assert body["dueDate"] == "2026-03-05T09:00:00+0800"


async def test_the_created_task_is_remembered_for_the_next_write():
    transport = FakeTransport(json=dict(SERVER_TASK))
    client = DidaApiClient(token="tok-123", transport=transport)

    await client.create_task({"title": "写周报", "projectId": "inbox"})
    await client.update_task("inbox", "t-1", {"title": "写月报"})

    assert transport.last_json["kind"] == "TEXT"


async def test_a_repeat_rule_survives_an_update_that_carries_its_due_date_back():
    repeating = {
        "id": "t-2",
        "projectId": "inbox",
        "title": "每天倒垃圾",
        "dueDate": "2026-03-05T21:00:00+0800",
        "timeZone": "Asia/Shanghai",
        "repeatFlag": "RRULE:FREQ=DAILY;INTERVAL=1",
    }
    transport = FakeTransport(json=dict(repeating))
    client = DidaApiClient(token="tok-123", transport=transport)
    await client.get_task("inbox", "t-2")

    await client.update_task("inbox", "t-2", {"title": "每天倒垃圾（改标题）"})

    body = transport.last_json
    assert body["repeatFlag"] == "RRULE:FREQ=DAILY;INTERVAL=1"
    assert body["dueDate"] == "2026-03-05T21:00:00+0800"


# --- 字段被静默忽略：status（api-contracts.md 第 5 条） ----------------------


async def test_status_is_refused_because_the_server_would_ignore_it():
    transport = FakeTransport(json=dict(SERVER_TASK))
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(FieldIgnoredError) as caught:
        await client.update_task("inbox", "t-1", {"status": 2})

    assert caught.value.field == "status"
    assert transport.requests == []  # 完成要走 complete_task，不是写 status（ADR 0002）

    with pytest.raises(FieldIgnoredError):
        await client.create_task({"title": "写周报", "projectId": "inbox", "status": 2})

    assert transport.requests == []


async def test_the_snapshot_status_is_not_written_back():
    transport = FakeTransport(json=dict(SERVER_TASK))
    client = DidaApiClient(token="tok-123", transport=transport)
    await client.get_task("inbox", "t-1")

    await client.update_task("inbox", "t-1", {"title": "写月报"})

    assert "status" not in transport.last_json


# --- 失败一律结构化 ---------------------------------------------------------


async def test_forbidden_is_an_auth_error_too_even_with_no_content():
    transport = FakeTransport()
    transport.enqueue(httpx.Response(403))  # 文档：401/403/404 都可能没有响应体
    client = DidaApiClient(token="bad-token", transport=transport)

    with pytest.raises(AuthError) as caught:
        await client.list_tags()

    assert caught.value.status_code == 403


async def test_not_found_is_classified_by_status_code_not_by_a_payload():
    transport = FakeTransport()
    transport.enqueue(httpx.Response(404, content=b""))
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(ServerRejectionError) as caught:
        await client.get_task("inbox", "t-404")

    assert caught.value.status_code == 404


async def test_a_2xx_that_is_not_json_is_a_structured_error():
    transport = FakeTransport()
    transport.enqueue(httpx.Response(200, content=b"<html>captive portal</html>"))
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(MalformedResponseError) as caught:
        await client.list_projects()

    assert caught.value.status_code == 200
    assert isinstance(caught.value.__cause__, ValueError)


async def test_an_aware_datetime_keeps_its_own_wall_clock_and_offset():
    transport = FakeTransport(json={"id": "t-1"})
    client = DidaApiClient(token="tok-123", transport=transport)

    await client.create_task(
        {
            "title": "写周报",
            "projectId": "inbox",
            "dueDate": datetime(2026, 3, 5, 9, 30, tzinfo=timezone(timedelta(hours=8))),
        }
    )

    assert transport.last_json["dueDate"] == "2026-03-05T09:30:00+0800"


async def test_an_aware_datetime_with_sub_seconds_keeps_them():
    transport = FakeTransport(json={"id": "t-1"})
    client = DidaApiClient(token="tok-123", transport=transport)

    await client.create_task(
        {
            "title": "写周报",
            "projectId": "inbox",
            "dueDate": datetime(2026, 3, 5, 9, 30, 0, 500000, tzinfo=timezone.utc),
        }
    )

    assert transport.last_json["dueDate"] == "2026-03-05T09:30:00.500+0000"


async def test_a_naive_datetime_is_rejected_rather_than_given_an_offset():
    transport = FakeTransport(json={"id": "t-1"})
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(InvalidDateError) as caught:
        await client.create_task(
            {"title": "写周报", "projectId": "inbox", "dueDate": datetime(2026, 3, 5, 9, 30)}
        )

    assert caught.value.field == "dueDate"
    assert transport.requests == []


# --- 2xx 但形状不对：一律结构化错误 -----------------------------------------
#
# 「2xx」只说明服务端没报错，不说明载荷是文档说的那个形状。清单被删、代理插了个数组、
# 服务端改版漏字段——裸着往下走，调用方取字段时漏出的就是 AttributeError / KeyError /
# TypeError，绕过整个 DidaError 族（工单 #24）。每个端点在这里声明自己期望的形状。


async def test_a_non_object_project_data_payload_is_a_structured_error():
    """``GET .../data`` 回了 ``200`` + ``[]``：结构化错误，不是取字段时的裸 AttributeError。"""
    transport = FakeTransport(json=[])
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(MalformedResponseError) as caught:
        await client.get_project_data("inbox")

    assert caught.value.status_code == 200


async def test_get_task_that_gets_an_array_is_a_structured_error():
    transport = FakeTransport(json=[{"id": "t-1"}])
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(MalformedResponseError) as caught:
        await client.get_task("inbox", "t-1")

    assert caught.value.status_code == 200


async def test_create_task_that_gets_an_array_is_a_structured_error():
    transport = FakeTransport(json=[{"id": "t-1"}])
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(MalformedResponseError):
        await client.create_task({"title": "写周报", "projectId": "inbox"})


async def test_update_task_that_gets_an_array_is_a_structured_error():
    transport = FakeTransport(json=[{"id": "t-1"}])
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(MalformedResponseError):
        await client.update_task("inbox", "t-1", {"title": "写月报"})


async def test_a_task_payload_without_an_id_is_a_structured_error():
    """任务原文缺 ``id``：认不出是哪条任务，别让它在存储层变成裸 ``KeyError``。"""
    transport = FakeTransport(json={"title": "写周报"})
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(MalformedResponseError):
        await client.get_task("inbox", "t-1")


async def test_project_data_whose_tasks_is_not_an_array_is_a_structured_error():
    transport = FakeTransport(json={"project": {"id": "inbox"}, "tasks": {"t1": "写周报"}})
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(MalformedResponseError):
        await client.get_project_data("inbox")


async def test_a_task_inside_project_data_without_an_id_is_a_structured_error():
    transport = FakeTransport(json={"project": {"id": "inbox"}, "tasks": [{"title": "写周报"}]})
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(MalformedResponseError):
        await client.get_project_data("inbox")


async def test_project_data_whose_project_is_not_an_object_is_a_structured_error():
    transport = FakeTransport(json={"project": [{"id": "inbox"}], "tasks": []})
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(MalformedResponseError):
        await client.get_project_data("inbox")


async def test_project_data_may_leave_project_and_tasks_out():
    """``project`` / ``tasks`` 缺席是合法数据（这个清单就是空的），不是形状错误。"""
    transport = FakeTransport(json={"columns": []})
    client = DidaApiClient(token="tok-123", transport=transport)

    payload = await client.get_project_data("inbox")

    assert payload == {"columns": []}


async def test_no_endpoint_ever_leaks_a_bare_exception_on_a_wrong_shape():
    """把契约按类钉死：2xx + 形状不对 ⇒ ``DidaError``，绝不漏裸异常给调用方。

    逐个端点的例子在上面；这一条是横着扫一遍——任何端点、任何非预期载荷，
    漏出来的都不许是 ``AttributeError`` / ``KeyError`` / ``TypeError``。
    """
    payloads: list[Any] = [
        [],
        {},
        None,
        "ok",
        7,
        {"project": [], "tasks": {}},
        [{"name": "没有 id"}],
    ]
    calls = [
        ("list_projects", lambda client: client.list_projects()),
        ("list_tags", lambda client: client.list_tags()),
        ("list_completed", lambda client: client.list_completed()),
        ("get_project_data", lambda client: client.get_project_data("inbox")),
        ("get_task", lambda client: client.get_task("inbox", "t-1")),
        (
            "create_task",
            lambda client: client.create_task({"title": "写周报", "projectId": "inbox"}),
        ),
        ("update_task", lambda client: client.update_task("inbox", "t-1", {"title": "写月报"})),
    ]

    for payload in payloads:
        for name, call in calls:
            transport = FakeTransport(json=payload)
            client = DidaApiClient(token="tok-123", transport=transport)
            try:
                await call(client)
            except DidaError:
                pass
            except Exception as exc:  # 裸异常就是这条工单要挡的东西
                pytest.fail(
                    f"{name} 收到 {payload!r} 时漏出了裸 {type(exc).__name__}：{exc}"
                )


async def test_list_projects_that_gets_an_object_is_a_structured_error():
    """清单索引本该是数组：一个 ``{}`` 被当成「没有清单」，用户看到的是整屏空，且不报错。"""
    transport = FakeTransport(json={"id": "inbox", "name": "收集箱"})
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(MalformedResponseError) as caught:
        await client.list_projects()

    assert caught.value.status_code == 200


async def test_a_project_entry_that_is_not_an_object_is_a_structured_error():
    transport = FakeTransport(json=["inbox"])
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(MalformedResponseError):
        await client.list_projects()


async def test_a_project_without_an_id_is_a_structured_error():
    transport = FakeTransport(json=[{"name": "收集箱"}])
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(MalformedResponseError):
        await client.list_projects()


async def test_list_tags_that_gets_an_object_is_a_structured_error():
    transport = FakeTransport(json={"name": "工作"})
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(MalformedResponseError):
        await client.list_tags()


async def test_a_tag_without_a_name_is_a_structured_error():
    transport = FakeTransport(json=[{"label": "工作", "sortOrder": 1}])
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(MalformedResponseError):
        await client.list_tags()


async def test_list_completed_that_gets_an_object_is_a_structured_error():
    transport = FakeTransport(json={"tasks": []})
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(MalformedResponseError):
        await client.list_completed()


async def test_a_completed_task_without_an_id_is_a_structured_error():
    transport = FakeTransport(json=[{"title": "写周报", "status": 2}])
    client = DidaApiClient(token="tok-123", transport=transport)

    with pytest.raises(MalformedResponseError):
        await client.list_completed()

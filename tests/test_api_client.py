"""接缝二：HTTP 传输层可注入。

只钉请求形状（方法、URL、认证头、请求体字段）与失败的结构化表达，
不在这里测任何业务逻辑。
"""

from dida.api.client import DidaApiClient
from dida.api.errors import (
    AuthError,
    DatelessRepeatError,
    FieldIgnoredError,
    InvalidDateError,
    MalformedResponseError,
    NetworkError,
    ServerRejectionError,
)
from dida.testing import FakeTransport
from datetime import datetime, timedelta, timezone
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

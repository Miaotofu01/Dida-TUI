"""接缝二：HTTP 传输层可注入。

只钉请求形状（方法、URL、认证头、请求体字段）与失败的结构化表达，
不在这里测任何业务逻辑。
"""

from dida.api.client import DidaApiClient
from dida.api.errors import AuthError, NetworkError, ServerRejectionError
from dida.testing import FakeTransport
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

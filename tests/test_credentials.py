"""首次运行的凭据：粘贴一次、验证通过才落盘（用户故事 1 + 2 + 3 + 7）。

**这不是界面测试**：``paste_token`` 是组合根上的一条独立路径（``dida.bootstrap``），
断的是文件有没有落盘、权限对不对、请求发了几个，以及 token 会不会漏进错误消息里。
v1 把这三条放在 ``tests/test_sync_session.py`` 里（那个文件随三栏界面一起作废），
结论一条不少地搬到这里——凭据这一块与界面无关，不该跟着界面重写一起消失。
"""

from __future__ import annotations

import httpx
import pytest

from dida.api.errors import NetworkError
from dida.bootstrap import paste_token
from dida.config import CredentialsError, load_config

TOKEN = "tok-1234"


class Transport:
    """假传输：按状态码或异常应答，并记下每一个请求。"""

    def __init__(
        self, *, status: int | None = None, error: Exception | None = None, json: object = None
    ) -> None:
        self.status = status
        self.error = error
        self.json = [] if json is None else json
        self.requests: list[httpx.Request] = []

    async def send(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.status is not None:
            return httpx.Response(self.status)
        return httpx.Response(200, json=self.json)


async def test_the_first_run_verifies_the_token_before_storing_it(tmp_path):
    """粘贴 → 一次「列清单」验证 → 通过才落盘（0600）。"""
    path = tmp_path / "config.toml"
    transport = Transport(json=[{"id": "inbox", "name": "收集箱", "sortOrder": 0}])

    config = await paste_token(transport=transport, prompt=lambda _: f"  {TOKEN}  ", path=path)

    assert config.token == TOKEN, "粘贴来的空格要去掉"
    assert load_config(path).token == TOKEN, "验证通过才落盘"
    assert (path.stat().st_mode & 0o777) == 0o600, "配置文件权限 0600"
    assert [request.url.path for request in transport.requests] == ["/open/v1/project"], (
        "验证就是一次「列清单」"
    )
    assert transport.requests[0].headers["Authorization"] == f"Bearer {TOKEN}", "token 用在请求头里"


async def test_a_rejected_token_is_not_stored_and_never_printed(tmp_path):
    """验证没过（网络失败）：什么都不落盘，而且 token 一个字都不许出现在错误里。"""
    path = tmp_path / "config.toml"
    transport = Transport(error=NetworkError("连不上"))

    with pytest.raises(Exception) as caught:
        await paste_token(transport=transport, prompt=lambda _: TOKEN, path=path)

    assert TOKEN not in str(caught.value), "token 不许进错误消息（日志、回滚都看得到）"
    assert not path.exists(), "验证没过就一个文件都不该写"


async def test_an_auth_rejection_tells_the_user_to_paste_again(tmp_path):
    """401/403：明确说「凭据失效，重新粘贴」，而不是笼统的网络错误（用户故事 7）。"""
    path = tmp_path / "config.toml"
    transport = Transport(status=401)

    with pytest.raises(CredentialsError) as caught:
        await paste_token(transport=transport, prompt=lambda _: TOKEN, path=path)

    assert "重新粘贴" in str(caught.value)
    assert TOKEN not in str(caught.value)
    assert not path.exists()

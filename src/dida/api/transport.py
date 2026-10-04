"""HTTP 传输接缝。

客户端只依赖 :class:`Transport` 协议；测试注入 ``dida.testing.FakeTransport``，
生产用 :class:`HttpxTransport`。所有请求都是 **异步** 的：TUI 单进程，
网络等待不能阻塞界面。
"""

from __future__ import annotations

from typing import Protocol

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(10.0)


class Transport(Protocol):
    """发出一个请求，拿回一个响应。"""

    async def send(self, request: httpx.Request) -> httpx.Response:
        """传输层失败必须抛 ``httpx.TransportError``；其它异常视为 bug。"""
        ...


class HttpxTransport:
    """真实传输：包一层 ``httpx.AsyncClient``。"""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client if client is not None else httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
        self._owns_client = client is None

    async def send(self, request: httpx.Request) -> httpx.Response:
        return await self._client.send(request)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

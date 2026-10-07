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
    """真实传输：包一层 ``httpx.AsyncClient``。

    **不读环境里的代理设置**（``trust_env=False``）。``httpx`` 默认会把 ``all_proxy`` /
    ``ALL_PROXY`` / ``http_proxy`` 这些环境变量当成自己的代理配置，而这个 app 说的是
    ``api.dida365.com``——一个直连就通的国内服务。让一个终端里为别的工具设的变量决定
    「这个 app 有没有网」，代价是实测过的：本机 ``all_proxy=socks5://…`` 而没装 ``socksio``
    时，``httpx.AsyncClient()`` 当场抛 ``ImportError``，整个 app 退化成「只能看缓存」，
    连它自己的 token 都验不了（``bootstrap._UnusableTransport`` 就是为这一幕写的）。

    所以网络怎么走由这个 app 自己说了算。要让它走代理，得写进它自己的配置——需要的时候再加。
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = (
            client
            if client is not None
            else httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, trust_env=False)
        )
        self._owns_client = client is None

    async def send(self, request: httpx.Request) -> httpx.Response:
        return await self._client.send(request)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

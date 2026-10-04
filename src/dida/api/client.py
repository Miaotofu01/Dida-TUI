"""滴答 API 客户端（第 2 个深模块）。

本工单只钉住两件事，其余端点与四个本地守卫由 t07 补全：

- 传输层可注入（``dida.api.transport.Transport``），测试能断言请求形状；
- 失败一律表达为 ``dida.api.errors`` 里的结构化错误。

网络层永远是全量刷新（ADR 0001）：未完成任务只能逐清单 ``GET .../data`` 拉，
日期窗口只允许用在已完成流上。
"""

from __future__ import annotations

from typing import Any, Mapping

import httpx

from dida.api.errors import AuthError, DidaError, NetworkError, ServerRejectionError
from dida.api.transport import Transport

DEFAULT_BASE_URL = "https://api.dida365.com"


class DidaApiClient:
    """滴答 Open API 的薄封装。"""

    def __init__(
        self,
        *,
        token: str,
        transport: Transport,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self._token = token
        self._transport = transport
        self._base_url = base_url.rstrip("/")

    async def get_project_data(self, project_id: str) -> dict[str, Any]:
        """GET /open/v1/project/{id}/data —— 该清单的未完成任务全量，无分页。"""
        response = await self._send(self._request("GET", f"/open/v1/project/{project_id}/data"))
        return response.json()

    async def create_task(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """POST /open/v1/task —— 请求体透传，字段校验与守卫归 t07。"""
        response = await self._send(self._request("POST", "/open/v1/task", body=body))
        return response.json()

    def _request(
        self, method: str, path: str, *, body: Mapping[str, Any] | None = None
    ) -> httpx.Request:
        kwargs: dict[str, Any] = {} if body is None else {"json": body}
        return httpx.Request(
            method,
            f"{self._base_url}{path}",
            headers={"Authorization": f"Bearer {self._token}"},
            **kwargs,
        )

    async def _send(self, request: httpx.Request) -> httpx.Response:
        """发出请求，把失败翻译成结构化错误（t07 在此补守卫）。"""
        try:
            response = await self._transport.send(request)
        except httpx.TransportError as exc:
            raise NetworkError(str(exc)) from exc
        if response.status_code in (401, 403):
            raise AuthError(
                f"凭据被拒绝：HTTP {response.status_code}", status_code=response.status_code
            )
        if response.status_code >= 400:
            raise ServerRejectionError(
                f"服务端拒绝：HTTP {response.status_code}", status_code=response.status_code
            )
        return response


__all__ = ["DidaApiClient", "DidaError", "DEFAULT_BASE_URL"]

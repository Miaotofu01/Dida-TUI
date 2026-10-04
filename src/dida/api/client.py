"""滴答 API 客户端（第 2 个深模块）。

职责是窄的：Bearer 认证、逐端点的请求形状、以及**四个本地守卫**（t07 补全）。
成功路径上它不认识领域概念——服务端给什么字段，就原样带回来，一个也不丢。

- 传输层可注入（``dida.api.transport.Transport``），测试能断言请求形状；
- 失败一律表达为 ``dida.api.errors`` 里的结构化错误，裸异常不出这一层。

网络层永远是全量刷新（ADR 0001）：未完成任务只能逐清单 ``GET .../data`` 拉，
日期窗口只允许用在已完成流上。

**写路径为什么是「快照 + 改动」**：更新时把本地没见过、但服务端认识的那些字段
（手机端设置的）丢掉，是这张工单要挡的静默失败之一。所以读回来的每一份任务都会
被记住（``_snapshots``），更新时与本次改动合并后再发；调用方也可以显式递一份
快照进来（``snapshot=``），比如本地存储里那份更权威的。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

import httpx

from dida.api.errors import (
    AuthError,
    DidaError,
    MalformedResponseError,
    NetworkError,
    ServerRejectionError,
)
from dida.api.guards import (
    guard_writable,
    merge_snapshot,
    normalize_dates,
    prepare_write_body,
)
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
        #: 最近见过的任务原文，按任务 id 索引：写回时的合并底稿。
        self._snapshots: dict[str, dict[str, Any]] = {}

    async def list_projects(
        self, *, offset: int | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """GET /open/v1/project —— 清单分页（服务端 ``limit`` 默认 200）。

        ``offset`` / ``limit`` 只有在调用方给了才写进查询串：t03 用无参数的这一次调用
        验证凭据，多出查询参数会改变它的形状。
        """
        params: dict[str, Any] = {}
        if offset is not None:
            params["offset"] = offset
        if limit is not None:
            params["limit"] = limit
        response = await self._send(self._request("GET", "/open/v1/project", params=params))
        return self._payload_array(response, endpoint="清单索引", item_keys=("id",))

    async def list_tags(self) -> list[dict[str, Any]]:
        """GET /open/v1/tag —— 全部标签（``OpenTag``：name / label / sortOrder / color / type）。"""
        response = await self._send(self._request("GET", "/open/v1/tag"))
        return self._payload_array(response, endpoint="标签列表", item_keys=("name",))

    async def list_completed(
        self,
        *,
        project_ids: Sequence[str] | None = None,
        start_date: str | datetime | None = None,
        end_date: str | datetime | None = None,
    ) -> list[dict[str, Any]]:
        """POST /open/v1/task/completed —— 已完成流，按完成时间窗口。

        日期窗口**只允许**用在这里（ADR 0001）：未完成任务永远是逐清单全量拉。
        三个字段都是可选的，没给就不写进请求体。服务端一次最多回 200 条。
        """
        body: dict[str, Any] = {}
        if project_ids is not None:
            body["projectIds"] = list(project_ids)
        if start_date is not None:
            body["startDate"] = start_date
        if end_date is not None:
            body["endDate"] = end_date
        response = await self._send(
            self._request("POST", "/open/v1/task/completed", body=normalize_dates(body))
        )
        return self._payload_array(response, endpoint="已完成流", item_keys=("id",))

    async def get_project_data(self, project_id: str) -> dict[str, Any]:
        """GET /open/v1/project/{id}/data —— 该清单的未完成任务全量，无分页。

        这是全量刷新的那一次调用（ADR 0001），所以它顺带把每个任务的原文记进
        ``_snapshots``：刷新完就能安全地写回，不用先把任务再拉一遍。
        """
        response = await self._send(self._request("GET", f"/open/v1/project/{project_id}/data"))
        payload = self._payload_object(response, endpoint=f"清单 {project_id} 的 data")
        # ``project`` / ``tasks`` 缺席（或 ``null``）是合法数据：这个清单就是空的。给错形状
        # 才是错误——被安静地当成空清单，用户看到的是「我的任务不见了」。
        project = payload.get("project")
        if project is not None and not isinstance(project, Mapping):
            raise self._malformed(response, f"清单 {project_id} 的 project", "一个对象", project)
        tasks_endpoint = f"清单 {project_id} 的 tasks"
        tasks = payload.get("tasks")
        if tasks is not None:
            if not isinstance(tasks, list):
                raise self._malformed(response, tasks_endpoint, "一个数组", tasks)
            self._require_items(response, tasks, endpoint=tasks_endpoint, item_keys=("id",))
        for task in tasks or []:
            self._remember(task)
        return payload

    async def get_task(self, project_id: str, task_id: str) -> dict[str, Any]:
        """GET /open/v1/project/{projectId}/task/{taskId} —— 单个任务的完整字段。"""
        response = await self._send(
            self._request("GET", f"/open/v1/project/{project_id}/task/{task_id}")
        )
        return self._remember(
            self._payload_object(response, endpoint=f"任务 {task_id} 的原文", required=("id",))
        )

    async def create_task(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """POST /open/v1/task —— 请求体透传，但先过本地守卫（日期、重复规则、不可写字段）。"""
        guard_writable(body)
        response = await self._send(
            self._request("POST", "/open/v1/task", body=prepare_write_body(body))
        )
        return self._remember(self._payload_object(response, endpoint="新建任务的响应"))

    async def update_task(
        self,
        project_id: str,
        task_id: str,
        changes: Mapping[str, Any],
        *,
        snapshot: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /open/v1/task/{taskId} —— 更新任务（文档里**没有** PATCH，见 api-contracts.md）。

        ``id`` 与 ``projectId`` 是文档要求的必填字段，由参数给出，不劳调用方重复。
        ``snapshot`` 是服务端权威的那份完整任务（本地存储里那份）；省略时用客户端
        最近见过的同一任务。合并底稿上没有的字段才会真的缺席。

        文档没说省略的字段是被保留还是被清空（ticket #22 的实测问题）。合并着发在两种
        语义下都对：全量替换时这是必需的，部分更新时只是多带了几笔旧值。
        """
        guard_writable(changes)
        base = snapshot if snapshot is not None else self._snapshots.get(task_id)
        body = merge_snapshot(base, changes)
        body["id"] = task_id
        body["projectId"] = project_id
        response = await self._send(
            self._request("POST", f"/open/v1/task/{task_id}", body=prepare_write_body(body))
        )
        return self._remember(
            self._payload_object(response, endpoint=f"更新任务 {task_id} 的响应")
        )

    async def complete_task(self, project_id: str, task_id: str) -> None:
        """POST .../task/{taskId}/complete —— 无请求体、无响应体。"""
        await self._send(
            self._request("POST", f"/open/v1/project/{project_id}/task/{task_id}/complete")
        )

    async def delete_task(self, project_id: str, task_id: str) -> None:
        """DELETE .../task/{taskId} —— 响应体不可信，不解析。"""
        await self._send(self._request("DELETE", f"/open/v1/project/{project_id}/task/{task_id}"))

    def _payload(self, response: httpx.Response) -> Any:
        """解析 2xx 的响应体：不是 JSON 也要是结构化错误，不是裸 JSONDecodeError。"""
        try:
            return response.json()
        except ValueError as exc:
            raise MalformedResponseError(
                f"服务端返回 HTTP {response.status_code}，但响应体不是 JSON",
                status_code=response.status_code,
            ) from exc

    def _payload_object(
        self, response: httpx.Response, *, endpoint: str, required: Sequence[str] = ()
    ) -> Any:
        """2xx 的载荷必须是**对象**，且带上必需字段，否则结构化报错。

        取字段（``payload["tasks"]``、``payload.get(...)``）之前先在这里把形状钉死：
        服务端给个数组时，裸着往下走漏出来的是 ``AttributeError``，绕过整个 ``DidaError``
        族（工单 #24）。
        """
        payload = self._payload(response)
        if not isinstance(payload, Mapping):
            raise self._malformed(response, endpoint, "一个对象", payload)
        self._require(response, endpoint, payload, required)
        return payload

    def _payload_array(
        self, response: httpx.Response, *, endpoint: str, item_keys: Sequence[str] = ()
    ) -> Any:
        """2xx 的载荷必须是**数组**，且每一项都是带必需字段的对象。

        空数组是合法数据（就是没有清单 / 没有标签 / 这个窗口没完成过任务），不是错误；
        给个对象才是——``{}`` 被当成空数组，用户看到的是「我的清单不见了」而且不报错。
        """
        payload = self._payload(response)
        if not isinstance(payload, list):
            raise self._malformed(response, endpoint, "一个数组", payload)
        self._require_items(response, payload, endpoint=endpoint, item_keys=item_keys)
        return payload

    def _require_items(
        self,
        response: httpx.Response,
        items: Sequence[Any],
        *,
        endpoint: str,
        item_keys: Sequence[str] = (),
    ) -> None:
        """数组的每一项都必须是带必需字段的对象；第几项不对要能一眼看出来。"""
        for index, item in enumerate(items):
            where = f"{endpoint}[{index}]"
            if not isinstance(item, Mapping):
                raise self._malformed(response, where, "一个对象", item)
            self._require(response, where, item, item_keys)

    def _require(
        self,
        response: httpx.Response,
        endpoint: str,
        payload: Mapping[str, Any],
        required: Sequence[str],
    ) -> None:
        """必需字段缺席（或为空）＝形状不对：结构化报错，别留给调用方一个裸 ``KeyError``。"""
        missing = [key for key in required if not payload.get(key)]
        if missing:
            raise MalformedResponseError(
                f"{endpoint} 的响应缺字段：{'、'.join(missing)}",
                status_code=response.status_code,
            )

    def _malformed(
        self, response: httpx.Response, endpoint: str, expected: str, payload: Any
    ) -> MalformedResponseError:
        """形状不对时的结构化错误：带上端点与**实际**收到的东西，方便查。"""
        return MalformedResponseError(
            f"{endpoint} 期望{expected}，收到 {type(payload).__name__}",
            status_code=response.status_code,
        )

    def _remember(self, task: Any) -> Any:
        """记下服务端给的一份任务原文（写回时的合并底稿），并原样返回。"""
        if isinstance(task, Mapping) and isinstance(task.get("id"), str):
            self._snapshots[task["id"]] = dict(task)
        return task

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> httpx.Request:
        kwargs: dict[str, Any] = {} if body is None else {"json": body}
        return httpx.Request(
            method,
            f"{self._base_url}{path}",
            headers={"Authorization": f"Bearer {self._token}"},
            params=params or None,
            **kwargs,
        )

    async def _send(self, request: httpx.Request) -> httpx.Response:
        """发出请求，把失败翻译成结构化错误。

        按**状态码**分类，不解析错误载荷：文档里 401/403/404 都可能没有响应体
        （见 api-contracts.md）。传输层失败是 ``NetworkError``，401/403 是
        ``AuthError``，其它 ≥400 是 ``ServerRejectionError``，都带 ``status_code``。
        """
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

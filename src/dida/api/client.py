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
    BatchRejectedError,
    DidaError,
    MalformedResponseError,
    NetworkError,
    ServerRejectionError,
)
from dida.api.guards import (
    guard_writable,
    merge_snapshot,
    prepare_batch_body,
    prepare_completed_window_body,
    prepare_project_body,
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

    async def create_project(self, body: Mapping[str, Any]) -> dict[str, Any] | None:
        """POST /open/v1/project —— 新建清单（工单 #42）。

        文档给了**两种成功形状**：``200 → Project`` 与 ``201 → No Content``（:1193–1194），
        而且没说什么时候给哪一种。所以返回类型是「那一份清单，或者 ``None``」——把
        「201 没有响应体」当成坏数据，会在**成功**那条路上抛 ``MalformedResponseError``，
        而这条路只有真的连一次服务端才会走到。

        请求体原样透传，形状由调用方按文档给（名字、颜色）：这里不认识领域概念。
        """
        response = await self._send(self._request("POST", "/open/v1/project", body=body))
        return self._payload_object_or_none(response, endpoint="新建清单的响应")

    async def update_project(
        self,
        project_id: str,
        changes: Mapping[str, Any],
        *,
        snapshot: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """POST /open/v1/project/{projectId} —— 改清单（文档里是 POST，不是 PATCH，:1228）。

        ``snapshot`` 是本地那份清单原文：不打算改的字段靠它 echo 回去，
        :func:`~dida.api.guards.prepare_project_body` 再把请求体收窄到文档接受的字段。
        ``sortOrder`` 尤其要紧——文档写着 "default 0"，省略它可能把用户的清单顺序重置
        （api-shapes §B10）。

        成功形状同样有两种：``200 → Project`` / ``201 No Content``。
        """
        body = prepare_project_body(snapshot, changes)
        response = await self._send(
            self._request("POST", f"/open/v1/project/{project_id}", body=body)
        )
        return self._payload_object_or_none(response, endpoint=f"更新清单 {project_id} 的响应")

    async def batch_update(self, updates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """POST /open/v1/task/batch —— 批量更新（工单 #38：**取消完成的唯一路径**）。

        这条用法官方文档**一字未提**（：551–590 通篇没有 ``status``、也没有 ``delete`` 数组）：
        实测是批量更新带 ``status: 2`` 能把任务完成、带 ``status: 0`` 能把已完成的任务改回
        未完成（spec 的实测事实第 1 条）。所以它按「实测确认的用法」写，不写成文档化特性。

        批量更新是**合并语义**：每一条只发 :data:`~dida.api.guards.BATCH_UPDATE_FIELDS`
        那几个字段，标题 / 描述 / 备注 / 优先级一个都不发——多带一个字段就是拿本地那一份
        去覆盖服务端，那正是「更新时没带回去的字段会把手机端设置的东西抹掉」的另一半。

        响应里的 ``id2error`` 必须读（见 :meth:`_batch_errors`）：每个任务的失败藏在
        ``200 OK`` 里。
        """
        body = prepare_batch_body({"update": [dict(item) for item in updates]})
        response = await self._send(self._request("POST", "/open/v1/task/batch", body=body))
        payload = self._payload_object(response, endpoint="批量更新的响应")
        self._reject_batch_errors(payload, response)
        return payload

    @staticmethod
    def _reject_batch_errors(payload: Mapping[str, Any], response: httpx.Response) -> None:
        """``id2error`` 非空就是失败，哪怕 HTTP 是 200（openapi :567 的那张码表）。

        ``id2etag`` 不读：它不是结果，只是服务端顺手给的 etag，而且这一层不认识它。
        """
        errors = payload.get("id2error")
        if errors is None or errors == {}:
            return
        if not isinstance(errors, Mapping):
            raise MalformedResponseError(
                f"批量更新的响应里 id2error 期望一个对象，收到 {type(errors).__name__}",
                status_code=response.status_code,
            )
        listed = "、".join(f"{task_id}: {code}" for task_id, code in errors.items())
        raise BatchRejectedError(
            f"批量更新失败了 {len(errors)} 条（服务端返回 HTTP {response.status_code}）：{listed}",
            errors={str(task_id): str(code) for task_id, code in errors.items()},
            status_code=response.status_code,
        )

    async def delete_project(self, project_id: str) -> None:
        """DELETE /open/v1/project/{projectId} —— 删清单（工单 #42）。

        没有请求体；响应是 ``200 No Content``（401/403/404 也是 No Content，:1291–1294），
        所以响应体一个字节都不解析——「不可信」在这里就是字面意思。

        **服务端没有回收站、没有撤销删除的接口**：文档通篇搜不到 undelete / restore /
        已删除列表（api-shapes §A6）。删掉一个清单时它里面的任务会怎样，文档也一个字没说
        （:1278–1302 通篇只有路径、参数、响应表与一个请求示例），所以确认文案必须如实说
        「不知道」，并且不承诺任何恢复手段。
        """
        await self._send(self._request("DELETE", f"/open/v1/project/{project_id}"))

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
        窗口**两端**都过本地日期守卫，规则一致：合法字符串原样回写、带时区的
        ``datetime`` 按自己的 offset 序列化、其余形式结构化报错（工单 #26）。
        """
        body: dict[str, Any] = {}
        if project_ids is not None:
            body["projectIds"] = list(project_ids)
        if start_date is not None:
            body["startDate"] = start_date
        if end_date is not None:
            body["endDate"] = end_date
        response = await self._send(
            self._request(
                "POST",
                "/open/v1/task/completed",
                body=prepare_completed_window_body(body),
            )
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
        """POST /open/v1/task —— 请求体透传，但先过本地守卫（日期、重复规则、不可写字段）。

        成功形状有**两种**，文档两条都写着：``200 → Task`` 与 ``201 → No Content``。走
        ``_payload_object`` 的实现在第二种上会把一次**成功**报成
        ``MalformedResponseError``（「响应体不是 JSON」），于是推送按失败退避重试——而新建
        **不是幂等的**，重试就是让服务端多一条。所以空体照
        :meth:`_payload_object_or_none` 的口径读作「服务端没给那条任务」，返回 ``{}``。

        ``{}`` 这一份是**诚实**的：认领（``Store.adopt_created``）需要服务端给的 id，
        没有 id 就没有认领——本地那条临时任务活到下一次全量刷新，那时服务端的索引里已经有
        它了（真 id 那一行写进来、临时那一行被剪掉），屏幕上始终只有一条。排在那条临时 id
        后面的改动因此推不出去，写入那一侧当场拒绝它们（#53），不排一条永远失败的改动。
        """
        guard_writable(body)
        response = await self._send(
            self._request("POST", "/open/v1/task", body=prepare_write_body(body))
        )
        created = self._payload_object_or_none(response, endpoint="新建任务的响应")
        return self._remember(created) if created is not None else {}

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

    async def move_task(
        self, from_project_id: str, to_project_id: str, task_id: str
    ) -> list[dict[str, Any]]:
        """POST /open/v1/task/move —— 把一条任务搬去另一个清单（工单 #45）。

        **请求体是 JSON 数组**，不是对象（``openapi-dida365.md:504`` 的 "A JSON array
        containing task move operations"，例子 ``:530–536``）：一项一次搬运，
        ``fromProjectId`` / ``toProjectId`` / ``taskId`` 三个都必填（``:508–510``）。
        一次搬一条，所以数组里就一项——这是本客户端**唯一**一个数组请求体的端点。

        **响应也是数组**，每项 ``{id, etag}``（``:516``、例子 ``:542–547``）——**不是**
        被搬的那条 Task。所以返回类型是那个数组（调用方要的是「搬成功了」，那条任务的
        新样子由全量刷新带回来）。``201 → No Content``（``:517``）也是成功形状：空响应体
        给空数组，不按坏数据报错。

        ``inbox`` 这个别名在这份文档的这一节里**一次都没出现**（``:497–548``）：
        收集箱与真实清单之间能不能双向搬，文档沉默。这里不猜——调用方给什么 id 就发什么
        id（收集箱那一行带的是服务端返回的那个 id，见 ``sync.read.resolve_lists``）。
        """
        body = [
            {
                "fromProjectId": from_project_id,
                "toProjectId": to_project_id,
                "taskId": task_id,
            }
        ]
        response = await self._send(self._request("POST", "/open/v1/task/move", body=body))
        return self._payload_array_or_none(response, endpoint="搬运的响应")

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

    def _payload_object_or_none(
        self, response: httpx.Response, *, endpoint: str, required: Sequence[str] = ()
    ) -> dict[str, Any] | None:
        """同 :meth:`_payload_object`，但**空响应体是合法的**：那种成功形状表示「没有内容」。

        openapi 里 ``POST /open/v1/project`` 与它的更新都写着两种成功：``200 → Project``
        与 ``201 → No Content``（:1193–1194、:1244–1245）。走 :meth:`_payload` 的实现在
        第二种上会抛 ``MalformedResponseError``——一次**成功**被报成坏数据。

        判据是**响应体空不空**，不是状态码：204 与 201 都可能不带体，而 200 带空体时
        「没有内容」同样是它说出来的意思。非空的体照旧按对象解析，形状不对仍然报错。
        """
        if not response.content:
            return None
        return self._payload_object(response, endpoint=endpoint, required=required)

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

    def _payload_array_or_none(
        self, response: httpx.Response, *, endpoint: str, item_keys: Sequence[str] = ()
    ) -> list[Any]:
        """同 :meth:`_payload_array`，但**空响应体是合法的**：那种成功形状表示「没有内容」。

        搬运（``POST /open/v1/task/move``）写着两种成功：``200 → {id, etag} 数组`` 与
        ``201 → No Content``（``openapi-dida365.md:516–517``）。判据与
        :meth:`_payload_object_or_none` 同一条：看**响应体空不空**，不看状态码。
        """
        if not response.content:
            return []
        return self._payload_array(response, endpoint=endpoint, item_keys=item_keys)

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
        body: Mapping[str, Any] | Sequence[Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> httpx.Request:
        """拼一个请求。

        ``body`` 收 ``Mapping`` **也收 ``Sequence``**：``POST /open/v1/task/move`` 的请求体
        是 JSON 数组（``openapi-dida365.md:504``），它是唯一一个这样的端点。
        """
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

"""测试替身，供所有工单的测试复用。

- :class:`ManualClock` —— 时钟接缝（``dida.clock.Clock``）。
- :class:`FakeTransport` —— HTTP 传输接缝（``dida.api.transport.Transport``）。
- :class:`InMemorySource` —— 只读的内存替身（``dida.sync.view.ViewSource``）；写路径够不着它。
- :class:`FakeBackend` —— **接缝一的假后端**：#80 第一步之后它内部装的是**真货**：真
  :class:`~dida.sync.engine.SyncEngine` + 真本地库（``Store(":memory:")``）+ 假传输层，
  对外仍是「摆数据 + 记下调用」那套字段，所以用它的测试文件一行都不用改。

``FakeBackend`` 记下的那些字段（``writes`` / ``completed`` / ``moved``……）是**对外的账本**：
#80 的第一步只换内部实现，账本照旧（第二步 #86 才收拾抄来的判断与没人读的数组）。
写方法一律交给真引擎走真写路径——乐观落库、入队、推送、认领、出队全在引擎与本地库里发生，
替身不再自己抄一份。**「这一次到底改了没有」也由引擎回答**（#79：判据本体
:func:`~dida.sync.writes.is_a_change`，比的是本地那一份原文），替身照它的回报记账：回
``False``（空操作）时账本里**不留这一笔**——一次没发生的写不该看起来像发生过（#79 之前旧
替身自己抄了一份判断，两个时刻各问一次；#80 之后判断只有引擎那一处）。
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

import httpx

from dida.api.client import DidaApiClient
from dida.clock import Clock
from dida.storage.store import (
    COMPLETED_STATUS,
    UNCOMPLETED_STATUS,
    VIEW_ID_PREFIX,
    RefreshReport,
    Store,
)
from dida.sync.engine import (
    CompletedReport,
    ListRow,
    ReadModel,
    SyncEngine,
    SyncStatus,
    TaskDetail,
    TaskList,
    ViewDefinition,
    WriteKind,
)
from dida.sync.view import (
    INBOX_ID,
    ListSnapshot,
    SyncState,
    TaskSnapshot,
)


_SNAPSHOT_FIELDS = frozenset(
    {"title", "content", "desc", "priority", "completed", "due", "all_day", "tags"}
)
"""一次写里能直接盖进 :class:`TaskSnapshot` 的那些字段名。

引擎给的是**服务端字段名**（``content`` / ``desc`` / ``title``……），快照上那几位恰好同名；
其余（``dueDate``、``items``、未知字段）只并进服务端原文。替身不自己翻译字段——那一层
是 ``Store`` 的事，替身照它的口径做最小的那一份。

``tags`` 在里面（#45）：``Store`` 读快照时是从原文里取 ``tags`` 的（``_snapshot``），所以
一次改标签在真库里当场就反映到读路径上；替身少了这一条就会「写进去了但屏幕上没变」——
那正是接缝一要断的那句话。
"""


class ManualClock:
    """由测试摆布的时钟。"""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta

    def set(self, now: datetime) -> None:
        self._now = now


@dataclass(frozen=True)
class CreatedTask:
    """假后端记下的一条新建：**它落在哪儿**也在这里（#39）。

    落点（``list_id``）是这个记录存在的理由：``FakeBackend.create`` 原来把
    ``list_name=收集箱`` 写死，于是「在 工作 里新建，落在 工作」这条断言会安静地绿成
    「落在收集箱」。谁要断落点，读这一个字段。
    """

    id: str
    title: str
    list_id: str
    due: datetime | None = None
    all_day: bool = False
    tags: tuple[str, ...] = ()


class FakeTransport:
    """假传输：记录每个请求，按队列回放响应（或抛异常）。

    默认回放一个 ``json=`` 的 200 响应；用 :meth:`enqueue` 追加更多响应或异常。
    """

    def __init__(self, *, json: Any = None, status_code: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self._responses: deque[httpx.Response | Exception] = deque()
        self._default: httpx.Response = httpx.Response(status_code, json=json)

    def enqueue(self, response: httpx.Response | Exception) -> None:
        self._responses.append(response)

    @property
    def last_request(self) -> httpx.Request:
        assert self.requests, "没有收到任何请求"
        return self.requests[-1]

    @property
    def last_json(self) -> Any:
        """最后一个请求的请求体（按 JSON 解析）。"""
        return json.loads(self.last_request.content)

    async def send(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self._responses.popleft() if self._responses else self._default
        if isinstance(response, Exception):
            raise response
        return response


def _server_payload(snapshot: TaskSnapshot) -> dict[str, Any]:
    """快照 → 服务端原文那样的字典（只拼 ``Store._snapshot`` 会读的那几个字段）。

    摆数据那一侧唯一的「快照 → 原文」翻译，:class:`InMemorySource` 与 :class:`FakeBackend`
    共用同一份——少一处抄，就少一处漂。
    """
    payload: dict[str, Any] = {
        "id": snapshot.id,
        "projectId": snapshot.list_id,
        "title": snapshot.title,
        "priority": snapshot.priority,
        "status": COMPLETED_STATUS if snapshot.completed else 0,
    }
    if snapshot.due is not None:
        payload["dueDate"] = snapshot.due.isoformat()
        payload["isAllDay"] = snapshot.all_day
    if snapshot.completed_at is not None:
        payload["completedTime"] = snapshot.completed_at.isoformat()
    if snapshot.desc:
        payload["desc"] = snapshot.desc
    if snapshot.content:
        payload["content"] = snapshot.content
    if snapshot.tags:
        payload["tags"] = list(snapshot.tags)
    if snapshot.repeat_flag:
        payload["repeatFlag"] = snapshot.repeat_flag
    if snapshot.reminders:
        payload["reminders"] = list(snapshot.reminders)
    return payload


def _list_payload(
    list_id: str,
    name: str,
    *,
    color: str | None = None,
    group_id: str | None = None,
    kind: str | None = None,
    permission: str | None = None,
    is_inbox: bool = False,
    sort_order: int | None = None,
) -> dict[str, Any]:
    """一行清单 → 服务端 ``Project`` 那样的字典（``Store._write_list`` 认的那几个字段）。

    ``sortOrder`` 由摆数据的人给：真库按它排序，替身给的序号让「怎么加的就怎么排」这件事
    在接缝一上照旧成立（``InMemorySource`` 的字典顺序就是它的旧行为）。
    """
    return {
        "id": list_id,
        "name": name,
        "color": color,
        "groupId": group_id,
        "kind": kind,
        "permission": permission,
        "isInbox": is_inbox,
        "sortOrder": sort_order,
    }


class InMemorySource:
    """本地缓存的内存替身：清单、任务快照、同步状态。

    生产实现是 t08 的 ``Store``；在它落地之前，引擎与 TUI 的测试都用这个。
    引用一个不存在的清单名时会顺手建出这条清单，这样测试里加任务不必先建清单。
    """

    def __init__(self) -> None:
        self.state = SyncState()
        """同步状态，测试可以直接摆布（``source.state = SyncState(...)``）。"""

        self._lists: dict[str, ListSnapshot] = {}
        self._tasks: dict[str, TaskSnapshot] = {}
        self._views: dict[str, ViewDefinition] = {}
        self._raw: dict[str, Mapping[str, Any]] = {}
        self._seq = 0

    def add_list(
        self,
        name: str,
        *,
        id: str | None = None,
        color: str | None = None,
        group_id: str | None = None,
        kind: str | None = None,
        permission: str | None = None,
        is_inbox: bool = False,
    ) -> ListSnapshot:
        """加一条清单；``id`` 默认就是名字。

        ``is_inbox`` 摆 ``True`` 就是客户端补出来的收集箱那一行（服务端的清单索引里没有它）。
        ``kind`` / ``permission`` 是服务端 ``Project`` 上那两个字段（``TASK``/``NOTE``、
        ``write``/``read``/``comment``），清单索引页靠它们标出进不去的行（用户故事 23 / 24）。
        """
        snapshot = ListSnapshot(
            id=id if id is not None else name,
            name=name,
            color=color,
            group_id=group_id,
            kind=kind,
            permission=permission,
            is_inbox=is_inbox,
        )
        self._lists[snapshot.id] = snapshot
        return snapshot

    def save_list(self, payload: Mapping[str, Any]) -> None:
        """按一行清单的**原文**写进内存缓存（#42 的乐观写那一份）。

        与 ``Store.save_list`` 同一口径：字段名用服务端的驼峰（``groupId`` / ``isInbox``……），
        没提的字段当「不知道」（``None``）——替身不自己编，编出来的东西会让「改完之后
        那一行长什么样」变成空话。
        """
        self._lists[str(payload["id"])] = ListSnapshot(
            id=str(payload["id"]),
            name=str(payload.get("name") or ""),
            color=payload.get("color"),
            group_id=payload.get("groupId"),
            kind=payload.get("kind"),
            permission=payload.get("permission"),
            is_inbox=bool(payload.get("isInbox")),
        )

    def drop_list(self, list_id: str) -> None:
        """本地摘掉一行清单（#42 删除的本地效果）。"""
        self._lists.pop(list_id, None)

    def set_completed(self, task_id: str, *, completed: bool) -> None:
        """把一条任务标成完成 / 未完成（工单 #38 的乐观写在内存里的那一半）。

        与真 ``Store`` 同一条口径：**只动 ``status``**，``completedTime`` 一个字不动。
        取消完成之后完成时间戳还留着正是实测行为（spec 的实测事实第 1 条），而「还算不算
        已完成」只看 ``status``——替身要是顺手把时间戳也清了，接缝一就再也测不到那句话。
        """
        snapshot = self._tasks.get(task_id)
        if snapshot is None:
            return
        status = COMPLETED_STATUS if completed else UNCOMPLETED_STATUS
        self._tasks[task_id] = replace(snapshot, completed=completed)
        raw = self._raw.get(task_id)
        if raw is not None:
            self._raw[task_id] = {**raw, "status": status}

    def add_view(self, name: str, *, id: str | None = None, **conditions: Any) -> ViewDefinition:
        """加一个自定义视图（#36）：``conditions`` 就是 :class:`ViewDefinition` 的那几维。

        替身**不自己求值**——成员怎么算出来是视图求值那一层的判断（``evaluate_view``），
        真引擎的读路径从这里读定义、当场求值。所以「这个视图选中了谁」在接缝一上与生产
        走的是同一份实现（#39 的教训：替身自己编一份，测试就会静默断言成别的东西）。
        """
        definition = ViewDefinition(
            id=id if id is not None else name, name=name, **conditions
        )
        self._views[definition.id] = definition
        return definition

    def add_task(
        self,
        title: str,
        *,
        list_name: str = "收集箱",
        list_id: str | None = None,
        due: datetime | None = None,
        all_day: bool = False,
        priority: int = 0,
        completed: bool = False,
        id: str | None = None,
        completed_at: datetime | None = None,
        desc: str = "",
        content: str = "",
        tags: tuple[str, ...] = (),
        repeat_flag: str = "",
        reminders: tuple[str, ...] = (),
        raw: Mapping[str, Any] | None = None,
    ) -> TaskSnapshot:
        """加一条任务快照；``id`` 默认 ``t1``、``t2``……（按加入顺序）。

        ``completed_at`` 是服务端的完成时刻：已完成区（t12）按它决定谁在窗口里。
        ``desc`` / ``content`` / ``tags`` 是右栏常驻显示的那三样（工单 #20）：替身照
        ``Store`` 的口径把它们摆进快照，读路径因此与生产那一份走同一条。

        ``raw`` 是**服务端原文里多出来的那些字段**（重复规则、提醒、子任务、我们不认识的
        字段）：替身把它们盖在按快照拼出来的那份原文上，详情页因此读得到它们（#33）。
        替身不自己编这些字段——编出来的东西会让「详情页读得到」这句话变成空话（#39 的教训）。

        ``list_id`` 显式给的时候就用它（而 ``list_name`` 只是显示用的名字）：``create``
        那条路必须能把任务放进**指定的那个**清单里，否则「落点对不对」根本测不出来。
        """
        self._seq += 1
        snapshot = TaskSnapshot(
            id=id if id is not None else f"t{self._seq}",
            title=title,
            list_id=list_id if list_id is not None else list_name,
            due=due,
            all_day=all_day,
            priority=priority,
            completed=completed,
            completed_at=completed_at,
            desc=desc,
            content=content,
            tags=tags,
            repeat_flag=repeat_flag,
            reminders=reminders,
        )
        self._lists.setdefault(snapshot.list_id, ListSnapshot(id=snapshot.list_id, name=list_name))
        self._tasks[snapshot.id] = snapshot
        self._raw[snapshot.id] = {**self._payload_of(snapshot), **(raw or {})}
        return snapshot

    def task_payload(self, task_id: str) -> Mapping[str, Any] | None:
        """一条任务的完整原文（详情页要的重复规则、提醒、子任务、未知字段都在里面）。

        按 ``Store`` 读服务端原文的口径反着拼一份：字段名与 ``_snapshot`` 认的那几个一一
        对应，测试摆进来的 ``raw`` 盖在上面。
        """
        return self._raw.get(task_id)

    def _payload_of(self, snapshot: TaskSnapshot) -> dict[str, Any]:
        """快照 → 服务端原文那样的字典（与 :func:`_server_payload` 同一份实现）。"""
        return _server_payload(snapshot)

    def view_definitions(self) -> tuple[ViewDefinition, ...]:
        """自定义视图的**定义**（#36），按加进来的顺序。

        与 ``Store.views()`` 同一口径：给定义不给成员——成员要「全量缓存 + 当前逻辑日」
        才算得出来，替身与存储层一样不读时钟。
        """
        return tuple(self._views.values())

    def view_definition(self, view_id: str) -> ViewDefinition | None:
        """一个视图的定义；没加过就是 ``None``。"""
        return self._views.get(view_id)

    def save_view(self, definition: ViewDefinition) -> None:
        """写下一行视图（新建与改都是覆盖式地写）；**位置照旧不动**（字典改已有的键
        不会把它挪到末尾，与 ``Store.save_view`` 保住 ``position`` 是同一件事）。"""
        self._views[definition.id] = definition

    def drop_view(self, view_id: str) -> None:
        """本地摘掉一行视图（只动这一行，一条任务都不碰）。"""
        self._views.pop(view_id, None)

    def new_view_id(self) -> str:
        """一个还没被占用的本地视图 id（与 ``Store.new_view_id`` 同一个前缀与算法）。"""
        index = 1
        while f"{VIEW_ID_PREFIX}{index}" in self._views:
            index += 1
        return f"{VIEW_ID_PREFIX}{index}"

    def lists(self) -> tuple[ListSnapshot, ...]:
        return tuple(self._lists.values())

    def apply_changes(self, task_id: str, changes: Mapping[str, Any]) -> None:
        """把一次写盖到缓存里那条任务上（假后端 ``write`` 的本地效果）。

        与 ``Store`` 同一条口径：本地当场生效（乐观写），服务端随后到。认得出的字段（标题、
        描述、备注、优先级、状态）盖进快照，其余原样并进那份服务端原文——详情页的只读字段
        读的就是原文。

        ``dueDate`` / ``isAllDay`` 多一层翻译：服务端的字段名（``dueDate``）与快照上那两位
        （``due`` / ``all_day``）不是同一个拼法，而 ``Store._snapshot`` 就是在这两个名字之间
        翻译的。替身不翻译的话，「改完截止时间屏幕上就变了」这句话在接缝一根本测不到
        （#44：详细页那一格读的是快照上的 ``due``）。**显式的 ``None`` 是清除**——与 ``Store``
        把 ``dueDate: null`` 读成「没有日期」同一个口径（``dida.vocabulary.read_time`` 只认字符串）。

        **``projectId`` 那一条是搬运**（#45）：快照上「在哪个清单」那一位叫 ``list_id``，
        与 ``Store._write_task`` 同一条口径（它也是从 ``projectId`` 算出 ``list_id`` 那一列）。
        不翻这一下的话，「搬完在读路径上人在新清单里」这句话在接缝一根本测不到。
        """
        snapshot = self._tasks.get(task_id)
        if snapshot is None:
            return
        raw = {**self._raw.get(task_id, {}), **changes}
        if "dueDate" in changes or "isAllDay" in changes:
            due = raw.get("dueDate")
            snapshot = replace(
                snapshot,
                due=datetime.fromisoformat(due) if isinstance(due, str) else None,
                all_day=bool(raw.get("isAllDay")),
            )
        known = {key: value for key, value in changes.items() if key in _SNAPSHOT_FIELDS}
        if changes.get("projectId"):
            known["list_id"] = str(changes["projectId"])
        if "tags" in known:
            known["tags"] = tuple(known["tags"])
        snapshot = replace(snapshot, **known) if known else snapshot
        self._tasks[task_id] = snapshot
        self._raw[task_id] = raw

    def tasks(self) -> tuple[TaskSnapshot, ...]:
        return tuple(self._tasks.values())

    def resolve_id(self, id: str) -> str:
        """照 ``ViewSource`` 那一口实现：替身这里认不出别名，所以原样给回去。

        这不是「说假话」：替身的 ``create`` 直接给真 id（``t1`` 这种），认领换名在它这半边
        根本不存在，所以没有别名可认。**代价要说清**（工单 #75 / ADR-0009）：那一整类问题在
        接缝一上复现不出来，回归测试只能落在真 ``Store`` 那条接缝上
        （``tests/test_local_id_after_claim.py``）。
        """
        return id

    def sync_state(self) -> SyncState:
        return self.state


class _FakeBackendServer(FakeTransport):
    """假后端用的假服务端：记下每个请求，并按端点回一个让**真写路径**走得通的响应。

    与基类 :class:`FakeTransport` 的差别只有一处：默认响应不是「空 body」，而是**按端点
    给形状**。真写路径上的每一次推送都要有回应才能出队——空 body 会让更新那一笔被读成
    「响应体不是 JSON」、按失败退避，队列永远不空，于是「推完之后待推送回到 0」这件事在
    接缝一上变成假的。所以这里按文档给形状：更新与批量更新是对象、搬运是数组、新建
    带上服务端给的 id（认领因此真的发生）。

    它只做两件事：**记请求**、**回一个说得过去的响应**——没有任何业务判断。

    要试「某一次请求失败了」，就在这上面 :meth:`~dida.testing.FakeTransport.enqueue` 一个异常
    或一条 4xx/5xx 响应：失败摆在**传输层**，那才是错误真正的来源。摆进来的那一份照旧
    先于按端点拼的响应出队。
    """

    def __init__(self) -> None:
        super().__init__()
        self._created = 0
        self.tags: list[dict[str, Any]] = []
        """摆进来的那一份「服务端有的标签」（``GET /open/v1/tag`` 的响应体）。"""

    async def send(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._responses:
            response = self._responses.popleft()
            if isinstance(response, Exception):
                raise response
            return response
        return self._respond(request)

    def _respond(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        body: Any = {}
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = {}
        if method == "POST" and path == "/open/v1/task":
            self._created += 1
            return httpx.Response(200, json={**body, "id": f"srv-task-{self._created}"})
        if method == "POST" and path == "/open/v1/project":
            self._created += 1
            return httpx.Response(200, json={**body, "id": f"srv-list-{self._created}"})
        if method == "POST" and path == "/open/v1/task/batch":
            return httpx.Response(200, json={"id2etag": {}, "id2error": {}})
        if method == "POST" and path == "/open/v1/task/move":
            moved = body[0] if isinstance(body, list) and body else {}
            return httpx.Response(200, json=[{"id": moved.get("taskId", ""), "etag": "etag"}])
        if method == "POST" and path == "/open/v1/task/completed":
            return httpx.Response(200, json=[])
        if method == "GET" and path == "/open/v1/project":
            return httpx.Response(200, json=[])
        if method == "GET" and path == "/open/v1/tag":
            return httpx.Response(200, json=self.tags)
        if method == "GET" and path.endswith("/data"):
            return httpx.Response(200, json={"project": None, "tasks": []})
        return httpx.Response(200, json={})


class FakeBackend:
    """接缝一的假后端：内部装的是**真货**，对外照旧（#80 的第一步）。

    内部：真 :class:`~dida.storage.store.Store`（``:memory:``）+ 真
    :class:`~dida.sync.engine.SyncEngine` + 真 :class:`~dida.api.client.DidaApiClient`，
    网络走 :class:`_FakeBackendServer`。读（``list_index`` / ``tasks_in`` / ``task_detail``
    / ``status``……）委托给真引擎；**写走真写路径**：真引擎解析 id、真库乐观落库并入队、
    真客户端把请求发到假传输层，推成功之后认领与出队也都在真库上发生。

    对外那套记录字段与旧版一字不差——测试读的就是它们（``writes`` / ``completed`` /
    ``created_tasks`` / ``moved``……）。替身里抄来的判断与没人读的数组留给第二步（#86）。

    用法::

        backend = FakeBackend(clock=ManualClock(T0), day_end="04:00")
        backend.add_task("写周报", list_name="工作", due=...)
        app = DidaApp(backend)
    """

    def __init__(self, *, clock: Clock, day_end: str = "24:00") -> None:
        self.clock = clock
        self.source = Store(":memory:")
        """真本地库（内存 SQLite）；替身不再自己实现一份。"""
        self.transport = _FakeBackendServer()
        """假传输层：发出去的请求在这里看得见（方法、路径、请求体）。"""
        self.client = DidaApiClient(token="tok", transport=self.transport)
        self.refreshes = 0
        """``refresh()`` 被调用的次数。"""

        self.pushes = 0
        """``push_pending()`` 被调用的次数（t21 的周期泵与手动同步）。"""

        self.manual_pushes: list[bool] = []
        """每次 ``push_pending()`` 收到的 ``manual``（工单 #71），按调用顺序。"""

        self.completed_pulls = 0
        """``refresh_completed()`` 被调用的次数（t21 的 ``r``）。"""

        self.completed: list[str] = []
        """``complete(task_id)`` 收到的任务 id，按调用顺序。"""

        self.uncompleted: list[str] = []
        """``uncomplete(task_id)`` 收到的任务 id，按调用顺序（工单 #38）。"""

        self.deferred: list[str] = []
        """``defer(task_id, days=...)`` 收到的任务 id，按调用顺序。"""

        self.deferred_days: list[int] = []
        """每次顺延前进了几个逻辑日（``g`` 是 1、``G`` 是 7），与 ``deferred`` 一一对应。"""

        self.rescheduled: list[str] = []
        """``reschedule(task_id, due=, all_day=)`` 收到的任务 id，按调用顺序（t14 的改期）。"""

        self.rescheduled_due: list[datetime | None] = []
        """每次改期改到的截止时刻（``None`` ＝ 清除这一格），与 ``rescheduled`` 一一对应。"""

        self.rescheduled_all_day: list[bool] = []
        """每次改期是不是全天，与 ``rescheduled`` 一一对应。"""

        self.created: list[str] = []
        """``create(title, ...)`` 收到的标题，按调用顺序（t15 的新建）。"""

        self.created_due: list[datetime | None] = []
        """每次新建写进去的截止时刻，与 ``created`` 一一对应。"""

        self.created_all_day: list[bool] = []
        """每次新建是不是全天，与 ``created`` 一一对应。"""

        self.created_priority: list[int | None] = []
        """每次新建写进去的优先级（API 取值 1/3/5；没写是 ``None``），与 ``created`` 一一对应。"""

        self.created_tags: list[tuple[str, ...]] = []
        """每次新建写进去的标签，与 ``created`` 一一对应。"""

        self.created_tasks: list[CreatedTask] = []
        """每次新建的完整记录，**含落点**（#39）：要断「落在哪个清单里」就读这里。"""

        self.deleted: list[str] = []
        """``delete(task_id)`` 收到的任务 id，按调用顺序（t16 的删除）。"""

        self.moved: list[tuple[str, str]] = []
        """``move_task(task_id, to_list_id=)`` 收到的每一笔（任务 id + 目标清单 id），按顺序
        （#45 的搬运）。与 ``writes`` 分开记：搬运**不是**一次普通字段更新，混在一起就看不出
        它到底走了哪条路。"""

        self.writes: list[tuple[str, dict[str, Any]]] = []
        """``write(task_id, changes=)`` 收到的每一笔（任务 id + 改动的字段），按调用顺序。

        详细页逐字段编辑（#43）与挑选型字段（#45）断的就是「改完一个字段立刻写出去、而且
        只带这一个字段」——描述与备注互不覆盖那件事，在这一层看得最清楚。
        """

        self.write_error: Exception | None = None
        """摆一个异常进去，``write`` / ``move_task`` 就抛它（试 TUI 遇到引擎拒绝时的反应）。

        **这一格是方法级的，故意的**：它模拟的是引擎**同步拒绝**（#25 的
        ``UnknownTaskError``——本地没有这条任务的底稿），不是网络或服务端拒绝。挪到传输层
        复现不出来：那样乐观写会先落地（屏幕上新标题已经变了），而后端才失败——那正是
        ``tests/test_detail_page.py::test_a_refused_save_says_which_refusal_it_was`` 要断的
        反面。真正的网络 / 服务端失败（标签列表、推送）一律摆在传输层。
        """

        self.delete_error: Exception | None = None
        """摆一个异常进去，``delete`` 就抛它（试 TUI 遇到引擎拒绝时的反应）。

        与 :attr:`write_error` 同一格：模拟的是引擎**同步拒绝**（本地没有这条任务的底稿），
        不是网络失败——传输层复现不出「当场拒绝」，删除那一笔会先乐观落地。
        """

        self.created_lists: list[tuple[str, str | None]] = []
        """``create_list(name, color=)`` 收到的每一笔，按顺序（#42）。"""

        self.updated_lists: list[tuple[str, str | None]] = []
        """``update_list(list_id, name=, color=)`` 收到的每一笔，按顺序（#42）。"""

        self.deleted_lists: list[str] = []
        """``delete_list(list_id)`` 收到的清单 id，按顺序（#42）。"""

        self.created_views: list[ViewDefinition] = []
        """``create_view(definition)`` 收到的每一笔，按顺序（#36）。"""

        self.updated_views: list[ViewDefinition] = []
        """``update_view(definition)`` 收到的每一笔，按顺序（#36）。"""

        self.deleted_views: list[str] = []
        """``delete_view(view_id)`` 收到的视图 id，按顺序（#36）。"""

        self._seq = 0
        """``add_task`` 不给 id 时的计数器（``t1``、``t2``……）。"""
        self._list_order = 0
        """摆进来的清单的 ``sortOrder``：让「怎么加的就怎么排」照旧成立。"""
        self._planted_pending: int | None = None
        """``set_sync_state(pending_count=)`` 摆进来的数（真库的待推送是算出来的，见 :meth:`status`）。"""

        self._engine = SyncEngine(
            clock=clock,
            day_end=day_end,
            source=self.source,
            client=self.client,
        )

    # ---------------------------------------------------------------- 引擎的三件事（网络）

    async def refresh(self) -> RefreshReport:
        """真引擎的 ``refresh()`` 是 async 的（网络等待不阻塞界面），假后端跟着它。

        **不委托给真引擎**：真 ``refresh()`` 会按「服务端这次取全了」的断言剪枝，而替身
        摆进来的数据不是服务端事实——委托它就会把整份缓存删空。这一层只记下「刷过几次」。
        """
        self.refreshes += 1
        return RefreshReport()

    async def push_pending(self, *, manual: bool = False) -> int:
        """推一轮待推送改动：**交给真引擎的真推送循环**（t21 的周期泵会调它）。

        ``manual`` 照原样转给引擎（工单 #71：「按 ``r`` 时传的是 ``manual=True``、启动刷新与
        周期泵传 ``False``」这条接线在这一层看得最清楚）。返回值是真引擎推成功的条数。
        """
        self.pushes += 1
        self.manual_pushes.append(manual)
        return await self._engine.push_pending(manual=manual)

    async def refresh_completed(self) -> CompletedReport:
        """拉一次已完成流（``r`` 的第三件事）。

        与 :meth:`refresh` 同一个理由：真引擎那一次会按服务端的窗口写库，而替身没有服务端
        数据。这里只记下「拉过几次」，已完成区由 :meth:`set_sync_state` 与缓存摆布。
        """
        self.completed_pulls += 1
        now = self.clock.now()
        return CompletedReport(start=now, end=now)

    # ---------------------------------------------------------------- 摆数据（进真库）

    def add_list(
        self,
        name: str,
        *,
        id: str | None = None,
        color: str | None = None,
        group_id: str | None = None,
        kind: str | None = None,
        permission: str | None = None,
        is_inbox: bool = False,
    ) -> ListSnapshot:
        """加一条清单；``id`` 默认就是名字。

        ``is_inbox`` 摆 ``True`` 就是客户端补出来的收集箱那一行（服务端的清单索引里没有它）。
        ``kind`` / ``permission`` 是服务端 ``Project`` 上那两个字段（``TASK``/``NOTE``、
        ``write``/``read``/``comment``），清单索引页靠它们标出进不去的行（用户故事 23 / 24）。
        """
        list_id = id if id is not None else name
        self._list_order += 1
        self.source.save_list(
            _list_payload(
                list_id,
                name,
                color=color,
                group_id=group_id,
                kind=kind,
                permission=permission,
                is_inbox=is_inbox,
                sort_order=self._list_order,
            )
        )
        return next(row for row in self.source.lists() if row.id == list_id)

    def add_view(self, name: str, *, id: str | None = None, **conditions: Any) -> ViewDefinition:
        """摆一个自定义视图（#36）：``conditions`` 就是 :class:`ViewDefinition` 的那几维。

        替身**不自己求值**——成员怎么算出来是视图求值那一层的判断（``evaluate_view``），
        真引擎的读路径从这里读定义、当场求值。所以「这个视图选中了谁」在接缝一上与生产
        走的是同一份实现（#39 的教训：替身自己编一份，测试就会静默断言成别的东西）。
        """
        definition = ViewDefinition(
            id=id if id is not None else name, name=name, **conditions
        )
        self.source.save_view(definition)
        return definition

    def add_task(
        self,
        title: str,
        *,
        list_name: str = "收集箱",
        list_id: str | None = None,
        due: datetime | None = None,
        all_day: bool = False,
        priority: int = 0,
        completed: bool = False,
        id: str | None = None,
        completed_at: datetime | None = None,
        desc: str = "",
        content: str = "",
        tags: tuple[str, ...] = (),
        repeat_flag: str = "",
        reminders: tuple[str, ...] = (),
        raw: Mapping[str, Any] | None = None,
    ) -> TaskSnapshot:
        """加一条任务快照；``id`` 默认 ``t1``、``t2``……（按加入顺序）。

        ``completed_at`` 是服务端的完成时刻：已完成区（t12）按它决定谁在窗口里。
        ``desc`` / ``content`` / ``tags`` 是右栏常驻显示的那三样（工单 #20）。``raw`` 是
        **服务端原文里多出来的那些字段**（重复规则、提醒、子任务、我们不认识的字段）。

        过的是真库（``Store.apply_refresh``）与同一份「快照 → 原文」翻译
        （:func:`_server_payload`）——替身不自己拼第二份，也就不会跟真货说不一样的话。
        清单行不存在时顺手补一条（与旧行为同一条：测试里加任务不必先建清单）。
        """
        self._seq += 1
        snapshot = TaskSnapshot(
            id=id if id is not None else f"t{self._seq}",
            title=title,
            list_id=list_id if list_id is not None else list_name,
            due=due,
            all_day=all_day,
            priority=priority,
            completed=completed,
            completed_at=completed_at,
            desc=desc,
            content=content,
            tags=tags,
            repeat_flag=repeat_flag,
            reminders=reminders,
        )
        if not any(row.id == snapshot.list_id for row in self.source.lists()):
            self._list_order += 1
            self.source.save_list(
                _list_payload(snapshot.list_id, list_name, sort_order=self._list_order)
            )
        self.source.apply_refresh(tasks=[{**_server_payload(snapshot), **(raw or {})}])
        return snapshot

    def set_sync_state(
        self, *, last_refresh_at: datetime | None = None, pending_count: int = 0
    ) -> None:
        """摆同步状态：``last_refresh_at`` 写进真库，``pending_count`` 记成覆盖值。

        真库里「待推送几条」是**算出来的**（``Store.pending_count`` 数两张队列表），摆不进
        去。而既有测试用它摆出「离线、还有 N 笔没推」那一屏，所以这里记下这个数，在
        :meth:`status` 里盖上去——对外行为与旧替身一字不差。
        """
        self._planted_pending = pending_count
        self.source.set_sync_state(last_refresh_at=last_refresh_at)

    # ---------------------------------------------------------------- 读（委托真引擎）

    def read_model(self) -> ReadModel | None:
        """读：委托给真引擎——整份读模型与它的三个投影都是生产那一份（#81）。

        替身不自己拼一份：一次界面重画只装配一遍这件事，在接缝一上也看得见。
        """
        return self._engine.read_model()

    def list_index(self) -> tuple[ListRow, ...]:
        """读：委托给真引擎——三种行怎么组装、收集箱那一行是谁、条数怎么数，都是生产那一份。"""
        return self._engine.list_index()

    def tasks_in(self, container_id: str) -> TaskList:
        """读：委托给真引擎（某个容器的全部未完成任务 + 该显示的那部分已完成）。"""
        return self._engine.tasks_in(container_id)

    def move_targets(self) -> tuple[ListRow, ...]:
        """读：委托给真引擎（真实清单、进得去、服务端已经见过的那些，#45）。

        真引擎读的是**真队列**（``pending_lists``）：还没被认领的清单从这一份里滤掉。
        """
        return self._engine.move_targets()

    def set_tags(self, *names: str) -> None:
        """摆一份「服务端有的标签」（#45）：那一个端点下一次就回这一份。

        摆的是**假服务端的响应体**（``GET /open/v1/tag``），不是替身内存里另存一份：
        :meth:`load_tags` 走真引擎、真客户端、真传输层，标签从哪儿来只剩服务端这一处；
        要试「拉不到」，就往传输层摆一个网络错或 4xx/5xx（``self.transport.enqueue(...)``）。
        """
        self.transport.tags = [{"name": name} for name in names]

    async def load_tags(self) -> tuple[str, ...]:
        """读：委托给真引擎（发出 ``GET /open/v1/tag``，拉一次、记在引擎内存里）。"""
        return await self._engine.load_tags()

    def tags(self) -> tuple[str, ...]:
        """读：委托给真引擎（服务端那一份 ∪ 本地任务上出现过的那些）。"""
        return self._engine.tags()

    def task_detail(self, task_id: str) -> TaskDetail | None:
        """读：委托给真引擎（详情页的字段，含原文里我们不认识的那些）。"""
        return self._engine.task_detail(task_id)

    def set_day_end(self, day_end: str) -> bool:
        """配置：换掉日界（工单 #46）——读路径全都委托给真引擎，这一处也一样。

        界面上的「今天」、逾期判定与状态栏那一格都是这台真引擎算的，所以日界必须换在它身上：
        替身自己记一个值，屏幕说的就会跟引擎说的不一样。
        """
        return self._engine.set_day_end(day_end)

    def status(self) -> SyncStatus:
        """读：真引擎的状态，``pending_count`` 有摆过的值就盖上去。

        与 ``set_sync_state`` 同一个理由：真库那个数是算出来的，而既有测试用它摆
        「离线、还有 N 笔」那一屏。没摆过（``None``）就是真库算出来的那个数。
        """
        status = self._engine.status()
        if self._planted_pending is None:
            return status
        return replace(status, pending_count=self._planted_pending)

    def logical_day(self) -> date:
        """读：现在是哪个逻辑日（工单 #46 的心跳）——同样委托真引擎，替身不自己算一份。"""
        return self._engine.logical_day()

    # ---------------------------------------------------------------- 写（真写路径）

    def complete(self, task_id: str) -> None:
        """写：记下这一笔，并交给真引擎完成它（乐观落库 + 立即推送）。

        本地没有这条任务时**只记录**（旧替身的对外契约：``complete`` 从不当场拒绝；
        引擎那一条会抛 ``UnknownTaskError``，而这是替身与真实调用方不重叠的一格）。
        """
        self.completed.append(task_id)
        if self.source.task_payload(task_id) is None:
            return
        self._engine.complete(task_id)

    def uncomplete(self, task_id: str) -> None:
        """写：记下这一笔，并交给真引擎取消完成（本地 ``status`` 写回 0，完成时间戳不动）。"""
        self.uncompleted.append(task_id)
        if self.source.task_payload(task_id) is None:
            return
        self._engine.uncomplete(task_id)

    def defer(self, task_id: str, *, days: int = 1) -> bool:
        """写：记下这一笔，并交给真引擎按逻辑日顺延，把引擎的回报原样交回。

        **回报这次到底挪了没有**（#79）：顺延的判据与真写路径都在引擎那一份里（落点是不是
        同一个逻辑日、有没有截止时间可挪），替身不再自己抄一遍。回 ``False`` 时这一笔
        **照样记在账本上**：账本说的是「调用收到过没有」，一条本地不存在的任务也一样记
        （``tests/test_fake_backend.py`` 钉着它）。
        """
        self.deferred.append(task_id)
        self.deferred_days.append(days)
        return self._engine.defer(task_id, days=days)

    def reschedule(self, task_id: str, *, due: datetime | None, all_day: bool = False) -> bool:
        """写：交给真引擎改期（只动 ``dueDate`` 与 ``isAllDay``），把它的回报原样交回。

        **同值收敛**（#79）：那一刻与本地那一份相同时引擎什么都不写、回 ``False``——判据是
        生产那**一个**本体 :func:`~dida.sync.writes.is_a_change`（在本地那份原文上比），替身
        不再自己写第二份比较；空操作**不记账**（``rescheduled`` 里不留这一笔），一次什么都
        没改的提交因此看起来也不像一次写（#39 的教训：替身编一份自己的判断，测试就会静默
        断言成别的东西）。

        本地没有这条任务的底稿时与 :meth:`write` 一样不当场拒绝，只回 ``False``。
        """
        if self.source.task_payload(task_id) is None:
            return False
        changed = self._engine.reschedule(task_id, due=due, all_day=all_day)
        if changed:
            self.rescheduled.append(task_id)
            self.rescheduled_due.append(due)
            self.rescheduled_all_day.append(all_day)
        return changed

    def create(
        self,
        title: str,
        list_id: str = INBOX_ID,
        *,
        due: datetime | None = None,
        all_day: bool = False,
        priority: int | None = None,
        tags: Sequence[str] = (),
    ) -> str:
        """写：交给真引擎新建（本地临时 id、乐观落库、立即推送、推成功后认领真 id）。

        **落点由 ``list_id`` 给**（#39 的验收标准 8）：默认收集箱只是给「落点不是这条测试的
        重点」的那些调用留的方便（与引擎那一侧「在视图里建传 ``INBOX_ID``」是同一个值）。

        落点那条清单**还没被认领**（id 是 ``local-list-…``）时真引擎抛
        :class:`~dida.sync.writes.UnclaimedListError`（#39 / #53），这里**一条都不记**——
        拒绝就是拒绝。
        """
        task_id = self._engine.create(
            title, list_id, due=due, all_day=all_day, priority=priority, tags=tags
        )
        self.created.append(title)
        self.created_due.append(due)
        self.created_all_day.append(all_day)
        self.created_priority.append(priority)
        self.created_tags.append(tuple(tags))
        self.created_tasks.append(
            CreatedTask(
                id=task_id,
                title=title,
                list_id=list_id,
                due=due,
                all_day=all_day,
                tags=tuple(tags),
            )
        )
        return task_id

    def delete(self, task_id: str) -> None:
        """写：记下这一笔，并交给真引擎删除（本地当场摘掉快照、推送走 ``DELETE``）。

        摆了 ``delete_error`` 就记完这一笔再抛：模拟引擎当场拒绝（#25 的
        ``UnknownTaskError``），好试 TUI 拿到结构化错误时的反应。
        """
        self.deleted.append(task_id)
        if self.delete_error is not None:
            raise self.delete_error
        if self.source.task_payload(task_id) is None:
            return
        self._engine.delete(task_id)

    def move_task(self, task_id: str, *, to_list_id: str) -> bool:
        """写：交给真引擎搬运（``POST /open/v1/task/move``，#45），把它的回报原样交回。

        搬到它**已经在**的那个清单（或者目标为空）= 引擎什么都不写、回 ``False``——判据是
        引擎的 :func:`~dida.sync.writes.is_a_move`，替身不再自己比一遍；空操作**不记账**
        （``moved`` 里不留这一笔），否则替身会记下一笔「搬了」而生产那一条根本没写——接缝一
        断的就是这句话。摆了 ``write_error`` 就记完这一笔再抛（与 ``write`` 同一个口子：
        引擎当场拒绝时界面要说得出具体原因）——抛在交给引擎**之前**，「当场拒绝」的意思就是
        这一笔没写成。本地没有这条任务的底稿时与 :meth:`write` 一样不当场拒绝，只回 ``False``。
        """
        if self.write_error is not None:
            self.moved.append((task_id, to_list_id))
            raise self.write_error
        if self.source.task_payload(task_id) is None:
            return False
        changed = self._engine.move_task(task_id, to_list_id=to_list_id)
        if changed:
            self.moved.append((task_id, to_list_id))
        return changed

    def write(
        self,
        task_id: str,
        *,
        changes: Mapping[str, Any] | None = None,
        kind: WriteKind = WriteKind.UPDATE,
    ) -> bool:
        """写：交给真引擎走真写路径（乐观落库 + 入队 + 立即推送），把它的回报原样交回。

        **同值收敛**（#79）：盖上去的字段与本地那一份原文逐位相同时引擎什么都不做、回
        ``False``——判据是生产那**一个**本体 :func:`~dida.sync.writes.is_a_change`，替身不再
        自己写第二份比较；空操作**不记账**（``writes`` 里不留这一笔），一次没发生的写因此
        看起来也不像发生过（#39 的教训：替身编一份自己的判断，接缝一上就会静默断言成别的
        东西）。

        摆了 ``write_error`` 就记完这一笔再抛，试 TUI 拿到结构化错误时说不说得清——抛在交给
        引擎**之前**，「引擎当场拒绝」的意思就是这一笔没写成。本地没有这条任务的底稿时与旧
        替身一样不当场拒绝，只回 ``False``。
        """
        local = dict(changes or {})
        if self.write_error is not None:
            self.writes.append((task_id, local))
            raise self.write_error
        if self.source.task_payload(task_id) is None:
            return False
        changed = self._engine.write(task_id, changes=changes, kind=kind)
        if changed:
            self.writes.append((task_id, local))
        return changed

    def create_list(self, name: str, *, color: str | None = None) -> str:
        """写：记下这一笔，并交给真引擎新建清单（本地临时 id + 乐观落库 + 立即推送）。

        本地临时 id 由真库的发号器给（服务端建好之后才给真 id）。
        """
        self.created_lists.append((name, color))
        return self._engine.create_list(name, color=color)

    def update_list(
        self, list_id: str, *, name: str | None = None, color: str | None = None
    ) -> bool:
        """写：交给真引擎改那一行（没给的字段照旧不动），把它的回报原样交回。

        **同值收敛**（#79）：交回来的两位与本地那一行相同时引擎什么都不写、回 ``False``——
        判据是生产那一份（:func:`~dida.sync.lists.is_list_edit`，本体在 ``writes``），替身
        不再自己比一遍；空操作**不记账**（``updated_lists`` 里不留这一笔）。本地没有这一行的
        原文时与旧替身一样不当场拒绝，只回 ``False``。
        """
        if self.source.list_payload(list_id) is None:
            return False
        changed = self._engine.update_list(list_id, name=name, color=color)
        if changed:
            self.updated_lists.append((list_id, name, color))
        return changed

    def delete_list(self, list_id: str) -> None:
        """写：记下这一笔，并交给真引擎删那一行（只动清单那一行）。"""
        self.deleted_lists.append(list_id)
        if self.source.list_payload(list_id) is None:
            return
        self._engine.delete_list(list_id)

    def create_view(self, definition: ViewDefinition) -> str:
        """写：记下这一笔，并交给真引擎落库（**只在本地**，视图不推服务端）。"""
        self.created_views.append(definition)
        return self._engine.create_view(definition)

    def update_view(self, definition: ViewDefinition) -> bool:
        """写：交给真引擎改那一行（位置照旧不动），把它的回报原样交回。

        **同值收敛**（#79）：交回来的那份与本地那一行一样时引擎什么都不做、回 ``False``——
        判据是生产那一份（:func:`~dida.sync.views.is_view_edit`，本体在 ``writes``），替身不
        自己再比一遍；空操作**不记账**（``updated_views`` 里不留这一笔）。
        """
        changed = self._engine.update_view(definition)
        if changed:
            self.updated_views.append(definition)
        return changed

    def delete_view(self, view_id: str) -> None:
        """写：记下这一笔，并交给真引擎摘掉那一行（**一条任务都不碰**）。"""
        self.deleted_views.append(view_id)
        self._engine.delete_view(view_id)

    def view_definition(self, view_id: str) -> ViewDefinition | None:
        """读：委托给真引擎（本地库里那一行定义；没建过就是 ``None``）。"""
        return self._engine.view_definition(view_id)

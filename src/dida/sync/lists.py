"""清单的建 / 改 / 删（工单 #42）：乐观写、立即推送、失败进重试队列。

清单与任务走**同一条写路径的口径**（spec 的同步引擎一节：乐观入队 + 立即推送 + 指数退避
重试 + 「待推送改动豁免于服务端权威」），但词汇是另一套：

- 任务的写词汇在 :mod:`dida.sync.writes`，那里每种写都对应「本地快照怎么变」与「打哪个
  **任务**端点」，队列落在 ``pending_changes``（一行一条任务改动）。
- 清单没有任务 id，改动也不作用在任务快照上，所以这里另起一套：:class:`ListWriteKind`
  与它自己的队列表（``pending_list_changes``）。**硬塞进任务那套**的下场是把 ``list_id``
  写进 ``task_id`` 那一列——一个字段两个意思，读的人第一步就错。

## 三个决定

**新建用本地临时 id。** 服务端建好之后才给真 id，而「建完立刻出现在清单列表页」是
ADR-0002 的手感要求（本地先动、服务端随后到）。所以本地先有一行 ``local-list-N``，
推送成功拿到服务端的 ``Project`` 之后**认领**它（:meth:`Store.adopt_created_list`）——
不认领的话，下一次刷新会把真 id 那一行拉回来，而同一条清单在屏幕上出现两遍。

**推不动的改动留在队列里，本地那一份不许被撤销。** 与任务一样：状态栏那个「待推送 N」
就是它（:meth:`Store.pending_count` 两张表一起数），到点了由 :meth:`push_pending` 再试。
刷新时服务端的原文也**不许**盖回去（``Store.apply_refresh`` 对有待推送改动的清单整行豁免）。

**删清单只动清单那一行。** 文档对「删掉一个清单时它里面的任务会怎样」一个字都没写
（openapi-dida365.md:1278–1302 通篇只有路径、参数、响应表与一个请求示例），所以客户端
不替它猜：既不在本地顺手删掉那些任务（猜「一起删了」），也不把它们搬去收集箱（猜
「移走了」）。留下的是**孤儿任务**——#41 的剪枝特意保住「还有没推成功的改动」的那些任务，
删清单正是产生它们的那条路；``sync/view.py`` 的清单名回退（写清单 id）就是它们在屏幕上的
样子。这条取舍写在工单 #42 的报告里，不是悄悄补上的。

## 颜色

服务端的 ``color`` 是自由字符串（文档只给了 ``#F18181`` 一个样例，没有枚举、没有取值表），
所以 :data:`LIST_COLORS` 是**客户端自己的一档**：色值原样发给服务端，屏幕上只写它的名字。
终端**不画这些颜色**——颜色跟随用户自己的主题（ADR-0007 一），所以这些 hex 是 API 数据，
不是视觉常量；``dida/tui`` 里一个都不许出现（``tests/test_architecture.py`` 的源码扫描）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence, runtime_checkable

from dida.api.errors import DidaError
from dida.sync.push import backoff_delay
from dida.sync.view import ViewSource

if TYPE_CHECKING:  # storage 反过来 import 本模块（与 dida.sync.writes 同一条规矩）
    from dida.storage.store import PendingListChange

__all__ = [
    "LIST_COLORS",
    "ListColor",
    "ListLocalEffect",
    "ListWire",
    "ListWriteKind",
    "ListWriteTarget",
    "ProjectWriter",
    "UnknownListError",
]


class ListLocalEffect(Enum):
    """一次清单写在**本地**的效果；存储层照着它改 ``lists`` 那一行。"""

    SAVE = "save"
    """写下这一行（新建与改名都是覆盖式地写）。"""

    DROP = "drop"
    """本地摘掉这一行（删除的本地效果不是「等推送成功再摘」）。"""


class ListWire(Enum):
    """推送时调客户端的哪一个方法；引擎照着它分派。"""

    CREATE_PROJECT = "create_project"
    UPDATE_PROJECT = "update_project"
    DELETE_PROJECT = "delete_project"


@dataclass(frozen=True)
class ListBehaviour:
    """一种清单写的行为说明（见 :data:`_BEHAVIOUR`）。"""

    local: ListLocalEffect
    wire: ListWire


class ListWriteKind(Enum):
    """一次清单写的种类；值与队列表 ``pending_list_changes.kind`` 那一列一一对应。"""

    CREATE = "list_create"
    UPDATE = "list_update"
    DELETE = "list_delete"

    @property
    def local(self) -> ListLocalEffect:
        """本地那一行怎么变（存储层读这个，不读成员名）。"""
        return _BEHAVIOUR[self].local

    @property
    def wire(self) -> ListWire:
        """推送调客户端的哪一个方法（分派读这个）。"""
        return _BEHAVIOUR[self].wire


_BEHAVIOUR: dict[ListWriteKind, ListBehaviour] = {
    ListWriteKind.CREATE: ListBehaviour(local=ListLocalEffect.SAVE, wire=ListWire.CREATE_PROJECT),
    ListWriteKind.UPDATE: ListBehaviour(local=ListLocalEffect.SAVE, wire=ListWire.UPDATE_PROJECT),
    ListWriteKind.DELETE: ListBehaviour(local=ListLocalEffect.DROP, wire=ListWire.DELETE_PROJECT),
}
"""**一处**记全每种清单写的行为（与 :mod:`dida.sync.writes` 的 ``_BEHAVIOUR`` 同一条规矩）。"""


@dataclass(frozen=True)
class ListColor:
    """清单颜色的一档：``value`` 是发给服务端的原文，``label`` 是屏幕上写的名字。"""

    value: str
    label: str


LIST_COLORS: tuple[ListColor, ...] = (
    ListColor("#F18181", "红"),
    ListColor("#FFA94D", "橙"),
    ListColor("#FFD43B", "黄"),
    ListColor("#69DB7C", "绿"),
    ListColor("#4DABF7", "蓝"),
    ListColor("#9775FA", "紫"),
    ListColor("#F783AC", "粉"),
    ListColor("#ADB5BD", "灰"),
)
"""新建清单时能挑的那几档。

**只有第一个是文档给过的**（``#F18181``，:1185 的样例）；其余是客户端自己配的一档近似。
服务端把 ``color`` 当自由字符串收——文档没有枚举、没有取值表、也没有说认不出会怎样，
所以这不是「照文档抄下来的一张表」。清单上已经有的颜色（手机端挑的）照原样显示、照原样
回写，不会因为不在这一档里就被换掉。
"""


class UnknownListError(DidaError):
    """这条清单写推不出去，所以拒绝它：本地没有这一行（工单 #42）。

    与任务的 :class:`~dida.sync.writes.UnknownTaskError` 同一条口径：本地没有这一行的原文，
    请求就拼不出来（改名要把 ``sortOrder`` 之类 echo 回去，删除要知道删的是谁）。凭空入队
    只会留下一条永远推不出去的改动，让状态栏那个数一直非零。
    """

    def __init__(self, list_id: str) -> None:
        super().__init__(f"本地缓存里没有清单 {list_id}：拒绝这一笔改动，因为它永远推不出去")
        self.list_id = list_id
        """请求写入的那条清单 id，UI 可以直接显示出来。"""


@runtime_checkable
class ProjectWriter(Protocol):
    """推送清单改动要的那三个客户端方法；t07 的 ``DidaApiClient`` 满足它。"""

    async def create_project(self, body: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """``POST /open/v1/project``：新建清单，返回服务端建好的那一条（可能是空的 201）。"""
        ...

    async def update_project(
        self,
        project_id: str,
        changes: Mapping[str, Any],
        *,
        snapshot: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any] | None:
        """``POST /open/v1/project/{projectId}``：改清单。"""
        ...

    async def delete_project(self, project_id: str) -> None:
        """``DELETE /open/v1/project/{projectId}``：删清单。"""
        ...


@runtime_checkable
class ListWriteTarget(ViewSource, Protocol):
    """本地副本在清单写路径上要会的那几件事；t08 的 ``Store`` 满足它。"""

    def list_payload(self, list_id: str) -> dict[str, Any] | None:
        """一条清单的原文（改名时要 echo 回去的那些字段在里面）；本地没有就是 ``None``。"""
        ...

    def new_local_list_id(self) -> str:
        """一个还没被占用的本地临时清单 id（新建时先占位，推送成功后认领服务端的 id）。"""
        ...

    def enqueue_list(
        self,
        *,
        list_id: str,
        kind: ListWriteKind,
        payload: Mapping[str, Any],
        now: datetime,
        local: Mapping[str, Any] | None = None,
    ) -> PendingListChange:
        """入队一条待推送的清单改动，并让它在本地立刻生效。"""
        ...

    def pending_lists(self) -> Sequence[PendingListChange]:
        """还没推成功的清单改动，按发生顺序。"""
        ...

    def record_list_attempt(
        self,
        change_id: int,
        *,
        error: str | None = None,
        next_retry_at: datetime | None = None,
    ) -> None:
        """记一次推送失败：尝试次数 +1、最后一次错误、下次重试时刻。"""
        ...

    def resolve_list(self, change_id: int) -> None:
        """这条改动已经推到服务端了，出队。"""
        ...

    def adopt_created_list(self, local_id: str, payload: Mapping[str, Any]) -> None:
        """新建推成功：把本地那行临时 id 的清单挪到服务端给的 id 上。"""
        ...


class ListMixin:
    """清单的建 / 改 / 删：本地先动、立即推送、推不动就退避重试。

    方法挂在组装好的 :class:`~dida.sync.engine.SyncEngine` 上（要用 ``self._clock`` /
    ``self._source`` / ``self._push_lock`` / ``self._schedule_push``）。
    """

    def create_list(self, name: str, *, color: str | None = None) -> str:
        """新建清单：本地当场多出一行，然后立即排一轮推送（返回本地那一行的 id）。

        请求体只有名字与颜色：``groupId`` 与 ``sortOrder`` 都不是新建能决定的
        （文档的建清单请求体表 :1184–1188），所以不猜、也不假装能把清单放进项目组。

        ``color=None`` 表示**不发这个字段**（服务端自己挑默认色）——不是「清空颜色」。
        """
        target = self._list_target()
        local_id = target.new_local_list_id()
        target.enqueue_list(
            list_id=local_id,
            kind=ListWriteKind.CREATE,
            payload=_request_body(name=name, color=color),
            now=self._clock.now(),
            # 本地那一行比请求体多一个临时 id：服务端还不知道它，界面得先有东西可画。
            local={"id": local_id, "name": name, "color": color},
        )
        self._push_now()
        return local_id

    def update_list(
        self, list_id: str, *, name: str | None = None, color: str | None = None
    ) -> None:
        """改清单的名字与颜色：本地当场生效并入队，然后立即排一轮推送。

        没给的字段**不动**（``color=None`` 是「别动颜色」，不是「清空颜色」）：请求体里
        只有这次真的要改的那些，其余的由客户端在推送时从本地原文里 echo 回去
        （``sortOrder`` 尤其要紧——文档写着 "default 0"，省略它可能把清单顺序重置）。
        """
        target = self._list_target()
        current = target.list_payload(list_id)
        if current is None:
            raise UnknownListError(list_id)
        changes: dict[str, Any] = {}
        if name is not None:
            changes["name"] = name
        if color is not None:
            changes["color"] = color
        target.enqueue_list(
            list_id=list_id,
            kind=ListWriteKind.UPDATE,
            payload=changes,
            now=self._clock.now(),
            local={**current, **changes},
        )
        self._push_now()

    def delete_list(self, list_id: str) -> None:
        """删清单：本地那一行当场消失，推送走 ``DELETE``，推不动就留在队列里。

        **只动清单那一行**（模块文档里的第三个决定）：它里面的任务在服务端会怎样，
        文档没写，所以本地一条都不动——猜「一起删了」或猜「搬去收集箱」都是编事实。
        """
        target = self._list_target()
        if target.list_payload(list_id) is None:
            raise UnknownListError(list_id)
        target.enqueue_list(
            list_id=list_id,
            kind=ListWriteKind.DELETE,
            payload={},
            now=self._clock.now(),
        )
        self._push_now()

    async def push_pending(self) -> int:
        """推一轮：**先清单那几种，再交给任务那一份**（两边共用引擎那一把推送锁）。

        清单的改动排在前面只是顺序，不是优先级：两边的失败都各自留在自己的队列里，
        返回值是这一轮推成功的**总条数**（状态栏那句「已推送 N 处改动」说的是它）。
        """
        pushed = await self._push_lists()
        return pushed + await super().push_pending()  # type: ignore[misc]

    async def _push_lists(self) -> int:
        """把到期的清单改动依次推给服务端，返回推成功的条数。

        与任务的 :meth:`~dida.sync.push.PushMixin.push_pending` 同一条口径：一条失败不影响
        后面那些，失败的记一次尝试并按 ``backoff_delay`` 排下一次。等待发生在调用方
        （周期泵、``r``、下一次写），这一层不睡。

        **每一笔都重新取一次队列**（不是先取一份快照再遍历）：新建推成功会把这一条清单排在
        后面的改动挪到服务端给的 id 上（``Store.adopt_created_list``），同一轮里紧接着的那
        一笔必须看见新的 id。循环一定会停：每一轮要么删掉一行、要么把它的 ``next_retry_at``
        推到将来（于是它不再是「到期的第一笔」）。
        """
        target = self._list_target()
        writer = self._list_writer()
        pushed = 0
        async with self._push_lock:
            while True:
                now = self._clock.now()
                change = next(
                    (
                        item
                        for item in target.pending_lists()
                        if item.next_retry_at is None or item.next_retry_at <= now
                    ),
                    None,
                )
                if change is None:
                    return pushed
                try:
                    await self._send_list(writer, target, change)
                except DidaError as exc:
                    target.record_list_attempt(
                        change.id,
                        error=str(exc),
                        next_retry_at=now + backoff_delay(change.attempts),
                    )
                    continue
                target.resolve_list(change.id)
                pushed += 1

    async def _send_list(
        self, writer: ProjectWriter, target: ListWriteTarget, change: PendingListChange
    ) -> None:
        """把一条待推送的清单改动交给客户端。失败是结构化错误，照旧往外抛（由调用方退避）。"""
        wire = change.kind.wire
        if wire is ListWire.CREATE_PROJECT:
            # 新建才知道服务端给的 id，所以这一条推成功之后要顺手认领它。
            self._adopt_created_list(target, change, await writer.create_project(change.payload))
        elif wire is ListWire.UPDATE_PROJECT:
            await writer.update_project(
                change.list_id,
                change.payload,
                # 底稿是本地那一行的原文：不打算改的字段（sortOrder 最要紧）靠它 echo 回去。
                snapshot=target.list_payload(change.list_id),
            )
            # 推成功之后把**刚发出去的那一份**盖回本地：服务端建好这条清单时回的原文
            # （认领那一步）里是**旧**名字，而这一笔改名就排在它后面——本地那一行因此可能
            # 显示成服务端刚回的那份，与刚刚发出去的内容不一致。用户写下的那份才是屏幕上
            # 该有的，直到下一次全量刷新由服务端权威裁决。
            local = target.list_payload(change.list_id)
            if local is not None:
                target.save_list({**local, **change.payload})
        else:
            await writer.delete_project(change.list_id)

    def _adopt_created_list(
        self, target: ListWriteTarget, change: PendingListChange, created: Any
    ) -> None:
        """新建推成功：把本地那行临时 id 的清单挪到服务端给的 id 上。

        是**合并**而不是替换：服务端给的字段盖上去，它没提的字段（本地那份颜色、项目组）
        留着。响应按文档就是那条建好的清单，但不拿这个赌——真正的服务端权威裁决在全量
        刷新那条路上。

        服务端没回一个带 id 的清单时**什么都不做**：这条改动已经推成功了，不能当失败重试
        （新建不是幂等的，重试就是建两条）。本地那行临时 id 会活到下一次刷新——那时服务端
        的索引里已经有它了，真 id 那一行写进来、临时那一行被剪掉，屏幕上始终只有一条。
        """
        if not isinstance(created, Mapping) or not created.get("id"):
            return
        local = target.list_payload(change.list_id) or {}
        target.adopt_created_list(
            change.list_id,
            {**local, **created, "id": str(created["id"])},
        )

    def _list_target(self) -> ListWriteTarget:
        """清单写路径要写的那个本地副本。没接上就大声报错——绝不假装写成功了。"""
        if not isinstance(self._source, ListWriteTarget):
            raise RuntimeError(
                "清单的写路径需要本地存储：SyncEngine(source=Store(...))；"
                "只读的 ViewSource 存不下待推送改动"
            )
        return self._source

    def _list_writer(self) -> ProjectWriter:
        """推送清单改动要的那个客户端。没接上就大声报错——绝不假装推过了。"""
        if not isinstance(self._client, ProjectWriter):
            raise RuntimeError("清单的写路径需要 API 客户端：SyncEngine(client=DidaApiClient(...))")
        return self._client


def _request_body(*, name: str | None, color: str | None) -> dict[str, Any]:
    """建 / 改清单的请求体：**只有**真的要写的字段。

    ``None`` 的字段直接不出现在请求体里：``color=None`` 到底是「别动」还是「清空」文档
    没写，而这两种意思在请求体里长得一样（一个 null）。宁可少发一个字段，也不发一个猜来的。
    """
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if color is not None:
        body["color"] = color
    return body

"""清单的建 / 改 / 删（工单 #42）：乐观写、立即推送、失败进重试队列。

清单与任务走**同一条写路径的口径**（spec 的同步引擎一节：乐观入队 + 立即推送 + 固定节奏退避
重试 + 「待推送改动豁免于服务端权威」），但词汇是另一套：

- 任务的写词汇在 :mod:`dida.sync.writes`，那里每种写都对应「本地快照怎么变」与「打哪个
  **任务**端点」，队列落在 ``pending_changes``（一行一条任务改动）。
- 清单没有任务 id，改动也不作用在任务快照上，所以这里另起一套：:class:`ListWriteKind`
  与它自己的队列表（``pending_list_changes``）。**硬塞进任务那套**的下场是把 ``list_id``
  写进 ``task_id`` 那一列——一个字段两个意思，读的人第一步就错。
- **机器只有一台**（#84）：推一轮的那台泵住在 :mod:`dida.sync.pump`，这一片交给它的是
  :class:`ListQueue`——「读队列、可寻址吗、记一次失败、出队、发出去」五件事接在清单这一族的
  动词与分派表 :data:`_LIST_WIRE` 上。两份词汇表与两张队列表照旧分开，重复的只有机器本身；
  认领新建的清单那一步并进了唯一那一份全量刷新（:mod:`dida.sync.refresh`），这一片不再各写
  一份 ``refresh`` / ``push_pending``、不再靠 ``super()`` 串起来。

## 四条不变量（#54 之后）

第五条（#57）：**临时 id 的所有权跟着记录走**。剪枝可以只剪掉那一**行**（它没有「还没到
服务端的改动」时），但那条记录还在原地等认领——所以分配器（
:meth:`~dida.storage.store.Store.new_local_list_id`）**两处一起看**：行与记录。只看行就会把
一个还挂着记录的号再发一次，于是一个号上出现两条清单的记录；那时按 id 找记录的任何一处都会
挑错**一条**，最坏是拿另一条清单的名字去删服务端上的一行（#57 的探针就是这么删掉用户手机上
一条清单的）。配套的两条硬规矩：**一个号上那几条记录说的不是同一条清单时，什么都不许做**
（:class:`AmbiguousLocalListError`，大声拒绝），以及**加一种记录 / 一种端点时必须显式分类**
（:attr:`~dida.sync.lists.ListWriteKind.counts_as_pending` / ``holds_its_row`` /
:attr:`ListWire.addresses_an_id`——默认值取保守的那一侧）。

**这四条不是清单专属的形状**：任务的写路径上是同一个洞（#53 的「认领了但没把队列里的改动带走」，
#39 的「排在一条还没成真的新建后面」），同一套不变量对它一样成立——差别只在第 3 条：任务的
标题不是身份，按标题对回来太弱，所以任务那条路自愈（下一次全量刷新把真 id 那行写回来、
把临时那行剪掉）。清单这边多一步按名字认领，是因为服务端建完清单**不回 id** 这条路真的会走到。

`POST /open/v1/project` 的响应表里两种成功都写着（``200 → Project`` / ``201 → No Content``），
所以「建好了、但服务端没回 id」不是边角情况。落在那个状态上的一行如果还照旧排队，之后每一次
改名与删除都会打到一个**服务端从没见过的 id** 上——404、永远重试，而删掉的那条下一次刷新
还会回来。所以四条一起成立才没有这个洞：

1. **不可寻址的 id 不许发请求**：带着 :data:`LOCAL_LIST_PREFIX` 的 id 服务端没见过，改 / 删
   打到它上面只会得到一个 404。:func:`is_addressable` 是这个判断唯一的地方。（新建是例外：
   ``POST /open/v1/project`` 的 URL 里没有 id。）
2. **一条还没成真的新建，拥有这一行唯一的队列记录**：后来的改名**并进**它（改名 = 改请求体里
   的 ``name``），而不是在它后面排第二条改动——排上去也发不出去。
3. **认领是单独一步**：新建成功却没拿到 id 时，那一条记录换成 :attr:`ListWriteKind.AWAIT_ID`
   （一份**认领记录**，不是要发的请求体），下一次全量刷新拿服务端的清单集把它**按名字**对回来。
   只在**唯一**的时候认：同名的新行只有一条、等着的记录也只有一条；分不清就一个都不动，等下一次。
4. **删一条还没认领的清单不许把它丢掉**：服务端那行**已经存在**，只是我们不知道它的 id。本地
   先把那一行摘掉，队列里留一笔「认领之后立刻删」——不留的话，刷新就会把「已经删掉的清单」
   又写回来（#42 的验收标准 7）。

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
from typing import (
    Any,
    Awaitable,
    Callable,
    Mapping,
    Protocol,
    Sequence,
    runtime_checkable,
)

from dida.api.errors import DidaError
from dida.sync.view import ListSnapshot, ViewSource
from dida.sync.writes import is_a_change
from dida.vocabulary import (
    LOCAL_LIST_PREFIX,
    ListLocalEffect,
    ListWire,
    ListWriteKind,
    PendingListChange,
    is_local_list_id,
)

__all__ = [
    "AmbiguousLocalListError",
    "LIST_COLORS",
    "LOCAL_LIST_PREFIX",
    "ListColor",
    "ListLocalEffect",
    "ListWire",
    "ListWriteKind",
    "ListWriteTarget",
    "ProjectWriter",
    "UnknownListError",
    "is_addressable",
    "is_list_edit",
    "is_local_list_id",
]

# ``LOCAL_LIST_PREFIX`` 与 ``is_local_list_id`` 是**转发**，不是定义（#39 / #54 的裁定：
# 「这个 id 服务端见过没有」这条形状判断全仓库只许有一处）。定义处是 :mod:`dida.vocabulary`
# （#78 搬过去的：本地库与写路径两边共用，谁都不许 import 对方）。
# 名字在这里转出来，是为了让 ``from dida.sync.lists import LOCAL_LIST_PREFIX`` 这一族读法照旧。


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


def is_addressable(change: PendingListChange) -> bool:
    """这一笔清单改动现在**发得出去**吗（#54）。

    判断只有这一处，而且读的是词表：**这一种调用要不要一个服务端认得的 id** 写在
    :attr:`ListWire.addresses_an_id` 上（默认要），**这个 id 服务端见过没有**写在
    :func:`is_local_list_id` 上（全树只此一处判断）。两件事都不是在这里按成员名数的。

    三类不发的：:attr:`ListWriteKind.AWAIT_ID`（它是一份「还没认领」的记录，不是要发的改动）、
    改 / 删一个**本地临时 id**（服务端没有那个清单，打过去只会 404、然后永远重试）、
    以及任何新加的、没说清要不要 id 的端点（默认要，于是没分类就不发）。
    新建不在此列：``POST /open/v1/project`` 的 URL 里没有 id，它照发。

    发不出去不等于丢掉：它留在队列里（状态栏那个数照旧算它），等认领拿到真 id 之后自然
    变得可寻址。
    """
    wire = change.kind.wire
    if wire is ListWire.NONE:
        return False
    return not wire.addresses_an_id or not is_local_list_id(change.list_id)


def is_list_edit(
    *,
    current_name: object,
    current_color: object,
    name: str | None = None,
    color: str | None = None,
) -> bool:
    """这次改清单与**本地那一份**比，是不是一次真改动——**判据只此一处**（工单 #66）。

    没给的字段（``None``）不算改动：``update_list`` 的合同就是「``None`` = 别动这一格」
    （``color=None`` 是「别动颜色」，不是「清空颜色」）。给了的字段与当前值相同也不算——
    一次「保存」把原样交回来的那份写下去，与它根本不该发生是同一条。

    比较本身交给 :func:`dida.sync.writes.is_a_change`（工单 #79 立的**那一个**判据本体）：
    这一处只把两个当前值摆成它的形状（没给的字段干脆不出现在 ``changes`` 里），不自己再比
    一遍。所以「改字段」与「改清单」用的是同一条口径。

    引擎在 :meth:`ListMixin.update_list` 里问它，决定**写不写**，并把答案回报给调用方
    （#79：这条路径的签名从 ``-> None`` 变成 ``-> bool``）。界面**不再**问第二遍——它已经按
    回报值决定说不说「已保存」了。

    参数是**两个当前值**而不是一份本地原文：调用方手里是 ``list_payload`` 那份词典里的两位，
    而这条判据只该认值。
    """
    changes: dict[str, Any] = {}
    if name is not None:
        changes["name"] = name
    if color is not None:
        changes["color"] = color
    return is_a_change(
        {"name": current_name, "color": current_color},
        changes,
    )


class AmbiguousLocalListError(DidaError):
    """一个本地临时 id 上那几条记录说的**不是同一条清单**（#57）。

    这是不变量被破坏的状态（一条还没认领的清单只该有一条记录）。**不许按顺序挑一条**：
    挑错就是拿另一条清单的名字去改 / 删服务端上的一行——探针里那一次删掉了用户手机上的
    一条清单，事后待推送回到 0、界面上一个字都没有。所以这一层大声拒绝：什么都不做，
    让调用方把话说到屏幕上去。
    """

    def __init__(self, list_id: str, kinds: Sequence[str] = ()) -> None:
        detail = f"（{'、'.join(kinds)}）" if kinds else ""
        super().__init__(
            f"本地临时清单 {list_id} 上挂着不止一条记录{detail}：状态不对，这一笔不做。"
            "先同步一次，再看这个清单"
        )
        self.list_id = list_id
        """那个状态不对的临时 id，UI 可以直接显示出来。"""


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


class ListQueue:
    """清单那本账接给一台泵的适配器（#84）。

    泵（:class:`~dida.sync.pump.PumpMixin`）不认清单词汇：它只问那五件事，这个类把它们接到
    清单这一族的动词与 :data:`_LIST_WIRE` 上。任务那一本是另一个适配器
    （:class:`~dida.sync.push.TaskQueue`）——两份词汇表与两张队列表照旧分开，共用的是**机器**。
    """

    def __init__(self, target: ListWriteTarget, writer: ProjectWriter) -> None:
        self._target = target
        self._writer = writer

    def pending(self) -> Sequence[PendingListChange]:
        """还没推成功的清单改动，按发生顺序（泵每一笔都重新取一次：认领会改 id）。"""
        return self._target.pending_lists()

    def addressable(self, change: PendingListChange) -> bool:
        """这一笔现在发得出去吗——判据本体是 :func:`is_addressable`（一处）。"""
        return is_addressable(change)

    def record_attempt(
        self,
        change_id: int,
        *,
        error: str | None = None,
        next_retry_at: datetime | None = None,
    ) -> None:
        self._target.record_list_attempt(change_id, error=error, next_retry_at=next_retry_at)

    def resolve(self, change_id: int) -> None:
        self._target.resolve_list(change_id)

    async def send(self, change: PendingListChange) -> bool:
        """按线路调用形状查表把这一笔发出去；返回值 = 这一条**可以出队了**吗。

        失败照旧是结构化错误，往外抛（由泵退避）。新建成功但服务端没回 id 时回 ``False``：
        那一笔已经发出去了，但这一行还欠一个真 id，记录得留着（#54）。
        """
        handler = _LIST_WIRE.get(change.kind.wire)
        if handler is None:
            raise NotImplementedError(f"推送还没有实现「{change.kind.value}」这一种改动")
        return await handler(self, change)

    async def create_project(self, change: PendingListChange) -> bool:
        """新建：才知道服务端给的 id，所以推成功之后要顺手认领它。"""
        created = await self._writer.create_project(change.payload)
        if not isinstance(created, Mapping) or not created.get("id"):
            _park_created(self._target, change)
            return False
        _adopt_created_list(self._target, change, created)
        return True

    async def update_project(self, change: PendingListChange) -> bool:
        """改清单：底稿是本地那一行的原文（``sortOrder`` 最要紧，靠它 echo 回去）。"""
        await self._writer.update_project(
            change.list_id,
            change.payload,
            snapshot=self._target.list_payload(change.list_id),
        )
        # 推成功之后把**刚发出去的那一份**盖回本地：服务端建好这条清单时回的原文（认领那一步）
        # 里是**旧**名字，而这一笔改名就排在它后面——本地那一行因此可能显示成服务端刚回的那份，
        # 与刚刚发出去的内容不一致。用户写下的那份才是屏幕上该有的，直到下一次全量刷新由服务端
        # 权威裁决。
        local = self._target.list_payload(change.list_id)
        if local is not None:
            self._target.save_list({**local, **change.payload})
        return True

    async def delete_project(self, change: PendingListChange) -> bool:
        """删除：``DELETE``，这一条推成功就可以出队。"""
        await self._writer.delete_project(change.list_id)
        return True


_LIST_WIRE: dict[ListWire, Callable[[ListQueue, PendingListChange], Awaitable[bool]]] = {
    ListWire.CREATE_PROJECT: ListQueue.create_project,
    ListWire.UPDATE_PROJECT: ListQueue.update_project,
    ListWire.DELETE_PROJECT: ListQueue.delete_project,
}
"""**一处**记全每种清单线路调用形状怎么打（#84）：加一种端点形状在这里加一行。

:attr:`ListWire.NONE`（认领记录）没有行，也到不了这里——泵先用 :func:`is_addressable` 把它
滤掉。真到了就大声报错，绝不假装发过了。
"""


def _park_created(target: ListWriteTarget, change: PendingListChange) -> None:
    """新建成功了、但服务端没回 id（``201 No Content``，文档允许的两种成功之一，#54）。

    这一行**还欠一个真 id**，所以那一条记录留在这里、换成 :attr:`ListWriteKind.AWAIT_ID`
    ——一份认领记录（当时发出去的名字 + 建之前本地认得的那些 id），下一次全量刷新拿服务端的
    清单集把它对回来。

    **不能**把这一笔当成成功出队：出队之后这一行就停在临时 id 上，之后每一次改名与删除都会
    打到一个服务端没见过的 id 上、永远推不出去。
    """
    record: dict[str, Any] = {
        "sentName": change.payload.get("name"),
        "sentColor": change.payload.get("color"),
        "knownIds": _known_list_ids(target),
    }
    superseded = next(
        (
            item
            for item in target.pending_lists()
            if item.list_id == change.list_id
            and item.kind is ListWriteKind.DELETE
            and not is_addressable(item)
        ),
        None,
    )
    if superseded is not None:
        # 这一行建好之后用户已经删了它：那一条删除接替这一条记录。留两条同名记录的话，
        # 认领那一步会以为「同名的有两条、分不清」而一直不动。
        target.amend_list_change(superseded.id, payload={**record, **superseded.payload})
        target.resolve_list(change.id)
        return
    target.amend_list_change(change.id, kind=ListWriteKind.AWAIT_ID, payload=record)


def _adopt_created_list(
    target: ListWriteTarget, change: PendingListChange, created: Any
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
    local = target.list_payload(change.list_id)
    if local is None:
        # 这一行在新建出去的过程中被删掉了（用户删了它，那一笔删除排在新建后面）：认领
        # 只做「把队列挪到真 id 上」，**不把那一行写回来**——写回来就是「删掉的清单又出现
        # 了」。删除随后自己去删服务端那一行。
        target.identify_list(
            local_id=change.list_id, real_id=str(created["id"]), remove=True
        )
        return
    target.adopt_created_list(
        change.list_id,
        {**local, **created, "id": str(created["id"])},
    )


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
    ) -> bool:
        """改清单的名字与颜色：本地当场生效并入队，然后立即排一轮推送。

        **回报这次到底改了没有**（工单 #79）：一位都不变时什么都不写——不入队、不排推送，
        回 ``False``；真改了回 ``True``。调用方按这个值决定说不说「已保存」，不必自己再比
        一遍（#66 落地时界面确实问过同一个判据，#79 之后它不再问）。

        没给的字段**不动**（``color=None`` 是「别动颜色」，不是「清空颜色」）：请求体里
        只有这次真的要改的那些，其余的由客户端在推送时从本地原文里 echo 回去
        （``sortOrder`` 尤其要紧——文档写着 "default 0"，省略它可能把清单顺序重置）。

        给的字段**都与本地那一份相同**时什么都不写（#66 的验收标准 3，判据在
        :func:`is_list_edit`）：不入队、不排推送。``esc`` 从 #66 起是「保存并退出」，
        所以「开了表单又没改」这条路真的会走到——写一笔没发生的改动不是「多带了一笔」，
        它会让状态栏那个「待推送」为一个空操作亮着。
        """
        target = self._list_target()
        # 认领换过名的话，这一行现在叫另一个 id（工单 #75 / ADR-0009），先解析成真那个——否则
        # 下面 ``is_local_list_id`` 会把一个**已经被认领**的清单判成「还没推出去」，那一笔改名会
        # 被并进一条早就不该在的队列记录里。与 ``PushMixin.write`` 同一个形状、同一个名字。
        list_id = target.resolve_id(list_id)
        current = target.list_payload(list_id)
        if current is None:
            raise UnknownListError(list_id)
        if not is_list_edit(
            current_name=current.get("name"),
            current_color=current.get("color"),
            name=name,
            color=color,
        ):
            return False
        changes: dict[str, Any] = {}
        if name is not None:
            changes["name"] = name
        if color is not None:
            changes["color"] = color
        if is_local_list_id(list_id):
            self._fold_into_unclaimed(target, list_id, current=current, changes=changes)
            return True
        target.enqueue_list(
            list_id=list_id,
            kind=ListWriteKind.UPDATE,
            payload=changes,
            now=self._clock.now(),
            local={**current, **changes},
        )
        self._push_now()
        return True

    def _fold_into_unclaimed(
        self,
        target: ListWriteTarget,
        list_id: str,
        *,
        current: Mapping[str, Any],
        changes: Mapping[str, Any],
    ) -> None:
        """把一次改名**并进**「这一行还没被服务端认领」的那条队列记录里（#54）。

        临时 id 是不可寻址的（:func:`is_addressable`），所以排在它后面的第二条改动一辈子都
        发不出去。并进去之后，这一行仍然只有一条队列记录：新建还没发出去时它照旧是那条新建
        （发的就是改名之后的那一份）；已经建好、在等 id 时它换成一次普通的改名——那一笔
        **算进**待推送，因为它确实是用户刚做、还没到服务端的改动。
        """
        pending = _owning_change(target, list_id)
        if pending is None:
            raise UnknownListError(list_id)  # 不该发生：临时 id 的行一定有它那条记录
        target.amend_list_change(
            pending.id,
            kind=(
                ListWriteKind.CREATE
                if pending.kind is ListWriteKind.CREATE
                else ListWriteKind.UPDATE
            ),
            payload={**pending.payload, **changes},
            local={**current, **changes},
        )
        self._push_now()

    def delete_list(self, list_id: str) -> None:
        """删清单：本地那一行当场消失，推送走 ``DELETE``，推不动就留在队列里。

        **只动清单那一行**（模块文档里的第三个决定）：它里面的任务在服务端会怎样，
        文档没写，所以本地一条都不动——猜「一起删了」或猜「搬去收集箱」都是编事实。
        """
        target = self._list_target()
        # 与 :meth:`update_list` 同一处、同一个理由（工单 #75 / ADR-0009）：被认领过的清单拿旧 id
        # 来删，要落成一次真删除，而不是被当成「还没推出去的那一行」挂进认领记录等 id。
        list_id = target.resolve_id(list_id)
        current = target.list_payload(list_id)
        if current is None:
            raise UnknownListError(list_id)
        if is_local_list_id(list_id):
            self._park_delete(target, list_id)
            return
        target.enqueue_list(
            list_id=list_id,
            kind=ListWriteKind.DELETE,
            payload={},
            now=self._clock.now(),
        )
        self._push_now()

    def _park_delete(self, target: ListWriteTarget, list_id: str) -> None:
        """删一条**还没被认领**的清单（#54）：那一笔删除只能等 id 到手才发得出去。

        服务端那行**已经存在**（新建成功了），我们只是不知道它的 id——所以既不能现在删
        （没有 id 可删），也不能把这一笔丢掉（丢掉的话服务端那行还在，下一次刷新就把它写回来，
        屏幕上就是「删掉的清单又回来了」）。本地先把那一行摘掉，队列里留一笔带着**认领记录**
        的删除：认领之后它就是一笔普通的删除（:meth:`_identify_created_lists`）。

        新建**还没发出去**时是另一格：那一条留着照发（它可能已经到过服务端、只是回执没回来），
        删除排在它后面——认领（不管是 200 的直接认领还是 201 的按名字认领）会把两笔一起挪到
        真 id 上。
        """
        pending = _owning_change(target, list_id)
        record = _identify_record(target, pending)
        if record.get("sentName") is None:
            # 认不出这一行建出去时叫什么（队列记录不全，旧版本留下的状态）：用本地那一行现在
            # 的名字兜底。认领是**唯一才认**，对不上就等下一次，不会认错。
            record["sentName"] = (target.list_payload(list_id) or {}).get("name")
        if pending is not None and pending.kind is not ListWriteKind.CREATE:
            # 新建已经发出去了（在等 id；用户在等的时候还改过名也一样）：**这一条**记录改成
            # 删除，本地那一行摘掉。一行只留一条记录——留两条同名的，认领那一步就分不清谁是谁，
            # 于是永远不动。
            target.amend_list_change(pending.id, kind=ListWriteKind.DELETE, payload=record)
            target.drop_list(list_id)
        else:
            target.enqueue_list(
                list_id=list_id,
                kind=ListWriteKind.DELETE,
                payload=record,
                now=self._clock.now(),
            )
        self._push_now()

    def _identify_created_lists(self) -> int:
        """把「建好了、但服务端没回 id」的清单认回来（#54），返回认了几条。

        钥匙是**当时发出去的名字**（``sentName``）：服务端那一行现在就叫这个名字。认定的规矩
        只有一条——**唯一才认**：

        - 服务端那边同名的新行只有一条；
        - 等着的记录也只有一条（两条同名的记录说明本地建过两条同名的，分不清谁是谁）；
        - 「新」= 不在 ``knownIds`` 里。本来就有一张同名清单的用户，不能因为名字一样就把
          自己那张老清单认成刚建的这一条。

        分不清就一条都不动、等下一次全量刷新（宁可不动，也不许认错）。
        """
        target = self._source
        if not isinstance(target, ListWriteTarget):
            return 0
        records = tuple(target.pending_lists())
        parked = [item for item in records if not is_addressable(item)]
        if not parked:
            return 0
        # 一个号上那几条记录说的不是同一条清单时，那个号整个**不许动**（#57）：陈旧的那一条
        # 唯一对上一行时正是探针里删掉用户清单的那一步。
        by_id: dict[str, list[PendingListChange]] = {}
        for item in records:
            by_id.setdefault(item.list_id, []).append(item)
        reused = {
            list_id for list_id, group in by_id.items() if not _records_are_one_list(group)
        }
        rows = tuple(target.lists())
        identified = 0
        for change in sorted(parked, key=lambda item: item.id):
            if change.list_id in reused:
                continue
            name = _record_name(change) or _name_of(rows, change.list_id)
            if not name:
                continue
            known = set(change.payload.get("knownIds") or ())
            candidates = [
                row
                for row in rows
                if row.name == name and row.id not in known and not is_local_list_id(row.id)
            ]
            waiting = [
                item
                for item in parked
                if (_record_name(item) or _name_of(rows, item.list_id)) == name
            ]
            if len(candidates) != 1 or len(waiting) != 1:
                continue
            identified += self._identify(target, change, candidates[0])
        return identified

    @staticmethod
    def _identify(
        target: ListWriteTarget, change: PendingListChange, row: ListSnapshot
    ) -> int:
        """认一条：把这一行的队列记录挪到服务端那个 id 上，并按它的意思办（#54）。"""
        if change.kind is ListWriteKind.DELETE:
            # 服务端那行还在（我们正是靠它认出的 id），而用户已经把它删了：认领要顺手把
            # 刷新刚写进来的那一行**摘掉**，否则屏幕上就是「删掉的清单又回来了」。
            # 删除本身留着，认领之后它就是一笔普通的删除，下一次推送去删。
            target.identify_list(local_id=change.list_id, real_id=row.id, remove=True)
            return 1
        if change.kind is ListWriteKind.AWAIT_ID:
            # 建出去的那一份就是用户要的：真 id 那一行刷新已经写进来了，这里只要合上。
            target.identify_list(local_id=change.list_id, real_id=row.id)
            target.resolve_list(change.id)
            return 1
        # 改名并进来的那一笔：本地先显示用户要的那一份（不然屏幕上会闪一下服务端的旧名字），
        # 记录留着——认领之后它是一笔普通的改名，下一次推送去改。
        desired = {
            key: value
            for key, value in change.payload.items()
            if key in ("name", "color") and value is not None
        }
        server = target.list_payload(row.id) or {}
        target.identify_list(
            local_id=change.list_id,
            real_id=row.id,
            row={**server, **desired, "id": row.id},
        )
        if all(server.get(key) == value for key, value in desired.items()):
            # 服务端那一份就是用户要的那一份（比如改了一圈又改回原名）：这一笔没有什么要发的，
            # 认领之后直接出队——少发一个什么都不改的请求。
            target.resolve_list(change.id)
        return 1

    def _list_queue(self) -> ListQueue:
        """清单那本账（#84）：交给那一台泵的就是它。

        泵不认清单词汇——它只认 :class:`~dida.sync.pump.PushQueue` 那五件事，这个适配器把它们
        接到清单这一族的动词（``pending_lists`` / ``record_list_attempt`` / ``resolve_list``）
        与分派表 :data:`_LIST_WIRE` 上。
        """
        return ListQueue(self._list_target(), self._list_writer())

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


def _records_are_one_list(records: Sequence[PendingListChange]) -> bool:
    """这几条记录说的是**同一条**清单吗（#57 的第 2 条不变量）。

    一个本地临时 id 上只该有这些形状：

    - 一条「自己的」记录：新建 / 改名 / 还没认领（``CREATE`` / ``UPDATE`` / ``AWAIT_ID``）；
    - 以及**最多一条**排在它后面的删除（新建还没发出去就被删掉的那一格，见
      :meth:`ListMixin._park_delete`）——它与那条新建必须是**同一个名字**，否则说明这个号
      被两条清单用过了。

    别的组合都说明 id 被复用了（两条记录争一个号）。那时**什么都不许做**：挑一条就是拿
    另一条清单的名字去改 / 删服务端上的一行。
    """
    owning = [item for item in records if item.kind is not ListWriteKind.DELETE]
    deletes = [item for item in records if item.kind is ListWriteKind.DELETE]
    if len(owning) > 1 or len(deletes) > 1:
        return False
    if not owning or not deletes:
        return True
    if owning[0].kind is not ListWriteKind.CREATE:
        return False  # 删除只与「还没发出去的新建」配对；别的组合都是复用的痕迹
    return _record_name(owning[0]) == _record_name(deletes[0])


def _record_name(change: PendingListChange) -> str | None:
    """一条记录说的是哪条清单的名字：认领记录用 ``sentName``（发出去时的名字），
    还没发出去的新建用它请求体里的 ``name``。"""
    return change.payload.get("sentName") or change.payload.get("name")


def _owning_change(target: ListWriteTarget, list_id: str) -> PendingListChange | None:
    """这一行「自己的」那条记录；一条都没有就是 ``None``（#54 / #57）。

    删除不算「自己的」：一行上同时排着新建与删除时（见 :meth:`ListMixin._park_delete`），
    改名要并进那一条新建，不是并进删除。

    **两条以上就是状态坏了**：不挑第一条，当场 :class:`AmbiguousLocalListError`
    ——挑错就是删错清单，而这一层是唯一能拦住它的地方。
    """
    records = [item for item in target.pending_lists() if item.list_id == list_id]
    if not _records_are_one_list(records):
        raise AmbiguousLocalListError(list_id, [item.kind.value for item in records])
    return next((item for item in records if item.kind is not ListWriteKind.DELETE), None)


def _identify_record(
    target: ListWriteTarget, pending: PendingListChange | None
) -> dict[str, Any]:
    """一份认领记录：这一行建出去时叫什么（``sentName``）、建之前本地认得哪些 id（``knownIds``）。

    ``sentName`` 是对上服务端那一行的钥匙；``knownIds`` 是排除项——本来就有一张同名清单的
    用户，不能因为名字一样就被认成刚建的那一条。一条记录都没有时只剩排除项（靠本地那一行的
    名字兜底），那是给「队列是旧版本留下的」那类状态用的。
    """
    if pending is None:
        return {"knownIds": _known_list_ids(target)}
    if "sentName" in pending.payload:
        # 新建已经发出去了（在等 id）：钥匙是**当时发出去**的那个名字，不是用户后来改成的名字
        # ——服务端那一行现在叫的还是前者。
        return {
            "sentName": pending.payload.get("sentName"),
            "knownIds": list(pending.payload.get("knownIds") or ()),
        }
    return {
        "sentName": pending.payload.get("name"),
        "knownIds": _known_list_ids(target),
    }


def _known_list_ids(target: ListWriteTarget) -> list[str]:
    """本地已经认得的清单 id（不含本地临时那些）：建之前就有的，认领时要排除掉。"""
    return sorted(row.id for row in target.lists() if not is_local_list_id(row.id))


def _name_of(rows: Sequence[ListSnapshot], list_id: str) -> str:
    """本地那一行现在叫什么（认领记录里没有名字时的兜底；那一行已经没了就是空串）。"""
    return next((row.name for row in rows if row.id == list_id), "")


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

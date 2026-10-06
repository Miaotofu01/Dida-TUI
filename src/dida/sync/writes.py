"""写词汇：一次乐观写有哪几种、每一种怎么落地（t32）。

**这个模块是写类型的唯一定义处。** 引擎的 ``WriteKind`` 与存储的 ``ChangeKind`` 说的是同一
套词汇，指的也是**同一个对象**（``dida.storage.store`` 里那个名字只是别名）：加一种写只需要
在这里加一个成员，两层立刻都认得它。

在 t32 之前这套词汇被写了两遍——引擎一份、存储一份，成员一字不差地重复，中间还有一个按值
换算的 ``_storage_kind()``。重复本身不难受，难受的是它能漂：一边加了成员另一边没加，直到真
走到那条写路径才炸，而那时错误离原因已经很远了。

这一层是叶模块：只 import 标准库与 :mod:`dida.api.errors`，谁先 import 都行。为什么单独一个
模块，而不是把词表留在存储层：``dida.storage.store`` 在模块级 import ``dida.sync.view``，
所以引擎**不能**在顶层 import 存储（那条延迟 import 的注释记着这件事）。

**一种写的全部行为都记在同一个地方**：:data:`_BEHAVIOUR` 那张表，每个成员一行——本地快照
怎么变（``local``）、推送调客户端的哪一个方法（``wire``）、要不要顺手写下 ``status``
（``marks_completed``）、冲突裁决时整条任务豁不豁免（``whole_row``）。分派这些行为的地方
（:meth:`dida.storage.store.Store.enqueue`、:meth:`~dida.storage.store.Store._exempt_fields`、
:meth:`dida.sync.push.PushMixin._send` / ``_local_effect``）一律**读表**，不再逐个成员写 ``if``：
以前新增一种写要在两处枚举、一个换算函数、三处分派里各改一次，现在只在这里加一行。

写路径上共享的另外两样也在这里：本地副本要会的那几件事（:class:`WriteTarget`），以及
「这条写没有底稿、推不出去」的那个错误（:class:`UnknownTaskError`）——三片写路径
（:mod:`dida.sync.push` / :mod:`dida.sync.schedule` / :mod:`dida.sync.subtasks` …）都要它们，
放在这里才不会让它们互相 import。

加一种写类型：在 :class:`WriteKind` 里加一个成员、在 :data:`_BEHAVIOUR` 里加一行，就完了——
两层枚举、换算、分派都读这一处。只有「这条写要打一个**新形状**的端点」（例如 v2 的
``task/move`` 搬运、``task/batch`` 取消完成）才另外要在 :class:`WireCall` 里加一种调用形状：
那是新的外部行为，不是要同步的词汇。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence, runtime_checkable

from dida.api.errors import DidaError
from dida.sync.view import ViewSource

if TYPE_CHECKING:  # 只为了标注：storage 反过来 import dida.sync.view，运行时不能在这里 import
    from dida.storage.store import PendingChange

__all__ = ["LocalEffect", "UnknownTaskError", "WireCall", "WriteKind", "WriteTarget"]


class LocalEffect(Enum):
    """一次写在**本地快照**上的效果；存储层照着它改本地那一份。"""

    MERGE = "merge"
    """把 ``payload`` 的字段盖上去。本地没有这条任务时就是「凭空多出一条」——新建走这里。"""

    REMOVE = "remove"
    """本地摘掉这条任务（删除的本地效果不是「等推送成功再摘」）。"""


class WireCall(Enum):
    """推送时调客户端的哪一个方法；引擎照着它分派。

    端点形状是 API 的事实，不是词汇的一部分，所以单独一个枚举——但**每一种写属于哪一种
    形状**记在这一处的表里，不散在分派代码里。
    """

    CREATE_TASK = "create_task"
    """``POST /open/v1/task``，请求体就是这份 payload。"""

    UPDATE_TASK = "update_task"
    """``POST /open/v1/task/{taskId}``，改动 + 本地那份完整底稿（未知字段靠它回写）。"""

    COMPLETE_TASK = "complete_task"
    """``POST .../task/{taskId}/complete``，没有请求体。"""

    DELETE_TASK = "delete_task"
    """``DELETE .../task/{taskId}``，没有请求体。"""

    MOVE_TASK = "move_task"
    """``POST /open/v1/task/move``：**数组**请求体、``{id, etag}`` 数组响应（工单 #45）。

    本仓库里唯一一个「请求体是数组」的端点，所以它单独一种调用形状（``:504``、``:516``）。
    """


@dataclass(frozen=True)
class WriteBehaviour:
    """一种写的行为说明（见模块文档的 :data:`_BEHAVIOUR`）。"""

    local: LocalEffect
    wire: WireCall
    marks_completed: bool = False
    """本地还要顺手写下「已完成」的 ``status``（值从存储层取，同一份 API 事实只留一处）。"""

    whole_row: bool = False
    """冲突裁决时**整条任务**豁免于服务端权威（删除就是这一种）。"""


class WriteKind(Enum):
    """一次乐观写的种类。

    值与存储层 ``pending_changes.kind`` 那一列直接对应（``ChangeKind`` 就是本枚举的别名）。
    :meth:`~dida.sync.push.PushMixin.write` 只服务**已有任务**的改 / 完成 / 删；新建走
    :meth:`~dida.sync.create.CreateMixin.create`，它的本地效果是「凭空多出一条任务」，
    与那三条盖字段的路径不是一回事。
    """

    CREATE = "create"
    """新建：本地先造一条（临时 id），推送走 ``POST /open/v1/task``（t15）。"""

    UPDATE = "update"
    """改字段：把 ``changes`` 推给 ``POST /open/v1/task/{taskId}``。"""

    COMPLETE = "complete"
    """完成：本地立刻标记完成，推送走 ``POST .../task/{taskId}/complete``（无请求体）。"""

    DELETE = "delete"
    """删除：本地立刻摘掉快照，推送走 ``DELETE .../task/{taskId}``。"""

    MOVE = "move"
    """搬运：本地把 ``projectId`` 换成目标清单，推送走 ``POST /open/v1/task/move``（#45）。

    **本地效果是 ``MERGE`` 而不是 ``REMOVE``**：那条任务一条都不少，换的只是它在哪个清单。
    推成功之后全量刷新带回来的原文里 ``projectId`` 已经是新的了，两边对得上。
    """

    @property
    def behaviour(self) -> WriteBehaviour:
        """这一种写在本地与推送两端分别怎么落地（:data:`_BEHAVIOUR` 那一行）。"""
        return _BEHAVIOUR[self]

    @property
    def local(self) -> LocalEffect:
        """本地快照怎么变（存储层读这个，不读成员名）。"""
        return _BEHAVIOUR[self].local

    @property
    def wire(self) -> WireCall:
        """推送调客户端的哪一个方法（推送分派读这个，不读成员名）。"""
        return _BEHAVIOUR[self].wire

    @property
    def marks_completed(self) -> bool:
        """本地要不要顺手写下 ``status``（完成是唯一的一种）。"""
        return _BEHAVIOUR[self].marks_completed

    @property
    def whole_row(self) -> bool:
        """整条任务豁不豁免于服务端权威（删除是唯一的一种）。"""
        return _BEHAVIOUR[self].whole_row


_BEHAVIOUR: dict[WriteKind, WriteBehaviour] = {
    WriteKind.CREATE: WriteBehaviour(local=LocalEffect.MERGE, wire=WireCall.CREATE_TASK),
    WriteKind.UPDATE: WriteBehaviour(local=LocalEffect.MERGE, wire=WireCall.UPDATE_TASK),
    WriteKind.COMPLETE: WriteBehaviour(
        local=LocalEffect.MERGE, wire=WireCall.COMPLETE_TASK, marks_completed=True
    ),
    WriteKind.DELETE: WriteBehaviour(
        local=LocalEffect.REMOVE, wire=WireCall.DELETE_TASK, whole_row=True
    ),
    WriteKind.MOVE: WriteBehaviour(local=LocalEffect.MERGE, wire=WireCall.MOVE_TASK),
}
"""**一处**记全每种写的行为。加一种写只改这里（外加它要打的新端点形状）。

每个成员一个不少：``tests/test_write_kind.py`` 会逐个访问这些属性，漏一行当场红。
"""


@runtime_checkable
class WriteTarget(ViewSource, Protocol):
    """本地副本在写路径上要会的那几件事；t08 的 ``Store`` 满足它。

    比 :class:`~dida.sync.view.ViewSource` 宽：乐观写要能把改动落进本地库并入队，
    推送要能读回完整原文（未知字段的底稿）、按 id 出队、记一次失败。
    只读的替身（``InMemorySource``）不满足它——写入会大声报错，不假装写成功了。
    """

    def task_payload(self, task_id: str) -> dict[str, Any] | None:
        """一条任务的完整原文，喂给 ``update_task(snapshot=)`` 的那一份。"""
        ...

    def enqueue(
        self,
        *,
        task_id: str,
        kind: WriteKind,
        payload: Mapping[str, Any],
        now: datetime,
        list_id: str | None = None,
    ) -> PendingChange:
        """入队一条待推送改动，并让它在本地立刻生效。"""
        ...

    def pending(self) -> Sequence[PendingChange]:
        """还没推成功的改动，按发生顺序。"""
        ...

    def record_attempt(
        self,
        change_id: int,
        *,
        error: str | None = None,
        next_retry_at: datetime | None = None,
    ) -> None:
        """记一次推送失败：尝试次数 +1、最后一次错误、下次重试时刻。"""
        ...

    def resolve(self, change_id: int) -> None:
        """这条改动已经推到服务端了，出队。"""
        ...

    def adopt_created(self, local_id: str, payload: Mapping[str, Any]) -> None:
        """新建推成功：把本地那条临时 id 的任务挪到服务端给的 id 上（t15）。"""
        ...


class UnknownTaskError(DidaError):
    """这条写推不出去，所以拒绝它：本地没有一条能拼出请求的底稿（工单 #25）。

    三条写端点都要任务**真实的清单 id**：更新是 ``POST /open/v1/task/{taskId}``，请求体
    要求 ``id`` + ``projectId``（api-contracts.md）；完成与删除把它写在路径里。这份清单
    只存在于本地那份 ``task_payload`` 底稿里——**没有别处可查**，引擎手里只有 ``task_id``。

    没有底稿还硬写下去的实测后果：存储层拿收集箱兜底（``Store._list_of``），凭空造出一行
    没有 ``projectId`` 的快照，请求带着一个**猜来的**清单发出去，失败后那条改动永远留在
    队列里。状态栏那个数一直非零，用户读到的是「等一下就好」，而它永远不会好——这正是
    规范「如实呈现」要消灭的那类安静错误。所以宁可当场大声拒绝。
    """

    def __init__(self, task_id: str) -> None:
        super().__init__(
            f"本地缓存里没有任务 {task_id} 的可用底稿（写操作需要它的 projectId）："
            "拒绝入队，因为这条改动永远推不出去"
        )
        self.task_id = task_id
        """请求写入的那条任务 id，UI 可以直接显示出来。"""

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

__all__ = [
    "LOCAL_TASK_PREFIX",
    "LocalEffect",
    "UnclaimedTaskError",
    "UnknownTaskError",
    "WireCall",
    "WriteKind",
    "WriteTarget",
    "is_addressable",
    "is_addressable_task",
    "is_local_task_id",
]

LOCAL_TASK_PREFIX = "local-"
"""本地临时**任务** id 的前缀（t15 / #39）：新建的任务在服务端给出真 id 之前先用它占位。

它同时是一个**状态**：id 还挂着这个前缀，就说明那条任务的新建**还没有被认领**
（:meth:`~dida.storage.store.Store.adopt_created` 才是认领那一步）。服务端从没见过这个
id，所以任何带着它的改动都推不出去——:meth:`~dida.sync.create.CreateMixin.create` 造 id
与 :func:`is_addressable` 判「发不发得出去」读的是**同一个**常量，两处不会漂。
"""


def is_local_task_id(value: str) -> bool:
    """这个 id 是不是本地临时占位的（服务端没见过它）。

    与 :func:`dida.sync.lists.is_local_list_id` 同形同名：清单与任务各有一个本地占位前缀，
    两个判断各留一处，所以别处不许再写 ``startswith("local-")``。
    """
    return value.startswith(LOCAL_TASK_PREFIX)


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
}
"""**一处**记全每种写的行为。加一种写只改这里（外加它要打的新端点形状）。

四个成员一个不少：``tests/test_write_kind.py`` 会逐个访问这些属性，漏一行当场红。
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


def is_addressable(change: PendingChange) -> bool:
    """这一笔任务改动现在**发得出去**吗（#53）。

    与清单版的 :func:`dida.sync.lists.is_addressable` 是同一个判断、同一个名字，判据只差
    一处，差的是**要确认哪一个 id**：

    - 清单那条路的 URL 里是**清单** id（``POST /open/v1/project/{projectId}``），所以它看
      ``change.list_id``；
    - 任务这条路的 URL 里是**任务** id（``POST /open/v1/task/{taskId}``、
      ``.../task/{taskId}/complete``、``DELETE .../task/{taskId}``），所以它看
      ``change.task_id``。任务这一侧没有等价于「清单的 projectId」那样单独要确认的第二个
      id：``projectId`` 只在更新请求**体**里，而它的缺席由 :class:`UnknownTaskError` 那条
      底稿检查挡着（``write()`` 先要 ``task_payload`` 里有 ``projectId`` 才肯入队）。

    不发的两类（与清单版同形）：

    - 改 / 完成 / 删一个**本地临时 id** —— 服务端没有那个任务，打过去只会 404、然后永远
      重试、永远出不了队；
    - （新建不在此列：``POST /open/v1/task`` 的 URL 里没有 id，带着临时 id 的**正是它自己**
      ——它就是去换真 id 的那一笔，照发。）

    发不出去不等于丢掉：它留在队列里，等认领拿到真 id 之后自然变得可寻址。

    **这一处也是「入队之前先问一句」的那个判断**：写入那一侧要的无非是「这条任务现在可寻址
    吗」，而它手里还没有那笔改动——所以它走下面那个 :func:`is_addressable_task`，判据由这里
    分派，全仓库仍然只有一个出处。
    """
    return is_addressable_task(change.task_id, change.kind)


def is_addressable_task(task_id: str, kind: WriteKind = WriteKind.UPDATE) -> bool:
    """``is_addressable`` 的判据本体：这个任务 id + 这种写，现在发得出去吗。

    ``kind`` 默认按「已有任务的写」算（改 / 完成 / 删都是同一条判据）；只有新建例外，
    因为它的 URL 里没有 id。写入那一侧在**入队之前**问的就是这个函数。
    """
    if kind.wire is WireCall.CREATE_TASK:
        return True
    return not is_local_task_id(task_id)


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


class UnclaimedTaskError(DidaError):
    """这条任务的新建**还没被认领**，所以拒绝在它上面排队（#53）。

    认领 = 服务端建好之后回了 id、``adopt_created`` 把本地那条从 ``local-…`` 挪到真 id 上。
    在那之前，本地这一行的 id 服务端**从没见过**：排在它上面的改动会 POST 到
    ``/open/v1/task/local-…``、404、退避重试、**永远出不了队**——状态栏那个数一直非零，
    而用户的编辑永远到不了服务端，正是 :class:`UnknownTaskError` 挡的那类安静错误。

    为什么不干脆接受它：这条路径**会自愈**（下一次全量刷新带回真 id 那一行、并剪掉临时那
    一行），所以诚实的回答是「等这一步同步完」，不是安静地排一条永远失败的改动。
    完整机制（把改动并进那条还没成真的新建、或认领延迟到下一次刷新）归 #54；这一层只做
    最小的那一版：**拒绝，并且如实说出为什么**。

    ``POST /open/v1/task`` 的响应表里 200 带 body、201 无 content **两条都写着**
    （``openapi-dida365.md`` 的 Create Task 一节），所以「认领没发生」是一条真实会走到的路，
    不是假设。
    """

    def __init__(self, task_id: str) -> None:
        super().__init__(
            f"这条任务还在等同步（本地 id 是 {task_id}，服务端还没给它 id）："
            "这一步同步完再改它"
        )
        self.task_id = task_id
        """还没被认领的那条任务 id，UI 可以直接显示出来。"""

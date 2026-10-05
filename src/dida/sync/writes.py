"""写词汇：一次乐观写有哪几种（t32）。

**这个模块是写类型的唯一定义处。** 引擎的 ``WriteKind`` 与存储的 ``ChangeKind`` 说的是同一
套词汇，指的也是**同一个对象**（``dida.storage.store`` 里那个名字只是别名）：加一种写只需要
在这里加一个成员，两层立刻都认得它。

在 t32 之前这套词汇被写了两遍——引擎一份、存储一份，成员一字不差地重复，中间还有一个按值
换算的 ``_storage_kind()``。重复本身不难受，难受的是它能漂：一边加了成员另一边没加，直到真
走到那条写路径才炸，而那时错误离原因已经很远了。

这一层是叶模块：只 import 标准库与 :mod:`dida.api.errors`，谁先 import 都行。为什么单独一个
模块，而不是把词表留在存储层：``dida.storage.store`` 在模块级 import ``dida.sync.view``，
所以引擎**不能**在顶层 import 存储（那条延迟 import 的注释记着这件事）。

写路径上共享的另外两样也在这里：本地副本要会的那几件事（:class:`WriteTarget`），以及
「这条写没有底稿、推不出去」的那个错误（:class:`UnknownTaskError`）——三片写路径
（:mod:`dida.sync.push` / :mod:`dida.sync.schedule` / :mod:`dida.sync.subtasks` …）都要它们，
放在这里才不会让它们互相 import。

加一种写类型，改动落在这里与它的推送分支（:meth:`dida.sync.push.PushMixin._send`）——
词表本身不再有第二处要同步。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence, runtime_checkable

from dida.api.errors import DidaError
from dida.sync.view import ViewSource

if TYPE_CHECKING:  # 只为了标注：storage 反过来 import dida.sync.view，运行时不能在这里 import
    from dida.storage.store import PendingChange

__all__ = ["UnknownTaskError", "WriteKind", "WriteTarget"]


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

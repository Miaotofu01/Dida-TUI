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
（``marks_completed`` / ``clears_completed`` 这一对，完成与取消完成）、冲突裁决时整条任务
豁不豁免（``whole_row``）。分派这些行为的地方
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
    "LOCAL_ID_PREFIXES",
    "LOCAL_LIST_PREFIX",
    "LOCAL_TASK_PREFIX",
    "LocalEffect",
    "UnclaimedListError",
    "UnclaimedTaskError",
    "UnknownTaskError",
    "WireCall",
    "WriteKind",
    "WriteTarget",
    "is_a_move",
    "is_addressable",
    "is_addressable_task",
    "is_local_id",
    "is_local_list_id",
    "is_local_task_id",
    "project_in",
]

LOCAL_LIST_PREFIX = "local-list-"
"""本地临时**清单** id 的前缀（#42 / #54）：新建的清单在服务端给出真 id 之前先用它占位。

它同时是**一条语义**（#54）：带这个前缀的 id 服务端**没见过**，所以任何「打到一个 id 上」
的请求都不许发出去。存储层用它生成 id（:meth:`~dida.storage.store.Store.new_local_list_id`），
写路径用它判断能不能发。

**它住在这一层**（:mod:`dida.sync.writes`）：这是叶模块，谁先 import 都行；而
:mod:`dida.sync.lists` 一 import 就跑 ``from dida.sync.push import backoff_delay``，
``push`` 又 import ``writes``——反过来把判断放在 ``lists`` 里会成环（实测：四个入口
全部 ``ImportError: cannot import name ... from partially initialized module``）。
``lists`` 从这一层把它与判断一起取回去用，所以它们的**定义处**仍然只有这一处。
"""

LOCAL_TASK_PREFIX = "local-task-"
"""本地临时**任务** id 的前缀（t15 / #39）：新建的任务在服务端给出真 id 之前先用它占位。

它同时是一个**状态**：id 还挂着这个前缀，就说明那条任务的新建**还没有被认领**
（:meth:`~dida.storage.store.Store.adopt_created` 才是认领那一步）；服务端从没见过这个
id，所以任何带着它的改动都推不出去。

**刻意不与清单前缀重叠**（#39）：原来这里是 ``local-``，而 ``local-`` 恰好是
``local-list-`` 的**前缀**——一个 OR 起来判断两族的 :func:`is_local_id` 就必须先比长的，
那是「今天对、加一个前缀就错」的顺序依赖（这个仓库已经为「一条判断两处实现」付过一次
代价）。改成不重叠之后，判断与顺序无关，:func:`is_local_id` 也就没有歧义了。
"""

LOCAL_ID_PREFIXES: tuple[str, ...] = (LOCAL_LIST_PREFIX, LOCAL_TASK_PREFIX)
"""本地临时 id 的**全部**前缀——「本地临时 id」这件事的登记处。

存储层生成 id、写路径判断能不能发，读的都是这里。将来加一族本地 id（比如标签）只加一条；
``tests/test_local_ids.py`` 有一道对象级守卫：**任何一条不许是另一条的前缀**（这正是
``local-`` 那条老前缀踩过的坑），加错了它先红。
"""


def is_local_id(value: str) -> bool:
    """这个 id 是本地临时占位的吗（服务端没见过它）——**形状判断只有这一处**。

    两族前缀都不允许是彼此的前缀（见 :data:`LOCAL_TASK_PREFIX` 与
    ``tests/test_local_ids.py`` 那道守卫），所以这里 ``any(...)`` 的顺序**没有语义**：
    任何顺序给同一个答案。
    """
    return any(value.startswith(prefix) for prefix in LOCAL_ID_PREFIXES)


def is_local_list_id(value: str) -> bool:
    """这个 id 是不是本地临时**清单**占位的（服务端没见过它）。

    它只是 :func:`is_local_id` 的**窄化读法**（「是不是我这一族的」）——窄化是有意义的：
    清单与任务各有自己的 id 空间，而写路径要问的正是「我这一族的这个 id 服务端见过没有」。
    """
    return value.startswith(LOCAL_LIST_PREFIX)


def is_local_task_id(value: str) -> bool:
    """这个 id 是不是本地临时**任务**占位的（服务端没见过它）。同 :func:`is_local_list_id`。"""
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

    BATCH_UPDATE_TASK = "batch_update_task"
    """``POST /open/v1/task/batch`` 的 ``update`` 数组，每一条只带 id / projectId / status。

    取消完成走这一种（工单 #38）。它是一个**新形状**的端点（请求体是一个对象、里面装着
    数组，而不是一条任务），所以在这里单独列一种调用形状——这是新的外部行为，不是要同步
    的词汇。这条用法官方文档一字未提，是实测确认的（spec 的实测事实第 1 条）。
    """

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

    clears_completed: bool = False
    """本地还要把 ``status`` 写回「未完成」那一档（取消完成是唯一的一种，工单 #38）。

    值与 :attr:`marks_completed` 一样从存储层取，而且**只动 ``status``**：完成时间戳不动
    ——实测取消完成不会清掉它，而「还算不算已完成」的判据只有 ``status``。
    """

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

    UNCOMPLETE = "uncomplete"
    """取消完成：本地把 ``status`` 写回 ``0``，推送走 ``task/batch`` 的 ``update``（工单 #38）。

    与服务端权威的关系与别的写一样：本地先动，刷新时那条 ``status`` 由待推送改动豁免，
    服务端真的照做了才由它说了算（``-1`` 已放弃也是服务端可能给的答案）。
    """

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
    def clears_completed(self) -> bool:
        """本地要不要把 ``status`` 写回「未完成」那一档（取消完成是唯一的一种）。"""
        return _BEHAVIOUR[self].clears_completed

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
    WriteKind.UNCOMPLETE: WriteBehaviour(
        local=LocalEffect.MERGE,
        wire=WireCall.BATCH_UPDATE_TASK,
        clears_completed=True,
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


def is_addressable(change: PendingChange) -> bool:
    """这一笔任务改动现在**发得出去**吗（#53）。

    与清单版的 :func:`dida.sync.lists.is_addressable` 是同一个判断、同一个名字，判据只差
    一处，差的是**这次请求要说哪几个 id**：

    - 清单那条路的 URL 里是**清单** id（``POST /open/v1/project/{projectId}``），所以它看
      ``change.list_id``；
    - 任务那条路要说**两个**：``taskId``（``POST /open/v1/task/{taskId}``、
      ``.../task/{taskId}/complete``、``DELETE .../task/{taskId}``）与 ``projectId``
      （``MOVE`` 的 ``fromProjectId``、更新请求体里的那个、完成与删除路径里的那个）。两个
      都得是服务端见过的——落点在一条**还没推出去的清单**里时，``projectId`` 也是本地的
      （``local-list-…``），实测后果与「任务 id 是临时的」一模一样（#45 的 merger 探针）。
      判据本体在 :func:`is_addressable_task`，这一处只是把改动上那两个 id 取出来交给它。

    发不出去不等于丢掉：它留在队列里，等认领拿到真 id 之后自然变得可寻址。**新建不在此列**：
    ``POST /open/v1/task`` 的 URL 里没有 id，带着临时任务 id 的**正是它自己**——它就是去换真
    id 的那一笔；但它请求体里的 ``projectId`` 仍然得是服务端见过的（在
    :func:`is_addressable_task` 里判，写入那一侧也据此拒绝）。
    """
    return is_addressable_task(
        change.task_id,
        change.kind,
        project_id=change.list_id,
        target_project_id=project_in(change.payload),
    )


def is_addressable_task(
    task_id: str,
    kind: WriteKind = WriteKind.UPDATE,
    *,
    project_id: str | None = None,
    target_project_id: str | None = None,
) -> bool:
    """``is_addressable`` 的判据本体：这一笔写要说的每个 id 服务端都见过吗——**判据只此一处**。

    任务的「可寻址」= **``projectId`` 与 ``taskId`` 都已确认**（#53 的补充，有实测证据）：

    - **``projectId``**（``project_id``）：任务落在一条还没推出去的清单里时，它的清单 id 还是
      本地的（``local-list-…``）——服务端没有这个清单，任何点名它的请求（搬运用
      ``fromProjectId``、更新放在请求体里、完成与删除写在路径里）都会 404、退避重试、
      **永远出不了队**。
    - **``taskId``**：还挂着本地临时前缀的，服务端没有这个任务，改 / 完成 / 删都会打到一个
      不存在的任务上。**新建不在此列**：它的 URL 里没有 id，带着临时任务 id 的正是它自己。
    - **``target_project_id``**：搬运还要说清**搬到哪去**，那一边同样可能是一条还没推出去的
      清单。两个清单 id 都要确认，所以两个都查。

    「服务端见过没有」这一问只有 :func:`is_local_id` 一处实现，这里不自己写前缀比较——
    将来多一族本地 id 也不会漏。

    写入那一侧在**入队之前**问的就是这个函数（它手里还没有那笔改动），``is_addressable``
    在推送循环里问的也是它：同一个判据，两个时刻各问一次。
    """
    if project_id is not None and is_local_id(project_id):
        return False
    if target_project_id is not None and is_local_id(target_project_id):
        return False
    if kind.wire is WireCall.CREATE_TASK:
        return True
    return not is_local_id(task_id)


def is_a_move(current_list_id: str | None, target_list_id: str | None) -> bool:
    """这一下「搬到某个清单」算不算一次**真**改动——**判据只此一处**（工单 #58 的 T6）。

    不算的两种：目标是空的（没挑清单，不成一次搬运），或者目标就是它**现在待的那个清单**
    （那不是一次改动；凭空入队一笔「同一个清单之间搬」只会让状态栏那个数多一个没有意义的
    数，服务端那边也没人知道该怎么理解它）。

    **两个时刻各问一次**，与 :func:`is_addressable_task` 同一个形状：

    - 引擎在 :meth:`~dida.sync.push.PushMixin.move_task` 里问它，决定**写不写**（一次
      真改动才乐观落库、才入队、才排推送）。这条判断长在引擎这一层，是因为 ``move_task``
      是公开的写入口——谁都可能调它。
    - 界面在 :meth:`~dida.tui.app.DidaApp._apply_pick` 里问它，决定**推不推**。那一问不是
      多余：``move_task`` 的签名是 ``-> None``，回不了话，界面不问就会为一次根本没发生的
      改动推一轮（``tests/test_picker_fields.py`` 钉着那句 ``pushes == 0``）。

    以前这两处各写了一遍同一个比较，而界面那处的注释还写着「由引擎自己挡（``move_task``）」
    ——注释说引擎管、代码下面又自己管了一遍，而且两份会漂。现在实现只有这一份。
    """
    if not target_list_id:
        return False
    return (current_list_id or "") != target_list_id


def project_in(payload: object) -> str | None:
    """一笔改动里点名的**目标**清单（``{"projectId": …}``），没提就是 ``None``。

    只有搬运（``MOVE``）会在 payload 里带 ``projectId``：改期 / 完成 / 删除的请求体里都没有
    它（``status`` 不是写字段，``dueDate`` 那一类也不点清单）。认不出来的形状当「没提」——
    与读路径对脏数据的口径一致，不猜。

    **两个时刻各问一次，实现只有这一份**（工单 #58 的 T5）：写入那一侧拼改动时问一次
    （:meth:`~dida.sync.push.PushMixin.write`），推送循环里问一次
    （:func:`is_addressable`，以及 ``PushMixin._send`` 归因失败时）。它曾经在
    :mod:`dida.sync.push` 里逐字又写了一份——``push`` 本来就 import 这一层，所以那第二份
    是纯粹的重复，而重复是会漂的：一边改了形状、另一边没改，只有真走到那条路才炸。
    """
    if not isinstance(payload, Mapping):
        return None
    named = payload.get("projectId")
    return None if named is None else str(named)


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


class UnclaimedListError(DidaError):
    """这次写要点的那个**清单**服务端还没见过，所以拒绝它（#39 / #53 的补充）。

    一条任务落在**还没推出去的清单**里时（那条清单的 id 还是 ``local-list-…``），对这条任务
    的每一次写都会点名一个服务端没有的 ``projectId``：``MOVE`` 的 ``fromProjectId`` 取的是
    改动行上的 ``list_id``，完成与删除写在路径里，更新写在请求体里。实测的后果与
    :class:`UnclaimedTaskError` 一模一样——404、退避重试、**那笔改动永远出不了队**，队列永久
    增长，状态栏那个数永远不归零（#45 的 merger 探针量到过）。

    与「这条任务自己还没被认领」分开说的理由：用户该做的事不一样——这一句说的是**那条清单**
    还没同步完，等它同步完（#54 的认领机制会在下一次刷新按名字把它认回来）这一整类写就都能
    干了，而不是这一条任务有问题。

    与 :class:`~dida.sync.lists.UnknownListError` 不是一回事：那个是「本地没有这一行清单」，
    这个是「本地有这一行，但服务端还没有它」。

    「服务端见过没有」由 :func:`is_local_id` 一处判定；这一层只说**哪一半**没过（``list_id``
    落在用户看得见的那句话里）。
    """

    def __init__(self, list_id: str) -> None:
        super().__init__(f"这个清单还没同步完（服务端还不认识 {list_id}）：等它同步完再来")
        self.list_id = list_id
        """没被认领的那条清单 id，UI 可以直接显示出来。"""


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

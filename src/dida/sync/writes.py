"""写词汇的**行为**那一半：一次乐观写有哪几种、每一种怎么落地（t32 / #78）。

写类型的定义处是 :mod:`dida.vocabulary`（#78 搬过去的）：``WriteKind`` / ``WireCall`` /
``LocalEffect`` / ``WriteBehaviour`` 与本地 id 前缀都在那里，本地库与写路径两边共用。
引擎的 ``WriteKind`` 与存储的 ``ChangeKind`` 说的是同一套词汇、指的也是**同一个对象**
（``dida.storage.store`` 里那个名字只是别名）：加一种写只需要在词表里加一个成员，两层
立刻都认得它。

在 t32 之前这套词汇被写了两遍——引擎一份、存储一份，成员一字不差地重复，中间还有一个按值
换算的 ``_storage_kind()``。重复本身不难受，难受的是它能漂：一边加了成员另一边没加，直到真
走到那条写路径才炸，而那时错误离原因已经很远了。

这一层是叶模块：只 import 标准库、:mod:`dida.api.errors` 与共用词汇，谁先 import 都行。
在 #78 之前它和存储层互相 import，靠「只在类型检查时 import」那一处补丁撑着——共用词汇
独立成模块之后，那个环不存在了。

**一种写的全部行为都记在同一个地方**：:data:`dida.vocabulary._WRITE_BEHAVIOUR` 那张表，
每个成员一行——本地快照怎么变（``local``）、推送调客户端的哪一个方法（``wire``）、要不要
顺手写下 ``status``（``marks_completed`` / ``clears_completed`` 这一对，完成与取消完成）、
冲突裁决时整条任务豁不豁免（``whole_row``）。分派这些行为的地方
（:meth:`dida.storage.store.Store.enqueue`、:meth:`~dida.storage.store.Store._exempt_fields`、
:meth:`dida.sync.push.PushMixin._send` / ``_local_effect``）一律**读表**，不再逐个成员写 ``if``：
以前新增一种写要在两处枚举、一个换算函数、三处分派里各改一次，现在只在那里加一行。

写路径上共享的另外两样也在这里：本地副本要会的那几件事（:class:`WriteTarget`），以及
「这条写没有底稿、推不出去」的那个错误（:class:`UnknownTaskError`）——三片写路径
（:mod:`dida.sync.push` / :mod:`dida.sync.schedule` / :mod:`dida.sync.subtasks` …）都要它们，
放在这里才不会让它们互相 import。

加一种写类型：在 :class:`~dida.vocabulary.WriteKind` 里加一个成员、在那张表里加一行，就完了
——两层枚举、换算、分派都读那一处。只有「这条写要打一个**新形状**的端点」（例如 v2 的
``task/move`` 搬运、``task/batch`` 取消完成）才另外要在
:class:`~dida.vocabulary.WireCall` 里加一种调用形状：那是新的外部行为，不是要同步的词汇。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from dida.api.errors import DidaError
from dida.sync.view import ViewSource
from dida.vocabulary import (
    LOCAL_ID_PREFIXES,
    LOCAL_LIST_PREFIX,
    LOCAL_TASK_PREFIX,
    LocalEffect,
    PendingChange,
    TEXT_FIELDS,
    WireCall,
    WriteBehaviour,
    WriteKind,
    is_local_id,
    is_local_list_id,
    is_local_task_id,
    read_priority,
    read_tags,
    read_text,
    read_time,
)

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
    "is_a_change",
    "is_a_move",
    "is_addressable",
    "is_addressable_task",
    "is_local_id",
    "is_local_list_id",
    "is_local_task_id",
    "project_in",
]


def is_a_change(
    current: Mapping[str, Any] | None, changes: Mapping[str, Any] | None
) -> bool:
    """这次要盖上去的字段与**本地那一份原文**逐位比，有没有一位不同——**判据只此一处**（#79）。

    这是六条写路径（改字段 / 搬运 / 改期 / 顺延 / 改清单 / 改视图）共用的那一个判据本体：
    「这一次到底改了没有」只在这里回答一次，写的那一次把答案回报给调用方，界面不再各问一遍
    （ADR-0008 第二节记的那一次空写就是漏了它）。比的是**本地那一份原文**（服务端字段名那一
    层），不是界面上某一份缓存的显示值——界面手里的成品可能与原文差着归一化，拿它比会答错。

    归一化口径与本地库读那一份时（``Store._snapshot`` 一族）一致，每一条都有理由：

    - **文本**（``title`` / ``content`` / ``desc``）：缺省是空串，「没有这个字段」与
      「写一个空串」是同一个意思；
    - **``priority``**：缺省是 ``0``（API 的「无」就是 ``0``）；
    - **``tags``**：比**集合**——挑中的那几个标签变没变，不是它们排在第几个；
    - **``isAllDay``**：看**真假**；
    - **``dueDate``**：「缺省」与「显式 ``null``」是同一件事（都是「没有截止时间」），
      有值时比那一刻（同一时刻的两种写法不是改动）；
    - **认不出的字段**：原样比（``timeZone`` 这种我们只负责带回服务端的东西，一个字都不能动）。

    只比 ``changes`` 里提到的那几位：没提的字段这一次不盖，也就无所谓改没改。
    """
    if not changes:
        return False
    local: Mapping[str, Any] = current if isinstance(current, Mapping) else {}
    for key, value in changes.items():
        if key in TEXT_FIELDS:
            if read_text(local.get(key)) != read_text(value):
                return True
        elif key == "priority":
            if read_priority(local.get(key)) != read_priority(value):
                return True
        elif key == "tags":
            if set(read_tags(local.get(key))) != set(read_tags(value)):
                return True
        elif key == "isAllDay":
            if bool(local.get(key)) != bool(value):
                return True
        elif key == "dueDate":
            if read_time(local.get(key)) != read_time(value):
                return True
        elif local.get(key) != value:
            return True
    return False


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
    数，服务端那边也没人知道该怎么理解它）。比目标与当前值的是那**一个**判据本体
    :func:`is_a_change`（本层）——这一处只是把两个 id 摆成它的形状，不自己再比一遍。

    引擎在 :meth:`~dida.sync.push.PushMixin.move_task` 里问它，决定**写不写**，并把答案
    回报给调用方（#79：这条路径的签名从 ``-> None`` 变成 ``-> bool``）。界面**不再**问第二遍
    ——它已经按回报值决定推不推了。在 #58 落地到 #79 之间，界面确实问过同一个函数（那时
    ``move_task`` 回不了话）：判据一直只有一份，变的是**谁不再问**。
    """
    if not target_list_id:
        return False
    return is_a_change({"projectId": current_list_id or ""}, {"projectId": target_list_id})


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

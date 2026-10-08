"""共用词汇（#78）：本地副本与同步引擎**都要用**的那点类型与常量。

这一层**谁都不依赖**（只用标准库），却是 :mod:`dida.sync` 与 :mod:`dida.storage.store`
两边的共同下界：任务与清单的快照、待推送改动的记录（任务与清单两条）、刷新报告、同步
状态、完成 / 未完成那两个状态码、写入种类与线路调用形状、本地 id 前缀、视图定义的那份原文。

放在这里的**判定标准只有一条**：``sync`` 与 ``storage`` 都要用它，而它谁都不用。在它出现
之前，这批词汇住在本地库那一侧，``sync`` 只好用 6 处「只在类型检查时 import」加 3 处
函数内 import 绕开那个环，每处还留一句注释解释为什么不能写在文件开头。搬到这里之后依赖
方向是一条线：``sync ─► vocabulary ◄─ storage``，新加一个共用类型也不必先猜「放哪边才
不会成环」。

它是**词汇**，不是实现：这里没有业务判断（只有形状判断 :func:`is_local_id` 一族与
「定义 ↔ 落库原文」的翻译），也不认识 SQLite、HTTP、Textual。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# 本地 id 前缀：服务端没见过的那些 id 长什么样
# ---------------------------------------------------------------------------

LOCAL_LIST_PREFIX = "local-list-"
"""本地临时**清单** id 的前缀（#42 / #54）：新建的清单在服务端给出真 id 之前先用它占位。

它同时是**一条语义**（#54）：带这个前缀的 id 服务端**没见过**，所以任何「打到一个 id 上」
的请求都不许发出去。存储层用它生成 id（:meth:`~dida.storage.store.Store.new_local_list_id`），
写路径用它判断能不能发。

**它住在这个共用模块里**：存储层与写路径都要它，而两边谁都不许 import 对方（#78）。
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


# ---------------------------------------------------------------------------
# 快照与同步状态：缓存里那份原文的读法
# ---------------------------------------------------------------------------

INBOX_ID = "inbox"
"""收集箱在 API 里的 projectId 别名。"""

INBOX_NAME = "收集箱"


@dataclass(frozen=True)
class ListSnapshot:
    """缓存里的一条清单（API 叫 project）：只有事实，没有判断。

    ``color`` / ``group_id`` / ``kind`` / ``permission`` 是服务端 ``Project`` 上的字段
    （``kind`` 是 ``TASK`` / ``NOTE``，``permission`` 是 ``write`` / ``read`` / ``comment``），
    清单索引页要用它们标出「装不了任务的」「改不动的」那些行（用户故事 23 / 24）。
    ``is_inbox`` 是客户端认出来的收集箱那一行——服务端的清单索引里没有它（实测）。
    """

    id: str
    name: str
    color: str | None = None
    group_id: str | None = None
    kind: str | None = None
    permission: str | None = None
    is_inbox: bool = False


@dataclass(frozen=True)
class TaskSnapshot:
    """缓存里的一条任务快照：只有事实，没有判断。"""

    id: str
    title: str
    list_id: str
    due: datetime | None = None
    """截止时刻（带时区）；``all_day=True`` 时它是个**日期标记**，按 ``.date()`` 读
    （写出去的正常形状是那一天的 UTC 午夜，#73；修好之前的历史数据可能是本地午夜）。"""

    all_day: bool = False
    priority: int = 0
    """``0`` / ``1`` / ``3`` / ``5``（无 / 低 / 中 / 高），与 API 一致。"""

    completed: bool = False

    completed_at: datetime | None = None
    """完成时刻（服务端的 ``completedTime``）；本地刚完成、服务端还没认过的那些是 ``None``。"""

    desc: str = ""
    """服务端的 ``desc``：GLOSSARY 里它是**备注**（详情页「备注」那一行画的就是它）。"""

    content: str = ""
    """服务端的 ``content``：GLOSSARY 里它是**描述**。

    ``desc`` 与 ``content`` 是两个字面不同的字段，这里不合并、也不互相兜底——详情页两行
    各画各的，改一个不会覆盖另一个。**v1 把这两个标反了**（描述当成 ``desc``），这份
    spec 纠正它：描述 = ``content``、备注 = ``desc``（``GLOSSARY.md`` 的「任务」一节，
    翻转的落点在 :func:`dida.tui.pages.detail.fields_of`）。
    """

    tags: tuple[str, ...] = ()
    """服务端的 ``tags``：标签名，按服务端给的顺序。"""

    repeat_flag: str = ""
    """服务端的 ``repeatFlag``（重复规则原文，如 ``RRULE:FREQ=WEEKLY``）。

    任务行只需要「是不是重复任务」（空串 = 不是），规则原文照旧原样留着——它只读，而且
    回写时一个字都不许动（spec 的「改期绝不触碰重复规则」）。
    """

    reminders: tuple[str, ...] = ()
    """服务端的 ``reminders``（提醒触发器原文）：行里只读「有没有提醒」，不改。"""


@dataclass(frozen=True)
class SyncState:
    """缓存里的同步状态。"""

    last_refresh_at: datetime | None = None
    pending_count: int = 0


# ---------------------------------------------------------------------------
# 写入词汇（任务那一条路）
# ---------------------------------------------------------------------------


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

    本仓库里唯一一个「请求体是数组」的端点，所以它单独一种调用形状（``push.py`` 的
    ``_send_move`` 一族）。
    """


@dataclass(frozen=True)
class WriteBehaviour:
    """一种写的行为说明（见 :data:`_WRITE_BEHAVIOUR`）。"""

    local: LocalEffect
    wire: WireCall
    marks_completed: bool = False
    """本地还要顺手写下「已完成」的 ``status``（值从 :data:`COMPLETED_STATUS` 取，同一份
    API 事实只留一处）。"""

    clears_completed: bool = False
    """本地还要把 ``status`` 写回「未完成」那一档（取消完成是唯一的一种，工单 #38）。

    值与 :attr:`marks_completed` 一样从 :data:`UNCOMPLETED_STATUS` 取，而且**只动
    ``status``**：完成时间戳不动——实测取消完成不会清掉它，而「还算不算已完成」的判据
    只有 ``status``。
    """

    whole_row: bool = False
    """冲突裁决时**整条任务**豁免于服务端权威（删除就是这一种）。"""

    converges: bool = False
    """同值收敛：这次要盖上去的字段与本地那一份原文逐位相同时**什么都不做**（工单 #79）。

    「什么都没改」不是每一种写都有的一档：**改字段**有（用户把原样交回来），完成 / 取消完成 /
    删除**没有**——它们是不可逆的对外动作，再来一次就是再来一次，没有「省下这一笔」的道理
    （spec 的已知例外，那个判断的判据是 :func:`dida.sync.writes.is_a_change`）。所以这一位是
    **每一种写各自的事实**，与 :attr:`marks_completed` 同一个形状：写路径读它，不逐个成员写
    ``if``。
    """


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
        """这一种写在本地与推送两端分别怎么落地（:data:`_WRITE_BEHAVIOUR` 那一行）。"""
        return _WRITE_BEHAVIOUR[self]

    @property
    def local(self) -> LocalEffect:
        """本地快照怎么变（存储层读这个，不读成员名）。"""
        return _WRITE_BEHAVIOUR[self].local

    @property
    def wire(self) -> WireCall:
        """推送调客户端的哪一个方法（推送分派读这个，不读成员名）。"""
        return _WRITE_BEHAVIOUR[self].wire

    @property
    def marks_completed(self) -> bool:
        """本地要不要顺手写下 ``status``（完成是唯一的一种）。"""
        return _WRITE_BEHAVIOUR[self].marks_completed

    @property
    def clears_completed(self) -> bool:
        """本地要不要把 ``status`` 写回「未完成」那一档（取消完成是唯一的一种）。"""
        return _WRITE_BEHAVIOUR[self].clears_completed

    @property
    def whole_row(self) -> bool:
        """整条任务豁不豁免于服务端权威（删除是唯一的一种）。"""
        return _WRITE_BEHAVIOUR[self].whole_row

    @property
    def converges(self) -> bool:
        """同值收敛：盖上去的字段与本地那一份相同时什么都不写（只有改字段这一种）。"""
        return _WRITE_BEHAVIOUR[self].converges


_WRITE_BEHAVIOUR: dict[WriteKind, WriteBehaviour] = {
    WriteKind.CREATE: WriteBehaviour(local=LocalEffect.MERGE, wire=WireCall.CREATE_TASK),
    WriteKind.UPDATE: WriteBehaviour(
        local=LocalEffect.MERGE, wire=WireCall.UPDATE_TASK, converges=True
    ),
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
（名字带 ``_WRITE_`` 是为了与清单那一条的 :data:`_LIST_BEHAVIOUR` 分开——两张表住在这个
模块里，不重名。）"""


# ---------------------------------------------------------------------------
# 写入词汇（清单那一条路）
# ---------------------------------------------------------------------------


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

    NONE = "none"
    """不发任何请求：这一种改动不是「要发出去的东西」，而是一份**记录**（见 ``AWAIT_ID``）。"""

    @property
    def addresses_an_id(self) -> bool:
        """这一种调用是不是把主语写在 URL 里（那就要求那个 id 服务端认得，#57）。

        默认**是**：加一种新端点时忘了想这件事，得到的是「先不发」（改动留在队列里、状态栏
        那个数照旧算它），而不是「打到一个服务端没见过的 id 上」。只有两种是例外，而且都是
        URL 里根本没有 id 的：新建与「不发」。
        """
        return self not in (ListWire.CREATE_PROJECT, ListWire.NONE)


@dataclass(frozen=True)
class ListBehaviour:
    """一种清单写的行为说明（见 :data:`_LIST_BEHAVIOUR`）。

    ``counts_as_pending`` 与 ``holds_its_row`` 是**两处会读它的地方**（#57 的检查 8）：
    状态栏那个「待推送 N」数不算它（:meth:`~dida.storage.store.Store.pending_count`），
    剪枝要不要为它留住那一行（:meth:`~dida.storage.store.Store._has_dirty_list_change`）。
    两个默认值都取**保守**的那一侧（算改动、留住行）：加一种记录时忘了想这两件事，得到的是
    「多算一个数、多留一行」，不是「用户的东西不声不响地被剪掉」。
    """

    local: ListLocalEffect
    wire: ListWire
    counts_as_pending: bool = True
    """算不算「还没到服务端的改动」（状态栏那个数）。"""

    holds_its_row: bool = True
    """剪枝要不要为它留住本地那一行（以及：那一行是不是「用户还没上去的东西」）。"""


class ListWriteKind(Enum):
    """一次清单写的种类；值与队列表 ``pending_list_changes.kind`` 那一列一一对应。"""

    CREATE = "list_create"
    UPDATE = "list_update"
    DELETE = "list_delete"

    AWAIT_ID = "list_await_id"
    """新建**成功了**，但服务端没回 id（``201 No Content``）：这一行还欠一个真 id（#54）。

    这一种的 ``payload`` **不是**要发出去的请求体，而是一份**认领记录**：当时发出去的名字
    （``sentName``）、当时那份颜色（``sentColor``）、以及建之前本地就认得的那些 id
    （``knownIds``——按名字对的时候要把它们排除掉，不然「我本来就有一张同名清单」会被认成
    刚建的那一条）。认领发生在下一次全量刷新（:meth:`~dida.sync.lists.ListMixin._identify_created_lists`）。

    ``wire`` 是 :attr:`ListWire.NONE`：它从来不发请求，所以也**不算**进「待推送」那个数
    （建都建好了，用户没有欠服务端什么；等他真改了名字，那一笔会换成普通的 ``UPDATE``）。
    """

    @property
    def local(self) -> ListLocalEffect:
        """本地那一行怎么变（存储层读这个，不读成员名）。"""
        return _LIST_BEHAVIOUR[self].local

    @property
    def wire(self) -> ListWire:
        """推送调客户端的哪一个方法（分派读这个）。"""
        return _LIST_BEHAVIOUR[self].wire

    @property
    def counts_as_pending(self) -> bool:
        """这一种算不算「还没到服务端的改动」（状态栏那个数读它，不读成员名）。"""
        return _LIST_BEHAVIOUR[self].counts_as_pending

    @property
    def holds_its_row(self) -> bool:
        """剪枝要不要为这一种留住本地那一行（读它，不读成员名）。"""
        return _LIST_BEHAVIOUR[self].holds_its_row


_LIST_BEHAVIOUR: dict[ListWriteKind, ListBehaviour] = {
    ListWriteKind.CREATE: ListBehaviour(local=ListLocalEffect.SAVE, wire=ListWire.CREATE_PROJECT),
    ListWriteKind.UPDATE: ListBehaviour(local=ListLocalEffect.SAVE, wire=ListWire.UPDATE_PROJECT),
    ListWriteKind.DELETE: ListBehaviour(local=ListLocalEffect.DROP, wire=ListWire.DELETE_PROJECT),
    ListWriteKind.AWAIT_ID: ListBehaviour(
        local=ListLocalEffect.SAVE,
        wire=ListWire.NONE,
        counts_as_pending=False,  # 建都建好了：用户没有欠服务端什么
        holds_its_row=False,  # 服务端索引里找不到它就是影子，剪掉才对（号仍然被这条记录占着）
    ),
}
"""**一处**记全每种清单写的行为（与 :data:`_WRITE_BEHAVIOUR` 同一条规矩）。"""


# ---------------------------------------------------------------------------
# 状态码、待推送改动与刷新报告：本地库那一侧的数据形状
# ---------------------------------------------------------------------------

COMPLETED_STATUS = 2
"""任务「已完成」的 ``status`` 值（api-contracts.md：Completed 是 2，不是 1）。"""

UNCOMPLETED_STATUS = 0
"""任务「未完成」的 ``status`` 值（``0`` 是正常、``-1`` 是已放弃；spec 的「本地判定已完成
一律看 ``status``」）。

取消完成写回的就是这一档（工单 #38）。**它旁边的完成时间戳不会被清掉**——实测
（spec 的实测事实第 1 条）取消完成只改 ``status``，所以「还算不算已完成」只认这一个值，
不认有没有 ``completedTime``。与 :data:`COMPLETED_STATUS` 并排放在这里：同一个 API 事实
（状态码表）只写一处，调用点一个字面量都不写。
"""


@dataclass(frozen=True)
class PendingChange:
    """一条还没推到服务端的本地改动（spec 的待推送改动 schema）。"""

    id: int
    """本地行号；推送成功时用它 :meth:`~dida.storage.store.Store.resolve`。"""

    task_id: str
    list_id: str
    """改动发生时任务所在的清单；推送要拿它拼 URL。"""

    kind: WriteKind
    payload: dict[str, Any]
    """改动涉及的字段（本地已生效的那一份）。"""

    created_at: datetime
    attempts: int = 0
    next_retry_at: datetime | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class PendingListChange:
    """一条还没推到服务端的**清单**改动（工单 #42）。

    与 :class:`PendingChange` 长得像但不共用：清单改动没有任务 id，也不是作用在任务快照
    上的字段合并。``payload`` 对建 / 改是要发出去的请求体（只有真的要写的字段），对删除是
    空的——推送要 echo 回去的东西（``sortOrder``）不在队列里，而在 ``lists`` 那一行的
    原文里（:meth:`~dida.storage.store.Store.list_payload`），因为那才是用户看到的那一份。
    """

    id: int
    """本地行号；推送成功时用它 :meth:`~dida.storage.store.Store.resolve_list`。"""

    list_id: str
    """这一笔改的是哪个清单（新建时是本地那个临时 id）。"""

    kind: ListWriteKind
    payload: dict[str, Any]
    """要发出去的请求体（删除是空的）。"""

    created_at: datetime
    attempts: int = 0
    next_retry_at: datetime | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class StoredSyncState:
    """缓存里的同步状态（spec 的同步状态 schema）。

    比 :class:`SyncState` 宽：那个是引擎给 TUI 的视图模型，只有上次刷新时间与待推送数量；
    游标和逻辑日只在存储层与引擎之间流动，所以单独一个类型，不去改 t05 已经定稿的协议。
    """

    completed_cursor: str | None = None
    """已完成流拉到哪了（t09 的窗口从这里续）。"""

    last_refresh_at: datetime | None = None
    logical_day: date | None = None
    """上次算出来的逻辑日；缓存里那些「今天」的读法就是按它写的。"""


@dataclass(frozen=True)
class FieldOverride:
    """一次全量刷新对某条任务某个字段的处置。

    ``field`` 是 ``"*"`` 时表示**整条任务**：本地有一条还没推成功的删除，服务端那份
    整个不许写回来，否则用户删掉的任务会在下一次刷新时复活。

    ``task_id`` 在清单那一行上装的是**清单 id**（#42）：清单的整行豁免（本地有一笔还没推
    成功的建 / 改 / 删）与任务是同一条规矩，报告的读者要的就是那个 id 加「整行」这个事实。
    沿用 ``task_id`` 这个名字是因为这一处是清单侧唯一用到它的地方，为它另起一个类型不值当。
    """

    task_id: str
    field: str
    local: Any
    server: Any


@dataclass(frozen=True)
class RefreshReport:
    """一次全量刷新的结果。

    ``written_*`` 是这次真正写进去的行数：同一份数据拉第二次全是 0，界面因此不闪、光标
    因此不丢。``pruned_*`` 是这次**从本地库里删掉**的行数——服务端已经没有它们了（#41）。

    ``overwritten`` 是服务端权威真的把本地值盖掉的字段（ADR-0002 要求这种覆盖能被
    用户看见）；``suppressed`` 是被待推送改动挡回去的那些——两者都不含没变化的东西，
    所以重复拉同一份数据的报告是空的。
    """

    written_lists: int = 0
    written_tasks: int = 0
    pruned_lists: int = 0
    pruned_tasks: int = 0
    overwritten: tuple[FieldOverride, ...] = ()
    suppressed: tuple[FieldOverride, ...] = ()


# ---------------------------------------------------------------------------
# 读本地那一份原文的口径：服务端字段名 → 本地那一份的值（#79）
#
# 本地库（``Store._snapshot``）与写路径（``dida.sync.writes.is_a_change``）都要把服务端原文
# 的一格读成一个值，而**两边必须读成同一个值**——否则「这次到底改了没有」会在两个口径之间
# 答错（写路径拿判据比一遍、本地库拿另一种读法看一遍）。这一族就是那份口径本身：谁读都用它，
# 不再各写一遍（``tests/test_architecture.py`` 的「同名的函数体不许写两份」正盯着这件事）。
# ---------------------------------------------------------------------------

TEXT_FIELDS = frozenset({"title", "content", "desc"})
"""本地原文里那几段**文字**：缺省读作空串（:func:`read_text`）。"""


def read_time(value: Any) -> datetime | None:
    """服务端给的一个日期字符串 → 那一刻；不是字符串、或者吃不下，就当**没有**。

    文档的形状是 ``yyyy-MM-dd'T'HH:mm:ssZ``，实测里偏移既可能是 ``+0800`` 也可能是
    ``+08:00``，还可能带毫秒。``fromisoformat``（3.11+）这几种都吃得下；吃不下就当作没有
    截止时间，绝不让一条脏日期把整个刷新带崩。``None`` 与「这个字段不在原文里」是同一个
    意思——**「缺省」与显式的 ``null`` 是同一件事**。
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def read_text(value: Any) -> str:
    """服务端给的一段文字 → 字符串；不是字符串就当没有。

    「没有这个字段」与「写了一个空串」因此是同一个值——本地库与写路径都按这一条读。
    """
    return value if isinstance(value, str) else ""


def read_priority(value: Any) -> int:
    """服务端给的优先级 → 整数；缺省与脏值都是 ``0``（API 的「无」就是 ``0``）。"""
    try:
        return int(value or 0)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


def read_tags(value: Any) -> tuple[str, ...]:
    """服务端给的标签数组 → 标签名元组；不是数组、或者混了别的东西，就跳过那一项。

    顺序照服务端给的来（排序是服务端的事）；比「改了没有」的时候由调用方按**集合**比。
    """
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


# ---------------------------------------------------------------------------
# 视图定义：过滤条件与它落本地库的那份原文（视图只在本地）
# ---------------------------------------------------------------------------

ANY_VALUE = "any"
"""「不限」那一档的值。

三处「不限」（截止时间 / 完成状态 / 完成时间）用**同一个**值：表单里「不限」只有一个意思，
三处写法不同的话，读的人就得逐个记住哪个字段用哪个拼法。
"""


class Completion(Enum):
    """完成状态那一维（spec 的过滤维度之一）：未完成 / 已完成 / 不限。

    「所有」是**全部未完成**任务，不是「全部任务」——已完成的不进任何内置视图。
    """

    UNFINISHED = "unfinished"
    COMPLETED = "completed"
    ANY = "any"


@dataclass(frozen=True)
class DueWindow:
    """截止时间落在哪一段，**相对当前逻辑日**表达（spec 的过滤维度之一）。

    ``first`` / ``last`` 是相对「今天」的**天数偏移**（``0`` = 今天，``-1`` = 昨天，
    ``6`` = 六天后），``None`` 表示那一侧没有边界。``dated`` / ``undated`` 说**有日期的**
    与**没有日期的**分别收不收——「无日期」是 spec 里独立的一个区间词，不是某一段的边界
    （所以它没法靠 ``first``/``last`` 表达：任何一段偏移都是「有日期」的一段）。

    于是三个内置视图的截止条件各是一句话：「今天」= ``DueWindow(first=None, last=0)``
    （上界今天、下界不设 ⇒ 逾期全在）；「最近七天」= ``DueWindow(first=0, last=6)``；
    「所有」= 不设（``None``），所以没有日期的任务也在。
    """

    first: int | None = 0
    last: int | None = 0
    dated: bool = True
    undated: bool = False

    def covers(self, day: date | None, *, today: date) -> bool:
        """这个逻辑日（``None`` = 没有截止时间）落不落在这一段里。"""
        if day is None:
            return self.undated
        if not self.dated:
            return False
        if self.first is not None and day < today + timedelta(days=self.first):
            return False
        if self.last is not None and day > today + timedelta(days=self.last):
            return False
        return True


@dataclass(frozen=True)
class ViewDefinition:
    """一个视图的**过滤条件 + 名字**（内置与自定义是同一个类型）。

    六个维度全在这一个类型上，判据全在 :func:`dida.sync.views._matches` 一处（#35 加了
    前两维，#36 加了后四维）：

    - ``due``：截止时间落在哪一段（相对当前逻辑日）；
    - ``completion``：完成状态（未完成 / 已完成 / 不限）；
    - ``lists``：清单范围——只收列出来的那几个清单里的任务；
    - ``priorities``：优先级——只收这几档（API 的线上编码 0/1/3/5）；
    - ``tags``：标签——命中任一个就算；
    - ``completed_days``：完成时间——完成的**逻辑日**落在最近 N 个逻辑日内（含今天）。

    **后四维的默认值都是「不筛」**（空元组 / ``None``），所以内置视图那三个定义一个字都没改。
    加一维就是往这里加一个字段、往 ``_matches`` 里加一条判据，不是另写一条求值路径。

    它住在这里（而不是 :mod:`dida.sync.views`）的原因：本地库要原样存下它、再读回来，
    而本地库不许 import ``sync``（#78）；求值仍留在 ``sync/views.py``。
    """

    id: str
    name: str
    due: DueWindow | None = None
    """截止时间的区间；``None`` = 不限（没有日期的任务因此也在）。"""

    completion: Completion = Completion.UNFINISHED

    lists: tuple[str, ...] = ()
    """清单范围：服务端的 project id；空元组 = 不限（所有清单）。"""

    priorities: tuple[int, ...] = ()
    """优先级：``0`` / ``1`` / ``3`` / ``5``；空元组 = 不限。"""

    tags: tuple[str, ...] = ()
    """标签名；空元组 = 不限。"""

    completed_days: int | None = None
    """完成时间的窗口（最近几个逻辑日，含今天）；``None`` = 不限。

    它与 ``completion`` 是**两维**，各自独立：完成状态说「要不要已完成的」，这一维说
    「完成于什么时候」。「最近完成」那个例子是两者一起用（``COMPLETED`` + ``7``）。
    ``Completion.UNFINISHED`` 配上一个窗口是**永远筛不出东西**的组合（未完成的任务没有
    完成时间），所以表单那一层拒绝它（``dida.sync.views.ViewFormProblem``）。
    """


def view_payload(definition: ViewDefinition) -> dict[str, Any]:
    """一份定义 → 落库的那份原文（JSON 装得下的纯数据）。

    六个维度都在里面，所以加一维**不必**改表（``views`` 那一行是一份原文，不是六列）。
    ``id`` 也在里面，好让 :func:`view_from_payload` 能独立把一份定义读回来；不过读的时候
    以 ``views.id`` 那一列为准（一个定义的身份只有一个来源）。
    """
    return {
        "id": definition.id,
        "name": definition.name,
        "due": None if definition.due is None else asdict(definition.due),
        "completion": definition.completion.value,
        "lists": list(definition.lists),
        "priorities": list(definition.priorities),
        "tags": list(definition.tags),
        "completed_days": definition.completed_days,
    }


def view_from_payload(payload: Mapping[str, Any], *, view_id: str = "") -> ViewDefinition:
    """那份原文 → 一份定义；认不出来的部分**退回默认**，不把整屏带走。

    库里那一行读不成样子时（手改过、旧版本写的），读路径上没有「坏一行就整屏空掉」的道理
    ——与空缓存给空视图、缺失的 ``projectId`` 不猜成字面量 ``inbox`` 同一条口径。认得出来的
    部分照旧留着。
    """
    due = payload.get("due")
    return ViewDefinition(
        id=view_id or str(payload.get("id") or ""),
        name=str(payload.get("name") or ""),
        due=_due_of(due),
        completion=_completion_of(payload.get("completion")),
        lists=_string_tuple(payload.get("lists")),
        priorities=_int_tuple(payload.get("priorities")),
        tags=_string_tuple(payload.get("tags")),
        completed_days=_completed_days_of(str(payload.get("completed_days") or "")),
    )


def _due_of(value: Any) -> DueWindow | None:
    """原文里那一维 → 一个区间；不是字典、或者四个字段一个都没有，就是**不限**。

    ``{}`` 不能当成 ``DueWindow()``：那是个默认值——``first=0, last=0``，也就是「只收今天
    到期的」。坏一行就悄悄换成一个筛错东西的视图，比读不出来坏得多。
    """
    if not isinstance(value, Mapping):
        return None
    known = {key: value[key] for key in ("first", "last", "dated", "undated") if key in value}
    return DueWindow(**known) if known else None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value)


def _int_tuple(value: Any) -> tuple[int, ...]:
    """数字那一维：认不出来的项**丢掉**，不让一个 ``"x"`` 把整行读崩。"""
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[int] = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return tuple(out)


def _completion_of(value: str | None) -> Completion:
    """那一档的值 → 完成状态；认不出来的（手改过的库）当作默认的「未完成」。"""
    return next(
        (item for item in Completion if item.value == value), Completion.UNFINISHED
    )


def _completed_days_of(value: str | None) -> int | None:
    """那一档的值 → 窗口天数；``ANY_VALUE`` / 认不出来的都是「不限」。"""
    if not value or value == ANY_VALUE:
        return None
    return int(value) if value.isdigit() and int(value) > 0 else None

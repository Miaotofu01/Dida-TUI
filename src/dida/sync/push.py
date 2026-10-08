"""写路径：乐观入队、立即推送、退避重试（t10；完成 t11、删除 t16）。

这一片只管一件事：**一次本地写怎么变成一次服务端写**——乐观地落进本地库并入队，推送排在
事件循环上立刻跑，推不动就留在队列里按注入的钟退避重试。这一轮该不该试由 :func:`can_attempt`
一处判定（还没放弃、而且到点了；已经放弃的只有手动同步 ``r`` 才试，工单 #71），等多久由
:func:`backoff_delay` 按 :data:`RETRY_SCHEDULE` 算，两者都是纯函数（不掷骰子、不睡眠）。

:class:`PushMixin` 的方法挂在组装好的 :class:`~dida.sync.engine.SyncEngine` 上：它们要用
``self._clock`` / ``self._source`` / ``self._write_target()``，单独一个 mixin 不完整。

**这里不列写类型的成员**（t32）：打哪个端点读 ``kind.wire``，本地要不要补 ``status`` 读
``kind.marks_completed``——两者都写在 :mod:`dida.sync.writes` 那张表里。**打哪个端点这件事
也是一张表**（#84）：:data:`_TASK_WIRE` 把每一种线路调用形状接到一个小函数上，加一种**新形状**
的端点只在那里加一行（那是新的外部行为，不是要同步的词汇）；加一种复用既有形状的写，只改
:mod:`dida.vocabulary` 的 ``_WRITE_BEHAVIOUR`` 一行。

**推这一轮的那台泵不在这里**：重试循环只有一台，住在 :mod:`dida.sync.pump`；这一片交给它的
是清单那本账的邻居——:class:`TaskQueue`（「读队列、可寻址吗、记一次失败、出队、发出去」五件
事接在任务这一族的动词上）。#84 之前任务是各写一台泵，清单又各写一台，靠 ``super()`` 串起来。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence, runtime_checkable

from dida.api.errors import DidaError
from dida.sync.writes import (
    UnclaimedListError,
    UnclaimedTaskError,
    UnknownTaskError,
    WireCall,
    WriteKind,
    is_a_change,
    is_a_move,
    is_addressable,
    is_addressable_task,
    is_local_list_id,
    is_local_task_id,
    project_in,
)
from dida.vocabulary import COMPLETED_STATUS, PendingChange, UNCOMPLETED_STATUS


RETRY_SCHEDULE: tuple[timedelta, ...] = (
    timedelta(seconds=5),
    timedelta(seconds=30),
    timedelta(minutes=1),
    timedelta(minutes=5),
)
"""自动重试的等待表（工单 #71）：失败一次等一格，从 5 秒一路拉到 5 分钟。

四格等待对应五次尝试（写完之后立刻那一次 + 四次重试）。表就是策略本身——读这张表
不需要再读 :func:`backoff_delay` 的函数体。
"""

MAX_PUSH_ATTEMPTS = 5
"""自动重试的次数上限（工单 #71）：试满五次就不再自动重试了。

正好是 :data:`RETRY_SCHEDULE` 那四格等待用完的那一刻：``attempts`` 是**已经失败过的次数**，
够到它就说明五次尝试都失败过。放弃**不是丢弃**——改动留在队列里，状态栏照旧数它，
只有手动同步（``r``）还能再试一次（见 :func:`is_given_up`）。
"""


def backoff_delay(attempts: int) -> timedelta:
    """失败 ``attempts`` 次之后再推要等多久：照 :data:`RETRY_SCHEDULE` 取一格，封顶 5 分钟。

    ``attempts`` 是**已经失败过的次数**（存储层在 ``record_attempt`` 里 +1），所以第一次
    失败等 5 秒。纯函数：没有时钟、没有随机抖动——抖动会让测试变成掷骰子，而这个 app 的
    重试节奏本来就由用户的下一次按键与状态栏那个数兜着。

    查表而不是算 ``base * 2**attempts``：``attempts`` 只被「失败过几次」推着涨，放着断网的
    那天也能攒到四十几，而 ``timedelta`` 装不下 ``2**46`` 秒（``2 × 2**46`` 秒 = 1628906115
    天，上限是 999999999 天）。写成整段乘法的话，第一条装不下的 ``attempts`` 会把**算退避**
    这一步变成 ``OverflowError``：网络一通、推送成功、这条改动出队，就绕开了；可只要网络还没
    通，**每次开 app 都会崩在这一跳**——连本地缓存那一屏都看不到（用户故事 62 的「没网也能
    分诊」当场失效）。查表对任何 ``attempts`` 都给出一个不超过最后一格的间隔。
    """
    if attempts < 0:
        return RETRY_SCHEDULE[0]
    if attempts >= len(RETRY_SCHEDULE):
        return RETRY_SCHEDULE[-1]
    return RETRY_SCHEDULE[attempts]


def _is_due(change: PendingChange, now: datetime) -> bool:
    """这条改动现在能不能推：没排过重试的立刻推，排过的要等到点。

    「到点」是 ``<=``：注入的钟刚好走到 ``next_retry_at`` 时就算到期。
    """
    return change.next_retry_at is None or change.next_retry_at <= now


def is_given_up(attempts: int) -> bool:
    """自动重试是不是已经放弃这条改动（工单 #71）：失败次数够到 :data:`MAX_PUSH_ATTEMPTS`。

    「还要不要自动重试」只有这一处判据——一台重试泵（:mod:`dida.sync.pump`）对任务与清单
    两本账问的都是它，不是各写一遍。放弃**不删任何东西**：改动留在队列里、状态栏照旧数它，
    只有手动同步（``r``）能再问它一次。
    """
    return attempts >= MAX_PUSH_ATTEMPTS


def can_attempt(change: PendingChange, now: datetime, *, manual: bool) -> bool:
    """这一轮推送该不该试这条改动（工单 #71）：还没放弃、而且到点了。

    这是两个推送循环共同问的那句话。``manual`` = 用户按了 ``r``（手动同步）：豁免**放弃**
    那道闸，已经放弃的改动再试一次；写完之后立刻那次推送与周期泵都不豁免，所以重启不会
    悄悄把重试循环接回去。手动对已放弃的改动连 ``next_retry_at`` 那道时间闸也一并豁免
    ——它已经不上日程了，手动是唯一还能再问一次的路，问的时候不该再等一个已经没有意义的
    时刻（非放弃的改动照旧要到点，手动不改变它们原来的节奏）。
    """
    if is_given_up(change.attempts):
        return manual
    return _is_due(change, now)


def _unaddressable(
    task_id: str, *, project: str | None, target_project: str | None
) -> DidaError:
    """这次写发不出去，**是哪一半**没过：返回说清原因的那个错误（#53）。

    判据只有 :func:`~dida.sync.writes.is_addressable_task` 一处（它在两个时刻各被问一次：
    入队之前、以及推送循环里）；这里只负责在**已经判定发不出去**之后，把它归到用户看得懂的
    那一种原因上：点名的清单服务端没见过（:class:`UnclaimedListError`，包括搬运的目标清单），
    还是这条任务自己还没被认领（:class:`UnclaimedTaskError`）。

    两个窄化判断（「是哪一族」）与判据读的是同一张前缀登记表，所以「判据说不行、这里一个都
    对不上」这一支走不到——``tests/test_local_ids.py`` 把那条不变量钉住了（判据说 False 的
    每一组输入，三个 id 里至少有一个是本地占位的）。
    """
    for named in (project, target_project):
        if named is not None and is_local_list_id(named):
            return UnclaimedListError(named)
    if is_local_task_id(task_id):
        return UnclaimedTaskError(task_id)
    # 到不了这里（不变量由上面那条测试守着）。真到了也仍然按「任务这一半」报：那是这句诊断里
    # 最保守的一种说法，绝不假装这一次发得出去。
    return UnclaimedTaskError(task_id)


@runtime_checkable
class TaskWriter(Protocol):
    """推送要的那几个写操作；t07 的 ``DidaApiClient`` 满足它。

    故意不认识领域概念：引擎给它清单 id、任务 id 与（更新时的）底稿，它只管按文档发。
    """

    async def create_task(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """``POST /open/v1/task``：新建任务，返回服务端建好的那一条（t15）。"""
        ...

    async def update_task(
        self,
        project_id: str,
        task_id: str,
        changes: Mapping[str, Any],
        *,
        snapshot: Mapping[str, Any] | None = None,
    ) -> Any:
        """``POST /open/v1/task/{taskId}``：更新任务（文档里没有 PATCH）。"""
        ...

    async def complete_task(self, project_id: str, task_id: str) -> None:
        """``POST /open/v1/project/{projectId}/task/{taskId}/complete``：无请求体。"""
        ...

    async def batch_update(self, updates: Sequence[Mapping[str, Any]]) -> Any:
        """``POST /open/v1/task/batch``：批量更新（取消完成的唯一路径，工单 #38）。

        每一条只带 id / projectId / status；逐条失败藏在 ``200 OK`` 的 ``id2error`` 里，
        由客户端读出来抛结构化错误。
        """
        ...

    async def delete_task(self, project_id: str, task_id: str) -> None:
        """``DELETE /open/v1/project/{projectId}/task/{taskId}``。"""
        ...

    async def move_task(
        self, from_project_id: str, to_project_id: str, task_id: str
    ) -> Sequence[Mapping[str, Any]]:
        """``POST /open/v1/task/move``：把一条任务搬去另一个清单（工单 #45）。

        请求体是**数组**、响应是 ``{id, etag}`` 的**数组**——本仓库唯一一个这样的端点。
        """
        ...


class TaskQueue:
    """任务那本账接给一台泵的适配器（#84）。

    泵（:class:`~dida.sync.pump.PumpMixin`）不认任务词汇：它只问这五件事，这个类把它们接到
    任务这一族的动词与 :data:`_TASK_WIRE` 上。清单那一本是另一个适配器
    （:class:`~dida.sync.lists.ListQueue`）——两份词汇表照旧分开，共用的是**机器**。
    """

    def __init__(self, target: WriteTarget, writer: TaskWriter) -> None:
        self._target = target
        self._writer = writer

    def pending(self) -> Sequence[PendingChange]:
        """还没推成功的任务改动，按发生顺序（泵每一笔都重新取一次）。"""
        return self._target.pending()

    def addressable(self, change: PendingChange) -> bool:
        """这一笔要说的每个 id 服务端都见过吗——判据本体在
        :func:`~dida.sync.writes.is_addressable_task`（一处）。"""
        return is_addressable(change)

    def record_attempt(
        self,
        change_id: int,
        *,
        error: str | None = None,
        next_retry_at: datetime | None = None,
    ) -> None:
        self._target.record_attempt(change_id, error=error, next_retry_at=next_retry_at)

    def resolve(self, change_id: int) -> None:
        self._target.resolve(change_id)

    async def send(self, change: PendingChange) -> bool:
        """按线路调用形状查表把这一笔发出去。失败照旧往外抛（由泵退避）。"""
        handler = _TASK_WIRE.get(change.kind.wire)
        if handler is None:
            raise NotImplementedError(f"推送还没有实现「{change.kind.value}」这一种改动")
        await handler(self, change)
        return True

    async def create_task(self, change: PendingChange) -> None:
        """新建：才知道服务端给的 id，所以推成功之后顺手认领它（t15）。"""
        _adopt_created(self._target, change, await self._writer.create_task(change.payload))

    async def update_task(self, change: PendingChange) -> None:
        """改字段：请求体是这份改动，外加本地那份完整底稿（不认识的字段靠它回写）。"""
        await self._writer.update_task(
            change.list_id,
            change.task_id,
            change.payload,
            snapshot=self._target.task_payload(change.task_id),
        )

    async def complete_task(self, change: PendingChange) -> None:
        """完成：没有请求体的端点。"""
        await self._writer.complete_task(change.list_id, change.task_id)

    async def batch_update_task(self, change: PendingChange) -> None:
        """批量更新：请求体是 ``{update: [...]}``，每一条只带 id / projectId / status。

        取消完成是唯一走这条路的一种写（``_WRITE_BEHAVIOUR`` 说的一种写一种端点形状）；那条
        改动在本地要写下的 ``status`` 就是这条请求要发的值——两者同一个来源，不会出现
        「本地改成未完成、服务端收到的是别的」。
        """
        await self._writer.batch_update(
            [{"id": change.task_id, "projectId": change.list_id, **change.payload}]
        )

    async def delete_task(self, change: PendingChange) -> None:
        """删除：``DELETE``，没有请求体。"""
        await self._writer.delete_task(change.list_id, change.task_id)

    async def move_task(self, change: PendingChange) -> None:
        """搬运：``fromProjectId`` 是**入队那一刻**那条任务所在的清单（改动行上记着），
        ``toProjectId`` 是这次改动盖上去的 ``projectId``。请求体那一层再翻成文档的数组形状
        ——这里给的是「从哪到哪」，端点形状是客户端的事。"""
        await self._writer.move_task(
            change.list_id,
            str(change.payload.get("projectId") or ""),
            change.task_id,
        )


def _adopt_created(target: WriteTarget, change: PendingChange, created: Any) -> None:
    """新建推成功：把本地那条临时 id 的任务挪到服务端给的 id 上（t15）。

    不挪的后果不是「多一条看不见的行」：真 id 那条会被下一次全量刷新拉回来，临时 id
    这条要等那一次刷新的剪枝才消失（#41）——中间这段时间同一条任务在屏幕上出现两遍。

    是**合并**而不是替换：服务端给的字段盖上去，它没提的字段（用户刚写下的日期、
    ``projectId``）留在本地。响应按文档就是那条建好的任务，但不拿这个赌——真正的
    服务端权威裁决在全量刷新那条路上，那里每一笔覆盖都会如实记进报告（t08/t09），
    而不是在这里悄悄少掉用户写的一个日期。

    服务端没回一个带 id 的原文时**什么都不做**：这条改动已经推成功了，不能当失败重试
    （新建不是幂等的，重试就是建两条）。宁可留着一条临时 id 的本地任务，也不建两条。
    """
    if not isinstance(created, Mapping) or not created.get("id"):
        return
    local = target.task_payload(change.task_id) or {}
    target.adopt_created(
        change.task_id,
        {
            **local,
            **created,
            "projectId": created.get("projectId") or change.list_id,
        },
    )


_TASK_WIRE: dict[WireCall, Callable[[TaskQueue, PendingChange], Awaitable[None]]] = {
    WireCall.CREATE_TASK: TaskQueue.create_task,
    WireCall.UPDATE_TASK: TaskQueue.update_task,
    WireCall.COMPLETE_TASK: TaskQueue.complete_task,
    WireCall.BATCH_UPDATE_TASK: TaskQueue.batch_update_task,
    WireCall.DELETE_TASK: TaskQueue.delete_task,
    WireCall.MOVE_TASK: TaskQueue.move_task,
}
"""**一处**记全每种线路调用形状怎么打（#84）：加一种端点形状，在这里加一行。

键是 ``change.kind.wire``（「写入种类 → 线路调用形状」那张表已经在 :mod:`dida.vocabulary`
里是数据了），值是这一形状怎么把改动交给客户端。分派因此是查表，不是一串按成员名排的
``elif``；复用既有形状的写连这里都不用碰，只在 ``_WRITE_BEHAVIOUR`` 加一行。

表在 :class:`TaskQueue` 定义之后填：值要的是那几个函数对象，而它们住在那个类里（方法本体
要 ``self`` 上的本地副本与客户端）。
"""


class PushMixin:

    """乐观写与重试队列：**本地先动，服务端随后到**（ADR-0002）。

    三条写路径都从 :meth:`write` 走；:meth:`complete` / :meth:`delete` 只是它的
    两个预置（各自的 ``kind``）。本地没有这条任务的底稿时当场抛
    :class:`~dida.sync.writes.UnknownTaskError`，绝不入队一条永远推不出去的改动
    （工单 #25）。"""



    def write(
        self,
        task_id: str,
        *,
        changes: Mapping[str, Any] | None = None,
        kind: WriteKind = WriteKind.UPDATE,
    ) -> bool:
        """乐观写：本地当场生效并入队，然后**立即返回**；网络结果不是它的前置条件。

        **这一次到底改了没有由它自己回答**（工单 #79）：拿这次要盖上去的字段与本地那一份
        原文逐位比（判据本体 :func:`~dida.sync.writes.is_a_change`，一处），有一位不同就写、
        并回 ``True``；一位都相同就**什么都不做**——不入队、不排推送、不动本地那一份，
        回 ``False``。调用方（界面或别的谁都一样）按这个值决定还要不要往下走，不必把同一个
        判断再问一遍（ADR-0008 第二节记的那一次空写就是漏了这一句）。

        哪些写有「什么都没改」这一档由词表说：:attr:`~dida.vocabulary.WriteKind.converges`。
        改字段有；完成 / 取消完成 / 删除**没有**（它们是不可逆的对外动作，再来一次就是再来
        一次），所以那三条即使回 ``True`` 也不代表服务端上真的多了一个字段。

        ADR-0002 的口径：一次按键的手感比可撤销性值钱，所以本地先动，服务端随后到。
        改动进 :class:`~dida.vocabulary.PendingChange` 队列后，会立刻在事件循环上
        推一次（``push_on_change=False`` 时不排这一轮，改动留在队列里等下一次
        :meth:`push_pending`：手动同步 ``r``，或 t21 那个周期泵）；推不动就留在队列里按
        注入的钟退避重试，绝不让这一屏等网络。

        ``changes`` 是这次要盖上去的字段（本地与请求体同一份，未知字段的底稿由存储层
        拼好交给 :meth:`~dida.api.client.DidaApiClient.update_task` 的 ``snapshot=``）。
        写失败（服务端说不行、网络断了）在 UI 上表现为状态栏那个待推送数量，而不是异常。

        t11 的完成、t13 的顺延都从这一个入口走；它俩只需要给 ``task_id`` 与 ``changes``。

        本地缓存里没有这条任务（或者那份底稿没有 ``projectId``）时不入队，当场抛
        :class:`UnknownTaskError`：请求的清单 id 只存在于底稿里，凭空入队只会留下一条
        **永远推不出去**的改动，让状态栏那个数一直非零（工单 #25）。

        这次请求要说的每个 id 都得是服务端见过的（判据在
        :func:`~dida.sync.writes.is_addressable_task`，一处），否则当场抛**说清是哪一半**的那个
        错误：任务自己还没被认领是 :class:`UnclaimedTaskError`，落点在一条还没推出去的清单里是
        :class:`UnclaimedListError`（#53）。两种都**不入队**——排进去就是一条永远推不出去的
        改动，而它们都会自愈（下一次全量刷新把真 id 带回来），所以诚实的回答是「等同步完」。
        """
        target = self._write_target()
        # 认领换过名的话，这一行现在叫另一个 id（工单 #75 / ADR-0009），先解析成真那个。
        # **必须在这里定下来**——下面每一步都按 id 走：``is_addressable_task`` 会把临时 id 判成
        # 「服务端没见过」而拒绝这一笔（而它其实已经被认领了），进队那一行要是带着临时 id 更糟
        # ——那是一条永远推不出去的改动（#53 挡的正是这一类安静错误）。
        task_id = target.resolve_id(task_id)
        snapshot = target.task_payload(task_id)
        if snapshot is None or not snapshot.get("projectId"):
            raise UnknownTaskError(task_id)
        project = str(snapshot["projectId"])
        target_project = project_in(changes)
        if not is_addressable_task(
            task_id, kind, project_id=project, target_project_id=target_project
        ):
            raise _unaddressable(task_id, project=project, target_project=target_project)
        local = self._local_effect(kind, changes)
        if kind.converges and not is_a_change(snapshot, local):
            # 什么都没改：不入队、不排推送、不动本地那一份，如实回一句「没写」（工单 #79）。
            return False
        target.enqueue(
            task_id=task_id,
            kind=kind,
            payload=local,
            now=self._clock.now(),
        )
        self._push_now()
        return True

    def complete(self, task_id: str) -> None:
        """写：完成任务并立即推送（ADR 0002 的乐观写：本地先动，服务端随后到）。

        就是 :meth:`write` 的一个预置：本地当场标记完成（``status`` 由引擎补 ``2``），
        推送走没有请求体的 ``complete`` 端点。

        **反方向是 :meth:`uncomplete`，不是「没有这条路」**：ADR-0002 记的「完成不可逆」
        已被实测推翻（spec 的实测事实第 1 条），服务端那条路是 ``task/batch`` 的
        ``update`` 带 ``status: 0``。完成这条路本身仍然是不可逆的（它没有撤销参数），
        所以「按键的手感比可撤销性值钱」这条口径没变——变的是它**真的**可逆了。
        """
        self.write(task_id, kind=WriteKind.COMPLETE)

    def uncomplete(self, task_id: str) -> None:
        """写：取消完成并立即推送（工单 #38，``space`` 的第二个方向）。

        与 :meth:`complete` 同一条口径的预置：本地当场把 ``status`` 写回 ``0``（完成时间戳
        不动——实测取消完成不会清掉它），推送走 ``task/batch`` 的 ``update`` 数组。

        **为什么不是普通更新端点**：``status`` 在 ``POST /open/v1/task/{taskId}`` 上会被
        服务端静默忽略（所以本地守卫一直拒绝它），实测能生效的只有批量更新这一条路
        （spec 的实测事实第 1 条）。所以这一种写打的是一个**新形状**的端点，而它每一条
        只发 id / projectId / status——批量更新是合并语义，其余字段服务端自己保留。
        """
        self.write(task_id, kind=WriteKind.UNCOMPLETE)

    def delete(self, task_id: str) -> None:
        """写：删除一条任务（``d``），本地当场摘掉快照、推送走 ``DELETE``（t16）。

        就是 :meth:`write` 的一个预置，与 :meth:`complete` 同一条口径：乐观写 + 立即推送 +
        进重试队列。**没有「撤销删除」这条路径**——滴答清单的 Open API 里没有 undelete、
        没有回收站、也没有「已删除」列表（``api-contracts.md`` 通篇没有这一类端点），本地
        自己造一个只会在下一次刷新时被服务端权威抹掉（ADR-0002 的那条规矩，删除方向一样成立）。

        所以这次确认是唯一的防线，而它归 TUI：引擎这一层只保证「调用它就删」，不负责问。
        """
        self.write(task_id, kind=WriteKind.DELETE)

    def move_task(self, task_id: str, *, to_list_id: str) -> bool:
        """写：把这条任务搬到另一个清单（``POST /open/v1/task/move``，工单 #45）。

        **回报这次到底搬了没有**（工单 #79）：搬到它**已经在**的那个清单（或者目标为空）时
        什么都不写，回 ``False``——不入队、不排推送、不动本地那一份；真搬了回 ``True``。
        调用方按这个值决定还要不要推一轮，不必自己比一遍（#58 落地时界面确实问过同一个判据，
        #79 之后它不再问）。

        **不是一次普通字段更新**：搬运有自己的端点、自己的数组请求体，所以它是
        :class:`~dida.sync.writes.WriteKind` 里的一个成员，而不是 ``write(changes={"projectId": …})``
        的另一种叫法（spec :230 明确要求走搬运端点）。

        本地那一份**当场就换清单**（``projectId`` 盖进快照，读路径立刻把它画在新清单里），
        推送排在事件循环上立刻跑，推不动就留在队列里退避重试——与其余几条写同一条口径。

        ``to_list_id`` 与当前清单相同时**什么都不写**：那不是一次改动，凭空入队只会让状态栏
        多出一个永远没有意义的数。这条判断的**本体在** :func:`~dida.sync.writes.is_a_move`
        （「目标为空」与「就是它现在待的那个清单」两种都算没改），而它比的又是那**一个**判据
        本体 :func:`~dida.sync.writes.is_a_change`（#79：这一层是唯一的比较处）。
        本地没有这条任务的底稿时与 :meth:`write` 一样当场抛
        :class:`~dida.sync.writes.UnknownTaskError`——``fromProjectId`` 只存在于那份底稿里，
        拼不出请求的改动永远推不出去（工单 #25）。

        **目标清单必须服务端已经见过**（本地刚建、还没推上去的清单 id 是本地临时的）：
        搬过去会 404，而那条改动会永远留在队列里。挑选器因此只给
        :meth:`~dida.sync.engine.SyncEngine.move_targets` 那一份（#45）。
        """
        payload = self._write_target().task_payload(task_id)
        current = "" if payload is None else str(payload.get("projectId") or "")
        if not current:
            raise UnknownTaskError(task_id)
        if not is_a_move(current, to_list_id):
            return False
        # ``task_id`` 可能是认领换名之前的那个（光标停在一行刚建出来的任务上，工单 #75）：
        # 交给 ``write`` 去解析（它那一处是唯一的判据），这里只把「搬不搬」问清楚。
        return self.write(task_id, changes={"projectId": to_list_id}, kind=WriteKind.MOVE)

    async def wait_for_pushes(self) -> None:
        """等 :meth:`write` 排下的那几轮推送跑完。

        **确定性接缝**：``write()`` 不等网络，所以想知道「推完了没有」的一律是测试
        （生产路径上没有人 await 它）。它只等已经在飞的那几轮，自己不发起推送——
        到点该重试的由 :meth:`push_pending` 或下一次写来触发。
        """
        while self._inflight:
            await asyncio.gather(*tuple(self._inflight))

    def _push_now(self) -> None:
        """写完之后要不要**立刻**排一轮推送（配置键 ``push_on_change`` 的落点）。

        关掉它（``push_on_change=False``）时改动只入队：本地照旧当场生效，出队交给下一次
        :meth:`push_pending`——手动同步（``r``）或 t21 那个周期泵。默认是开的：
        ADR-0002 的「写操作立即推送」。
        """
        if self._push_on_change:
            self._schedule_push()

    def _schedule_push(self) -> None:
        """把「立刻推一轮」排到事件循环上（ADR-0002：写操作立即推送）。

        这就是「不等网络」的实现：写的人当场返回，推送在事件循环**同一根线程**上跑
        ——t08 的 sqlite 连接有线程亲和，写必须发生在创建连接的那条线程；协程正好满足，
        而新起一个 ``threading.Thread`` 会把它打破。

        没有事件循环（同步调用，比如纯本地测试）时不排：改动留在队列里，
        等下一次 :meth:`push_pending`（或下一次写）再推。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self.push_pending())
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    def _task_queue(self) -> TaskQueue:
        """任务那本账（#84）：交给那一台泵的就是它。

        泵不认任务词汇——它只认 :class:`~dida.sync.pump.PushQueue` 那五件事，这个适配器把
        它们接到任务这一族的动词上（``Store.pending`` / ``record_attempt`` / ``resolve``）与
        分派表 :data:`_TASK_WIRE` 上。清单那一本由 :class:`~dida.sync.lists.ListQueue` 给。
        """
        return TaskQueue(self._write_target(), self._writer())


    def _writer(self) -> TaskWriter:
        """推送要的那个客户端。没接上就大声报错——绝不假装推过了。"""
        if not isinstance(self._client, TaskWriter):
            raise RuntimeError("推送需要 API 客户端：SyncEngine(client=DidaApiClient(...))")
        return self._client

    def _local_effect(self, kind: WriteKind, changes: Mapping[str, Any] | None) -> dict[str, Any]:
        """这次改动在本地要盖上去的那一份字段。

        完成是唯一的例外：本地要让这条任务立刻从今日视图里消失，就得写 ``status``，
        而这个值只有 API 知道（Completed 是 ``2``，api-contracts.md 第 2 条）——由引擎补，
        不让 t11 自己记一个魔法数。它不会进请求体：完成走的是没有请求体的 ``complete``
        端点，而且 ``status`` 本来也不是新建/更新接受的字段（同文件第 5 条）。

        取消完成是同一个例外**反过来的那一半**（工单 #38）：本地要把 ``status`` 写回
        ``0``（完成时间戳不动），而那个值同样只有 API 知道。它进请求体——那一档正是
        ``task/batch`` 的 ``update`` 要发的东西（实测确认，spec 的实测事实第 1 条）。
        """
        merged = dict(changes or {})
        # 「本地立刻完成 / 立刻不再完成」这两件事都由词表说（dida.vocabulary 的 _WRITE_BEHAVIOUR）
        if kind.marks_completed:
            merged.setdefault("status", COMPLETED_STATUS)
        elif kind.clears_completed:
            merged.setdefault("status", UNCOMPLETED_STATUS)
        return merged

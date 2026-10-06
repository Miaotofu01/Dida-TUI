"""写路径：乐观入队、立即推送、退避重试（t10；完成 t11、删除 t16）。

这一片只管一件事：**一次本地写怎么变成一次服务端写**——乐观地落进本地库并入队，推送排在
事件循环上立刻跑，推不动就留在队列里按注入的钟退避重试。什么时候该重试由 :func:`_is_due`
算，等多久由 :func:`backoff_delay` 算，两者都是纯函数（不掷骰子、不睡眠）。

:class:`PushMixin` 的方法挂在组装好的 :class:`~dida.sync.engine.SyncEngine` 上：它们要用
``self._clock`` / ``self._source`` / ``self._write_target()``，单独一个 mixin 不完整。

**这里不列写类型的成员**（t32）：打哪个端点读 ``kind.wire``，本地要不要补 ``status`` 读
``kind.marks_completed``——两者都写在 :mod:`dida.sync.writes` 那张表里。只有「这条写要打一个
新形状的端点」才在这里加一个分支（那是新的外部行为，不是要同步的词汇）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence, runtime_checkable

from dida.api.errors import DidaError
from dida.sync.writes import UnknownTaskError, WireCall, WriteKind

if TYPE_CHECKING:  # storage 反过来 import dida.sync.view，运行时不能在这里 import
    from dida.storage.store import PendingChange


DEFAULT_BACKOFF_BASE = timedelta(seconds=2)
"""第一次推送失败之后等多久再试（之后每次翻倍）。"""


DEFAULT_BACKOFF_CAP = timedelta(minutes=5)
"""两次重试之间最多等多久。断网一整天也不该攒出一个巨大的间隔。"""


def backoff_delay(
    attempts: int,
    *,
    base: timedelta = DEFAULT_BACKOFF_BASE,
    cap: timedelta = DEFAULT_BACKOFF_CAP,
) -> timedelta:
    """失败 ``attempts`` 次之后再推要等多久：``base``、``2×base``、``4×base``…封顶 ``cap``。

    ``attempts`` 是**已经失败过的次数**（存储层在 ``record_attempt`` 里 +1），所以第一次
    失败等 ``base``。纯函数，参数从外面进来：没有时钟、没有随机抖动——抖动会让测试变成
    掷骰子，而这个 app 的重试节奏本来就由用户的下一次按键与状态栏那个数兜着。
    """
    return min(base * 2**attempts, cap)


def _is_due(change: PendingChange, now: datetime) -> bool:
    """这条改动现在能不能推：没排过重试的立刻推，排过的要等到点。

    「到点」是 ``<=``：注入的钟刚好走到 ``next_retry_at`` 时就算到期。
    """
    return change.next_retry_at is None or change.next_retry_at <= now


def _completed_status() -> int:
    """任务「已完成」的 ``status`` 值（``2``，api-contracts.md 第 2 条）。

    从存储层取而不是在这里再写一个字面量：同一份 API 事实只留一处。
    """
    from dida.storage.store import COMPLETED_STATUS

    return COMPLETED_STATUS


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
    ) -> None:
        """乐观写：本地当场生效并入队，然后**立即返回**；网络结果不是它的前置条件。

        ADR-0002 的口径：一次按键的手感比可撤销性值钱，所以本地先动，服务端随后到。
        改动进 :class:`~dida.storage.store.PendingChange` 队列后，会立刻在事件循环上
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
        """
        target = self._write_target()
        snapshot = target.task_payload(task_id)
        if snapshot is None or not snapshot.get("projectId"):
            raise UnknownTaskError(task_id)
        target.enqueue(
            task_id=task_id,
            kind=kind,
            payload=self._local_effect(kind, changes),
            now=self._clock.now(),
        )
        self._push_now()

    def complete(self, task_id: str) -> None:
        """写：完成任务并立即推送（ADR 0002，服务端不可逆）。

        就是 :meth:`write` 的一个预置：本地当场标记完成（``status`` 由引擎补 ``2``），
        推送走没有请求体的 ``complete`` 端点。没有「取消完成」这条路径——服务端没有
        这个接口，本地自己造一个只会在下一次刷新时被服务端权威抹掉（ADR-0002）。
        """
        self.write(task_id, kind=WriteKind.COMPLETE)

    def delete(self, task_id: str) -> None:
        """写：删除一条任务（``d``），本地当场摘掉快照、推送走 ``DELETE``（t16）。

        就是 :meth:`write` 的一个预置，与 :meth:`complete` 同一条口径：乐观写 + 立即推送 +
        进重试队列。**没有「撤销删除」这条路径**——滴答清单的 Open API 里没有 undelete、
        没有回收站、也没有「已删除」列表（``api-contracts.md`` 通篇没有这一类端点），本地
        自己造一个只会在下一次刷新时被服务端权威抹掉（ADR-0002 的那条规矩，删除方向一样成立）。

        所以这次确认是唯一的防线，而它归 TUI：引擎这一层只保证「调用它就删」，不负责问。
        """
        self.write(task_id, kind=WriteKind.DELETE)

    def move_task(self, task_id: str, *, to_list_id: str) -> None:
        """写：把这条任务搬到另一个清单（``POST /open/v1/task/move``，工单 #45）。

        **不是一次普通字段更新**：搬运有自己的端点、自己的数组请求体，所以它是
        :class:`~dida.sync.writes.WriteKind` 里的一个成员，而不是 ``write(changes={"projectId": …})``
        的另一种叫法（spec :230 明确要求走搬运端点）。

        本地那一份**当场就换清单**（``projectId`` 盖进快照，读路径立刻把它画在新清单里），
        推送排在事件循环上立刻跑，推不动就留在队列里退避重试——与其余几条写同一条口径。

        ``to_list_id`` 与当前清单相同时**什么都不写**：那不是一次改动，凭空入队只会让状态栏
        多出一个永远没有意义的数。本地没有这条任务的底稿时与 :meth:`write` 一样当场抛
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
        if current == to_list_id or not to_list_id:
            return
        self.write(task_id, changes={"projectId": to_list_id}, kind=WriteKind.MOVE)

    async def push_pending(self) -> int:
        """推一轮：把**到期**的待推送改动依次推给服务端，返回推成功的条数。

        这是重试队列唯一的泵。「什么时候该重试」由注入的钟判定：没排过重试的立刻推，
        排过的要等到 ``next_retry_at``。**没有 ``time.sleep``、没有真时钟、没有后台线程**
        ——等待发生在调用方（t14 那种定时器或下一次写），引擎只负责算清楚什么时候能推。

        一条失败不影响后面那些：队列按发生顺序走完，失败的留在队列里等下一次。
        """
        target = self._write_target()
        writer = self._writer()
        pushed = 0
        async with self._push_lock:
            for change in target.pending():
                now = self._clock.now()
                if not _is_due(change, now):
                    continue
                try:
                    await self._send(writer, target, change)
                except DidaError as exc:
                    # 推不动就留在队列里，记下这次失败与下一次的时刻。本地那份改动照旧
                    # 生效——ADR-0002 的豁免看的就是这个队列，用户的操作不会因为一次
                    # 网络抖动被撤销。
                    target.record_attempt(
                        change.id,
                        error=str(exc),
                        next_retry_at=now + backoff_delay(change.attempts),
                    )
                    continue
                target.resolve(change.id)
                pushed += 1
        return pushed

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

    async def _send(self, writer: TaskWriter, target: WriteTarget, change: PendingChange) -> None:
        """把一条待推送改动交给客户端。失败是结构化错误，照旧往外抛（由调用方退避）。

        ``kind`` 是引擎与存储共用的那一套词汇（:mod:`dida.sync.writes`），所以这里不需要
        再 import 存储层：判断用哪一个端点，与「改动存在哪里」无关。
        """
        wire = change.kind.wire  # 打哪一个端点由词表说（dida.sync.writes），不在这里再列一遍成员
        if wire is WireCall.UPDATE_TASK:
            await writer.update_task(
                change.list_id,
                change.task_id,
                change.payload,
                # 底稿是本地那份完整原文：不认识的字段靠它才能一个不丢地回写。
                snapshot=target.task_payload(change.task_id),
            )
        elif wire is WireCall.COMPLETE_TASK:
            await writer.complete_task(change.list_id, change.task_id)
        elif wire is WireCall.DELETE_TASK:
            await writer.delete_task(change.list_id, change.task_id)
        elif wire is WireCall.MOVE_TASK:
            # 搬运：``fromProjectId`` 是**入队那一刻**那条任务所在的清单（改动行上记着），
            # ``toProjectId`` 是这次改动盖上去的 ``projectId``。请求体那一层再翻成文档的
            # 数组形状——这里给的是「从哪到哪」，端点形状是客户端的事。
            await writer.move_task(
                change.list_id,
                str(change.payload.get("projectId") or ""),
                change.task_id,
            )
        elif wire is WireCall.CREATE_TASK:
            # 新建才知道服务端给的 id，所以这一条推成功之后要顺手认领它（t15）。
            self._adopt_created(target, change, await writer.create_task(change.payload))
        else:
            raise NotImplementedError(f"推送还没有实现「{change.kind.value}」这一种改动")

    def _adopt_created(
        self, target: WriteTarget, change: PendingChange, created: Any
    ) -> None:
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
        """
        merged = dict(changes or {})
        if kind.marks_completed:  # 「本地立刻完成」这件事由词表说（dida.sync.writes）
            merged.setdefault("status", _completed_status())
        return merged

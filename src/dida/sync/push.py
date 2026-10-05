"""写路径：乐观入队、立即推送、退避重试（t10；完成 t11、删除 t16）。

这一片只管一件事：**一次本地写怎么变成一次服务端写**——乐观地落进本地库并入队，推送排在
事件循环上立刻跑，推不动就留在队列里按注入的钟退避重试。什么时候该重试由 :func:`_is_due`
算，等多久由 :func:`backoff_delay` 算，两者都是纯函数（不掷骰子、不睡眠）。

:class:`PushMixin` 的方法挂在组装好的 :class:`~dida.sync.engine.SyncEngine` 上：它们要用
``self._clock`` / ``self._source`` / ``self._write_target()``，单独一个 mixin 不完整。

「改一种写的推送方式」与「加一种写」都落在 :meth:`PushMixin._send` 那一个分派里；写词汇本身
在 :mod:`dida.sync.writes`（t32 之后只有一处定义）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence, runtime_checkable

from dida.api.errors import DidaError
from dida.sync.writes import UnknownTaskError, WriteKind

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
        if change.kind is WriteKind.UPDATE:
            await writer.update_task(
                change.list_id,
                change.task_id,
                change.payload,
                # 底稿是本地那份完整原文：不认识的字段靠它才能一个不丢地回写。
                snapshot=target.task_payload(change.task_id),
            )
        elif change.kind is WriteKind.COMPLETE:
            await writer.complete_task(change.list_id, change.task_id)
        elif change.kind is WriteKind.DELETE:
            await writer.delete_task(change.list_id, change.task_id)
        elif change.kind is WriteKind.CREATE:
            # 新建才知道服务端给的 id，所以这一条推成功之后要顺手认领它（t15）。
            self._adopt_created(target, change, await writer.create_task(change.payload))
        else:
            raise NotImplementedError(f"推送还没有实现「{change.kind.value}」这一种改动")

    def _adopt_created(
        self, target: WriteTarget, change: PendingChange, created: Any
    ) -> None:
        """新建推成功：把本地那条临时 id 的任务挪到服务端给的 id 上（t15）。

        不挪的后果不是「多一条看不见的行」：下一次全量刷新会把服务端那条（真 id）拉回来，
        而临时 id 这条不会被清掉（``apply_refresh`` 不剪枝）——同一条任务在屏幕上出现两遍，
        而且永远合不上。

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
        if kind is WriteKind.COMPLETE:
            merged.setdefault("status", _completed_status())
        return merged

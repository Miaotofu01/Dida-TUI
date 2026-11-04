"""一台写入机器（#84）：重试泵与「抽哪个队列」的接口只有一份。

推送原来被完整地实现了两遍——任务那一条在 :mod:`dida.sync.push`、清单那一条在
:mod:`dida.sync.lists`：同一套循环（重读队列 → 到点没有 → 记一次失败 → 退避 → 出队）逐字
抄了两遍，各自挑自己那本账。**重复的是机器，不是词汇**——任务 id 与清单 id 是两件事，所以
两份词汇表（``_WRITE_BEHAVIOUR`` / ``_LIST_BEHAVIOUR``）与两张队列表照旧分开，这个决定不动。

这里只留机器本身：:meth:`PumpMixin.push_pending` 按 :class:`PushQueue` 抽两遍（清单那本、
任务那本）。每一本账由各自那一片给一个小适配器（``push.TaskQueue`` / ``lists.ListQueue``），
把「读队列、可寻址吗、记一次失败、出队、把这一笔发出去」五件事接到自己那一族的动词上。
「这一轮该不该试」（退避与放弃）是两边共用的策略，仍然只有一份
（:func:`~dida.sync.push.can_attempt` / :func:`~dida.sync.push.backoff_delay`），住在
:mod:`dida.sync.push`；这里只是问它。

``push_pending`` 只有一个实现之后，「谁先生效」不再由 :class:`~dida.sync.engine.SyncEngine`
那串基类的排列顺序决定（#84 之前 ``ListMixin`` 必须排在前面，清单那一份才会生效）。同一件事
的另一半在 :meth:`dida.sync.refresh.RefreshMixin.refresh`：认领新建的清单那一步并进了唯一
那一份刷新，清单那一片不再各写一份、不再靠 ``super()`` 串起来。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, Sequence

from dida.api.errors import DidaError
from dida.sync.push import backoff_delay, can_attempt


class PushQueue(Protocol):
    """一台泵要抽的那本账（#84）：五件事，各自那一片实现。

    泵不认任务与清单——它只认这五件事，所以「同一套循环写两遍」变成「同一套循环抽两本账」。
    本协议**不合并两份词汇**：``pending()`` 给出来的是各自那一族的改动类型，``send()`` 里怎么
    翻成请求体也是各自那一片的事。
    """

    def pending(self) -> Sequence[Any]:
        """还没推成功的改动，按发生顺序。

        **每一笔都重新取一次**（不是开头读一份快照再遍历）：新建推成功会把排在它后面的改动
        挪到服务端给的 id 上，同一轮里紧接着的那一笔必须看见新的 id。
        """
        ...

    def addressable(self, change: Any) -> bool:
        """这一笔现在发得出去吗。

        判据本体各自一份（任务 / 清单在领域里就是两件事），但**问它的时刻只有两处**：写路径
        与推送循环。这一处就是推送循环——发不出去的留在队列里等认领，不是记一次失败。
        """
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
        """这一笔已经推到服务端了，出队。"""
        ...

    async def send(self, change: Any) -> bool:
        """把这一笔交给服务端；返回值 = 这一条**可以出队了**吗。

        新建成功了、但服务端没回 id 时回 ``False``：那一笔已经发出去了，可这一行还欠一个真
        id，记录得留着（#54 的认领记录）。失败照旧抛 :class:`~dida.api.errors.DidaError`，
        由泵退避。
        """
        ...


class PumpMixin:
    """重试泵只有一台（#84）：按「抽哪个队列」参数化，退避与放弃照旧共用。

    方法挂在组装好的 :class:`~dida.sync.engine.SyncEngine` 上：它要用 ``self._clock`` /
    ``self._push_lock``，以及两片各自的适配器（``self._task_queue()`` /
    ``self._list_queue()``）。
    """

    async def push_pending(self, *, manual: bool = False) -> int:
        """推一轮：把两台队列里**该试**的待推送改动都推一遍，返回推成功的总条数。

        这是重试队列唯一的泵，两台队列共用它。「什么时候该重试」由注入的钟判定：没排过重试
        的立刻推，排过的要等到 ``next_retry_at``；**没有 ``time.sleep``、没有真时钟、没有后台
        线程**——等待发生在调用方（周期泵、下一次写、手动同步），引擎只负责算清楚什么时候能推。

        ``manual=True`` 是用户按了 ``r``：已经放弃（失败够 :data:`~dida.sync.push.MAX_PUSH_ATTEMPTS`
        次）的改动再试一次，放弃之前的那几种改动照旧按到点判定。默认 ``False``——写完之后立刻
        那次推送与周期泵都是自动路径，放弃的改动它们一律不发。

        **清单那一本先抽**（原来就是这一次序）：新建清单那一笔推成功会把它的临时 id 换成服务端
        给的 id，落在那个清单里的任务改动因此能在**同一轮**里变得可寻址，不用多等一轮。这是
        次序，不是优先级：两边的失败都各自留在自己的队列里。

        一条失败不影响后面那些：队列按发生顺序走完，失败的留在队列里等下一次。
        """
        pending_lists = await self._drain(self._list_queue(), manual=manual)
        return pending_lists + await self._drain(self._task_queue(), manual=manual)

    async def _drain(self, queue: PushQueue, *, manual: bool) -> int:
        """抽一本账：把这一队列里该试的改动依次推出去，返回推成功的条数。

        循环一定会停：每一轮要么出队一行、要么把它的 ``next_retry_at`` 推到将来、要么把它记进
        ``attempted``、要么让它变成**不可寻址**（新建回了 201 空 body，那一笔成了认领记录）
        ——四种结果都让它不再是这一轮该试的第一笔。``manual`` 对已放弃的改动不看
        ``next_retry_at``（#71），少了 ``attempted`` 这本账，同一笔会在这一轮里被反复重试。
        """
        pushed = 0
        attempted: set[int] = set()
        async with self._push_lock:
            while True:
                now = self._clock.now()
                change = next(
                    (
                        item
                        for item in queue.pending()
                        if item.id not in attempted
                        # 发不出去的（认领记录、打在一个服务端没见过的 id 上的改 / 删）不挑：
                        # 挑了就只会得到 404，或者干脆什么都不该做——它们等认领，不在这里等重试。
                        and queue.addressable(item)
                        and can_attempt(item, now, manual=manual)
                    ),
                    None,
                )
                if change is None:
                    return pushed
                attempted.add(change.id)
                try:
                    resolved = await queue.send(change)
                except DidaError as exc:
                    # 推不动就留在队列里，记下这次失败与下一次的时刻。本地那份改动照旧生效
                    # ——ADR-0002 的豁免看的就是这个队列，用户的操作不会因为一次网络抖动被撤销。
                    queue.record_attempt(
                        change.id,
                        error=str(exc),
                        next_retry_at=now + backoff_delay(change.attempts),
                    )
                    continue
                if resolved:
                    queue.resolve(change.id)
                # 请求确实发出去了就算推过：新建回了 201 空 body 也算——服务端已经收下了，
                # 只是没告诉我们它给那一行起了什么 id。
                pushed += 1

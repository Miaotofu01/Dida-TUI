"""构造时定下「这份副本会不会做某件事」的那**一处**（工单 #85）。

在这之前，「会不会做这件事」被拆成 13 处运行时探测（``isinstance(…, 某个 Protocol)`` /
``getattr`` + ``callable``）散在业务路径上，而「做不到」有四种说法（抛错 / 当成空 / 装作没这回事 /
换一种错）。后果是：换第二个实现时，入场检查放行，然后在三层调用深处炸掉。

现在只有一处问：:meth:`Capabilities.of` 在**引擎构造那一处**把注入的本地副本与客户端问一遍，
答案是这张表。业务路径不再探测——它们读这里的那一格：

- **要用的那一件不在**：一种说法，:class:`MissingCapability`，消息里说清缺的是哪一件、
  该注入什么。八条写 / 网络路径从前各说各的（八句不同的 ``RuntimeError``），现在都从这里出去。
- **读路径上那件事本来就没有**（只读替身没有队列、没有原文）：那是**这份副本的性质**，
  答案照旧是文档写好的那个空值（``None`` / ``()`` / ``frozenset()``），不是错误——
  与从前同一条行为，只是「为什么是空的」现在来自构造时定下的那一格，而不是临场再探一次。

**为什么拒绝不发生在引擎构造的那一秒**：只读替身是一个**完整**的替身（工单 #85 的验收标准
要求它「也能通过构造」），``SyncEngine(source=None)`` 更是一种刻意的降级态（空视图、写路径报错）。
构造时拒掉它们，就是把两种正当用法一起拒掉。所以「定下来」在构造处，「说出来」在真正要用它的
那一步——同一句话、同一个类型，只有一处写。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, TypeVar

from dida.sync.completed import CompletedReader
from dida.sync.lists import ListWriteKind, ListWriteTarget, ProjectWriter
from dida.sync.push import TaskWriter
from dida.sync.read import PayloadReader, ViewReader
from dida.sync.refresh import ProjectReader, RefreshTarget
from dida.sync.tags import TagReader
from dida.sync.views import ViewDefinition, ViewStore
from dida.sync.writes import WriteTarget

__all__ = ["Capabilities", "MissingCapability"]

P = TypeVar("P")


class MissingCapability(RuntimeError):
    """这份引擎缺一件东西，做不了这件事（工单 #85）——「做不到」唯一的那一种说法。

    消息是两半拼起来的一句话：前一半说清是哪一件事缺哪一件，后一半说清该注入什么。八条
    写 / 网络路径从前各写一句自己的 ``RuntimeError``（同一个病、八种说法），现在都从这里出去。
    """

    def __init__(self, what: str, how: str) -> None:
        super().__init__(f"{what}：{how}")


def _capability(value: object | None, protocol: type[P]) -> P | None:
    """``value`` 满足这个能力协议就是它本人，否则 ``None``（**唯一**一处能力探测）。"""
    return value if isinstance(value, protocol) else None  # type: ignore[return-value]


@dataclass(frozen=True)
class Capabilities:
    """构造时问清「谁在场、谁会做什么」的那一张表（工单 #85）。

    每一格要么是**能用的那件东西本人**，要么是 ``None``（这份副本没有这件能力）。业务路径
    读这里的那一格；「缺这一件」怎么说是下面那几个 ``require_*`` 的事，只有一处写。
    """

    payload: PayloadReader | None = None
    """读一条任务的**原文**（详情页的只读字段、子任务行）。"""

    views: ViewReader | None = None
    """读自定义视图的**定义**；没有就是「没有自定义视图」，不是错误。"""

    writes: WriteTarget | None = None
    """任务的写路径 + 那本队列（``pending()`` 也读它，状态栏的 ``last_error`` 从这来）。"""

    list_writes: ListWriteTarget | None = None
    """清单的写路径 + 那本队列（``pending_lists()`` 也读它，未认领的清单从这来）。"""

    refreshes: RefreshTarget | None = None
    """全量刷新要写进去的那一份。"""

    view_store: ViewStore | None = None
    """自定义视图的读 / 写（只在本地落库，没有服务端那一半）。"""

    projects: ProjectReader | None = None
    """客户端：拉清单索引与逐清单取数（刷新要的）。"""

    tasks: TaskWriter | None = None
    """客户端：任务那几条写端点。"""

    lists: ProjectWriter | None = None
    """客户端：清单的建 / 改 / 删端点。"""

    tags: TagReader | None = None
    """客户端：``GET /open/v1/tag``。"""

    completed: CompletedReader | None = None
    """客户端：已完成流。"""

    @classmethod
    def of(cls, source: object | None, client: object | None) -> Capabilities:
        """把注入的本地副本与客户端问一遍——**全程序唯一**一处能力探测（工单 #85）。"""
        return cls(
            payload=_capability(source, PayloadReader),
            views=_capability(source, ViewReader),
            writes=_capability(source, WriteTarget),
            list_writes=_capability(source, ListWriteTarget),
            refreshes=_capability(source, RefreshTarget),
            view_store=_capability(source, ViewStore),
            projects=_capability(client, ProjectReader),
            tasks=_capability(client, TaskWriter),
            lists=_capability(client, ProjectWriter),
            tags=_capability(client, TagReader),
            completed=_capability(client, CompletedReader),
        )

    # ---------------------------------------------------------------- 要用的那一件不在：一种说法

    def task_write_target(self) -> WriteTarget:
        """任务的写路径要的那一份本地副本。

        与 :meth:`list_write_target` 是**并列的两族**（任务的账与清单的账），不是「通用那一件
        与它的特例」——名字照这一点写。
        """
        return self._required(
            self.writes,
            "写路径需要本地存储",
            "SyncEngine(source=Store(...))；只读的 ViewSource 存不下待推送改动",
        )

    def list_write_target(self) -> ListWriteTarget:
        """清单的写路径要的那一份本地副本。"""
        return self._required(
            self.list_writes,
            "清单的写路径需要本地存储",
            "SyncEngine(source=Store(...))；只读的 ViewSource 存不下待推送改动",
        )

    def refresh_target(self) -> RefreshTarget:
        """全量刷新要写进去的那一份本地副本。"""
        return self._required(
            self.refreshes,
            "全量刷新需要本地存储",
            "SyncEngine(source=Store(...))；只读的 ViewSource 写不进去",
        )

    def view_target(self) -> ViewStore:
        """自定义视图的写路径要的那一份本地副本。"""
        return self._required(
            self.view_store,
            "视图的写路径需要本地存储",
            "SyncEngine(source=Store(...))；视图只存在本地库里，没有服务端那一半",
        )

    def project_reader(self) -> ProjectReader:
        """刷新取数要的那个客户端。"""
        return self._required(
            self.projects, "全量刷新需要 API 客户端", "SyncEngine(client=DidaApiClient(...))"
        )

    def task_writer(self) -> TaskWriter:
        """推送任务改动要的那个客户端。"""
        return self._required(
            self.tasks, "推送需要 API 客户端", "SyncEngine(client=DidaApiClient(...))"
        )

    def project_writer(self) -> ProjectWriter:
        """推送清单改动要的那个客户端。"""
        return self._required(
            self.lists, "清单的写路径需要 API 客户端", "SyncEngine(client=DidaApiClient(...))"
        )

    def tag_reader(self) -> TagReader:
        """拉标签列表要的那个客户端。"""
        return self._required(
            self.tags, "标签列表需要 API 客户端", "SyncEngine(client=DidaApiClient(...))"
        )

    def completed_reader(self) -> CompletedReader:
        """拉已完成流要的那个客户端。"""
        return self._required(
            self.completed, "已完成流需要 API 客户端", "SyncEngine(client=DidaApiClient(...))"
        )

    @staticmethod
    def _required(piece: P | None, what: str, how: str) -> P:
        if piece is None:
            raise MissingCapability(what, how)
        return piece

    # ---------------------------------------------------------------- 读：本来就没有的那件事

    def payload_of(self, task_id: str) -> Mapping[str, Any] | None:
        """一条任务的原文；这份副本读不出原文时就是 ``None``（「没有这条任务」）。"""
        reader = self.payload
        return None if reader is None else reader.task_payload(task_id)

    def view_definitions(self) -> tuple[ViewDefinition, ...]:
        """自定义视图的定义；没有这件能力就是「一个都没有」，不是错误。"""
        reader = self.views
        return () if reader is None else tuple(reader.view_definitions())

    def last_error(self) -> str | None:
        """队列里最后一条推不出去的改动报的错；这份副本没有队列时就是 ``None``。"""
        queue = self.writes
        if queue is None:
            return None
        errors = [str(change.last_error) for change in queue.pending() if change.last_error]
        return errors[-1] if errors else None

    def unseen_list_ids(self) -> frozenset[str]:
        """服务端还没见过的清单 id；这份副本没有清单队列时就是空集。"""
        queue = self.list_writes
        if queue is None:
            return frozenset()
        return frozenset(
            change.list_id for change in queue.pending_lists() if change.kind is ListWriteKind.CREATE
        )

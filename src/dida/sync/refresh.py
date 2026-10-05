"""全量刷新（t09，ADR-0001）：逐清单取回未完成 → 与本地快照 diff → 只写变化。

这一片只管一件事：**一次全量刷新怎么取数、怎么落库**。为什么是逐清单全量而不是日期窗口、
为什么全部取回之后才落库（中途失败不留半份刷新）、为什么收集箱要单独拉——都是这一个变化
原因。增量发生在本地（``apply_refresh`` 只写变化），不在这里。

:class:`RefreshMixin` 的方法挂在组装好的 :class:`~dida.sync.engine.SyncEngine` 上。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence, runtime_checkable

from dida.api.errors import MalformedResponseError
from dida.logical_day import logical_day
from dida.sync.view import INBOX_ID, ViewSource

if TYPE_CHECKING:  # storage 反过来 import dida.sync.view，运行时不能在这里 import
    from dida.storage.store import RefreshReport, StoredSyncState


class ProjectReader(Protocol):
    """全量刷新要的那两次网络调用；t07 的 ``DidaApiClient`` 满足它。

    故意只有两个方法：刷新路径不需要客户端的写操作，也不需要它认识领域概念。
    """

    async def list_projects(
        self, *, offset: int | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """``GET /open/v1/project``：清单索引（服务端说了算，不是本地缓存）。

        分页参数是给刷新翻页用的（#41）：服务端一页最多 ``limit`` 条，而响应里没有
        total，所以「还有没有下一页」只能由调用方自己按「这一页拿满了没有」推断。
        """
        ...

    async def get_project_data(self, project_id: str) -> dict[str, Any]:
        """``GET /open/v1/project/{id}/data``：该清单的未完成任务全量。"""
        ...


@runtime_checkable
class RefreshTarget(ViewSource, Protocol):
    """本地副本在刷新路径上要会的那几件事；t08 的 ``Store`` 满足它。

    比 :class:`~dida.sync.view.ViewSource` 宽：全量刷新要能把原文写进去。
    只读的替身（``InMemorySource``）不满足它——刷新时会大声报错，不假装刷过了。
    """

    def stored_sync_state(self) -> StoredSyncState:
        """同步状态原样读回：已完成流游标不能被我这次刷新清掉（那是 t12 的）。"""
        ...

    def apply_refresh(
        self,
        *,
        lists: Sequence[Mapping[str, Any]] = (),
        tasks: Sequence[Mapping[str, Any]] = (),
        prune_lists: bool = False,
        prune_unfinished_tasks: bool = False,
    ) -> RefreshReport:
        """只写变化地落一次全量刷新，返回这次到底写了什么。

        ``prune_*`` 是调用方在断言「这一路取全了」；断言成立时才剪掉服务端已经不给了的
        清单 / 未完成任务（#41）。没取全就断言，剪掉的正是没取到的那部分。
        """
        ...

    def set_sync_state(
        self,
        *,
        completed_cursor: str | None = None,
        last_refresh_at: datetime | None = None,
        logical_day: date | None = None,
    ) -> None:
        """整体写入同步状态（``None`` 是清空）。"""
        ...


class RefreshMixin:

    """一次全量刷新：先清单索引，再逐个清单的 data，最后才落库。

    它是 **async** 的：网络等待不能阻塞界面，而写本地库必须发生在创建那条 sqlite
    连接的同一个线程上（t08 的线程亲和）。异步协程跑在事件循环那一个线程里，
    两条同时满足——**不许**把它丢进 ``threading.Thread`` 工人里跑。"""



    async def refresh(self) -> RefreshReport:
        """全量刷新（ADR 0001）：逐清单取回未完成 → 与本地快照 diff → 只写变化。

        返回 :class:`~dida.storage.store.RefreshReport`：这次到底写了什么、服务端盖掉了
        哪些本地值、哪些被待推送改动挡回去了（ADR-0002 要求覆盖能被看见）。

        **网络层没有「增量」**：日期窗口会静默漏掉「日期在很久以后、但刚被改过」的任务，
        所以未完成任务的唯一来源是逐清单全量。增量发生在本地：同一份数据拉第二次时，
        ``written_lists`` 与 ``written_tasks`` 都是 0，界面因此不闪、光标因此不丢。

        取数顺序：先清单索引（服务端的），再逐个清单的 data。**全部取回之后才落库**，
        所以中途任何一次失败都不会留下半份刷新：要么整份落地，要么本地库一动不动。
        失败照旧是 :class:`~dida.api.errors.DidaError` 的结构化错误，不吞。
        """
        target = self._refresh_target()
        reader = self._reader()

        index = await _project_index(reader)
        known = {str(item["id"]) for item in index}
        fetched = [str(item["id"]) for item in index]
        if INBOX_ID not in known:
            # 收集箱：文档允许用字面量 "inbox" 当 projectId，而它是默认清单——索引里没有它也
            # 得拉，否则「随手记」的任务永远不出现，还不报错。
            fetched.append(INBOX_ID)

        lists: list[Mapping[str, Any]] = list(index)
        tasks: list[Mapping[str, Any]] = []
        for project_id in fetched:
            payload = await reader.get_project_data(project_id)
            tasks.extend(_unfinished_tasks(payload, project_id))
            # 索引没提过的清单，用它自己的原文补一行，左栏才不会漏掉一个装着任务的清单。
            if project_id not in known:
                project_payload = payload.get("project") if isinstance(payload, Mapping) else None
                if isinstance(project_payload, Mapping) and project_payload.get("id"):
                    lists.append(project_payload)
                    known.add(project_id)

        # 取数到这里已经取全了（清单索引翻页翻到底、每个清单的未完成都拿到），所以落库时
        # 一并剪枝：远端已经没有的清单与未完成任务从本地库里删掉（#41）。剪枝**只**发生在
        # 这一步，不在翻页途中——上面任何一页失败就已经抛出去了，本地库一动不动。
        report = target.apply_refresh(
            lists=lists,
            tasks=tasks,
            prune_lists=True,
            prune_unfinished_tasks=True,
        )

        # 只有整份落地了才记「刷新成功」：失败的那次不算已同步。游标原样带回去——
        # set_sync_state 的 None 是清空，顺手抹掉的话 t12 的已完成流会从头再拉一遍。
        now = self._clock.now()
        target.set_sync_state(
            completed_cursor=target.stored_sync_state().completed_cursor,
            last_refresh_at=now,
            logical_day=logical_day(now, self._day_end).label,
        )
        return report

    def _refresh_target(self) -> RefreshTarget:
        """刷新要写的那个本地副本。没接上就大声报错——绝不假装刷过了。"""
        if not isinstance(self._source, RefreshTarget):
            raise RuntimeError(
                "全量刷新需要本地存储：SyncEngine(source=Store(...))；"
                "只读的 ViewSource 写不进去"
            )
        return self._source

    def _reader(self) -> ProjectReader:
        """取数要的那个客户端。"""
        if self._client is None:
            raise RuntimeError("全量刷新需要 API 客户端：SyncEngine(client=DidaApiClient(...))")
        return self._client


PROJECT_PAGE_SIZE = 200
"""清单索引一页要多少条（#41）。

200 是文档写着的默认值：给了任一分页参数时，``limit`` 的缺省就是 200。文档**没有**写
上限，所以这里不试探更大的页——要一个文档没写过的数，可能被服务端静默截断，而截断在这里
恰恰是不可见的（响应里没有 total）。按文档的默认值要，服务端一定会照办。
"""


async def _project_index(reader: ProjectReader) -> list[Mapping[str, Any]]:
    """``GET /open/v1/project``：清单索引，**翻页翻到底**（#41）。

    服务端一页最多 :data:`PROJECT_PAGE_SIZE` 条，而响应是一个**没有 total 的裸数组**，
    所以「拿全了没有」只能这样推断：**拿满一整页就说明可能还有**，接着要下一页；拿到短页
    或空页才是到底了。这条推断是这里唯一的完整性信号——任何时候都不许看到第一页就当成
    拿全了，那正是「第 201 个清单起永远看不见」还不报错的由来。

    中途任何一页失败都照旧往上抛 :class:`~dida.api.errors.DidaError`：取数没取全就绝不
    落库（ADR-0001 的全有全无），因此一次没翻完的索引也不会被当成完整的索引去剪枝。
    """
    pages: list[Mapping[str, Any]] = []
    offset = 0
    while True:
        page = _project_index_page(
            await reader.list_projects(offset=offset, limit=PROJECT_PAGE_SIZE)
        )
        pages.extend(page)
        if len(page) < PROJECT_PAGE_SIZE:
            return pages
        offset += len(page)


def _project_index_page(payload: Any) -> list[Mapping[str, Any]]:
    """``GET /open/v1/project`` 的一页响应体 → 清单索引。

    **空数组是合法的**（就是没有清单），不是失败。形状不对则结构化报错：把 ``{}`` 当成
    「没有清单」会静默地什么都不刷新，那正是 ADR-0001 要挡的那类安静。
    """
    if not isinstance(payload, list) or any(
        not isinstance(item, Mapping) or not item.get("id") for item in payload
    ):
        raise MalformedResponseError(
            f"清单索引不是「带 id 的清单数组」：收到 {type(payload).__name__}"
        )
    return list(payload)


def _unfinished_tasks(payload: Any, project_id: str) -> list[Mapping[str, Any]]:
    """``ProjectData`` → 未完成任务原文。

    ``tasks`` 缺席或为空数组都是「这个清单没有未完成任务」（有效数据，不是错误）；
    形状不对才报错——一个形状不对的 ``tasks`` 被当成空数组，用户看到的就是「我的任务
    不见了」，而且不报错。

    服务端漏写 ``projectId`` 时补上取数用的那个清单 id：这条任务是从哪个清单拉回来的，
    只有引擎知道；不补的话 t08 会把它算进收集箱（左栏徽标与清单名全错，且不报错）。
    """
    if not isinstance(payload, Mapping):
        raise MalformedResponseError(
            f"清单 {project_id} 的 data 不是对象：收到 {type(payload).__name__}"
        )
    raw = payload.get("tasks")
    if raw is None:
        return []
    if not isinstance(raw, list) or any(not isinstance(task, Mapping) for task in raw):
        raise MalformedResponseError(
            f"清单 {project_id} 的 tasks 不是任务数组：收到 {type(raw).__name__}"
        )
    return [task if task.get("projectId") else {**task, "projectId": project_id} for task in raw]

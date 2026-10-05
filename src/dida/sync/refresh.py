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
from dida.sync.view import INBOX_ID, INBOX_NAME, ViewSource

if TYPE_CHECKING:  # storage 反过来 import dida.sync.view，运行时不能在这里 import
    from dida.storage.store import RefreshReport, StoredSyncState


class ProjectReader(Protocol):
    """全量刷新要的那两次网络调用；t07 的 ``DidaApiClient`` 满足它。

    故意只有两个方法：刷新路径不需要客户端的写操作，也不需要它认识领域概念。
    """

    async def list_projects(self) -> list[dict[str, Any]]:
        """``GET /open/v1/project``：清单索引（服务端说了算，不是本地缓存）。"""
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
    ) -> RefreshReport:
        """只写变化地落一次全量刷新，返回这次到底写了什么。"""
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

        index = _project_index(await reader.list_projects())
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
            fetched_tasks = _unfinished_tasks(payload, project_id)
            tasks.extend(fetched_tasks)
            # 索引没提过的清单，用它自己的原文补一行，左栏才不会漏掉一个装着任务的清单。
            if project_id not in known:
                row = _missing_project_row(payload, fetched_tasks, project_id)
                if row is not None:
                    lists.append(row)
                    known.add(project_id)

        report = target.apply_refresh(lists=lists, tasks=tasks)

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


def _project_index(payload: Any) -> list[Mapping[str, Any]]:
    """``GET /open/v1/project`` 的响应体 → 清单索引。

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

    服务端漏写 ``projectId`` 时补上**服务端返回的**容器 id（:func:`_container_id`）：
    这条任务是从哪个清单拉回来的，只有引擎知道；不补的话 t08 会把它算进「不知道在哪个
    清单」。**请求侧的字面量别名 ``inbox`` 不是答案**（#33）：收集箱的真实 id 是每账户
    不同的一串，拿别名补下去，归类一条都对不上；认不出服务端 id 时宁可空着。
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
    container_id = _container_id(payload, raw, project_id)
    if container_id is None:
        return list(raw)
    return [task if task.get("projectId") else {**task, "projectId": container_id} for task in raw]


def _container_id(payload: Any, tasks: Sequence[Mapping[str, Any]], project_id: str) -> str | None:
    """这次取回来的这个容器，**服务端认的 id** 是什么。

    优先用 ``payload["project"]["id"]``；否则用这批任务里任意一条带的 ``projectId``——实测
    （用户账户的缓存）收集箱的 data 里**没有** ``project`` 对象，它那个每账户一串的真实 id
    只在它的任务上。

    真实清单请求用的 id 本来就来自服务端的清单索引，所以兜底可以用它；请求侧的别名
    ``inbox`` 不算答案，这时返回 ``None``（认不出就不猜）。
    """
    project_payload = payload.get("project") if isinstance(payload, Mapping) else None
    if isinstance(project_payload, Mapping) and project_payload.get("id"):
        return str(project_payload["id"])
    for task in tasks:
        if task.get("projectId"):
            return str(task["projectId"])
    return None if project_id == INBOX_ID else project_id


def _missing_project_row(
    payload: Any, tasks: Sequence[Mapping[str, Any]], project_id: str
) -> Mapping[str, Any] | None:
    """索引里没提过的清单 → 客户端自己补的那一行；补不出来就 ``None``。

    正常清单用原文里的 ``project`` 对象。收集箱是特例：实测它的 data 里没有 ``project``
    对象，那就用这批任务带的 ``projectId`` 造一行（名字用客户端的叫法）——服务端的清单索引
    里没有收集箱，这一行只能由客户端补（GLOSSARY「收集箱」）。

    这一行还要标成收集箱：``Project`` 定义里没有「我是收集箱」这种字段（api-contracts.md
    的字段表里没有），而「这个容器是收集箱」只有刚用别名把它取回来的引擎知道。
    """
    project_payload = payload.get("project") if isinstance(payload, Mapping) else None
    if isinstance(project_payload, Mapping) and project_payload.get("id"):
        row: Mapping[str, Any] = project_payload
    elif project_id == INBOX_ID:
        learned = _container_id(payload, tasks, project_id)
        if learned is None:
            return None
        row = {"id": learned, "name": INBOX_NAME}
    else:
        return None
    return {**row, "isInbox": True} if project_id == INBOX_ID else row

"""子任务（t20）：读子任务行，勾选时**先重读该任务再只写这一次改动**。

这一片只管一件事：**子任务的读写顺序**。``items`` 是一整个数组，更新是「发什么就是什么」，
所以写回之前必须重读，否则会把别处刚改过的子任务一起抹掉（而且服务端一声不吭）。

v2 把子任务改成只读（spec 的「只读的字段」），这一片是 #43 决定留还是删的对象——留着的时候
它的结论（先读后写、本地待推送改动豁免）仍然有效。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from dida.sync.read import PayloadReader
from dida.sync.view import (
    SUBTASK_COMPLETED_STATUS,
    SUBTASK_NORMAL_STATUS,
    SubtaskItem,
    subtask_completed,
    subtask_items,
)
from dida.sync.writes import UnknownTaskError, WriteTarget


@runtime_checkable
class TaskReader(Protocol):
    """写前重读要的那一次网络调用；t07 的 ``DidaApiClient`` 满足它（工单 #20）。

    故意只有这一个方法：勾选子任务之前的重读不需要清单索引，也不需要任何写操作。
    单独一个协议而不是塞进 :class:`TaskWriter`，是因为「会推」与「会重读」是两件事，
    测试替身只满足前者时不该被迫实现后者。
    """

    async def get_task(self, project_id: str, task_id: str) -> dict[str, Any]:
        """``GET /open/v1/project/{projectId}/task/{taskId}``：单个任务的完整字段。"""
        ...


@dataclass(frozen=True)
class SubtaskWrite:
    """一次子任务勾选的账目：重读回来的那一份，以及服务端有没有说出别的事。"""

    task_id: str
    subtask_id: str
    items: tuple[SubtaskItem, ...]
    """重读之后本地这一份（右栏据此重画，不必再问一次引擎）。"""

    written: bool = True
    """有没有真的写回去：服务端那份里已经没有这个子任务时是 ``False``（以服务端为准）。"""

    changed_elsewhere: bool = False
    """重读发现这条任务在别处被改过：服务端那一份已经落地，**用户必须看得见**（ADR-0002）。"""


class SubtaskMixin:

    """子任务：读那一份原文里的数组，勾选时先重读、再只翻这一个条目。

    重读回来的那一份照旧走 ``apply_refresh`` 落库（服务端权威、待推送改动豁免），
    服务端盖掉了什么由 ``RefreshReport`` 如实报出来。"""



    def subtasks(self, task_id: str) -> tuple[SubtaskItem, ...]:
        """读：本地那份原文里的子任务数组 → 右栏的行（工单 #20）。

        读的是**任务原文**（``items`` 原样存在本地快照里），不是某个领域 dataclass：
        子任务不需要自己的表，它本来就属于这条任务这一条记录（GLOSSARY 的「子任务」）。

        只读的替身读不出原文时给空元组，与 :meth:`view` 空缓存给空视图同一条口径：
        读路径上没有可读的东西就是没有，不是错误。
        """
        return subtask_items(self._payload_of(task_id), now=self._clock.now(), day_end=self._day_end)

    async def toggle_subtask(self, task_id: str, subtask_id: str) -> SubtaskWrite:
        """写：勾选/取消勾选一个子任务——**写回之前先重读该任务**（工单 #20）。

        ``items`` 是**一整个数组**，而更新是「发什么就是什么」：拿本地那份旧数组直接写
        回去，会把别处（手机、网页版）刚改过的子任务一起抹掉，而且服务端一声不吭。所以
        先重读一次，以服务端那一份为底稿，只翻这一次那一个条目——别处的修改因此原样留着。
        这是本工单唯一不能省的顺序：**先读，后写**。

        重读回来的东西顺手走 :meth:`~dida.sync.engine.SyncEngine.refresh` 那条落库路径
        （``apply_refresh``）：服务端权威落地，本地还没推成功的改动照旧豁免（ADR-0002，
        否则一次网络抖动加上一次重读就会悄悄撤销用户自己的操作）。服务端盖掉了本地什么，
        由 ``RefreshReport`` 如实记下，其中 ``changed_elsewhere`` 报给用户——**覆盖必须被
        看见**，这一条与 ADR-0002 是同一句话。

        写走 :meth:`write` 那条写路径：本地当场生效、立即推送、推不动留在队列里退避重试。

        服务端那份里已经没有这个子任务时（别处删掉了）**什么都不写**：以服务端为准，
        ``written=False`` 说清这次没有写回去。
        """
        target = self._write_target()
        local = target.task_payload(task_id)
        if local is None or not local.get("projectId"):
            raise UnknownTaskError(task_id)
        server = await self._task_reader().get_task(str(local["projectId"]), task_id)  # ← 重读
        report = self._refresh_target().apply_refresh(tasks=[server])
        changed_elsewhere = bool(report.overwritten)
        merged = _toggled_items(self._subtask_base(target, task_id, server), subtask_id)
        if merged is None:
            return SubtaskWrite(
                task_id=task_id,
                subtask_id=subtask_id,
                items=self.subtasks(task_id),
                written=False,
                changed_elsewhere=changed_elsewhere,
            )
        self.write(task_id, changes={"items": merged})
        return SubtaskWrite(
            task_id=task_id,
            subtask_id=subtask_id,
            items=self.subtasks(task_id),
            changed_elsewhere=changed_elsewhere,
        )

    def _subtask_base(
        self, target: WriteTarget, task_id: str, server: Mapping[str, Any]
    ) -> Any:
        """这次重读之后要拿来翻的那一份 ``items``：默认是服务端那一份。

        例外只有一种：本地还有**没推成功的子任务改动**。那是用户自己的操作，服务端权威
        管不着它（ADR-0002 的豁免是逐字段的，而 ``items`` 正好是一个字段），拿服务端那一
        份当底稿会把用户刚勾上的又悄悄撤销——正是 ADR-0002 要挡的那种「一次失败推送加上
        一次刷新，用户的操作没了」。这时本地那一整个数组就是用户的意图。
        """
        if _has_pending_items(target, task_id):
            local = target.task_payload(task_id) or {}
            if isinstance(local.get("items"), list):
                return local["items"]
        return server.get("items")

    def _payload_of(self, task_id: str) -> Mapping[str, Any] | None:
        """本地那份任务原文；源读不出原文时当作「没有这条任务」。

        要的是**读**的能力（``PayloadReader``），不是写的能力：详情页与子任务行都只读它
        （#33 的详情读形状也从这里过），只读的替身存得下原文就该读得到。
        """
        source = self._source
        return source.task_payload(task_id) if isinstance(source, PayloadReader) else None

    def _task_reader(self) -> TaskReader:
        """写前重读要的那个客户端。没接上就大声报错——绝不假装重读过了。"""
        if not isinstance(self._client, TaskReader):
            raise RuntimeError("写前重读需要 API 客户端：SyncEngine(client=DidaApiClient(...))")
        return self._client


def _has_pending_items(target: WriteTarget, task_id: str) -> bool:
    """这条任务上有没有还没推成功的 ``items`` 改动（有的话它豁免于服务端权威）。"""
    return any(
        change.task_id == task_id and "items" in change.payload for change in target.pending()
    )


def _toggled_items(items: Any, subtask_id: str) -> list[Any] | None:
    """在 ``items`` 上**只翻** ``subtask_id`` 这一个条目；没有这个子任务时返回 ``None``。

    这就是「只合并本次改动的子任务」：其余条目原样带回去（连同一个对象都拷一份，不去改
    服务端给的那份载荷），包括我们不认识的字段——回写丢字段是这张工单要挡的静默失败之一。

    状态用子任务那一对取值 0/1（``SUBTASK_COMPLETED_STATUS`` / ``SUBTASK_NORMAL_STATUS``），
    不是任务级的 ``-1/0/2``。``completedTime`` **不在这里编**：那是服务端给的、我们原样
    带回的值，替它猜一个时刻就是在写一个没人测过的字段（``api-contracts.md`` 的纪律）。
    """
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        return None
    merged: list[Any] = []
    found = False
    for entry in items:
        if isinstance(entry, Mapping) and entry.get("id") == subtask_id:
            found = True
            flipped = dict(entry)
            flipped["status"] = (
                SUBTASK_NORMAL_STATUS
                if subtask_completed(entry.get("status"))
                else SUBTASK_COMPLETED_STATUS
            )
            merged.append(flipped)
        else:
            merged.append(dict(entry) if isinstance(entry, Mapping) else entry)
    return merged if found else None

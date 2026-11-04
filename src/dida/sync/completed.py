"""已完成流（t12）：按完成时间游标拉一个窗口，把任务写进本地副本并推进游标。

这一片只管一件事：**已完成任务怎么拉回来**。窗口两条边（配置窗口与游标）、满 200 条时
游标退到哪里、失败为什么不推进游标——都在这一个变化原因里。

这是**唯一**允许用时间游标做增量的数据流（ADR-0001）：``completedTime`` 是任务上唯一能被
服务端过滤的变化时间戳（t12 的实测）。未完成任务照旧走 :mod:`dida.sync.refresh` 的逐清单
全量，别把窗口学到这里来。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from dida.api.errors import MalformedResponseError
from dida.api.guards import api_date
from dida.sync.rows import completed_window_start, task_is_completed
from dida.vocabulary import StoredSyncState


DEFAULT_COMPLETED_WINDOW_HOURS = 168
"""已完成流往回看多少小时（配置键 ``completed_window_hours`` 的兜底值）：**7 天**。

与 ``Config.completed_window_hours`` 的默认值是同一个数，两处都从 24 改过来（工单 #37）：
只改配置那一处的话，**直接构造出来的** ``SyncEngine``（测试、别的组合根）窗口还是 24 小时，
而「只显示最近 7 天完成的」在屏幕上就还是 1 天。两处相等由
``tests/test_task_row_model.py`` 的一条守卫钉着。
"""


COMPLETED_PAGE_LIMIT = 200
"""``POST /open/v1/task/completed`` 一次最多回多少条（api-contracts.md）。"""


@runtime_checkable
class CompletedReader(Protocol):
    """已完成流要的那一次网络调用；t07 的 ``DidaApiClient`` 满足它。

    三个字段都可选（文档如此），引擎只给窗口两端——清单筛选恰恰是本地索引可能还不知道
    的事，见 :meth:`SyncEngine.refresh_completed`。
    """

    async def list_completed(
        self,
        *,
        project_ids: Sequence[str] | None = None,
        start_date: str | datetime | None = None,
        end_date: str | datetime | None = None,
    ) -> list[dict[str, Any]]:
        """``POST /open/v1/task/completed``：按完成时间窗口取已完成任务（一次最多 200 条）。"""
        ...


@dataclass(frozen=True)
class CompletedReport:
    """一次已完成流拉取的账目：窗口两端、写进本地库的条数、有没有撞上 200 条上限。"""

    start: datetime
    """这次窗口的左端（配置窗口与游标里较晚的那个）。"""

    end: datetime
    """这次窗口的右端，也是下次的游标（除非撞上限）。"""

    written_tasks: int = 0
    """真正写进本地副本的条数：同一份数据拉第二次是 0（ADR-0001 的「只写变化」）。"""

    truncated: bool = False
    """响应正好 200 条：文档说 200 是上限且无分页可续，所以别声称这一窗已经拿全了。"""


class CompletedStreamMixin:

    """拉一次已完成流：窗口 ``[游标, 现在]``，成功之后才推进游标。

    失败照旧是 :class:`~dida.api.errors.DidaError` 的结构化错误，
    **不推进游标**：没拉到的窗口下次还会再拉，绝不会被静默跳过。"""



    async def refresh_completed(self) -> CompletedReport:
        """拉一次已完成流：窗口 ``[游标, 现在]``，把任务写进本地副本，并推进游标。

        这是**唯一**允许用时间游标做增量的数据流（ADR-0001）：``completedTime`` 是任务上
        唯一能被服务端过滤的变化时间戳。未完成任务照旧走逐清单全量，别把窗口学到这里来。

        窗口的两条边：

        - 往回看不早于 ``now - completed_window_hours``（配置给的，t12）；
        - 不早于上次拉到的位置（同步状态里的游标），所以连着刷两次不会把同一段拉两遍。

        请求体里**不带** ``projectIds``：文档说三个字段都可选，而「哪些清单里完成过」正是
        本地索引可能还不知道的事（用户在手机上往一个新清单里记了一笔并完成）。不筛清单，
        就不会漏。

        失败照旧是 :class:`~dida.api.errors.DidaError` 的结构化错误，并且**不推进游标**：
        没拉到的窗口下次还会再拉，绝不会被静默跳过。
        """
        target = self._refresh_target()
        reader = self._completed_reader()
        now = self._clock.now()
        state = target.stored_sync_state()
        start = self._completed_window_start(now, state.completed_cursor)

        payload = await reader.list_completed(
            # 日期一律写成文档形式（``+0800``）并且不换时区：游标就是这么存回来的。
            start_date=api_date(start, field="startDate"),
            end_date=api_date(now, field="endDate"),
        )
        received = _completed_tasks(payload)
        # 双条件过滤的另一半在**客户端**：这个端点没有 ``status`` 参数（只有 projectIds /
        # startDate / endDate，而日期筛的是 completedTime），所以服务端只按时间回话。
        # 取消完成**不会清空** completedTime（实测），只按时间筛会把取消完成的任务又捞回来
        # ——它随后会以「未完成」的样子出现在清单里（本地判定看 status）。筛掉的照样算
        # 这一窗的账：截断与游标都数**收到的**那批（下面 received），不是筛完剩下的。
        tasks = [task for task in received if task_is_completed(task.get("status"))]
        # 与全量刷新同一套落库路径：只写变化，服务端权威，待推送改动豁免（t08）。
        report = target.apply_refresh(tasks=tasks)
        # 游标只在成功之后前进；上次刷新时间与逻辑日原样带回去（None 是清空）。
        target.set_sync_state(
            completed_cursor=self._completed_cursor(now, received),
            last_refresh_at=state.last_refresh_at,
            logical_day=state.logical_day,
        )
        return CompletedReport(
            start=start,
            end=now,
            written_tasks=report.written_tasks,
            truncated=len(received) >= COMPLETED_PAGE_LIMIT,
        )

    def _completed_window_start(self, now: datetime, cursor: str | None) -> datetime:
        """这次拉取从哪一刻开始：配置窗口与持久化游标里**较晚**的那个。"""
        start = completed_window_start(now, self._completed_window_hours)
        resumed = _parse_moment(cursor)
        return resumed if resumed is not None and resumed > start else start

    def _completed_cursor(self, now: datetime, tasks: Sequence[Mapping[str, Any]]) -> str:
        """拉完之后游标推到哪一刻（``tasks`` 是**收到的那一批**，含被 status 筛掉的）。

        正常情况就是 ``now``：``[上次的 now, 这次的 now]`` 首尾相接，不重不漏。**满 200 条
        时例外**：文档把 200 写成上限，而没有任何分页或游标参数可续，所以「一次拿全了」
        是站不住的。这时退到这批里最新的那个完成时刻，把没拿到的部分留给下一次——宁可
        重一点，也不静默丢掉用户完成的任务。
        """
        if len(tasks) < COMPLETED_PAGE_LIMIT:
            return now.isoformat()
        newest = max(
            (
                moment
                for moment in (_parse_moment(task.get("completedTime")) for task in tasks)
                if moment is not None
            ),
            default=None,
        )
        return (newest if newest is not None else now).isoformat()

    def _completed_reader(self) -> CompletedReader:
        """已完成流要的那个客户端。没接上就大声报错——绝不假装拉过了。"""
        return self._caps.completed_reader()


def _completed_tasks(payload: Any) -> list[Mapping[str, Any]]:
    """``POST /open/v1/task/completed`` 的响应体 → 任务原文。

    **空数组是合法的**（这个窗口里没人完成过任务），不是失败。形状不对则结构化报错：
    ``{}`` 被当成「没有已完成的任务」，用户看到的是「我的已完成不见了」而且不报错。
    """
    if not isinstance(payload, list) or any(
        not isinstance(task, Mapping) or not task.get("id") for task in payload
    ):
        raise MalformedResponseError(
            f"已完成流不是「带 id 的任务数组」：收到 {type(payload).__name__}"
        )
    return list(payload)


def _parse_moment(value: Any) -> datetime | None:
    """服务端的日期串（``completedTime``）或同步状态里的游标 → 时刻。

    解析不了、或者没有时区偏移，都算 ``None``：前者当作「没拉到过」从头按窗口拉，后者
    不该拿去和带时区的「现在」比较。宁可多拉一次，也不让一个脏游标把窗口悄悄挪走。
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None

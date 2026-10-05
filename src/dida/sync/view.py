"""同步引擎对外的视图模型与纯函数。

引擎的读路径只暴露这里定义的类型：TUI 拿到的是**已经判断好**的东西——
哪条任务属于哪个区、截止时间读作什么、优先级标记长什么样——它自己不做判断。

两组类型：

- 输入（缓存 → 引擎）：:class:`ListSnapshot` / :class:`TaskSnapshot` / :class:`SyncState`，
  :class:`ViewSource` 是它们的只读入口，t08 的 ``Store`` 实现它。
- 输出（引擎 → TUI）：:class:`TodayView` / :class:`TaskGroup` / :class:`TaskItem` /
  :class:`ListSummary`。

函数都是纯的：「现在」与 ``day_end`` 一律从参数进来，逻辑日判定交给
:mod:`dida.logical_day`，这里不重算任何日界。分区与排序的规矩：

- 逾期区置顶：有截止时间、且早于当前逻辑日的开始时刻。
- 今日区：截止时间落在当前逻辑日区间内 ``[start, end)``。
- 收集箱无日期区：没有截止时间的任务（读作「—」），排在今日**之后**。它自成一区的理由有两层：
  「今天要做完什么」这句话不该收留一件还没定日子的任务（story 23 要求这两者一眼可分），
  而分诊看的是「有没有日期」、不是「在哪个清单」——按清单拆开会让收集箱之外的无日期任务
  重新混回今日区，或者干脆从这一屏消失。
- 未来（下一个逻辑日起）的任务不属于这张「今日」视图；已完成的也不属于。
- 区内先按截止时间升序，没有截止时间的排在同区有截止时间的后面（按标题）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence

from dida.logical_day import logical_day

NO_DUE_TEXT = "—"
"""没有截止时间的读法；与「今天」一眼可分。"""

INBOX_ID = "inbox"
"""收集箱在 API 里的 projectId 别名。"""

INBOX_NAME = "收集箱"


class GroupKind(Enum):
    """中栏的分区身份。"""

    OVERDUE = "overdue"
    TODAY = "today"
    INBOX_UNDATED = "inbox_undated"
    """没有截止时间的那些：收集箱里等着分诊的一堆（story 16）。"""


GROUP_ORDER: tuple[GroupKind, ...] = (GroupKind.OVERDUE, GroupKind.TODAY, GroupKind.INBOX_UNDATED)
"""中栏的区序（接口契约的顺序）：逾期置顶 → 今日 → 收集箱无日期。

已完成区在中栏**底部**，由 :func:`completed_section` 单独给，不在这个序列里。
"""

_GROUP_TITLES = {
    GroupKind.OVERDUE: "逾期",
    GroupKind.TODAY: "今日",
    GroupKind.INBOX_UNDATED: "收集箱无日期",
}


@dataclass(frozen=True)
class ListSnapshot:
    """缓存里的一条清单（API 叫 project）。"""

    id: str
    name: str


@dataclass(frozen=True)
class TaskSnapshot:
    """缓存里的一条任务快照：只有事实，没有判断。"""

    id: str
    title: str
    list_id: str
    due: datetime | None = None
    """截止时刻（带时区）；全天任务的时刻是当天 00:00，看 ``all_day`` 分辨。"""

    all_day: bool = False
    priority: int = 0
    """``0`` / ``1`` / ``3`` / ``5``（无 / 低 / 中 / 高），与 API 一致。"""

    completed: bool = False

    completed_at: datetime | None = None
    """完成时刻（服务端的 ``completedTime``）；本地刚完成、服务端还没认过的那些是 ``None``。"""

    desc: str = ""
    """服务端的 ``desc``：这条任务的**描述**（工单 #20 的右栏要常驻显示它）。"""

    content: str = ""
    """服务端的 ``content``：这条任务的**备注/正文**。

    ``api-contracts.md`` 的 ``Task`` 字段表里 ``desc`` 与 ``content`` 是两个字面不同的字段：
    描述归描述、备注归备注，这里不合并、也不互相兜底——详情栏两行各画各的。
    """

    tags: tuple[str, ...] = ()
    """服务端的 ``tags``：标签名，按服务端给的顺序。"""


@dataclass(frozen=True)
class SyncState:
    """缓存里的同步状态。"""

    last_refresh_at: datetime | None = None
    pending_count: int = 0


class ViewSource(Protocol):
    """读路径的数据来源：本地缓存。t08 的 ``Store`` 是生产实现，测试用内存替身。"""

    def lists(self) -> Sequence[ListSnapshot]:
        """全部清单。"""
        ...

    def tasks(self) -> Sequence[TaskSnapshot]:
        """全部任务快照，含已完成。"""
        ...

    def sync_state(self) -> SyncState:
        """同步状态。"""
        ...


@dataclass(frozen=True)
class ListSummary:
    """左栏一行：清单 + 未完成条数徽标。"""

    id: str
    name: str
    unfinished: int


@dataclass(frozen=True)
class TaskItem:
    """中栏一行，字段都已经是可以直接画的成品。"""

    task_id: str
    title: str
    list_id: str
    list_name: str
    priority: int
    priority_mark: str
    due: datetime | None
    all_day: bool
    due_text: str

    desc: str = ""
    """描述，原样来自快照（TUI 不解析它）。"""

    content: str = ""
    """备注/正文，原样来自快照。"""

    tags_text: str = ""
    """标签的成品读法（``#工作 #季度``，见 :func:`format_tags`）。

    没有标签就是空串——「这一行要不要画」由这个空串回答，详情栏不自己判断有没有标签。
    """


@dataclass(frozen=True)
class TaskGroup:
    """中栏一个区：身份 + 标题（由身份定）+ 行。"""

    kind: GroupKind
    items: tuple[TaskItem, ...]

    @property
    def title(self) -> str:
        """分区标题，如「逾期」。"""
        return _GROUP_TITLES[self.kind]

    @property
    def count(self) -> int:
        """分区条数，显示在标题上。"""
        return len(self.items)


@dataclass(frozen=True)
class CompletedItem:
    """已完成区一行：标题、清单名、完成时刻的人类读法（都已经是成品）。"""

    task_id: str
    title: str
    list_name: str
    completed_at: datetime | None
    completed_text: str


@dataclass(frozen=True)
class CompletedSection:
    """中栏底部的已完成区：条数与行。

    折叠与否是 **TUI 的状态**（看点不看点是用户的事），所以这里没有 ``collapsed``；
    引擎只管「窗口内完成的有哪些」，行已经排好序、写好读法了。
    """

    items: tuple[CompletedItem, ...] = ()

    @property
    def count(self) -> int:
        """窗口内完成的条数，显示在「已完成 N 项」上。"""
        return len(self.items)


@dataclass(frozen=True)
class TodayView:
    """整屏要的数据：左栏清单与中栏分区（含底部的已完成区）。"""

    lists: tuple[ListSummary, ...]
    groups: tuple[TaskGroup, ...]
    completed: CompletedSection = CompletedSection()


def priority_mark(priority: int) -> str:
    """优先级标记：高 ``!``、中 ``~``、低与无 ``·``。"""
    return {5: "!", 3: "~"}.get(priority, "·")


PRIORITY_CYCLE: tuple[int, ...] = (0, 1, 3, 5)
"""四个档位的**线上编码**，按 ``p`` 键的循环顺序：无 → 低 → 中 → 高。"""


def next_priority(priority: int) -> int:
    """``p`` 的下一档：无 → 低 → 中 → 高 → 无，值都是 API 的线上编码 ``0/1/3/5``。

    稠密的 ``1/2/3`` 是**日期解析器的档位序号**（用户写的 ``!1``/``!2``/``!3``），不是要
    发给服务端的取值：``!3`` 是「高」，对应线上的 ``5``。两套编码只在这一处相接，
    ``!5`` 那种写法仍然是诊断（见 :mod:`dida.date_parser`）。

    认不出来的取值（服务端给了表外的数）当作「无」：``priority_mark`` 本来就把它们读作
    ``·``，从那儿往前推一档正好是「低」。
    """
    index = PRIORITY_CYCLE.index(priority) if priority in PRIORITY_CYCLE else -1
    return PRIORITY_CYCLE[(index + 1) % len(PRIORITY_CYCLE)]


def list_names(lists: Sequence[ListSnapshot]) -> dict[str, str]:
    """清单 id → 显示名；收集箱即使没有清单行也能叫出名字。"""
    names = {item.id: item.name for item in lists}
    names.setdefault(INBOX_ID, INBOX_NAME)
    return names


def format_tags(tags: Sequence[str]) -> str:
    """标签的人类读法：``#工作 #季度``；没有标签就是空串。

    ``#`` 是用户在日期解析器里写标签时用的那个记号（``交报告 #工作``），所以详情栏照它
    画——引擎把读法算好（与 ``due_text`` / ``priority_mark`` 同一个口径），TUI 只画。
    """
    return " ".join(f"#{tag}" for tag in tags)


def task_item(snapshot: TaskSnapshot, names: dict[str, str], *, now: datetime, day_end: str) -> TaskItem:
    """一条任务快照 → 一行成品。"""
    return TaskItem(
        task_id=snapshot.id,
        title=snapshot.title,
        list_id=snapshot.list_id,
        list_name=names.get(snapshot.list_id, snapshot.list_id),
        priority=snapshot.priority,
        priority_mark=priority_mark(snapshot.priority),
        due=snapshot.due,
        all_day=snapshot.all_day,
        due_text=format_due(snapshot.due, all_day=snapshot.all_day, now=now, day_end=day_end),
        desc=snapshot.desc,
        content=snapshot.content,
        tags_text=format_tags(snapshot.tags),
    )


def summarize_lists(lists: Sequence[ListSnapshot], tasks: Sequence[TaskSnapshot]) -> tuple[ListSummary, ...]:
    """左栏：每条清单一个未完成条数徽标，顺序照缓存给的来。"""
    unfinished: dict[str, int] = {}
    for snapshot in tasks:
        if not snapshot.completed:
            unfinished[snapshot.list_id] = unfinished.get(snapshot.list_id, 0) + 1
    return tuple(
        ListSummary(id=item.id, name=item.name, unfinished=unfinished.get(item.id, 0)) for item in lists
    )


def group_tasks(
    tasks: Sequence[TaskSnapshot],
    lists: Sequence[ListSnapshot],
    *,
    now: datetime,
    day_end: str,
) -> tuple[TaskGroup, ...]:
    """未完成任务 → 分区，按 :data:`GROUP_ORDER` 排：逾期 → 今日 → 收集箱无日期。

    空区不出现在结果里。分区只在这里做：TUI 拿到的是已经分好区的成品，它自己不判断
    「这条算不算今天」。
    """
    label = logical_day(now, day_end).label
    names = list_names(lists)
    buckets: dict[GroupKind, list[TaskItem]] = {kind: [] for kind in GROUP_ORDER}
    for snapshot in tasks:
        if snapshot.completed:
            continue
        if snapshot.due is None:
            kind = GroupKind.INBOX_UNDATED
        else:
            day = due_day(snapshot.due, all_day=snapshot.all_day, day_end=day_end)
            if day < label:
                kind = GroupKind.OVERDUE
            elif day == label:
                kind = GroupKind.TODAY
            else:
                continue  # 未来的任务不属于「今日」
        buckets[kind].append(task_item(snapshot, names, now=now, day_end=day_end))

    return tuple(
        TaskGroup(kind=kind, items=tuple(_by_due(buckets[kind])))
        for kind in GROUP_ORDER
        if buckets[kind]
    )


def due_day(due: datetime, *, all_day: bool, day_end: str) -> date:
    """这条任务的截止属于哪个逻辑日。

    有具体时刻的截止：按逻辑日偏移算（``day_end = "04:00"`` 时，昨天 23:00 属于今天）。
    全天任务的「截止」是**日期标记**（服务端写的是当天 00:00），它属于它写的那一天——
    按 00:00 这个时刻去套偏移会把它整天挪到前一个逻辑日。
    """
    return due.date() if all_day else logical_day(due, day_end).label


def format_due(due: datetime | None, *, all_day: bool, now: datetime, day_end: str) -> str:
    """截止时间的人类读法：「今天 18:00」「昨天 09:00」「3 天前」；没有截止时间给「—」。

    全天任务只给日词（「今天」），绝不给「今天 00:00」。哪一天用**逻辑日**判定：
    ``day_end = "04:00"`` 时，凌晨两点看到的昨天 23:00 截止读作「今天 23:00」。
    """
    if due is None:
        return NO_DUE_TEXT
    delta = (logical_day(now, day_end).label - due_day(due, all_day=all_day, day_end=day_end)).days
    if delta == 0:
        word = "今天"
    elif delta == 1:
        word = "昨天"
    elif delta == -1:
        word = "明天"
    elif delta > 1:
        word = f"{delta} 天前"
    else:
        word = f"{-delta} 天后"
    if all_day or abs(delta) > 1:
        return word
    return f"{word} {due.strftime('%H:%M')}"


def _by_due(items: list[TaskItem]) -> list[TaskItem]:
    """有截止时间的按时间升序在前，没有的按标题排在后面。"""
    dated = sorted((item for item in items if item.due is not None), key=lambda item: (item.due, item.title))
    undated = sorted((item for item in items if item.due is None), key=lambda item: (item.title, item.task_id))
    return dated + undated


# ------------------------------------------------------------------ 模糊过滤（t17）


def fuzzy_match(query: str, title: str) -> bool:
    """``query`` 是不是 ``title`` 的**有序子序列**（大小写不敏感）。

    子序列而不是子串：中文标题里隔着字也认（``写报`` 命中 ``写周报``），这才是「模糊」；
    顺序仍然算数（``报写`` 不命中）。空查询命中一切——那就是「没有过滤」。
    """
    if not query:
        return True
    rest = iter(title.casefold())
    return all(char in rest for char in query.casefold())


def filter_groups(groups: Sequence[TaskGroup], query: str) -> tuple[TaskGroup, ...]:
    """按查询筛掉不命中的行；整组都不命中就整组不留。

    只筛未完成任务的那两区（中栏）；底部的已完成区不在 ``groups`` 里，折叠着也不参与光标。
    留下来的行保持引擎给的顺序——排序是引擎的事，这里只做筛。

    组标题上的条数是 ``len(items)``（:class:`TaskGroup.count`），所以整组筛空必须整组丢掉：
    留下一个「今日 · 3 项」的空标题，就是在骗人。空查询原样返回，``Esc`` 因此就是「恢复
    完整列表」。
    """
    if not query:
        return tuple(groups)
    return tuple(
        TaskGroup(kind=group.kind, items=kept)
        for group in groups
        if (kept := tuple(item for item in group.items if fuzzy_match(query, item.title)))
    )


# ------------------------------------------------------------------ 已完成流（t12）


def completed_section(
    tasks: Sequence[TaskSnapshot],
    lists: Sequence[ListSnapshot],
    *,
    now: datetime,
    day_end: str,
    window_hours: int,
) -> CompletedSection:
    """已完成区：窗口 ``[now - window_hours, …]`` 内完成的任务，最近的排在最前。

    「完成于何时」只认服务端的 ``completedTime``（``completed_at``），不认本地那条
    ``status``：那是 ADR-0001 里唯一能被服务端过滤的变化时间戳，也是这条流唯一有意义的
    排序与过滤依据。本地刚按了完成、服务端还没认过的任务因此不会出现在这里——它要等
    下一次已完成流把它带着真正的完成时刻带回来。没有上界：服务端时钟快一点不该让用户
    刚做完的任务消失。

    纯函数：「现在」与窗口大小都从参数进来，这一层不读时钟（t12 的窗口由引擎按注入的
    配置给）。
    """
    window_start = now - timedelta(hours=window_hours)
    names = list_names(lists)
    rows = [
        CompletedItem(
            task_id=snapshot.id,
            title=snapshot.title,
            list_name=names.get(snapshot.list_id, snapshot.list_id),
            completed_at=snapshot.completed_at,
            completed_text=format_due(
                snapshot.completed_at, all_day=False, now=now, day_end=day_end
            ),
        )
        for snapshot in tasks
        if snapshot.completed
        and snapshot.completed_at is not None
        and snapshot.completed_at >= window_start
    ]
    return CompletedSection(
        items=tuple(sorted(rows, key=lambda row: (row.completed_at, row.title), reverse=True))
    )


# ------------------------------------------------------------------ 子任务（t20）

SUBTASK_NORMAL_STATUS = 0
SUBTASK_COMPLETED_STATUS = 1
"""子任务的完成状态是**另一对**取值（``api-contracts.md``）：Normal ``0`` / Completed ``1``。

不是任务级那一对 ``-1/0/2``：拿 ``status == 1`` 判任务完成是错的，拿 ``status == 2``
判子任务完成同样是错的。两对取值只在这里相接，别处一律用这两个常量。
"""


@dataclass(frozen=True)
class SubtaskItem:
    """右栏一行子任务：字段都已经是可以直接画的成品。"""

    subtask_id: str
    title: str
    completed: bool
    due_text: str = NO_DUE_TEXT
    """子任务 ``startDate`` 的人类读法；没有日期就是 :data:`NO_DUE_TEXT`。

    没有日期**不影响这一行存在**：``startDate`` 是可选的，标题与完成状态才是子任务必有
    的两样（工单 #20 的验收标准 #1）。
    """


def subtask_items(
    payload: Mapping[str, Any] | None, *, now: datetime, day_end: str
) -> tuple[SubtaskItem, ...]:
    """一条任务的原文 → 右栏的子任务行。

    只认服务端给的那一份 ``items``（``ChecklistItem``）：``status`` 是 0/1 那一对，日期
    字段叫 ``startDate``（与任务上的 ``dueDate`` 不是同一个名字，见 ``guards``）。

    没有 ``items``、``items`` 不是数组、某一条没有可用的 ``id``——都当作「没有这一行」
    跳过而不是报错：右栏是只读的展示，一条脏数据不该让整个详情栏空掉，也不该拦住
    其它子任务的勾选（勾选要的是 ``id``）。
    """
    if not isinstance(payload, Mapping):
        return ()
    raw = payload.get("items")
    if not isinstance(raw, list):
        return ()
    rows: list[SubtaskItem] = []
    for entry in raw:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("id"), str) or not entry["id"]:
            continue
        due = _subtask_moment(entry.get("startDate"))
        rows.append(
            SubtaskItem(
                subtask_id=entry["id"],
                title=str(entry.get("title") or ""),
                completed=subtask_completed(entry.get("status")),
                due_text=format_due(
                    due, all_day=bool(entry.get("isAllDay")), now=now, day_end=day_end
                ),
            )
        )
    return tuple(rows)


def subtask_completed(status: Any) -> bool:
    """``status`` → 「勾上了没有」。认不出来的一律当作没勾上（与 ``priority`` 同一口径）。

    子任务那一对取值是 0/1（``SUBTASK_COMPLETED_STATUS``），别拿任务级的 2 来比。
    """
    try:
        return int(status or 0) == SUBTASK_COMPLETED_STATUS
    except (TypeError, ValueError):
        return False


def _subtask_moment(value: Any) -> datetime | None:
    """子任务的 ``startDate`` → 时刻；吃不下、或者没有时区偏移就当没有日期。

    没有时区就不猜（那正是「时区写错静默位移」那个 trap），宁可这一行不显示日期。
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None

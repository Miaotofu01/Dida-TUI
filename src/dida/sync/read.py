"""v2 的三种读形状（#33）：清单索引 / 某个容器的任务列表 / 单条任务的详情。

v1 的读入口只有一个 :meth:`~dida.sync.engine.SyncEngine.view`，返回一个为「今日」硬编码了
三个分区的 :class:`~dida.sync.view.TodayView`：未来的任务整条丢掉、没有日期的任务不分清单地
汇成一区、也表达不了「现在打开的是哪个清单」。v2 的三层页面要的是三种形状，这里是它们
的类型与组装纯函数（TUI 只画，判断都在这一层）：

- :func:`list_index` —— **清单索引**：内置视图、自定义视图、真实清单三种行（:class:`ListKind`），
  每行带未完成条数；真实清单还带颜色、项目组、``kind``、``permission``。
- :func:`container_tasks` —— **某个容器的任务列表**：这个清单的**全部**未完成任务（未来的也在），
  外加这个容器里该显示的那部分已完成任务。
- :func:`task_detail` —— **单条任务的详情**：标题、描述、备注、清单、截止、优先级、标签，
  以及只读的重复规则、提醒、子任务与原文里我们不认识的字段。

**收集箱的身份是这一层的核心**（spec 的已实测 API 事实 #2）：服务端的清单索引里没有收集箱，
它的 ``projectId`` 是**每账户不同的一串**（形如 ``inbox`` 加数字），``"inbox"`` 只是请求侧
接受的别名。所以 :func:`resolve_lists` 自己补上那一行，而**归类、分组、计数一律用服务端
返回的那个 id**——缺失的 ``projectId`` 谁也不许再猜成字面量 ``inbox``（猜出来的那个 id
与真实 id 一条都对不上，还会让深链的兜底分支永远不可达）。

「现在」与 ``day_end`` 一律从参数进来（与 :mod:`dida.sync.view` 同一条规矩），这一层不读时钟。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Collection, Mapping, Protocol, Sequence, runtime_checkable

from dida.logical_day import logical_day
from dida.sync.view import (
    INBOX_ID,
    INBOX_NAME,
    CompletedSection,
    ListSnapshot,
    SubtaskItem,
    TaskItem,
    TaskSnapshot,
    by_due,
    completed_section,
    due_day,
    format_due,
    format_tags,
    list_names,
    priority_mark,
    subtask_items,
    task_item,
)

__all__ = [
    "BUILTIN_VIEW_IDS",
    "KNOWN_TASK_FIELDS",
    "ListKind",
    "ListRow",
    "PayloadReader",
    "TaskDetail",
    "TaskList",
    "ViewReader",
    "ViewRow",
    "builtin_view_rows",
    "container_tasks",
    "is_inbox_id",
    "list_index",
    "resolve_lists",
    "task_detail",
]


class ListKind(Enum):
    """清单索引里一行的身份：三种行，用前缀字符区分（#34，用户故事 10）。

    收集箱是 :attr:`LIST` 那一类（它是真实清单，只是置顶显示），靠 ``ListRow.is_inbox`` 认。
    """

    LIST = "list"
    """真实清单（API 叫 project）。"""

    CUSTOM = "custom"
    """用户自建的视图：一组保存下来的过滤条件，只存在本机。"""

    BUILTIN = "builtin"
    """内置视图：今天 / 最近七天 / 所有。"""


BUILTIN_VIEW_IDS: tuple[str, ...] = ("today", "next7", "all")
"""三个内置视图的 id，按清单索引里的顺序（spec：今天 / 最近七天 / 所有）。

它们是**写死的视图定义**（#35 会把求值长全：逾期置顶标红、排序、行里显示所属清单）；
这一层只用它们的身份与成员——行上的条数与进去看到的列表因此不可能对不上。
"""

_BUILTIN_VIEW_NAMES: dict[str, str] = {
    "today": "今天",
    "next7": "最近七天",
    "all": "所有",
}

BUILTIN_VIEW_DAYS = 7
"""「最近七天」的窗口：从当前逻辑日起的七个逻辑日（spec）。"""


def is_inbox_id(value: str) -> bool:
    """这个清单 id 是不是收集箱的。

    两种形状都算：请求侧的字面量别名 ``inbox``，以及服务端返回的那一串（``inbox`` 加一截
    数字，实测形状见 GLOSSARY 的「收集箱」）。**只认这两种**——v1 用的是「含 inbox」这种
    子串匹配，于是 ``my-inbox-list`` 这种真实清单 id 会被当成收集箱（深链拼装因此会开错页面）。

    这不是「把缺失的 id 猜成 inbox」：那是 #33 明令去掉的（见 :func:`resolve_lists`）。
    这里是**认出**服务端给的那个 id。
    """
    lowered = value.strip().lower()
    if lowered == INBOX_ID:
        return True
    if not lowered.startswith(INBOX_ID):
        return False
    return lowered[len(INBOX_ID) :].isdigit()


@dataclass(frozen=True)
class ListRow:
    """清单索引里的一行。字段都是可以直接画的成品（TUI 只画前缀与条数）。"""

    id: str
    """这一行的身份：真实清单是服务端的 project id（收集箱是那串每账户不同的 id）；
    视图是视图自己的 id。"""

    name: str
    kind: ListKind
    unfinished: int
    """这个容器里的未完成条数。"""

    is_inbox: bool = False
    """是不是收集箱那一行（它是客户端自己补的，见 :func:`resolve_lists`）。"""

    color: str | None = None
    """清单颜色（服务端的 ``color``）；视图没有颜色。"""

    group_id: str | None = None
    """项目组 id（服务端的 ``groupId``）；项目组只显示成不可进入的小标题。"""

    project_kind: str | None = None
    """服务端的 ``Project.kind``：``TASK`` / ``NOTE``。NOTE 清单装不了任务（用户故事 23）。"""

    permission: str | None = None
    """服务端的 ``Project.permission``：``write`` / ``read`` / ``comment``（用户故事 24）。"""

    @property
    def enterable(self) -> bool:
        """这一行能不能进去。

        ``kind`` 是 ``NOTE`` 的清单装不了任务，``permission`` 不是 ``write`` 的改不动
        （用户故事 23 / 24）——两种都只标记、不可进入。认不出来的（``None``）不拦：
        缺字段是「不知道」，不是「不行」。
        """
        if self.kind is not ListKind.LIST:
            return True
        if self.project_kind == "NOTE":
            return False
        return self.permission is None or self.permission == "write"


@dataclass(frozen=True)
class ViewRow:
    """清单索引里的一行视图（内置或自定义）：身份、名字，以及它选中的那些任务。

    ``task_ids`` 是**视图求值的结果**：内置视图由 :func:`builtin_view_rows` 算（#35 会把
    求值长全），自定义视图由本地库那一层算（#36 把视图定义落库并求值）。行上的未完成条数
    由 :func:`list_index` 从缓存里数**成员里未完成的那些**——索引里的数字与进去看到的列表
    因此来自同一次求值，不可能对不上（「最近完成」那种视图里也有已完成的成员，所以条数
    不是 ``len(task_ids)``）。
    """

    id: str
    name: str
    task_ids: tuple[str, ...] = ()
    builtin: bool = False


@dataclass(frozen=True)
class TaskList:
    """某个容器的任务列表：全部未完成任务 + 该显示的那部分已完成任务。"""

    container_id: str
    items: tuple[TaskItem, ...] = ()
    """这个容器的**全部**未完成任务，包括截止时间在未来的那些（v1 把它们整条丢掉了）。"""

    completed: CompletedSection = CompletedSection()
    """这个容器里窗口内完成的任务（真实清单才有；视图的成员由视图求值决定）。"""


@dataclass(frozen=True)
class TaskDetail:
    """单条任务的详情页要的全部字段（字段都是可以直接画的成品）。"""

    task_id: str
    title: str
    list_id: str
    list_name: str
    due: datetime | None
    all_day: bool
    due_text: str
    priority: int
    priority_mark: str
    tags: tuple[str, ...]
    tags_text: str
    desc: str
    """服务端的 ``desc``：GLOSSARY 里它是**备注**（与描述是两个独立字段）。"""

    content: str
    """服务端的 ``content``：GLOSSARY 里它是**描述**。"""

    repeat_flag: str = ""
    """重复规则（服务端的 ``repeatFlag``）：只读显示，不改。"""

    reminders: tuple[str, ...] = ()
    """提醒触发器（服务端的 ``reminders``）：只读显示，不改。"""

    subtasks: tuple[SubtaskItem, ...] = ()
    """子任务行（只读，用户故事 32 的「子任务只看不勾」）。"""

    raw: Mapping[str, Any] = field(default_factory=dict)
    """服务端那一份原文，原样保留（未知字段靠它带走，回写不丢字段）。"""

    @property
    def unknown(self) -> Mapping[str, Any]:
        """原文里这一层**不认识**的字段（详情页只读展示；回写靠整份 ``raw``）。"""
        return {key: value for key, value in self.raw.items() if key not in KNOWN_TASK_FIELDS}


KNOWN_TASK_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "projectId",
        "title",
        "isAllDay",
        "completedTime",
        "content",
        "desc",
        "dueDate",
        "items",
        "priority",
        "reminders",
        "tags",
        "repeatFlag",
        "repeatFrom",
        "sortOrder",
        "startDate",
        "status",
        "assigneeUsername",
        "timeZone",
        "kind",
        "parentId",
        "focusSummaries",
        "etag",
    }
)
"""``Task`` 定义里的字段（``api-contracts.md``）加例子里的 ``etag``：认识的不算「未知」。"""


@runtime_checkable
class ViewReader(Protocol):
    """本地库里那些自定义视图行（#36 把视图定义落库）。

    比 :class:`~dida.sync.view.ViewSource` 宽的一件可选能力：只读的替身没有它就是
    「没有自定义视图」，不是错误（与写路径上那几个 ``isinstance`` 门同一条口径）。
    """

    def views(self) -> Sequence[ViewRow]:
        """自定义视图的行，按用户自己的顺序。"""
        ...


@runtime_checkable
class PayloadReader(Protocol):
    """本地那份任务原文的读取口：详情页要的重复规则、提醒、子任务、未知字段都在里面。"""

    def task_payload(self, task_id: str) -> Mapping[str, Any] | None:
        """一条任务的完整原文；本地没有这条任务时 ``None``。"""
        ...


def resolve_lists(
    lists: Sequence[ListSnapshot], tasks: Sequence[TaskSnapshot]
) -> tuple[ListSnapshot, ...]:
    """缓存里的清单 + **客户端自己补的收集箱那一行**，收集箱排在最前。

    服务端的清单索引里没有收集箱（实测事实 #2），所以这一行是客户端补的；它的 id 与
    ``is_inbox`` 来自 :func:`_resolve_inbox`——**服务端返回的那个 id**，不是字面量别名。
    归类、分组、计数随后一律按这个 id 走（``unfinished.get(row.id)``），所以补出来的这一行
    数得到收集箱里的任务。
    """
    inbox_id, inbox_name, absorbed = _resolve_inbox(lists, tasks)
    inbox_row: ListSnapshot | None = None
    rest: list[ListSnapshot] = []
    for row in lists:
        if row.is_inbox or row.id == absorbed:
            if inbox_row is None:  # 收集箱只留一行：别的标记行并进这一行
                inbox_row = replace(row, name=inbox_name, is_inbox=True)
            continue
        rest.append(row)
    if inbox_row is None:
        inbox_row = ListSnapshot(id=inbox_id, name=inbox_name, is_inbox=True)
    return (inbox_row,) + tuple(rest)


def _resolve_inbox(
    lists: Sequence[ListSnapshot], tasks: Sequence[TaskSnapshot]
) -> tuple[str, str, str]:
    """收集箱那一行是谁：``(id, name, 被并进来的那一行的 id)``。

    认的顺序：

    1. 本地库里**被标成收集箱**的那一行——刷新时按服务端返回的 id 写下的，最权威；
    2. 缓存里那个**形如 ``inbox`` 加数字**的 id（清单行或任务上都可能带着它）：v1 时代的
       缓存里没有第 1 条，靠这一条也认得出收集箱——不认的话，那些任务一条都归不进去，
       左栏徽标会是 0 而且不报错；
    3. 都认不出来（空缓存、收集箱里还什么都没有）就用请求侧别名 ``inbox`` 占位：这一行
       下面没有任何任务可归类，等学到服务端那一串自然换成它。

    **不猜**的是任务的归属：缺失的 ``projectId`` 不会被写成本地字面量（见
    :func:`dida.sync.refresh` 的取数侧与 ``Store`` 的 ``_snapshot``）。
    """
    flagged = [row for row in lists if row.is_inbox]
    if flagged:
        row = next((item for item in flagged if is_inbox_id(item.id)), flagged[0])
        return row.id, (row.name or INBOX_NAME), row.id

    candidates = [row.id for row in lists if is_inbox_id(row.id)]
    candidates += [item.list_id for item in tasks if is_inbox_id(item.list_id)]
    if candidates:
        # 服务端那一串优先于字面量别名：别名只是请求侧的叫法，不是身份。
        chosen = sorted(candidates, key=lambda value: value.lower() == INBOX_ID)[0]
        named = next((row.name for row in lists if row.id == chosen and row.name != row.id), "")
        return chosen, (named or INBOX_NAME), chosen

    return INBOX_ID, INBOX_NAME, INBOX_ID


def list_index(
    lists: Sequence[ListSnapshot],
    tasks: Sequence[TaskSnapshot],
    *,
    now: datetime,
    day_end: str,
    views: Sequence[ViewRow] = (),
) -> tuple[ListRow, ...]:
    """清单索引：收集箱置顶 → 内置视图 → 自定义视图 → 真实清单（照缓存给的顺序）。

    ``views`` 是本地库里那些自定义视图行（#36）；内置视图这一层自己算
    （:func:`builtin_view_rows`）。三种行的条数都从同一份缓存里数，所以索引里的数字与
    进去看到的列表不可能对不上。
    """
    resolved = resolve_lists(lists, tasks)
    unfinished = _unfinished_counts(tasks)
    unfinished_ids = {snapshot.id for snapshot in tasks if not snapshot.completed}
    rows = [_list_row(resolved[0], unfinished)]
    rows += [
        _view_row(view, unfinished_ids)
        for view in builtin_view_rows(tasks, now=now, day_end=day_end) + tuple(views)
    ]
    rows += [_list_row(row, unfinished) for row in resolved[1:]]
    return tuple(rows)


def container_tasks(
    container_id: str,
    lists: Sequence[ListSnapshot],
    tasks: Sequence[TaskSnapshot],
    *,
    now: datetime,
    day_end: str,
    window_hours: int,
    views: Sequence[ViewRow] = (),
) -> TaskList:
    """某个容器的任务列表：全部未完成任务（未来的也在）+ 该显示的那部分已完成任务。

    认不出来的容器（清单被删了、光标停在一条已经不在的行上）给空列表，不是错误：
    读路径上没有可读的东西就是没有（与空缓存给空视图同一条口径）。
    """
    resolved = resolve_lists(lists, tasks)
    names = list_names(resolved)
    row = next(
        (
            item
            for item in list_index(lists, tasks, now=now, day_end=day_end, views=views)
            if item.id == container_id
        ),
        None,
    )
    if row is None:
        return TaskList(container_id=container_id)

    if row.kind is ListKind.LIST:
        members = [
            snapshot
            for snapshot in tasks
            if not snapshot.completed and snapshot.list_id == container_id
        ]
        completed = completed_section(
            [snapshot for snapshot in tasks if snapshot.list_id == container_id],
            resolved,
            now=now,
            day_end=day_end,
            window_hours=window_hours,
        )
    else:
        # 视图不是容器：成员由视图求值给（#35 的内置视图 / #36 的自定义视图）。
        members = _view_members(container_id, tasks, now=now, day_end=day_end, views=views)
        completed = CompletedSection()
    items = tuple(
        by_due([task_item(snapshot, names, now=now, day_end=day_end) for snapshot in members])
    )
    return TaskList(container_id=container_id, items=items, completed=completed)


def task_detail(
    snapshot: TaskSnapshot,
    payload: Mapping[str, Any] | None,
    names: Mapping[str, str],
    *,
    now: datetime,
    day_end: str,
) -> TaskDetail:
    """一条任务 → 详情页的成品字段（含原文里我们不认识的字段）。"""
    raw: Mapping[str, Any] = payload if isinstance(payload, Mapping) else {}
    return TaskDetail(
        task_id=snapshot.id,
        title=snapshot.title,
        list_id=snapshot.list_id,
        list_name=names.get(snapshot.list_id, snapshot.list_id),
        due=snapshot.due,
        all_day=snapshot.all_day,
        due_text=format_due(snapshot.due, all_day=snapshot.all_day, now=now, day_end=day_end),
        priority=snapshot.priority,
        priority_mark=priority_mark(snapshot.priority),
        tags=snapshot.tags,
        tags_text=format_tags(snapshot.tags),
        desc=snapshot.desc,
        content=snapshot.content,
        repeat_flag=str(raw.get("repeatFlag") or ""),
        reminders=tuple(str(item) for item in _as_sequence(raw.get("reminders"))),
        subtasks=subtask_items(raw, now=now, day_end=day_end),
        raw=raw,
    )


def builtin_view_rows(
    tasks: Sequence[TaskSnapshot], *, now: datetime, day_end: str
) -> tuple[ViewRow, ...]:
    """三个内置视图的行：今天（逾期 ∪ 今天到期）/ 最近七天 / 所有。

    只算**成员**（谁在这个视图里）；行里的排序、逾期置顶标红、所属清单名是 #35 的求值。
    """
    label = logical_day(now, day_end).label
    members: dict[str, list[str]] = {view_id: [] for view_id in BUILTIN_VIEW_IDS}
    for snapshot in tasks:
        if snapshot.completed:
            continue
        for view_id in BUILTIN_VIEW_IDS:
            if _in_builtin_view(view_id, snapshot, label=label, day_end=day_end):
                members[view_id].append(snapshot.id)
    return tuple(
        ViewRow(
            id=view_id,
            name=_BUILTIN_VIEW_NAMES[view_id],
            task_ids=tuple(members[view_id]),
            builtin=True,
        )
        for view_id in BUILTIN_VIEW_IDS
    )


def _in_builtin_view(
    view_id: str, snapshot: TaskSnapshot, *, label: date, day_end: str
) -> bool:
    """这条任务在不在这个内置视图里（逻辑日判定走 :func:`dida.sync.view.due_day`）。"""
    if view_id == "all":
        return True
    if snapshot.due is None:
        return False
    day = due_day(snapshot.due, all_day=snapshot.all_day, day_end=day_end)
    if view_id == "today":
        return day <= label  # 逾期 ∪ 截止于当前逻辑日
    return label <= day < label + timedelta(days=BUILTIN_VIEW_DAYS)


def _unfinished_counts(tasks: Sequence[TaskSnapshot]) -> dict[str, int]:
    """未完成条数，按清单 id 数（已完成的不算）。"""
    counts: dict[str, int] = {}
    for snapshot in tasks:
        if not snapshot.completed:
            counts[snapshot.list_id] = counts.get(snapshot.list_id, 0) + 1
    return counts


def _view_members(
    view_id: str,
    tasks: Sequence[TaskSnapshot],
    *,
    now: datetime,
    day_end: str,
    views: Sequence[ViewRow],
) -> list[TaskSnapshot]:
    """视图的成员：求值结果给的那些任务 id（求值里已经不在缓存里的 id 跳过）。"""
    rows = builtin_view_rows(tasks, now=now, day_end=day_end) + tuple(views)
    row = next((item for item in rows if item.id == view_id), None)
    if row is None:
        return []
    by_id = {snapshot.id: snapshot for snapshot in tasks}
    return [by_id[task_id] for task_id in row.task_ids if task_id in by_id]


def _view_row(view: ViewRow, unfinished_ids: Collection[str]) -> ListRow:
    """视图行 → 索引行。条数是**成员里未完成的那些**（「最近完成」那种视图里也有已完成的）。"""
    return ListRow(
        id=view.id,
        name=view.name,
        kind=ListKind.BUILTIN if view.builtin else ListKind.CUSTOM,
        unfinished=sum(1 for task_id in view.task_ids if task_id in unfinished_ids),
    )


def _list_row(snapshot: ListSnapshot, unfinished: Mapping[str, int]) -> ListRow:
    return ListRow(
        id=snapshot.id,
        name=snapshot.name,
        kind=ListKind.LIST,
        unfinished=unfinished.get(snapshot.id, 0),
        is_inbox=snapshot.is_inbox,
        color=snapshot.color,
        group_id=snapshot.group_id,
        project_kind=snapshot.kind,
        permission=snapshot.permission,
    )


def _as_sequence(value: Any) -> Sequence[Any]:
    """``reminders`` 那样的数组字段：不是数组就当没有（脏数据不该让详情页空掉）。"""
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    return ()

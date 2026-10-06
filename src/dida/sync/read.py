"""v2 的三种读形状（#33）：清单索引 / 某个容器的任务列表 / 单条任务的详情。

v1 的读入口只有一个为「今日」硬编码的 ``view() -> TodayView``（未来的任务整条丢掉、没有日期的
任务不分清单地汇成一区、也表达不了「现在打开的是哪个清单」），#58 把它连同那个类型一起删掉了。
v2 的三层页面要的是三种形状，这里是它们的类型与组装纯函数（TUI 只画，判断都在这一层）：

- :func:`list_index` —— **清单索引**：内置视图、自定义视图、真实清单三种行（:class:`ListKind`），
  每行带未完成条数；真实清单还带颜色、项目组、``kind``、``permission``。
- :func:`container_tasks` —— **某个容器的任务列表**：这个清单的**全部**未完成任务（未来的也在），
  外加这个容器里该显示的那部分已完成任务；视图那个容器的成员与顺序由**视图求值**给
  （:mod:`dida.sync.views`，#35），它不是一个容器。
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
from datetime import datetime
from enum import Enum
from typing import Any, Collection, Mapping, Protocol, Sequence, runtime_checkable

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
    format_due,
    format_tags,
    list_names,
    priority_mark,
    subtask_items,
    task_item,
)
from dida.sync.views import (
    ViewDefinition,
    builtin_view_definitions,
    evaluate_view,
    implied_due_for,
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
    "custom_view_rows",
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


BUILTIN_VIEW_IDS: tuple[str, ...] = tuple(
    definition.id for definition in builtin_view_definitions()
)
"""三个内置视图的 id，按清单索引里的顺序（spec：今天 / 最近七天 / 所有）。

定义本身（过滤条件与名字）在 :mod:`dida.sync.views`：它们是**写死的视图定义**，和自定义
视图走同一条求值路径（#35）。这一层只用它们的身份与成员——行上的条数与进去看到的列表
因此不可能对不上。
"""


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

    ``task_ids`` 是**视图求值的结果**：内置视图由 :func:`builtin_view_rows` 算，自定义视图由
    :func:`custom_view_rows` 算（#36：定义来自本地库，求值走同一个 ``evaluate_view``）。
    行上的未完成条数由 :func:`list_index` 从缓存里数**成员里未完成的那些**——索引里的数字与
    进去看到的列表因此来自同一次求值，不可能对不上（「最近完成」那种视图里也有已完成的成员，
    所以条数不是 ``len(task_ids)``）。

    ``definition`` 是这一行的**过滤条件**（内置的是三个写死的定义，自定义的是本地库读出来的
    那一份）：行上的身份、名字、成员都是它算出来的，而「在这个视图里新建的任务带不带日期」
    同样是它的语义（:func:`dida.sync.views.implied_due_for`）。所以它随行一起上来——读模型
    因此按**定义**回答，而不是按 id 去三个内置定义里查（#58 的 S1：那样查的话，窗口与
    「今天」完全一样的自建视图会拿到 ``None``）。``None`` = 这一行没有定义（只可能是手工
    拼出来的行），那时什么都不隐含。
    """

    id: str
    name: str
    task_ids: tuple[str, ...] = ()
    builtin: bool = False
    definition: ViewDefinition | None = None


@dataclass(frozen=True)
class TaskList:
    """某个容器的任务列表：全部未完成任务 + 该显示的那部分已完成任务。"""

    container_id: str
    items: tuple[TaskItem, ...] = ()
    """这个容器的**全部**未完成任务，包括截止时间在未来的那些（v1 把它们整条丢掉了）。

    视图里这一份的顺序由**视图求值**给（#35）：逾期置顶那份顺序是求值算出来的，这里不再
    重排一遍。
    """

    completed: CompletedSection = CompletedSection()
    """这个容器里窗口内完成的任务（真实清单才有；视图的成员由视图求值决定）。"""

    shows_list_name: bool = False
    """这一屏要不要在每条任务上写出所属清单名。

    **视图不是容器**，同一个清单名在这里重复出现是必要信息（用户故事 34 / 58）；真实清单
    里那个名字一整屏都写着，重复一百遍只是噪音。判断归读模型——页面自己去猜「这个容器
    是不是视图」就又多了一份会漂移的判断。
    """

    implied_due: datetime | None = None
    """在这个容器里新建一条任务时，它自动该带上的截止时间（``None`` = 不带，#39）。

    用户故事 35：「如果视图隐含了日期（比如在「今天」里建），新任务自动带上那个日期」。
    答案出自这个容器的**定义**（:func:`dida.sync.views.implied_due_for` 逐档写下了那五档
    截止条件各隐含什么）：今天在截止窗口里、而且今天就是它最新的那一天（``last == 0``）
    才隐含，隐含的就是当前逻辑日那一个日期标记。所以内置「今天」与窗口一样的**自建**视图
    给同一天（#58 的 S1），而真实清单、另外两个内置视图与其余几档截止条件都不隐含。

    它与 :attr:`shows_list_name` 是同一个判断的两面（两者都由「这个容器是不是视图」决定），
    所以一起在这里给：调用方读这一个字段就够，不必自己认识视图。
    """


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
    """本地库里那些自定义视图的**定义**（#36 把视图定义落库）。

    比 :class:`~dida.sync.view.ViewSource` 宽的一件可选能力：只读的替身没有它就是
    「没有自定义视图」，不是错误（与写路径上那几个 ``isinstance`` 门同一条口径）。

    给的是**定义**而不是算好的成员：成员要「全量缓存 + 当前逻辑日」才算得出来
    （``evaluate_view``），而本地副本手上没有逻辑日——它与 :mod:`dida.sync.view` 那一层
    同一条规矩，不读时钟。求值因此发生在引擎里（:func:`custom_view_rows`），与内置视图
    走的是同一个函数。
    """

    def view_definitions(self) -> Sequence[ViewDefinition]:
        """自定义视图的定义，按用户自己的顺序。"""
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
    inboxes = [row for row in lists if row.is_inbox or row.id == absorbed]
    rest = [row for row in lists if not (row.is_inbox or row.id == absorbed)]
    if inboxes:
        # 并成一行，id 用认出来的那一个（库里可能同时留着旧的字面量行与服务端那一串的行）。
        chosen = next((row for row in inboxes if row.id == inbox_id), inboxes[0])
        inbox_row = replace(chosen, id=inbox_id, name=inbox_name, is_inbox=True)
    else:
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
        row = sorted(flagged, key=lambda item: _inbox_rank(item.id))[0]
        return row.id, (row.name or INBOX_NAME), row.id

    candidates = [row.id for row in lists if is_inbox_id(row.id)]
    candidates += [item.list_id for item in tasks if is_inbox_id(item.list_id)]
    if candidates:
        chosen = sorted(candidates, key=_inbox_rank)[0]
        named = next((row.name for row in lists if row.id == chosen and row.name != row.id), "")
        return chosen, (named or INBOX_NAME), chosen

    return INBOX_ID, INBOX_NAME, INBOX_ID


def _inbox_rank(value: str) -> int:
    """收集箱身份的优先次序：服务端那一串（0）→ 请求侧别名（1）→ 别的（2）。

    别名只是请求侧的叫法，不是身份，所以它排在服务端返回的那一串后面（两者同时出现在
    缓存里时——v1 的旧行加上 #33 刷新写的新行——认服务端那一串）。
    """
    if value.strip().lower() == INBOX_ID:
        return 1
    return 0 if is_inbox_id(value) else 2


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
        shows_list_name = False
        implied_due = None
        items = tuple(
            by_due([task_item(snapshot, names, now=now, day_end=day_end) for snapshot in members])
        )
    else:
        # 视图不是容器：成员与顺序都由视图求值给（#35 的内置视图 / #36 的自定义视图）。
        view_row = _view_row_for(container_id, tasks, now=now, day_end=day_end, views=views)
        members = _members_of(view_row, tasks)
        completed = CompletedSection()
        shows_list_name = True
        implied_due = _implied_due(view_row, now=now, day_end=day_end)
        items = tuple(task_item(snapshot, names, now=now, day_end=day_end) for snapshot in members)
    return TaskList(
        container_id=container_id,
        items=items,
        completed=completed,
        shows_list_name=shows_list_name,
        implied_due=implied_due,
    )


def _implied_due(row: ViewRow | None, *, now: datetime, day_end: str) -> datetime | None:
    """这个视图隐含的日期（#39 / #58）：在它里面新建的任务自动带上的那个截止时间。

    **按定义回答，不按身份回答**：判断整个在
    :func:`dida.sync.views.implied_due_for` 一处（它逐档写下了那五档截止条件各隐含什么），
    这里只把这一行的定义递过去。所以窗口与内置「今天」一样的自建视图拿到的是同一天——
    「内置与自定义视图走同一条求值路径」这句话在读模型这一层是有内容的，不是口号（#58 的
    S1：原来这里按 id 去 ``builtin_view_definitions()`` 里查，自建的一律 ``None``）。

    ``row`` 是 ``None``（容器认不出来）或者这一行没有定义时都不隐含：不知道就什么都不带。
    """
    if row is None or row.definition is None:
        return None
    return implied_due_for(row.definition, now=now, day_end=day_end)


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
    """三个内置视图的行：今天（逾期 ∪ 今天到期）/ 最近七天 / 所有（#35 把求值长全）。

    它们就是**三个写死的视图定义**，走的是和自定义视图**同一条**求值路径
    （:func:`dida.sync.views.evaluate_view`）——这里没有 ``if view_id == ...`` 那种分支，
    所以「今天 = 逾期 ∪ 今天到期」「最近七天 = 七个逻辑日」这些判断只有定义那一处。

    ``task_ids`` 是求值给的**顺序**（#35）：行上的条数与进去看到的列表来自同一次求值，
    逾期置顶这件事也因此在索引与列表里是同一个答案。
    """
    return tuple(
        ViewRow(
            id=definition.id,
            name=definition.name,
            task_ids=tuple(
                item.snapshot.id
                for item in evaluate_view(definition, tasks, now=now, day_end=day_end)
            ),
            builtin=True,
            definition=definition,
        )
        for definition in builtin_view_definitions()
    )


def custom_view_rows(
    definitions: Sequence[ViewDefinition],
    tasks: Sequence[TaskSnapshot],
    *,
    now: datetime,
    day_end: str,
) -> tuple[ViewRow, ...]:
    """用户建的那些视图的行（#36）：**同一条**求值路径，只是定义来自本地库。

    与 :func:`builtin_view_rows` 是同一个形状、同一个 :func:`~dida.sync.views.evaluate_view`
    ——区别只有「定义从哪来」：那里是三个写死的，这里是本地库读出来的。所以「内置视图与
    自定义视图共用同一条求值路径」这句话在这一层就是字面意思，而视图的成员、顺序、条数
    不可能与内置视图有任何算法上的分别（验收标准的最后一条）。
    """
    return tuple(
        ViewRow(
            id=definition.id,
            name=definition.name,
            task_ids=tuple(
                item.snapshot.id
                for item in evaluate_view(definition, tasks, now=now, day_end=day_end)
            ),
            builtin=False,
            definition=definition,
        )
        for definition in definitions
    )


def _unfinished_counts(tasks: Sequence[TaskSnapshot]) -> dict[str, int]:
    """未完成条数，按清单 id 数（已完成的不算）。"""
    counts: dict[str, int] = {}
    for snapshot in tasks:
        if not snapshot.completed:
            counts[snapshot.list_id] = counts.get(snapshot.list_id, 0) + 1
    return counts


def _view_row_for(
    view_id: str,
    tasks: Sequence[TaskSnapshot],
    *,
    now: datetime,
    day_end: str,
    views: Sequence[ViewRow],
) -> ViewRow | None:
    """这个容器是哪一个视图行（内置那三行 + 本地库那几行里找）；认不出来就是 ``None``。

    行上带着它的**定义**（:attr:`ViewRow.definition`），所以调用方读到的成员与隐含日期都
    出自同一份条件——成员走 :func:`_members_of`，隐含日期走 :func:`_implied_due`。
    """
    rows = builtin_view_rows(tasks, now=now, day_end=day_end) + tuple(views)
    return next((item for item in rows if item.id == view_id), None)


def _members_of(row: ViewRow | None, tasks: Sequence[TaskSnapshot]) -> list[TaskSnapshot]:
    """这一行的成员：求值结果给的那些任务 id，**按求值给的顺序**（已经不在缓存里的 id 跳过）。

    顺序是承重的：视图的排序（逾期置顶、已完成沉底）由求值决定，这里再排一遍就把它盖掉了。
    """
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

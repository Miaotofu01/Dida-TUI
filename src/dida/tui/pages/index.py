"""层一：清单列表页——启动落在这里。

一屏里同时有三种行（用户故事 10）：**内置视图**、**自建视图**、**真实清单**。它们不是同一种
东西，所以各自一个前缀字符——**不靠颜色单独承担**（色弱、``NO_COLOR``、只有 8 色的终端上
颜色全都不算数）。收集箱是客户端自己补的那一行（服务端的清单索引里没有它），置顶显示并
有自己一眼能认出来的记号。真实清单按项目组归拢，项目组是**不可进入的小标题**：光标越过它，
``enter`` 永远落不到它上面。

``kind`` 是 ``NOTE`` 的清单装不了任务、``permission`` 不是 ``write`` 的改不动（用户故事
23/24）：两种都标记出来且**进不去**——进去只会看到一屏空白，或者改不动却不知道为什么。

可不可进入是引擎给的判断（:attr:`dida.sync.read.ListRow.enterable`），这一页只画与转发：
按 ``enter`` 时不可进入的行只报一句「进不去」，不进下一层。
"""

from __future__ import annotations

from typing import Mapping, Sequence

from rich.text import Text
from textual.message import Message

from dida.sync.engine import (
    COMPLETED_DAYS_CHOICES,
    COMPLETION_CHOICES,
    DUE_CHOICES,
    LIST_COLORS,
    VIEW_COMPLETED_DAYS_FIELD,
    VIEW_COMPLETION_FIELD,
    VIEW_DUE_FIELD,
    VIEW_LISTS_FIELD,
    VIEW_NAME_FIELD,
    VIEW_PRIORITY_FIELD,
    VIEW_TAGS_FIELD,
    ListKind,
    ListRow,
    ViewChoice,
    ViewDefinition,
    view_form_values,
)
from dida.tui import messages, theme
from dida.tui.keys import LAYER_INDEX, bindings_for
from dida.tui.overlays import FORM_HINT, FormField, FormOption
from dida.tui.pages.base import EMPTY_STYLE, CursorPage, Row, empty_row, rule_row

__all__ = [
    "BUILTIN_MARK",
    "BLOCKED_MARK",
    "CUSTOM_MARK",
    "DEFAULT_COLOR_OPTION",
    "INBOX_MARK",
    "KIND_LIST",
    "KIND_VIEW",
    "LIST_COLOR_FIELD",
    "LIST_MARK",
    "LIST_NAME_FIELD",
    "NEW_KIND_FIELD",
    "IndexPage",
    "group_by_project",
    "list_color_options",
    "list_form_fields",
    "list_line",
    "list_scope_ids",
    "list_write_refusal",
    "mark_of",
    "new_kind_fields",
    "project_heading",
    "view_form_fields",
    "view_form_hint",
    "view_write_refusal",
]

INBOX_MARK = theme.INBOX_MARK
"""收集箱：它是真实清单，但置顶显示，给它一个一眼能认出来的记号。"""

BUILTIN_MARK = theme.BUILTIN_MARK
"""内置视图（今天 / 最近七天 / 所有）。"""

CUSTOM_MARK = theme.CUSTOM_MARK
"""用户自建的视图：一组只存在本机的过滤条件。"""

LIST_MARK = theme.LIST_MARK
"""真实清单（API 叫 project）。

原来是 ``☰``：rich 量它 2 格而 Unicode 说它中性宽度，每一行会比终端实际画出来的宽一格
（今天没有对齐列所以看不出来，一旦有列就整列歪）。记号都住在 :mod:`dida.tui.theme`，
宽度由 ``tests/test_theme.py`` 守着。
"""

BLOCKED_MARK = f"{theme.BLOCKED_GLYPH} 不可进入"
"""进不去的清单（``kind`` 是 NOTE 或没有写权限）的记号。"""

HEADING_STYLE = theme.MUTED
"""项目组小标题那一档：安静的，不可停光标，怎么画都不影响光标。"""


def mark_of(row: ListRow) -> str:
    """这一行的前缀字符：三种行各自的记号（收集箱再多一个自己的）。"""
    if row.is_inbox:
        return INBOX_MARK
    if row.kind is ListKind.BUILTIN:
        return BUILTIN_MARK
    if row.kind is ListKind.CUSTOM:
        return CUSTOM_MARK
    return LIST_MARK


def list_line(row: ListRow) -> Text:
    """一行清单：前缀字符 + 名字 + 未完成条数（+ 进不去的记号）。

    条数与「不可进入」是**注解**，所以它们是暗的那一档；名字不带上色——光标压上来时整行
    才换成强调色（:meth:`~dida.tui.pages.base.CursorPage._redraw`），一屏里只有一行是亮的。
    """
    text = Text()
    text.append(f"{mark_of(row)} {row.name}  ")
    text.append(str(row.unfinished), style=EMPTY_STYLE)
    if not row.enterable:
        text.append(f"  {BLOCKED_MARK}", style=EMPTY_STYLE)
    return text


def project_heading(group_id: str) -> Text:
    """项目组小标题（不可进入）：一条暗色的抬头，下面跟一条通栏细线。

    服务端的清单索引只给 ``groupId``，没有组名（``GET /open/v1/project`` 的 ``Project`` 上
    只有这一个字段）——所以小标题如实显示那个 id，而不是编一个名字出来。
    """
    return theme.styled(f"项目组 {group_id}", HEADING_STYLE)


def group_by_project(
    rows: Sequence[ListRow],
) -> tuple[tuple[str | None, tuple[ListRow, ...]], ...]:
    """按项目组归拢：同一个组的真实清单凑到一起，桶摆在它第一次出现的地方。

    视图（内置与自建）不属于任何项目组，原地不动。没有 ``groupId`` 的清单也不凭空归组——
    就摆在它原来的位置上。返回值里 ``None`` 那一档表示「这一行没有组」。
    """
    seen: set[str] = set()
    out: list[tuple[str | None, tuple[ListRow, ...]]] = []
    for row in rows:
        group = row.group_id if row.kind is ListKind.LIST else None
        if group is None:
            out.append((None, (row,)))
        elif group not in seen:
            seen.add(group)
            out.append(
                (
                    group,
                    tuple(
                        item
                        for item in rows
                        if item.kind is ListKind.LIST and item.group_id == group
                    ),
                )
            )
    return tuple(out)


LIST_NAME_FIELD = "name"
"""清单表单里「名字」那一格的名字（表单浮层按字段名把值交回来，#42）。"""

LIST_COLOR_FIELD = "color"
"""清单表单里「颜色」那一格的名字。"""

DEFAULT_COLOR_OPTION = FormOption("", "默认")
"""颜色那一格的第一档：**不挑**颜色。

它的值是空串，于是请求体里根本不出现 ``color``（服务端自己挑一档默认色）——与「把颜色
清空」不是一回事，文档没写清空该发什么。
"""


def list_color_options() -> tuple[FormOption, ...]:
    """颜色那几档：值原样发给服务端，屏幕上只写它的名字。

    终端**不画**这些颜色（颜色跟随用户自己的主题，ADR-0007 一）；这一档表是 API 数据，
    所以它住在 :mod:`dida.sync.lists`，不在 :mod:`dida.tui.theme`。
    """
    return tuple(FormOption(color.value, color.label) for color in LIST_COLORS)


def list_form_fields(row: ListRow | None = None) -> tuple[FormField, ...]:
    """清单表单的字段：名字 + 颜色（新建时 ``row`` 是 ``None``）。

    改的时候两格都填上**当前值**：颜色尤其要紧——清单上那个颜色可能是手机端挑的，
    客户端这一档里没有它，而「只想改个名字」不该顺手把颜色换掉（字段层会把认不出来的值
    自己加成一档，照原样交回来）。
    """
    return (
        FormField(
            name=LIST_NAME_FIELD,
            label="名字",
            value="" if row is None else row.name,
        ),
        FormField(
            name=LIST_COLOR_FIELD,
            label="颜色",
            options=(DEFAULT_COLOR_OPTION, *list_color_options()),
            value="" if row is None else (row.color or ""),
        ),
    )


def list_write_refusal(row: ListRow) -> str | None:
    """这一行能不能按**清单**改 / 删；不能的话是**哪一句**话，能的话是 ``None``。

    三种行里只有「改得动的真实清单」能动：

    - 视图不是清单（视图那三条归 #36 的 :func:`view_write_refusal`，这里只是兜底），
    - 收集箱那一行是本机补的默认落点（改了下次刷新就变回去），
    - ``permission`` 不是 ``write`` 的清单改不动（用户故事 24）。
    """
    if row.kind is not ListKind.LIST:
        return messages.VIEW_ROW_MESSAGE
    if row.is_inbox:
        return messages.INBOX_LIST_MESSAGE
    if row.permission not in (None, "write"):
        return messages.readonly_list_message(row)
    return None


def view_write_refusal(row: ListRow) -> str | None:
    """这一行能不能按**视图**改 / 删；不能的话是哪句话（#36）。

    只有**内置**视图改不动：今天 / 最近七天 / 所有是三个写死的定义，本地库里没有对应的行
    ——既没有「改它」这回事，删掉它也没有落点。自建视图是本地库里的行，改得动也删得掉。
    """
    if row.kind is ListKind.BUILTIN:
        return messages.BUILTIN_VIEW_MESSAGE
    return None


KIND_LIST = "list"
KIND_VIEW = "view"
NEW_KIND_FIELD = "kind"
"""``n`` 那一句「清单还是视图」的选择器（#36 的验收标准 1）。

它是**界面**数据（两个选项怎么叫、默认选哪个），不是 API 数据，所以住在页面这一层
——与清单颜色那一档表（API 数据，住 ``sync/lists.py``）分得清清楚楚。
"""


def new_kind_fields() -> tuple[FormField, ...]:
    """``n`` 先问的那一格：清单还是视图（第一档是清单，``enter`` 直接过）。"""
    return (
        FormField(
            name=NEW_KIND_FIELD,
            label="类型",
            options=(FormOption(KIND_LIST, "清单"), FormOption(KIND_VIEW, "视图")),
            value=KIND_LIST,
        ),
    )


def view_form_hint() -> str:
    """视图表单底部那几行：先说清「只存在本机」，再说多个值怎么写，最后是键位。

    「只存在本机」放在**最前面**：它是这一屏最容易被误解的一件事（用户会以为换个设备还能
    看见），所以它得在第一行，而不是垫在键位说明后面。
    """
    return (
        f"{messages.VIEW_LOCAL_ONLY}{messages.VIEW_LOCAL_ONLY_WHY}\n"
        f"{messages.VIEW_FORM_SYNTAX}\n"
        f"{FORM_HINT}"
    )


def _options(choices: Sequence[ViewChoice]) -> tuple[FormOption, ...]:
    """领域那一层的取值表 → 控件那一份（``sync`` 不许 import Textual，翻在这里翻）。"""
    return tuple(FormOption(choice.value, choice.label) for choice in choices)


def list_scope_ids(rows: Sequence[ListRow]) -> dict[str, str]:
    """「清单范围」那一格认得的词 → 清单 id：**名字与 id 都能写**（#36）。

    只收真实清单：视图不是「清单范围」的候选（拿视图当范围没有意义），收集箱是真实清单
    （它是客户端补出来的一行），所以它在里面。名字与 id 撞车时 id 赢——id 是身份。
    """
    scope: dict[str, str] = {}
    for row in rows:
        if row.kind is not ListKind.LIST:
            continue
        scope[row.id] = row.id
        if row.name:
            scope.setdefault(row.name, row.id)
    return scope


def view_form_fields(
    definition: ViewDefinition | None = None,
    *,
    rows: Sequence[ListRow] = (),
    values: Mapping[str, str] | None = None,
) -> tuple[FormField, ...]:
    """视图表单的字段：名字 + 六个过滤维度（``definition`` 是 ``None`` 就是新建）。

    ``values`` 是**用户刚填的那一份**：表单被拒（认不出的清单名之类）之后重新打开时原样
    还给他，省得七个格子重填一遍。给了它就不看 ``definition``。

    清单范围那一格显示的是**名字**（用户认的是名字），交回去的仍是 id；完成状态与完成时间
    是**两格**——工单的 AC 说的就是「按完成状态与完成时间筛」，合成一格就表达不了
    「已完成 + 最近七天」以外的组合。
    """
    if values is not None:
        current = dict(values)
    else:
        current = view_form_values(
            definition or ViewDefinition(id="", name=""),
            names={row.id: row.name for row in rows if row.kind is ListKind.LIST},
        )
    return (
        FormField(name=VIEW_NAME_FIELD, label="名字", value=current[VIEW_NAME_FIELD]),
        FormField(
            name=VIEW_LISTS_FIELD,
            label="清单范围",
            value=current[VIEW_LISTS_FIELD],
        ),
        FormField(
            name=VIEW_DUE_FIELD,
            label="截止时间",
            options=_options(DUE_CHOICES),
            value=current[VIEW_DUE_FIELD],
        ),
        FormField(
            name=VIEW_PRIORITY_FIELD,
            label="优先级",
            value=current[VIEW_PRIORITY_FIELD],
        ),
        FormField(name=VIEW_TAGS_FIELD, label="标签", value=current[VIEW_TAGS_FIELD]),
        FormField(
            name=VIEW_COMPLETION_FIELD,
            label="完成状态",
            options=_options(COMPLETION_CHOICES),
            value=current[VIEW_COMPLETION_FIELD],
        ),
        FormField(
            name=VIEW_COMPLETED_DAYS_FIELD,
            label="完成时间",
            options=_options(COMPLETED_DAYS_CHOICES),
            value=current[VIEW_COMPLETED_DAYS_FIELD],
        ),
    )


class IndexPage(CursorPage):
    """清单列表页：内置视图、自建视图、真实清单、项目组小标题。"""

    LAYER = LAYER_INDEX
    BINDINGS = bindings_for(LAYER)
    EMPTY_TEXT = messages.EMPTY_INDEX_MESSAGE

    class Entered(Message):
        """用户按了 ``enter``：进这个容器（清单或视图）。"""

        def __init__(self, container_id: str) -> None:
            self.container_id = container_id
            super().__init__()

    class Refused(Message):
        """用户按了 ``enter``，但这一行进不去（NOTE 清单 / 没有写权限）。"""

        def __init__(self, message: str) -> None:
            self.message = message
            super().__init__()

    class NewList(Message):
        """用户按了 ``n``：新建（#36 起**先问一句「清单还是视图」**，外层开哪张表单由答案定）。"""

    class EditList(Message):
        """用户按了 ``e``：改这一行——清单给清单那张表单，自建视图给条件表单（#42 / #36）。"""

        def __init__(self, row_id: str) -> None:
            self.row_id = row_id
            super().__init__()

    class DeleteList(Message):
        """用户按了 ``d``：删这一行（外层先问一句；视图也是一次 ``y/n``）。"""

        def __init__(self, row_id: str) -> None:
            self.row_id = row_id
            super().__init__()

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._rows_by_id: dict[str, ListRow] = {}
        self._list_rows: tuple[ListRow, ...] = ()

    def row(self, row_id: str) -> ListRow | None:
        """这一行是谁（光标停在哪一行、要改哪一行，外层都从这里问）。"""
        return self._rows_by_id.get(row_id)

    def rows(self) -> tuple[ListRow, ...]:
        """这一页现在的**清单索引行**，按屏幕顺序（视图表单要拿真实清单当「清单范围」的候选）。

        ⚠ 名字不能是 ``self._rows``：那是 :class:`~dida.tui.pages.base.CursorPage` 存**画出来的
        行**的地方（``set_rows`` 写的），盖掉它会让整页的行都变成另一个东西——渲染照旧跑，
        只是拿到的行不对（实测：页面上还是那几行，而这里读回来的是显示行，``row.kind`` 直接
        报 AttributeError）。与 ``_animate`` / ``_name`` 那一类影子同名是同一个坑。
        """
        return self._list_rows

    def show_lists(self, rows: Sequence[ListRow]) -> None:
        """把引擎给的清单索引铺成一页（收集箱、视图、真实清单、项目组小标题）。"""
        self._list_rows = tuple(rows)
        self._rows_by_id = {row.id: row for row in rows}
        self.set_rows(self._build(rows))

    def _build(self, rows: Sequence[ListRow]) -> tuple[Row, ...]:
        """清单索引那一页的行：**先一条通栏细线**，然后是每个项目组的抬头 + 细线 + 成员。

        顶上那条细线是页面自己的第一行（不是第三行 chrome）：顶栏说「你在哪」，紧跟着一条
        暗线把 chrome 与内容分开，而底部仍然只有状态栏那一行——chrome 净增 0 行。
        """
        built: list[Row] = [rule_row()]
        for group_id, members in group_by_project(rows):
            if group_id is not None:
                built.append(Row(id=None, text=project_heading(group_id)))
                built.append(rule_row())
            built += [Row(id=row.id, text=list_line(row)) for row in members]
        if len(built) == 1:
            return (built[0], empty_row(self.EMPTY_TEXT))
        return tuple(built)

    def action_enter(self) -> None:
        """``enter``：进选中的那一行；进不去的行只报一句。"""
        row = self._rows_by_id.get(self.selected_id or "")
        if row is None:
            return
        if not row.enterable:
            self.post_message(self.Refused(messages.blocked_list_message(row)))
            return
        self.post_message(self.Entered(row.id))

    def action_new_list(self) -> None:
        """``n``：新建（浮层与写路径都在外层，这一页只转发）。

        从 #36 起它**先问一句「清单还是视图」**（验收标准 1）——两种东西后面完全是两回事：
        一个是服务端的容器，一个是只存在本机的过滤条件。这一页不问（它不认识浮层），
        只把按键转发出去。
        """
        self.post_message(self.NewList())

    def action_edit_list(self) -> None:
        """``e``：改光标这一行；没有可停的行时什么都不做。"""
        if self.selected_id is not None:
            self.post_message(self.EditList(self.selected_id))

    def action_delete_list(self) -> None:
        """``d``：删光标这一行（外层先问一句，这里只转发）。"""
        if self.selected_id is not None:
            self.post_message(self.DeleteList(self.selected_id))

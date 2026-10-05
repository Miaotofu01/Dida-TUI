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

from typing import Sequence

from rich.text import Text
from textual.message import Message

from dida.sync.engine import ListKind, ListRow
from dida.tui import messages
from dida.tui.keys import LAYER_INDEX, bindings_for
from dida.tui.pages.base import EMPTY_STYLE, CursorPage, Row, empty_row

__all__ = [
    "BUILTIN_MARK",
    "BLOCKED_MARK",
    "CUSTOM_MARK",
    "INBOX_MARK",
    "LIST_MARK",
    "IndexPage",
    "group_by_project",
    "list_line",
    "mark_of",
    "project_heading",
]

INBOX_MARK = "▣"
"""收集箱：它是真实清单，但置顶显示，给它一个一眼能认出来的记号。"""

BUILTIN_MARK = "▸"
"""内置视图（今天 / 最近七天 / 所有）。"""

CUSTOM_MARK = "★"
"""用户自建的视图：一组只存在本机的过滤条件。"""

LIST_MARK = "☰"
"""真实清单（API 叫 project）。"""

BLOCKED_MARK = "⚠ 不可进入"
"""进不去的清单（``kind`` 是 NOTE 或没有写权限）的记号。"""

HEADING_STYLE = "bold"
"""项目组小标题的样式；小标题本身不可停光标，怎么画都不影响光标。"""


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
    """一行清单：前缀字符 + 名字 + 未完成条数（+ 进不去的记号）。"""
    text = Text()
    text.append(f"{mark_of(row)} {row.name}  ")
    text.append(str(row.unfinished), style=EMPTY_STYLE)
    if not row.enterable:
        text.append(f"  {BLOCKED_MARK}", style=EMPTY_STYLE)
    return text


def project_heading(group_id: str) -> Text:
    """项目组小标题（不可进入）。

    服务端的清单索引只给 ``groupId``，没有组名（``GET /open/v1/project`` 的 ``Project`` 上
    只有这一个字段）——所以小标题如实显示那个 id，而不是编一个名字出来。
    """
    return Text(f"── 项目组 {group_id} ──", style=HEADING_STYLE)


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


class IndexPage(CursorPage):
    """清单列表页：内置视图、自建视图、真实清单、项目组小标题。"""

    LAYER = LAYER_INDEX
    BINDINGS = bindings_for(LAYER_INDEX)
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

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._rows_by_id: dict[str, ListRow] = {}

    def show_lists(self, rows: Sequence[ListRow]) -> None:
        """把引擎给的清单索引铺成一页（收集箱、视图、真实清单、项目组小标题）。"""
        self._rows_by_id = {row.id: row for row in rows}
        self.set_rows(self._build(rows))

    def _build(self, rows: Sequence[ListRow]) -> tuple[Row, ...]:
        built: list[Row] = []
        for group_id, members in group_by_project(rows):
            if group_id is not None:
                built.append(Row(id=None, text=project_heading(group_id)))
            built += [Row(id=row.id, text=list_line(row)) for row in members]
        if not built:
            return (empty_row(self.EMPTY_TEXT),)
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

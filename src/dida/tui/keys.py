"""键位表：这个界面收哪些键、每个键做什么——**按层**，一个数据结构两个读者。

两个读者是：

- 真实绑定：三张页面各自的 ``BINDINGS`` 与 app 的 ``BINDINGS`` 都从 :func:`bindings_for`
  长出来，没有人在别处手写第二条绑定；
- ``?`` 那张帮助：:func:`help_rows` 从同一张表里取当前这一层该看到的行。

手抄第二份的下场是加了键忘了写帮助——用户找不到那个功能，而测试只能看见两份表不一致，
说不出哪一份是对的。

**按层**是 v2 的要求（用户故事 119：帮助里是「当前这一层可用的键」，不是一张混杂所有层
的大表）。``enter`` 在清单列表页是「进入这个清单」、在任务列表页是「进入详细页」，
``esc`` 在任务列表页是「退回清单列表页」——同一个键在不同层做不同的事，所以它必须跟着层走。

**这是 #48 的接缝**（「键位守卫 + 分层帮助」）：那一张要在这张表上加一道守卫（键位只能用
终端一定会传上来的那些），并把帮助做成分层的样子。:func:`help_rows` 就是它要读的口子。

只用终端一定会传上来的键：字母、``enter``、``esc``、方向键。``ctrl+enter`` / ``ctrl+shift+*``
/ ``alt+方向键`` 一个都不绑——它们要么收不到，要么会静默塌缩成不加修饰的键（spec 的键位表
「明确不绑」那一节）。
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.cells import cell_len
from textual.binding import Binding

from dida.tui import theme

__all__ = [
    "BINDINGS",
    "GLOBAL",
    "KEY_NAMES",
    "LAYERS",
    "LAYER_DETAIL",
    "LAYER_INDEX",
    "LAYER_TITLES",
    "LAYER_TASKS",
    "HelpRow",
    "Key",
    "bindings_for",
    "help_body",
    "help_rows",
    "key_text",
]

GLOBAL = "global"
"""每一层都有的那几条：退出、手动同步、浏览器、帮助。"""

LAYER_INDEX = "index"
"""层一，清单列表页：启动落在这里。"""

LAYER_TASKS = "tasks"
"""层二，任务列表页：某个清单或视图里的任务。"""

LAYER_DETAIL = "detail"
"""层三，任务详细页（#43 接手扩展）。"""

LAYERS: tuple[str, ...] = (LAYER_INDEX, LAYER_TASKS, LAYER_DETAIL)
"""三层，按用户进入的顺序。``LAYERS`` 不含 :data:`GLOBAL`——那是每一层的附加项。"""


@dataclass(frozen=True)
class Key:
    """表里的一行：哪些键、做什么、帮助里怎么写。"""

    keys: tuple[str, ...]
    """Textual 的键名。多个键做同一件事时写在同一个 :class:`Key` 里（帮助里合成一行）。"""

    action: str
    """Textual 的动作名：真正被调用的是 ``action_<action>``（页面或 app 上的方法）。"""

    label: str
    """帮助里那一列说明。"""


BINDINGS: dict[str, tuple[Key, ...]] = {
    GLOBAL: (
        Key(("question_mark",), "help", "当前这一层的键位"),
        Key(("r",), "refresh", "手动同步"),
        Key(("o",), "open", "在浏览器里打开当前任务"),
        Key(("q",), "quit", "退出"),
    ),
    LAYER_INDEX: (
        Key(("j", "down"), "cursor_down", "下一行"),
        Key(("k", "up"), "cursor_up", "上一行"),
        Key(("enter",), "enter", "进入这一行"),
    ),
    LAYER_TASKS: (
        Key(("j", "down"), "cursor_down", "下一条"),
        Key(("k", "up"), "cursor_up", "上一条"),
        Key(("enter",), "enter", "任务详细页"),
        Key(("escape",), "back", "退回清单列表页"),
    ),
    LAYER_DETAIL: (
        Key(("escape",), "back", "退回任务列表页"),
    ),
}

KEY_NAMES: dict[str, str] = {
    "question_mark": "?",
    "escape": "esc",
    "enter": "enter",
}
"""帮助里怎么写这些键名：Textual 的名字（``question_mark``）不是给人看的。

**这里只许放宽度不含糊的拼法。** 这张表的输出进的是 ``?`` 那块 ``width: auto`` 的浮层，宽度由
rich 量出来的最宽那行决定；而 ``↓``（U+2193）与 ``↑``（U+2191）是东亚**歧义**宽度——rich 量 1
格、CJK 字体下终端画 2 格，于是 ``j / ↓`` 那一行比它自称的宽一格，说明列右移一格（#48 实测）。
方向键在 Textual 里的键名本来就是 ASCII 的 ``down`` / ``up``，所以它们**没有**条目：查不到就
原样显示键名，那正好是两边都算 1 格的拼法。
"""


def key_text(key: str) -> str:
    """一个键在帮助里的写法。"""
    return KEY_NAMES.get(key, key)


def bindings_for(layer: str) -> list[Binding]:
    """这一层真正的 Textual 绑定：全局那几条 + 这一层自己的。

    同一个 :class:`Key` 里的多个键合成一条 ``a,b`` 绑定——它们做的是同一件事，footer 上也
    只该占一格。动作名就是 :attr:`Key.action`：Textual 调的是 ``action_<action>``，所以
    「清单列表页的 ``enter``」与「任务列表页的 ``enter``」各自落到自己那个控件的方法上。
    """
    return [
        Binding(",".join(key.keys), key.action, key.label) for key in BINDINGS[GLOBAL] + BINDINGS[layer]
    ]


@dataclass(frozen=True)
class HelpRow:
    """``?`` 那张表里的一行：左边是怎么按，右边是做什么。"""

    key: str
    """帮助里的键位写法，如 ``j / down``。"""

    label: str

    keys: tuple[str, ...] = ()
    """这一行背后的原始键名（``("j", "down")``）——对账用，显示的是 :attr:`key`。"""


def help_rows(layer: str) -> tuple[HelpRow, ...]:
    """当前这一层的帮助行：全局 + 这一层，顺序与绑定表一致。

    一个 :class:`Key` 里的多个键并成一行：``j / down``。这不是「手抄一份」——键名与说明都
    是从表里读出来的，表改一行这里就跟着变。
    """
    return tuple(
        HelpRow(
            key=" / ".join(key_text(name) for name in key.keys),
            label=key.label,
            keys=key.keys,
        )
        for key in BINDINGS[GLOBAL] + BINDINGS[layer]
    )


LAYER_TITLES: dict[str, str] = {
    LAYER_INDEX: "清单列表页",
    LAYER_TASKS: "任务列表页",
    LAYER_DETAIL: "任务详细页",
}
"""``?`` 那张帮助的抬头：先说清「这是哪一层的键」。"""


def help_body(layer: str) -> str:
    """``?`` 那一屏的正文：当前这一层的键 + 说明，跟着绑定表走。

    抬头就是层名（用户故事 119：帮助里是**当前这一层**可用的键，而不是一张混杂了所有层的
    大表）。#48 接手时改的是这一段的排版与那道守卫，键与说明仍然只有 :data:`BINDINGS`
    这一个来源。

    **每一行都得「说多宽就多宽」**：这块浮层是 ``width: auto``，宽度由 rich 量出来的最宽那行
    决定，而终端按自己的宽度表画。所以抬头用 :data:`~dida.tui.theme.HEADING_RULE`（ASCII）
    而不是 ``─``，:data:`KEY_NAMES` 里也只许放宽度不含糊的拼法；``tests/test_keymap.py``
    的宽度守卫逐行对这两笔账。
    """
    rows = help_rows(layer)
    # 按**格**补空格，不按字符数：``ljust`` 遇到 2 格宽的字形（CJK、歧义宽度的符号）会把右边
    # 那一列推歪。今天键名里每个字形两边都算 1 格，所以输出与 ``ljust`` 一样——这道算法是给
    # 以后加进来的 CJK 字形留的（那时的前提是宽度守卫仍然绿）。
    width = max(cell_len(row.key) for row in rows) + 2
    rule = theme.HEADING_RULE
    lines = [f"{rule * 2} {LAYER_TITLES[layer]} {rule * 2}", ""]
    lines += [f"{theme.pad(row.key, width)}{row.label}" for row in rows]
    return "\n".join(lines)

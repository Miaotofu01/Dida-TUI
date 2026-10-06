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
「明确不绑」那一节）。这条规矩由 :func:`unreliable_reason` 一处判定，
``tests/test_keymap.py`` 拿它断「表里**没有**这种键」——不是「它有处理器」（#48：「绑定存在」
在真终端里永远绿，而用户按下去什么都不会发生）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

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
    "QUIT_ACTION",
    "QUIT_KEYS",
    "HelpRow",
    "Key",
    "bindings_for",
    "help_body",
    "help_rows",
    "key_text",
    "unreliable_reason",
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

QUIT_ACTION = "quit"
"""退出那个动作名——**只有这一个**（工单 #47）。

``q`` 与 ``Ctrl+C`` 都走它，所以两条路落在同一个判断上
（:meth:`dida.tui.app.DidaApp.action_quit`）。名字单独拿出来，是因为浮层上的退出键也得绑到
同一个动作上：浮层是模态的、绑定链很短，那一条见 :mod:`dida.tui.overlays`。
"""

QUIT_KEYS: tuple[str, ...] = ("q", "ctrl+c")
"""能退出 app 的那两个键。

``Ctrl+C`` 从 #47 起与 ``q`` 同级：它以前落在 Textual 自己的 ``help_quit`` 上（弹一句
「按 q 退出」、**不退出**），而 Textual 又把 ``ctrl+c`` 绑在 ``Screen.BINDINGS`` 的
``screen.copy_text`` 上，于是在浮层上它连那句提示都到不了、只是复制一段文字——一条按下去
什么都不说的岔路。两条路合成一条之后，这个分歧没有了。
"""


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
        Key(QUIT_KEYS, QUIT_ACTION, "退出"),
    ),
    LAYER_INDEX: (
        Key(("j", "down"), "cursor_down", "下一行"),
        Key(("k", "up"), "cursor_up", "上一行"),
        Key(("enter",), "enter", "进入这一行"),
        Key(("n",), "new_list", "新建清单或视图"),
        Key(("e",), "edit_list", "改这一行（清单或视图）"),
        Key(("d",), "delete_list", "删除这一行（清单或视图）"),
    ),
    LAYER_TASKS: (
        Key(("j", "down"), "cursor_down", "下一条"),
        Key(("k", "up"), "cursor_up", "上一条"),
        Key(("space",), "toggle_complete", "完成 / 取消完成"),
        Key(("enter",), "enter", "任务详细页"),
        Key(("escape",), "back", "退回清单列表页"),
        Key(("d",), "delete", "删除这条任务"),
        Key(("g",), "defer", "顺延到下一个逻辑日"),
        Key(("G",), "defer_week", "顺延一周"),
    ),
    LAYER_DETAIL: (
        Key(("j", "down"), "cursor_down", "下一个字段"),
        Key(("k", "up"), "cursor_up", "上一个字段"),
        Key(("enter",), "enter", "编辑这个字段"),
        Key(("escape",), "back", "结束编辑 / 退回任务列表页"),
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
格、CJK 字体下终端画 2 格，于是 ``j / ↓`` 那一行比它自称的宽一格，说明列右移一格（ADR-0007）。
方向键在 Textual 里的键名本来就是 ASCII 的 ``down`` / ``up``，所以它们**没有**条目：查不到就
原样显示键名，那正好是两边都算 1 格的拼法。
"""


_UNRELIABLE_KEYS: Final[dict[str, str]] = {
    "ctrl+enter": (
        "多数终端把它发成和 enter 同一个字节 0x0D，与 enter 分不开；kitty 协议的 CSI 13;5u 才"
        "送得到它，而那条推送正是 ADR-0006 关掉的（实测：xterm 的 modifyOtherKeys 送来的是 "
        "ctrl+\\r，不是 ctrl+enter）"
    ),
    "ctrl+return": "同 ctrl+enter：多一个拼法，一样收不到",
    "shift+enter": "kitty 协议专属（terminal-input-evidence.md §2 实测：只有 CSI-u 送得到）",
    "shift+space": "kitty 协议专属（同上）",
    "shift+backspace": "在不报告独立修饰符的终端上无效（textual#6612）",
}
"""点名的那几个收不到的键：键名 → 为什么收不到。"""

_UNRELIABLE_PREFIXES: Final[tuple[tuple[str, str], ...]] = (
    (
        "alt+",
        "Alt 组合要终端把 ESC 前缀单独报上来：实测 alt+enter 塌缩成 enter（textual#6663，"
        "按下会触发**另一个**动作），alt+方向键只有 kitty 协议送得到（spec 的「明确不绑」）",
    ),
    (
        "ctrl+shift+",
        "要终端把 Ctrl 与 Shift 两个修饰符分开报（CSI-u / kitty）；ADR-0006 关掉那条路之后"
        "它根本到不了这里",
    ),
)
"""整类收不到的键：键名前缀 → 为什么整类都收不到。"""


def unreliable_reason(key: str) -> str | None:
    """这个键终端一定送得上来的吗？送不上来就给出**理由**，可靠则 ``None``。

    规格里那句「终端能力守卫：一处集中决定哪些键可以用」就是这里：键位表与帮助都只许用
    可靠键，判据集中在这一个函数。理由是**量出来的**（``notes/terminal-input-evidence.md``
    §2、ADR-0006、textual#6612 / #6663 / #6721），不是「感觉有些终端不支持」——将来终端
    能力变了，改这里就得先把那几条实测重跑一遍。
    """
    name = key.strip().lower()
    if name in _UNRELIABLE_KEYS:
        return _UNRELIABLE_KEYS[name]
    for prefix, reason in _UNRELIABLE_PREFIXES:
        if name.startswith(prefix):
            return reason
    return None


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

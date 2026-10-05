"""键位表：一个数据结构，两个读者（真实绑定与 ``?`` 那张帮助）。

**为什么这值得一条测试**：手抄第二份表的下场是加了键忘了写帮助——用户找不到那个功能，
而测试只能看见两份表不一致，说不出哪一份是对的。所以这里断的是「帮助里的键就是真实绑定的
键」，而不是「帮助等于某个写死的字符串」。

分层是 v2 的要求（用户故事 119）：``?`` 列的是**当前这一层**可用的键，不是一张混杂了所有
层的大表。所以表按层存，帮助按层取。
"""

from __future__ import annotations

import unicodedata

from rich.cells import cell_len

from dida.tui.keys import (
    BINDINGS,
    GLOBAL,
    LAYERS,
    bindings_for,
    help_body,
    help_rows,
)


def drawn_width(text: str) -> int:
    """这段文字在「歧义宽度画双格」的 CJK 终端上占几格。

    与 rich 的 ``cell_len`` 只差东亚**歧义**宽度那一档：rich 按 1 格排版，CJK 字体下终端
    可能画 2 格（ADR-0007 的「字形宽度是承重项」）。全角/半角（W/F）两边都是 2 格、中性
    （N/Na/H）两边都是 1 格，都不算差别——所以两边不等的只有歧义宽度。
    """
    return sum(
        2 if unicodedata.east_asian_width(char) in ("A", "W", "F") else cell_len(char)
        for char in text
    )


def keys_of_bindings(layer: str) -> set[str]:
    """这一层真实绑定的键，一个绑定里那串 ``a,b`` 拆开。"""
    keys: set[str] = set()
    for binding in bindings_for(layer):
        keys.update(binding.key.split(","))
    return keys


def test_the_help_lists_exactly_the_keys_that_layer_binds():
    """帮助跟着绑定表走：它列出的键 === 真实绑定的键（多一个少一个都红）。"""
    for layer in LAYERS:
        listed = {key for row in help_rows(layer) for key in row.keys}

        assert listed == keys_of_bindings(layer), f"{layer} 这一层的帮助与绑定表不一致"


def test_every_layer_shares_the_globals_and_adds_its_own():
    """全局那几条每一层都有；每一层还有一条只属于它自己的键。"""
    globals_ = keys_of_bindings(GLOBAL)

    assert globals_, "全局表是空的——它该有退出、同步、浏览器、帮助"

    for layer in LAYERS:
        own = keys_of_bindings(layer) - globals_
        assert own, f"{layer} 这一层没有自己的键，那分层就没意义了"

        assert globals_ <= keys_of_bindings(layer), f"{layer} 缺了全局键"


def test_the_help_says_what_each_key_does():
    """每一行都有说明，而且说明不重复抄键名（``j`` 那一行不能只写「j」）。"""
    for layer in LAYERS:
        for row in help_rows(layer):
            assert row.label.strip(), f"{layer} 的 {row.key} 没有说明"
            assert row.label.strip() != row.key, f"{layer} 的 {row.key} 只抄了一遍键名"


def test_the_table_is_keyed_by_layer_not_one_flat_list():
    """表按层存：每个名字都是一层，而不是 v1 那种平铺的一串。"""
    assert set(BINDINGS) == {GLOBAL, *LAYERS}
    for layer, keys in BINDINGS.items():
        assert keys, f"{layer} 是空的"


def test_the_help_body_is_exactly_as_wide_as_the_terminal_will_draw_it():
    """帮助的每一行都得「说多宽就多宽」。

    ``?`` 那块浮层是 ``width: auto``（``theme.py`` 的 ``overlay_css``）：宽度由 **rich** 量出来
    的最宽那一行决定，而终端按**自己的**宽度表画。两者只要差一格，多出来的那几格就把右边框
    挤掉（工单 #48 的抬头 ``── X ──`` 正是这个反例：四个 ``─`` 都是歧义宽度，四个字符合起来
    让那一行比 rich 的量法宽 4 格）。

    这条量与 rich 的账的是同一件事的两面：rich 说这一行几格，CJK 终端就画几格。允许的只有
    「两边都算 2 格」（汉字）与「两边都算 1 格」（ASCII）这两种字形。
    """
    for layer in LAYERS:
        for line in help_body(layer).splitlines():
            assert drawn_width(line) == cell_len(line), (
                f"{layer} 这一层的帮助里有一行宽度说得不准：rich 说 {cell_len(line)} 格，"
                f"CJK 终端画 {drawn_width(line)} 格——多出来的格会把浮层的右边框挤掉：\n{line!r}"
            )


def test_every_page_binds_its_own_layer_from_the_one_table():
    """每一层的真实绑定都是从表里取的那一份，页面里没有手写的第二条。

    ``BINDINGS`` 手抄一份的下场与帮助手抄一份一样，只是更难发现：按键真的会不通。
    """
    from dida.tui.pages import DetailPage, IndexPage, TasksPage

    for page in (IndexPage, TasksPage, DetailPage):
        assert page.LAYER in LAYERS, f"{page.__name__} 的层名不在表里"
        assert page.BINDINGS == bindings_for(page.LAYER), f"{page.__name__} 的绑定不是从表里来的"

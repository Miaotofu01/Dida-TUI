"""测试辅助：断言「屏幕文本」。

唯一一处触碰 Textual 私有合成器的地方，其余测试只用这里的函数。
"""

from __future__ import annotations

from textual.app import App


def screen_text(app: App) -> str:
    """当前屏幕的纯文本（逐行去尾空格）。"""
    strips = app.screen._compositor.render_strips()  # type: ignore[attr-defined]
    return "\n".join(strip.text.rstrip() for strip in strips)


def screen_styled_text(app: App) -> str:
    """屏幕文本，每一行带 ANSI 样式。

    断「哪一行被高亮了」用：具体颜色随主题与终端变，所以测试比的是「这一段样式出现在
    哪一行」，而不是「这一行长什么样」。
    """
    strips = app.screen._compositor.render_strips()  # type: ignore[attr-defined]
    return "\n".join(strip.render(app.console) for strip in strips)


def screen_sgr(app: App) -> str:
    """屏幕字节：**真彩色终端**会从每一段里收到的那些 SGR 序列。

    与 :func:`screen_styled_text` 的差别只有一处，而正是它让这条断言能在任何机器上跑：
    这里显式拿一个 ``color_system="truecolor"`` 的控制台来渲染，不去问 ``app.console``
    是什么能力。跑测试的 shell 里 ``TERM=dumb`` / ``NO_COLOR=1`` 时，``app.console``
    根本不发颜色（Rich 会静默降级），那样断言「强调色是不是 ANSI 6」就永远是空的——
    测出来的是 shell 的样子，不是 app 的样子。

    逐段的颜色已经被 Filter（``ANSIToTruecolor``）处理过了，所以这里读到的就是终端收到的
    字节：``ansi_color=True`` 时是 ``\\x1b[36;49m``，没开时是 ``\\x1b[38;2;88;209;235…``。
    """
    from rich.console import Console

    console = Console(color_system="truecolor", force_terminal=True, width=400)
    strips = app.screen._compositor.render_strips()  # type: ignore[attr-defined]
    return "\n".join(strip.render(console) for strip in strips)

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

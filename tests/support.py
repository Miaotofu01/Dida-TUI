"""测试辅助：断言「屏幕文本」。

唯一一处触碰 Textual 私有合成器的地方，其余测试只用这里的函数。
"""

from __future__ import annotations

from textual.app import App


def screen_text(app: App) -> str:
    """当前屏幕的纯文本（逐行去尾空格）。"""
    strips = app.screen._compositor.render_strips()  # type: ignore[attr-defined]
    return "\n".join(strip.text.rstrip() for strip in strips)

"""测试辅助：断言「屏幕文本」。

唯一一处触碰 Textual 私有合成器的地方，其余测试只用这里的函数。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Any

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


async def sample_frames(
    app: App,
    action: Callable[[], Any],
    project: Callable[[App], Any],
) -> list[Any]:
    """一边做 ``action``（通常是按一个键）一边**逐帧**取 ``project(app)``。

    ``pilot.press`` 会等到这一屏静下来（Textual 的动画跑完才算 idle），所以「按完再抓屏」
    只能看到落定后的样子——动效本身只有一个并发采样的人看得见。

    采样任务取消之后**等它真的结束**再返回：``cancel()`` 只是投递，不等它会把一个待处理的
    取消留到下一个事件循环回合，而那个回合会吞掉紧接着的第一次 ``pilot.press``（``notes/
    progress.md`` 记过这个坑）。把它 await 掉，连着采样几次才是安全的。
    """
    frames: list[Any] = []

    async def sample() -> None:
        while True:
            frames.append(project(app))
            await asyncio.sleep(0.005)

    sampler = asyncio.create_task(sample())
    try:
        await action()
    finally:
        sampler.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sampler
    return frames

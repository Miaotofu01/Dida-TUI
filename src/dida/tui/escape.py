"""逃生舱：把一条任务交给系统浏览器（``o``，工单 #19）。

**只有浏览器这一条路。** ADR-0002 的「逃生舱的确切形态（已核实）」记着从官方 Electron
包里提取的证据：``dida365://`` 这个 scheme 不存在（包里只有 ``ticktick://``），Linux 版
``.desktop`` 没有注册任何协议处理器（无 ``MimeType=``），主进程没有
``setAsDefaultProtocolClient`` 且不处理 argv。所以「切到桌面 App」这一手做不出来——
这个键就是「按一个键，开一个浏览器标签页」。

它是纯客户端 hash 路由，URL 里不含凭据；能不能打开那条任务取决于浏览器里有没有登录态。
"""

from __future__ import annotations

import webbrowser

from dida.sync.engine import INBOX_ID, is_inbox_id

__all__ = ["open_in_browser", "task_url"]

URL_TEMPLATE = "https://dida365.com/webapp/#p/{project_id}/tasks/{task_id}"
"""厂商自己在「复制任务链接」里生成的那条网页版路由（ADR-0002 已核实）。"""


def task_url(project_id: str, task_id: str) -> str:
    """一条任务的网页版 URL（厂商「复制任务链接」的模板，一字不改）。

    ``project_id`` **是收集箱**时替换成字面量 ``inbox``：``GET /open/v1/project/{id}/data``
    接受 ``"inbox"`` 当清单 id（``api-contracts.md``），而厂商模板对收集箱写的就是这个字面量。
    收集箱的身份认两种形状（``is_inbox_id``）：请求侧别名 ``inbox``，以及服务端返回的那一串
    （形如 ``inbox`` 加一截数字——收集箱里的任务带的就是它，实测）。

    v1 认的是「**含** inbox」这种子串匹配，于是一个 id 里恰好带 inbox 的真实清单
    （``my-inbox-list``）会被拼成收集箱的链接——点开是错的页面。#33 收掉了那条猜法，
    只留「真的是收集箱」这一条；比较不区分大小写。
    """
    if is_inbox_id(project_id):
        project_id = INBOX_ID
    return URL_TEMPLATE.format(project_id=project_id, task_id=task_id)


def open_in_browser(url: str) -> bool:
    """把 URL 交给系统浏览器，返回 ``webbrowser.open`` 的原话。

    ``webbrowser`` 找不到任何浏览器时**抛** ``webbrowser.Error``，而 ``open()`` 回
    ``False`` 也是一种失败——两种都如实往上传，由 TUI 决定怎么出声。这一层不吞异常：
    完成在服务端不可逆（ADR-0002），这个键是它的补偿，静默失败比吵一句坏得多。
    """
    return bool(webbrowser.open(url))

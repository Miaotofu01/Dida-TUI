"""深链拼装（t19）：``o`` 交给浏览器的那条 URL 是怎么拼出来的。

这个文件是**搬过来的**（工单 #32 的「先搬后删」）：这四条结论原本钉在
``test_escape.py`` 里，而那个文件里另外一半是按键开屏的界面测试，界面重写会连文件一起删。
搬过来的是**不起界面的那一半**：``task_url`` 是纯函数，``open_in_browser`` 只是
``webbrowser.open`` 的一层，两者都不需要 ``DidaApp``、不需要 Pilot、不需要屏幕。

关于「这个文件为什么还 import ``dida.tui``」：v2 **保留**深链拼装（spec #30：
「可以原样保留……深链拼装」），#34 的验收标准里也有「按 ``o`` 在浏览器里打开当前任务」。
``task_url`` 就住在 ``dida.tui.escape`` 里，没有任何非 TUI 的落点——所以这个文件是
「模块活着、断言活着」，而不是 v1 的界面测试。它**不是** v1 那 19 个界面测试文件之一，
#34 删那 203 条时不要连它一起删。

``README`` 那句「TUI 只许 import ``dida.sync.engine``」管的是 ``src/dida/tui/**``，
测试文件不在这条规矩里（``tests/test_architecture.py`` 的扫描也只扫 ``src``）。
"""

from __future__ import annotations

import pytest

import dida.tui.escape as escape
from dida.tui.escape import open_in_browser, task_url


# ---------------------------------------------------------------- 纯函数：URL


def test_task_url_follows_the_vendors_copy_task_link_template():
    """期望值来自 ADR-0002 里那条厂商模板，不是照抄实现。"""
    assert (
        task_url("工作清单id", "任务id")
        == "https://dida365.com/webapp/#p/工作清单id/tasks/任务id"
    )


def test_task_url_substitutes_the_literal_inbox_when_the_project_id_contains_inbox():
    """``projectId`` 含 ``"inbox"`` 时用字面量 ``inbox``——厂商模板就是这么生成的。

    ⚠ 这一条与 #33 的「缺失的 projectId 不许再猜成字面量 ``inbox``」是两件事：这里说的是
    **服务端确实给了**一个含 ``inbox`` 的清单 id 时 URL 怎么写。#33 改的是缺失那种情况，
    改的时候别顺手把这一条也改了。
    """
    assert task_url("inbox", "t1") == "https://dida365.com/webapp/#p/inbox/tasks/t1"


@pytest.mark.parametrize("project_id", ["inbox", "INBOX", "inbox-123", "my-inbox-list"])
def test_task_url_treats_any_inbox_containing_project_id_as_the_literal(project_id):
    """「含 inbox」是字面包含，不分大小写：本地那份 id 可能是任何形状。"""
    assert task_url(project_id, "t9") == "https://dida365.com/webapp/#p/inbox/tasks/t9"


# ---------------------------------------------------------------- 默认开手：交给 webbrowser


def test_open_in_browser_hands_the_url_to_the_system_browser(monkeypatch):
    """生产默认值就是 ``webbrowser.open``，并且把它的返回值如实带回来。"""
    seen: list[str] = []

    def fake_open(url: str) -> bool:
        seen.append(url)
        return True

    monkeypatch.setattr(escape.webbrowser, "open", fake_open)

    assert open_in_browser("https://dida365.com/webapp/#p/work/tasks/t1") is True
    assert seen == ["https://dida365.com/webapp/#p/work/tasks/t1"]


def test_open_in_browser_reports_a_browser_that_says_no(monkeypatch):
    """``webbrowser.open`` 回 ``False`` = 这台机器上没有浏览器可用：如实带回去。

    这一层不吞「开不了」这件事——上层要据此出声（完成在服务端不可逆，``o`` 是它的补偿）。
    """
    monkeypatch.setattr(escape.webbrowser, "open", lambda url: False)

    assert open_in_browser("https://dida365.com/webapp/#p/work/tasks/t1") is False

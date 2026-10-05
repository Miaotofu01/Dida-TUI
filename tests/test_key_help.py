"""键位帮助表必须覆盖每一个真的绑上的键（评审修 A3）。

``KEY_HELP`` 自己写着这条规矩：「新绑一个键就要往这里加一行……漏一行的后果不是『少个
说明』，是那个功能没人找得到。」但一直只有单个键的测试（``tests/test_sync_session.py``
守着 ``r``），``s`` / ``t`` / ``Space`` / ``↑`` / ``↓`` / ``Tab`` 就这么漏掉了。

这里把那条规矩变成一条**完备性**测试：凡是 ``DidaApp``、``TaskPane``、``SubtaskPane``
绑了的键，帮助表里必须有一行。下一个加键的人因此不可能悄悄漏掉帮助——测试会点名那个键。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp
from dida.tui.panes import KEY_HELP, SubtaskPane, TaskPane

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)

DISPLAY_NAMES = {
    "question_mark": "?",
    "enter": "Enter",
    "space": "Space",
    "up": "↑",
    "down": "↓",
    "escape": "Esc",
    "tab": "Tab",
}
"""Textual 的键名 → 帮助表里写给用户看的写法（``?`` 不是 ``question_mark``）。"""


def help_keys() -> set[str]:
    """帮助表里出现过的每一个键；一格两键写成 ``j / k``，所以按 ``" / "`` 拆。

    不能按裸的 ``/`` 拆：``/``（过滤）自己就是一个键。
    """
    return {
        part.strip()
        for cell, _ in KEY_HELP
        for part in cell.split(" / ")
        if part.strip()
    }


def bound_keys() -> set[str]:
    """应用与两个常驻栏位真的绑上的键，按帮助表的写法。

    ``SubtaskPane`` 也在里面：``t``（勾选子任务）是它绑的，而它正是漏掉的那几行之一。
    """
    return {
        DISPLAY_NAMES.get(key.strip(), key.strip())
        for binding in (*DidaApp.BINDINGS, *TaskPane.BINDINGS, *SubtaskPane.BINDINGS)
        for key in binding.key.split(",")
    }


BUILT_IN_KEYS = {"Tab"}
"""Textual 自己处理的键：``Tab`` 是内建的焦点环，不在任何 ``BINDINGS`` 里。

它照样要有帮助那一行（spec 的键位表列了它），所以单独补上——测试不能只对 ``BINDINGS``
负责，否则这个键又会从表里悄悄溜走。
"""


def required_keys() -> set[str]:
    """帮助表该覆盖的全部键。"""
    return bound_keys() | BUILT_IN_KEYS


def test_the_help_table_covers_every_key_the_app_and_the_panes_bind():
    """完备性：绑了键就要有那一行。"""
    required = required_keys()
    assert {"q", "x", "Space", "j", "k", "c", "s", "t", "↑", "↓", "Tab"} <= required, (
        "扫描器没读到绑定，扫描逻辑失效了"
    )

    missing = sorted(required - help_keys())
    assert missing == [], f"帮助表里没有这些键：{missing}"


def overlay_text(app: DidaApp) -> str:
    """当前浮层盒子里的正文（与 ``tests/test_sync_session.py`` 同一个手法）。"""
    body = getattr(app.screen, "body", "")
    return body.plain if hasattr(body, "plain") else str(body)


async def test_the_help_overlay_shows_the_rows_that_were_missing():
    """屏上层：那几行真的画在 ``?`` 里，不是只存在于数据里。"""
    app = DidaApp(FakeBackend(clock=ManualClock(T0)))

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("question_mark")
        await pilot.pause()
        rows = overlay_text(app)

    expected = (
        ("x / Space", "完成"),
        ("s", "子任务"),
        ("t", "勾选"),
        ("↑ / ↓", "上下移动光标"),
        ("Tab", "切换"),
    )
    for key, what in expected:
        assert re.search(rf"^{re.escape(key)}\s+{what}", rows, re.M), f"帮助里没有「{key}」这一行"

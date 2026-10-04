"""逃生舱（t19）：``o`` 把光标下那条任务交给系统浏览器。

两条接缝：

- **接缝一（主）**：``DidaApp`` + 内存假后端，Pilot 按 ``o`` → 断言**交给浏览器的那条
  URL**（注入的假开手收到的东西）。浏览器在这里是注入的，所以测试永远不会真的开一个
  标签页——而「交给浏览器的是哪条 URL」正是这个键的全部行为。
- **无接缝（纯函数）**：``task_url`` 直接测；期望值是 ADR-0002
  「逃生舱的确切形态（已核实）」里那条厂商自己的「复制任务链接」模板，一个字都不改。

**只交给系统浏览器**：ADR-0002 记着官方桌面客户端不接受任务深链——``dida365://`` 这个
scheme 不存在、Linux 的 ``.desktop`` 没注册协议处理器、主进程也不处理 argv。所以这里
只断言浏览器那一条路，不假装还有「切到桌面 App」这一手。

没有浏览器可用时**必须出声**：完成在服务端不可逆（ADR-0002），这个键是它的补偿，
静默失败比吵一句坏得多。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import dida.tui.escape as escape
from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp, NO_BROWSER_PREFIX
from dida.tui.escape import open_in_browser, task_url
from dida.tui.panes import TaskPane

from support import screen_text

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)


class SpyBrowser:
    """假浏览器开手：只记录交给它的 URL，一个标签页都不开。

    ``result`` 摆成 ``False`` 就是「这台机器上没有浏览器可用」；``error`` 摆一个异常
    进去就是开手当场抛（``webbrowser`` 找不到浏览器时抛的就是 ``webbrowser.Error``）。
    """

    def __init__(self, *, result: bool = True, error: Exception | None = None) -> None:
        self.urls: list[str] = []
        self._result = result
        self._error = error

    def __call__(self, url: str) -> bool:
        self.urls.append(url)
        if self._error is not None:
            raise self._error
        return self._result

    @property
    def last_url(self) -> str:
        assert self.urls, "浏览器开手一次都没被叫到"
        return self.urls[-1]


def make_backend(*, list_name: str = "工作") -> FakeBackend:
    """一条今天到期的任务；``list_name`` 就是它的 projectId。"""
    backend = FakeBackend(clock=ManualClock(T0), day_end="24:00")
    backend.add_task("交季度报告", list_name=list_name, due=datetime(2026, 3, 14, 18, 0, tzinfo=TZ))
    return backend


# ---------------------------------------------------------------- 纯函数：URL


def test_task_url_follows_the_vendors_copy_task_link_template():
    """期望值来自 ADR-0002 里那条厂商模板，不是照抄实现。"""
    assert (
        task_url("工作清单id", "任务id")
        == "https://dida365.com/webapp/#p/工作清单id/tasks/任务id"
    )


def test_task_url_substitutes_the_literal_inbox_when_the_project_id_contains_inbox():
    """``projectId`` 含 ``"inbox"`` 时用字面量 ``inbox``——厂商模板就是这么生成的。"""
    assert (
        task_url("inbox", "t1")
        == "https://dida365.com/webapp/#p/inbox/tasks/t1"
    )


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


# ---------------------------------------------------------------- 接缝一：按 o


async def test_the_apps_default_opener_is_the_system_browser(monkeypatch):
    """不注入任何开手时，``o`` 走的就是 ``webbrowser``——生产那条路，替身不参与。"""
    seen: list[str] = []

    def fake_open(url: str) -> bool:
        seen.append(url)
        return True

    monkeypatch.setattr(escape.webbrowser, "open", fake_open)
    app = DidaApp(make_backend())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()

    assert seen == ["https://dida365.com/webapp/#p/工作/tasks/t1"]


async def test_pressing_o_hands_the_exact_url_of_the_selected_task_to_the_browser():
    """按 ``o``：交给浏览器的正是光标下那一条的 URL，一字不差。"""
    backend = make_backend()
    spy = SpyBrowser()
    app = DidaApp(backend, open_url=spy)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()

    assert spy.urls == ["https://dida365.com/webapp/#p/工作/tasks/t1"]


async def test_pressing_o_on_an_inbox_task_hands_over_the_literal_inbox_url():
    """收集箱里的任务：URL 里的 projectId 是字面量 ``inbox``，不是本地那份名字。"""
    backend = make_backend(list_name="inbox")
    spy = SpyBrowser()
    app = DidaApp(backend, open_url=spy)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()

    assert spy.urls == ["https://dida365.com/webapp/#p/inbox/tasks/t1"]


async def test_pressing_o_follows_the_cursor_to_another_task():
    """换一条再按 ``o``：交给浏览器的是**当前**光标下那一条，不是上一次那条。"""
    backend = FakeBackend(clock=ManualClock(T0), day_end="24:00")
    backend.add_task("交季度报告", list_name="工作", due=datetime(2026, 3, 14, 18, 0, tzinfo=TZ))
    backend.add_task("写周报", list_name="工作", due=datetime(2026, 3, 14, 19, 0, tzinfo=TZ))
    spy = SpyBrowser()
    app = DidaApp(backend, open_url=spy)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()
        await pilot.press("j")
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()

    assert spy.urls == [
        "https://dida365.com/webapp/#p/工作/tasks/t1",
        "https://dida365.com/webapp/#p/工作/tasks/t2",
    ]


async def test_pressing_o_with_no_task_selected_does_nothing_and_does_not_raise():
    """空屏上按 ``o``：什么都不做（与 ``x``/``g``/``e`` 同一条口径），不是错误。"""
    backend = FakeBackend(clock=ManualClock(T0), day_end="24:00")
    spy = SpyBrowser()
    app = DidaApp(backend, open_url=spy)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()

        assert app.is_running, "按一下 o 不该把界面带走"
        assert TaskPane.EMPTY_TEXT in screen_text(app), "屏幕照旧是「今天没有任务」那一屏"

    assert spy.urls == []


# ---------------------------------------------------------------- 没有浏览器：必须出声


async def test_no_browser_available_reports_the_url_loudly_instead_of_failing_silently():
    """开手回 ``False``（没有浏览器可用）：状态栏说清楚，并且把 URL 原样给人抄。"""
    backend = make_backend()
    spy = SpyBrowser(result=False)
    app = DidaApp(backend, open_url=spy)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()

        text = screen_text(app)

    url = "https://dida365.com/webapp/#p/工作/tasks/t1"
    assert spy.urls == [url], "还是试着交出去了——失败发生在浏览器那一侧"
    assert NO_BROWSER_PREFIX in text, "没有浏览器可用时必须在屏幕上说出来"
    assert url in text, "把 URL 原样给人，好自己粘到浏览器里"


async def test_a_browser_that_raises_is_reported_instead_of_crashing_the_app():
    """开手当场抛（``webbrowser.Error``）：不崩，照样把那句话与 URL 说出来。"""
    backend = make_backend()
    spy = SpyBrowser(error=escape.webbrowser.Error("could not locate runnable browser"))
    app = DidaApp(backend, open_url=spy)

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()

        text = screen_text(app)

    assert NO_BROWSER_PREFIX in text
    assert "https://dida365.com/webapp/#p/工作/tasks/t1" in text


# ---------------------------------------------------------------- 键位可见


async def test_the_footer_shows_the_escape_hatch_key_with_its_label():
    """``o`` 在 footer 上带标签显示：看不见的键等于不存在。"""
    app = DidaApp(make_backend(), open_url=SpyBrowser())

    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        text = screen_text(app)

    assert "浏览器" in text

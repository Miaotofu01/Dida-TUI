"""哪一页能被聚焦（工单 #60）。

三层页面**并排**停在 ``Stage`` 上，只有当前那一层在可见区内——另外两层在屏外，但**仍然可
聚焦**。``tab`` 是 Textual 默认的「把焦点移给下一个可聚焦控件」，它按**控件树**走、不看谁
在可见区（``Widget.visible`` 说的是 CSS 可见，不是「在这一屏里」），于是焦点被交给一个用户
看不见的页面：屏幕纹丝不动，接下来按的键却全落在别处。

``tab`` 不在 spec 的键位表里（清单层 ``n`` / ``e`` / ``d`` / ``→``，任务层 ``space`` /
``n`` / ``d`` / ``g`` / ``G`` / ``→`` / ``←``，详细层 ``j`` / ``k`` / ``enter`` / ``←`` /
``esc``）；它只在浮层里
有意义。**所以在页面上它本来就该什么都不做。**

**接缝一**：真 ``DidaApp`` + ``FakeBackend`` + Pilot。断的是外部行为：「我按了 tab，焦点还在
我看着的这一页上」。读的「里面」只有 ``app.focused``（Textual 自己的公开属性）与
``Widget.focusable`` / ``Screen.focus_chain``（框架决定焦点去哪时问的那两样）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from textual.widgets import Input

from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp, Stage
from dida.tui.keys import LAYER_DETAIL, LAYER_INDEX, LAYER_TASKS
from support import screen_text

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
WIDE = (100, 30)


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


def backend() -> FakeBackend:
    """两个清单、两条任务：够走到第二层与第三层。"""
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work", group_id="g1")
    fake.add_task("写周报", list_name="work", id="t1", due=at(14, 18))
    fake.add_task("交水费", list_name="work", id="t2")
    return fake


async def go_to_layer(pilot, app: DidaApp, layer: int) -> None:
    """走到第 ``layer`` 层（0 = 清单列表页，1 = 任务列表页，2 = 任务详细页）。"""
    for _ in range(20):
        if layer == 0 or app.index_page().selected_id == "work":
            break
        await pilot.press("j")
    if layer >= 1:
        await pilot.press("right")
        await pilot.pause()
    if layer >= 2:
        await pilot.press("right")
        await pilot.pause()


def page_of(app: DidaApp, layer: str):
    return {LAYER_INDEX: app.index_page(), LAYER_TASKS: app.tasks_page(), LAYER_DETAIL: app.detail_page()}[
        layer
    ]


CURRENT = {0: LAYER_INDEX, 1: LAYER_TASKS, 2: LAYER_DETAIL}
"""层号 → 那一层的 id（页面就是按层 id 挂上去的，见 ``app.compose``）。"""


@pytest.mark.parametrize("layer", [0, 1, 2])
async def test_tab_leaves_focus_on_the_page_you_are_looking_at(layer: int):
    """``tab`` / ``shift+tab`` 在三层上都不许把焦点交给屏外那一页（工单 #60）。

    ``shift+tab`` 也在这里：它走的是 ``focus_previous``、与 ``tab`` 是**两个**绑定，只堵一个
    的话另一个照样把焦点送出去。两个方向各按三次（比旧的那条焦点链长一格），每一次都要：
    焦点还在当前这一页、屏幕逐字节不变。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await go_to_layer(pilot, app, layer)
        page = page_of(app, CURRENT[layer])
        assert app.focused is page, f"走到第 {layer} 层之后焦点应当在那一页上，实际是 {app.focused!r}"

        for key in ("tab", "tab", "tab", "shift+tab", "shift+tab", "shift+tab"):
            before = screen_text(app)
            await pilot.press(key)
            await pilot.pause()

            assert app.focused is page, (
                f"第 {layer} 层按 {key}：焦点跑到 {app.focused!r} 上去了，"
                f"而用户看着的是 {page!r}——接下来按的键会落在看不见的那一页"
            )
            assert screen_text(app) == before, f"第 {layer} 层按 {key}，屏幕不该有任何变化"


@pytest.mark.parametrize("layer", [0, 1, 2])
async def test_only_the_page_on_screen_can_be_focused(layer: int):
    """**复发守卫（形状级）**：屏外那两页（以及轨道自己）根本不是「可聚焦」的东西。

    断的是框架决定焦点去哪时**实际问的那一句**（``Widget.focusable``，也就是
    ``Screen.focus_chain`` 收谁进链子、``Screen.set_focus`` 收不收那一下），所以它不依赖
    「用户按了哪个键」——将来多出别的聚焦入口（鼠标、程序化 ``focus_next``）也一样问这一句。

    谁把页面改回「一直可聚焦」，这条当场变红：焦点链里会重新出现看不见的那两页。

    「链子里只有一页」说的是**平时**：详细页进编辑之后，亮出来的那几个格子是在屏上的，
    它们本来就该进链子（下面 ``test_the_editor_...`` 那条盯着这件事），所以这里量的是没进
    编辑时的那一屏。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await go_to_layer(pilot, app, layer)

        current = page_of(app, CURRENT[layer])
        assert app.screen.focus_chain == [current], (
            "焦点链里不只有当前这一页——屏外的页面又能被聚焦了："
            f"{[repr(widget) for widget in app.screen.focus_chain]}"
        )
        assert current.focusable, "当前这一页必须能被聚焦（换层就是靠它接住键盘）"
        assert not app.query_one("#stage", Stage).focusable, (
            "平移轨道被当成焦点目标了：它自己没有键位，tab 不该停在它上面"
        )
        for other_layer in (LAYER_INDEX, LAYER_TASKS, LAYER_DETAIL):
            if other_layer == CURRENT[layer]:
                continue
            assert not page_of(app, other_layer).focusable, (
                f"第 {layer} 层时，屏外的 {other_layer} 那一页又能被聚焦了"
            )


async def test_focus_follows_the_layer_when_you_walk_in_and_out():
    """换层（``→`` / ``←``）之后焦点**跟着层走**，而且原来那一页立刻不再可聚焦。

    这一条看着「本来就该这样」，但它是 #60 的验收核心：只有当前那一页可聚焦。所以这里连
    「旧页立刻不可聚焦」一起断——只断「焦点在正确的页上」会漏掉「旧页还能被 ``tab`` 走进去」。

    **别在这里断 ``_show`` 里那两条语句的先后。** 曾经以为「先换层、后交焦点」是承重的
    （说颠倒过来页面就聚焦不了、键盘就死），实测推翻了：``Widget.focus()`` 在 Textual 8.2.8
    里是 ``app.call_later`` **延迟**执行的，把两条语句对调之后这个文件仍然全绿。要钉住顺序
    得 ``await`` 那次回调，而顺序本身在 ``allow_focus()`` **现算**之后已经无关紧要。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert app.focused is app.index_page()

        await go_to_layer(pilot, app, 1)
        assert app.layer == LAYER_TASKS
        assert app.focused is app.tasks_page(), f"进第二层之后焦点应当在那一页上：{app.focused!r}"
        assert not app.index_page().focusable, "离开之后，第一页不该还能被聚焦"

        await pilot.press("right")  # 1 → 2
        await pilot.pause()
        assert app.layer == LAYER_DETAIL
        assert app.focused is app.detail_page(), f"进第三层之后焦点应当在那一页上：{app.focused!r}"
        assert not app.tasks_page().focusable, "离开之后，第二页不该还能被聚焦"

        await pilot.press("left")  # 2 → 1
        await pilot.pause()
        assert app.focused is app.tasks_page()
        await pilot.press("left")  # 1 → 0
        await pilot.pause()
        assert app.focused is app.index_page()
        assert not app.detail_page().focusable, "离开之后，第三页不该还能被聚焦"


async def test_the_editor_that_is_actually_on_screen_is_still_focusable():
    """**不许多关**：详细页进编辑之后，亮出来的那个格子仍然可以聚焦（它在屏上）。

    第 60 条要挡的是「屏外的页面」，不是「页面自己按不了 tab」。编辑器那几格是
    ``display: none`` 藏着的，用户按下编辑键才亮出来——那时它们在屏上、本来就该能进焦点。
    这条盯着「别把闸关过头」：真按一下，焦点得进得去。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await go_to_layer(pilot, app, 2)
        page = app.detail_page()

        await pilot.press("enter")  # 详细页上 enter = 编辑选中的那个字段
        await pilot.pause()

        field = app.focused
        assert isinstance(field, Input), f"编辑那一格没拿到焦点：{field!r}"
        assert field.focusable, "亮出来的编辑器是在屏上的，它必须能聚焦"
        assert page.focusable, "当前这一页自己也要一直能聚焦（退出编辑要回到它身上）"

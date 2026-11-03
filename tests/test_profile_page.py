"""第四种页面：个人主页（工单 #91、ADR-0010）。

它**不在导航路径上**：清单列表页按 ``A`` 旁路进去，``←`` / ``esc`` 回原处，顶栏只有
「个人主页」一段。接缝是真 ``DidaApp`` + ``FakeBackend`` + Pilot：断的是外部行为——按了
什么键、屏幕上是什么，不读控件树、也不碰私有的行结构。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from dida.testing import FakeBackend, ManualClock
from dida.tui import theme
from dida.tui.app import DidaApp
from dida.tui.keys import LAYER_DETAIL, LAYER_INDEX, LAYER_PROFILE, LAYER_TASKS, LAYER_TITLES
from support import screen_text

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
WIDE = (100, 30)


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


def backend() -> FakeBackend:
    """三个清单：「学习」是中间那一行，用来量「回来时还在原来那一行」。"""
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_list("学习", id="study")
    fake.add_list("生活", id="life")
    fake.add_task("写周报", list_name="work", id="t1", due=at(14, 18))
    return fake


def lines(text: str) -> list[str]:
    return text.splitlines()


async def move_to_row(pilot, app: DidaApp, row_id: str) -> None:
    """把清单列表页的光标走到某一行（最多三十步，走不到就是测试自己坏了）。"""
    for _ in range(30):
        if app.index_page().selected_id == row_id:
            return
        await pilot.press("j")
    raise AssertionError(f"走不到 {row_id!r} 这一行（当前停在 {app.index_page().selected_id!r}）")


async def open_profile(pilot, app: DidaApp) -> None:
    """清单列表页按 ``A`` 进个人主页。"""
    await pilot.press("A")
    await pilot.pause()


async def test_a_on_the_index_opens_the_profile_page_off_the_nav_path():
    """清单列表页按 ``A`` 进得去；顶栏只有「个人主页」一段，状态栏照常三项（ADR-0010 一）。"""
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_profile(pilot, app)
        text = screen_text(app)

        assert app.layer == LAYER_PROFILE, f"按 A 之后应当落在个人主页，实际在 {app.layer!r}"

        top = lines(text)[0]
        assert LAYER_TITLES[LAYER_PROFILE] in top, f"顶栏没写「个人主页」：{top!r}"
        for other in (LAYER_INDEX, LAYER_TASKS, LAYER_DETAIL):
            assert LAYER_TITLES[other] not in top, f"顶栏多画了一层路径：{top!r}"
        assert top.count(theme.BUILTIN_MARK) == 1, (
            f"顶栏的路径只有一段（词标后面那一个分隔符），实际是：{top!r}"
        )

        bottom = lines(text)[-1]
        assert bottom.startswith("已同步"), f"状态栏那一行不见了：{bottom!r}"
        for part in ("待推送", "逻辑日"):
            assert part in bottom, f"状态栏少了「{part}」：{bottom!r}"


@pytest.mark.parametrize("key", ["left", "escape"])
async def test_the_way_back_lands_on_the_row_you_left_from(key: str):
    """``←`` 与 ``esc`` 都回清单列表页，光标还在离开前那一行（先走到中间一行再进出）。

    断的是整屏一样：光标那一行、它上面的行、状态栏都该与离开前逐字节相同——「层变了又
    变回来」如果顺手把光标弹回第一行，这里当场红。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await move_to_row(pilot, app, "study")
        before = screen_text(app)
        assert next(line for line in lines(before) if "学习" in line).startswith(theme.CURSOR_MARK)

        await open_profile(pilot, app)
        assert app.layer == LAYER_PROFILE
        await pilot.press(key)
        await pilot.pause()

        after = screen_text(app)
        assert app.layer == LAYER_INDEX, f"{key} 没把人送回清单列表页（在 {app.layer!r}）"
        assert after == before, f"回来后屏幕不一样了——光标该还在「学习」那一行：\n{after}"
        study = next(line for line in lines(after) if "学习" in line)
        assert study.startswith(theme.CURSOR_MARK), f"光标被挪走了：{study!r}"


@pytest.mark.parametrize("layer", [LAYER_TASKS, LAYER_DETAIL])
async def test_a_does_nothing_on_the_task_list_and_detail_pages(layer: str):
    """``A`` 只在清单列表页有入口：任务列表页与详细页上它什么都不做（层不变、屏幕不变）。

    这两个页面上压根没有 ``A`` 的绑定（它只挂在清单列表页那一层），所以断的是「按下去
    什么都不发生」——不是「它报了句错」。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await move_to_row(pilot, app, "work")
        await pilot.press("right")  # 清单列表页 → 任务列表页
        await pilot.pause()
        if layer == LAYER_DETAIL:
            await pilot.press("right")  # 任务列表页 → 详细页
            await pilot.pause()
        assert app.layer == layer

        before = screen_text(app)
        await pilot.press("A")
        await pilot.pause()

        assert app.layer == layer, f"{layer} 上按 A 把人带走了（在 {app.layer!r}）"
        assert screen_text(app) == before, f"{layer} 上按 A 不该动屏幕"


@pytest.mark.parametrize(
    ("account_bound", "shown", "hidden"),
    [(True, "已绑定", "未绑定"), (False, "未绑定", "已绑定")],
)
async def test_the_page_states_the_binding_fact_it_was_handed(
    account_bound: bool, shown: str, hidden: str
):
    """绑定状态是**组合根交给界面**的那一位事实：交给它什么，它就说「已绑定 / 未绑定」。

    页面自己不判断绑没绑（它读不到配置文件，也不许读——TUI 只 import ``dida.sync.engine``
    与 ``dida.tui``）。所以这里从 app 的构造参数注入，断的是屏幕上那句话。
    """
    app = DidaApp(backend(), account_bound=account_bound)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_profile(pilot, app)
        text = screen_text(app)

    assert "账号绑定" in text, "个人主页上少了「账号绑定」那一项"
    assert shown in text, f"交进来的是 account_bound={account_bound}，屏幕上该写「{shown}」"
    assert hidden not in text, f"屏幕上同时出现了「{hidden}」，绑定状态不该有两个答案"


async def test_the_page_shows_no_account_identifier():
    """这一页**说不清自己绑到了谁**：屏幕上不出现任何账号标识（GLOSSARY「账号绑定」）。

    官方 API 没有「当前用户」端点，所以用户名 / 邮箱 / 头像 / token 片段一个都不该有。
    这一页只许说「已绑定 / 未绑定」——这一条在屏幕文本上逐词对账。
    """
    app = DidaApp(backend(), account_bound=True)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_profile(pilot, app)
        body = "\n".join(lines(screen_text(app))[1:-1])

    for marker in ("token", "Bearer", "@", "用户名", "邮箱", "头像", "账户", "账号名"):
        assert marker not in body, f"个人主页的正文里出现了账号标识「{marker}」：\n{body}"

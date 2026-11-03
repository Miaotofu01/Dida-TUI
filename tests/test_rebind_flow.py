"""换绑在界面上的样子（工单 #92）：入口、掩码、验证失败、影响面确认、yes 与重启纸条。

**接缝一**：真 ``DidaApp`` + ``FakeBackend``（它自带一支 ``FakeAccount``）+ Pilot。断的都是
外部行为——按了什么键、屏幕上是什么、假端口收到了什么。真磁盘与真配置那一条在
``tests/test_rebind_flow_real.py``（换个文件名不合适：那是同一个流程的接缝二版本）。

这一票的判据在这里逐条对：入口只在个人主页（``b``，``h`` 里说得出来）；输入框是掩码；
401 与断网分开说；确认页列出四个数并要 ``yes``；``yes`` 之后退出并交回
:class:`~dida.tui.app.RebindRestart`。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from dida.api.errors import AuthError, NetworkError
from dida.sync.engine import RebindConfigError, RebindDeleteError, RebindImpact
from dida.testing import FakeBackend, ManualClock
from dida.tui.app import REBIND_GROUP, DidaApp, RebindRestart
from dida.tui.keys import LAYER_PROFILE, LAYER_TITLES
from support import screen_text

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
WIDE = (100, 30)

TOKEN = "newtoken"
"""要粘贴进来的新 token。全 ASCII 字母：``pilot.press`` 逐字打进去，屏幕上该一个字母都看不见。"""


def backend() -> FakeBackend:
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task("写周报", list_name="work", id="t1")
    return fake


async def open_rebind(pilot) -> None:
    """清单列表页 ``A`` 进个人主页，再按 ``b`` 开换绑那张表单。"""
    await pilot.press("A")
    await pilot.pause()
    await pilot.press("b")
    await pilot.pause()


async def type_and_submit(pilot, text: str) -> None:
    """在表单第一格（打开就有焦点）里打进 ``text``，然后 ``Enter`` 提交。"""
    await pilot.press(*text)
    await pilot.press("enter")
    await pilot.pause()


async def settle_rebind(pilot, app: DidaApp) -> None:
    """等换绑那几个 worker 跑完。

    只等 ``REBIND_GROUP``，**不等同步组**：换绑流程故意会和一条被闸门挡住的同步共存
    （那正是「先停在飞的工作」要测的那一条），``app.workers.wait_for_complete()`` 不带参数
    会把那条永远等下去。
    """
    for _ in range(200):
        await pilot.pause()
        if not any(
            worker.group == REBIND_GROUP and not worker.is_finished for worker in app.workers
        ):
            return
    raise AssertionError("换绑的 worker 一直没跑完")


async def submit_token(pilot, app: DidaApp, token: str = TOKEN) -> None:
    """填 token → 提交 → 等验证那个 worker 跑完（成功时它会开确认页）。"""
    await type_and_submit(pilot, token)
    await settle_rebind(pilot, app)


async def answer_confirm(pilot, app: DidaApp, answer: str) -> None:
    """在确认页里输入 ``answer`` 并提交。"""
    await type_and_submit(pilot, answer)


# ------------------------------------------------------------------ 入口


async def test_the_rebind_entry_only_exists_on_the_profile_page():
    """``b`` 是换绑的入口，只挂在个人主页那一层；``h`` 里说得出来（判据一）。

    清单列表页上按 ``b`` 什么都不发生，``h`` 里也没有「换绑」——入口不在那里。进了个人主页
    之后，账号绑定那一行就写着它，``h`` 里也有它。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        index_screen = screen_text(app)
        await pilot.press("b")
        await pilot.pause()
        assert screen_text(app) == index_screen, "清单列表页上按 b 不该发生任何事"

        await pilot.press("h")
        await pilot.pause()
        index_help = screen_text(app)
        await pilot.press("escape")
        await pilot.pause()
        assert "换绑" not in index_screen, "清单列表页上不该出现换绑入口"
        assert "换绑" not in index_help, "清单列表页的帮助里不该有换绑键"

        await pilot.press("A")
        await pilot.pause()
        profile_screen = screen_text(app)
        await pilot.press("h")
        await pilot.pause()
        profile_help = screen_text(app)

    assert app.layer == LAYER_PROFILE
    assert "换绑" in profile_screen, f"个人主页的账号绑定那一行没写换绑入口：\n{profile_screen}"
    assert LAYER_TITLES[LAYER_PROFILE] in profile_help
    assert "换绑" in profile_help, f"个人主页的 h 里没有换绑键：\n{profile_help}"


async def test_b_on_the_profile_page_opens_a_token_form():
    """``b`` 开出来的是一张输入 token 的表单；它只说这一件事。"""
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_rebind(pilot)
        text = screen_text(app)

    assert "换绑" in text
    assert "token" in text.lower(), "表单上得说清这里填的是 token"


# ------------------------------------------------------------------ 掩码


async def test_the_raw_token_never_appears_on_screen():
    """输入框是掩码：抓一帧，原始 token 不出现在屏幕任何位置（判据二）。

    掩码是 ``Input(password=True)`` 的显示效果——``Input.value`` 仍是明文，所以这一条只能
    断屏幕文本，正是判据要的那一条。
    """
    app = DidaApp(backend())

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_rebind(pilot)
        await pilot.press(*TOKEN)
        await pilot.pause()
        text = screen_text(app)

    assert TOKEN not in text, f"原始 token 出现在屏幕上了：\n{text}"
    assert "•" * len(TOKEN) in text, "输入框里该是一串掩码点"


# ------------------------------------------------------------------ 验证失败：停在输入框


async def test_a_rejected_token_stays_in_the_box_and_says_it_is_a_credential_problem():
    """401：停在输入框（带着刚才那枚 token，仍是掩码），而且说清是凭据被拒（判据三）。"""
    fake = backend()
    fake.account.verify_error = AuthError("凭据被拒绝：HTTP 401", status_code=401)
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_rebind(pilot)
        await submit_token(pilot, app, "badtoken")
        text = screen_text(app)

        assert fake.account.verified == ["badtoken"]
        assert fake.account.executed == [], "验证没过就不许走到执行"
        assert "凭据" in text, f"401 要说清是凭据问题：\n{text}"
        assert "401" in text
        assert "badtoken" not in text, "重开的表单里 token 仍必须是掩码"
        assert "•" * len("badtoken") in text, "重开的表单要带着刚才那枚 token（停在输入框）"
        assert app.layer == LAYER_PROFILE


async def test_a_network_failure_says_network_not_a_dead_token():
    """断网说的是网络，不是「token 失效」——两句话让用户做两件不同的事（判据四）。"""
    fake = backend()
    fake.account.verify_error = NetworkError("连不上服务端：断网了")
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_rebind(pilot)
        await submit_token(pilot, app)
        text = screen_text(app)

    assert "连不上服务端" in text, f"断网时要说网络：\n{text}"
    assert "凭据被拒绝" not in text, "网络问题不许被说成凭据失效"
    assert fake.account.executed == []


# ------------------------------------------------------------------ 影响面与 yes


async def test_the_confirm_page_lists_the_four_impact_counts():
    """确认页列出四个数，而且要的是 ``yes``（判据五）。

    四个数是端口在删库**之前**读出来的那一份（``impact_calls == 1``），一个都不许省：
    清单 / 任务 / 自定义视图 / 待推送。
    """
    fake = backend()
    fake.account.counts = RebindImpact(lists=3, tasks=4, views=2, pending=1)
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_rebind(pilot)
        await submit_token(pilot, app)
        text = screen_text(app)

        assert fake.account.impact_calls == 1, "确认页显示之前要读过一次影响面"
        assert fake.account.executed == [], "还没输入 yes，什么都不许执行"
        for piece in ("3 个清单", "4 条任务", "2 个自定义视图", "1 笔待推送", "yes"):
            assert piece in text, f"确认页少了「{piece}」：\n{text}"


@pytest.mark.parametrize("answer", ["no", "", "y"])
async def test_anything_but_yes_executes_nothing(answer: str):
    """不输入 ``yes``：什么都不执行（判据六在界面这一侧的半边：``execute`` 没被调用）。"""
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_rebind(pilot)
        await submit_token(pilot, app)
        await answer_confirm(pilot, app, answer)

        assert fake.account.executed == [], f"输入 {answer!r} 也执行了换绑"
        assert app.layer == LAYER_PROFILE, "没换绑就还在个人主页上"


async def test_yes_executes_the_rebind_and_hands_back_a_restart_on_the_profile_page():
    """``yes`` 之后：执行换绑，app 退出并把「落在个人主页」那张纸条交回去（判据七的界面半边）。

    ``notice`` / ``force_refresh`` 是 #93 / #94 的，这一票成功后**只**置 ``land_on_profile``。
    """
    fake = backend()
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_rebind(pilot)
        await submit_token(pilot, app)
        await answer_confirm(pilot, app, "yes")

    assert fake.account.executed == [TOKEN], "yes 之后才真的执行"
    assert app.return_value == RebindRestart(land_on_profile=True), (
        f"换绑成功后要交回一张重开便条，实际是 {app.return_value!r}"
    )


async def test_a_delete_failure_says_which_step_failed_and_what_was_left_alone():
    """删库失败：屏幕原句说出哪一步、原因，以及配置与旧库都没动（工单 #93 判据一）。

    这一条断的是交给下一个 app 的那句话；重启之后它真的出现在屏幕上、旧库也真的还能用，
    在 ``tests/test_rebind_flow_real.py`` 里按真磁盘断。
    """
    fake = backend()
    fake.account.execute_error = RebindDeleteError("database is busy")
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_rebind(pilot)
        await submit_token(pilot, app)
        await answer_confirm(pilot, app, "yes")

    result = app.return_value
    assert isinstance(result, RebindRestart), f"执行失败也要重启，实际 {result!r}"
    assert result.land_on_profile is False, "失败那条路不回个人主页"
    notice = result.notice or ""
    assert "删库失败" in notice, f"要说清是哪一步失败了：{notice}"
    assert "database is busy" in notice, f"要带上失败原因：{notice}"
    assert "配置" in notice and "旧库" in notice, f"要说清配置与旧库都没动：{notice}"


async def test_a_failed_execute_still_restarts_but_carries_the_failure_notice():
    """执行失败：不留在半死状态，退出并把失败那句话交给下一个 app（#92 的失败形状；#93 定原句）。"""
    fake = backend()
    fake.account.execute_error = RebindConfigError(
        "库已经删了，但写配置失败——配置里仍是旧账号，重开是旧账号 + 空库"
    )
    app = DidaApp(fake)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_rebind(pilot)
        await submit_token(pilot, app)
        await answer_confirm(pilot, app, "yes")

    result = app.return_value
    assert isinstance(result, RebindRestart), f"执行失败也要重启，实际 {result!r}"
    assert result.land_on_profile is False
    assert result.notice is not None and "库已经删了" in result.notice


# ------------------------------------------------------------------ 在飞的同步先停


class GatedBackend(FakeBackend):
    """``refresh`` 卡在一道闸门上，直到被取消——用来断「换绑前先把在飞的同步停掉」。"""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.started = asyncio.Event()
        self.cancelled = False

    async def refresh(self):  # noqa: ANN201 - 测试替身
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


async def test_the_in_flight_sync_is_cancelled_before_execute():
    """关库之前必须先把在飞的同步停掉——否则回来的协程会在关掉的库上炸（判据七的地雷）。

    这里让启动刷新卡住，然后在换绑执行的那一刻问一句「同步是不是已经被取消过了」：
    ``execute`` 看到 ``cancelled`` 才是先停后执行；没停的话它看到的是 ``False``。
    """
    fake = GatedBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    app = DidaApp(fake, refresh_on_start=True)
    seen: list[bool] = []
    original = fake.account.execute

    async def execute(token: str) -> None:
        seen.append(fake.cancelled)
        await original(token)

    fake.account.execute = execute  # type: ignore[method-assign]

    async with app.run_test(size=WIDE) as pilot:
        await fake.started.wait()
        await open_rebind(pilot)
        await submit_token(pilot, app)
        await answer_confirm(pilot, app, "yes")

    assert seen == [True], "执行换绑时，启动刷新那个 worker 还没被取消／等到结束"
    assert fake.refreshes == 0, "被取消的刷新不该假装跑完了"


# ------------------------------------------------------------------ 重启那三个参数（#92 建、#93/#94 用）


async def test_a_restarted_app_lands_on_the_profile_page():
    """``start_layer`` 真的能用：新 app 一开屏就落在个人主页（判据七的「落在个人主页」）。"""
    app = DidaApp(backend(), start_layer=LAYER_PROFILE)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert app.layer == LAYER_PROFILE
        assert LAYER_TITLES[LAYER_PROFILE] in screen_text(app)


async def test_a_restarted_app_shows_its_notice_at_mount():
    """``notice`` 在落地时用 toast 说出来（#93 的失败话术就搭这一条）。"""
    app = DidaApp(backend(), notice="库已经删了，但配置仍是旧账号")

    async with app.run_test(size=WIDE, notifications=True) as pilot:
        await pilot.pause()
        assert "库已经删了" in screen_text(app)


async def test_force_refresh_refreshes_even_when_the_config_says_not_to():
    """``force_refresh`` 压过 ``refresh_on_start``：换完绑要立刻重拉（#94 的接线，#92 先建）。"""
    fake = backend()
    app = DidaApp(fake, refresh_on_start=False, force_refresh=True)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert fake.refreshes == 1, "forced refresh 没跑"


async def test_an_ordinary_start_is_unchanged():
    """普通启动：不通知、不强制刷新，``refresh_on_start=False`` 就一轮都不跑。"""
    fake = backend()
    app = DidaApp(fake, refresh_on_start=False)

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()

    assert fake.refreshes == 0
    assert app.return_value is None

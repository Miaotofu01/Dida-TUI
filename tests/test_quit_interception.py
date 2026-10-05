"""退出拦截（#47）：``q`` 与 ``Ctrl+C`` 走**同一个判断**。

**接缝一**：真 ``DidaApp`` + ``FakeBackend`` + Pilot。断的是「按了这个键，屏幕上看得见什么、
app 还在不在跑」——不读控件树、不读内部状态。

这张票存在的理由是一个**第二条退出路径**：``q`` 已经被拦住，而 ``Ctrl+C`` 走的是框架那条
默认路径。待推送改动只活在本地库里（ADR-0002 的豁免代价），一次没被拦住的退出就是一条静默
丢改动的后门——用户以为「按了就是了」，而服务端并不知道他做过什么。

四种组合都要在：有/无待推送 × ``q`` / ``Ctrl+C``。其中两种「有」的是本票的正文，
两种「无」的钉住「别把拦截做得连正常退出都要问一句」。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dida.testing import FakeBackend, ManualClock
from dida.tui.app import DidaApp
from dida.tui.keys import BINDINGS, GLOBAL, LAYER_INDEX, bindings_for
from support import screen_text

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
WIDE = (100, 30)

QUIT_KEYS = ("q", "ctrl+c")
"""用户能用来退出 app 的那两个键——两条路必须是同一个判断。"""


def backend(*, pending: int = 0) -> FakeBackend:
    fake = FakeBackend(clock=ManualClock(T0))
    fake.add_list("工作", id="work")
    fake.add_task("写周报", list_name="work", id="t1", due=T0 + timedelta(hours=6))
    fake.set_sync_state(last_refresh_at=T0, pending_count=pending)
    return fake


def assert_exited(app: DidaApp) -> None:
    """这个 app 已经不在跑了。

    ``is_running`` 是 app 自己的公开属性（不是从控件树上猜的）：``run_test`` 还没收尾时它
    就已经是 ``False``，所以「按键之后它退没退」当场就能断，不必等 ``with`` 块结束。
    """
    assert app.is_running is False, "这个键该退出去了"


# ---------------------------------------------------------------- 同一个判断


def test_both_quit_keys_are_bound_to_the_same_action():
    """按下两个键走的是**同一个动作名**——「同一个判断」说的就是这件事。

    跟着绑定表断，不另抄一份：表里怎么写的，这里就怎么断（#48 那张表是唯一出处）。
    """
    table = {
        name: binding.action
        for binding in bindings_for(LAYER_INDEX)
        for name in binding.key.split(",")
    }
    actions = {table.get(key) for key in QUIT_KEYS}

    assert actions == {"quit"}, f"这两个键都该走 quit 那一个判断，实际是 {actions}"


def test_the_global_layer_offers_both_quit_keys():
    """两条路都写在全局那一层：三层页面都该能按这两个键退出（不用先退回层一）。"""
    offered = {name for key in BINDINGS[GLOBAL] for name in key.keys}

    assert set(QUIT_KEYS) <= offered, f"全局那一层少了退出键：{set(QUIT_KEYS) - offered}"


# ---------------------------------------------------------------- 有待推送改动：两个键都被拦


async def test_ctrl_c_is_interrupted_when_there_are_pending_changes():
    """**本票的正文**：有待推送改动时 ``Ctrl+C`` 也被拦下，并且说清有几处。

    这里断的是外部行为：屏幕还在、app 还在跑、浮层上写着那个数。
    """
    app = DidaApp(backend(pending=3))

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()
        interrupted = screen_text(app)

        assert app.is_running, "还有没推上去的改动时，Ctrl+C 不该直接退"
        assert "3 处" in interrupted, "要说清有几处没推上去"
        assert "仍然退出" in interrupted, "浮层里要给出「仍然退出」这条路"


async def test_q_is_interrupted_when_there_are_pending_changes():
    """另一条路同样被拦（``q`` 的行为不能被这次改动改坏）。"""
    app = DidaApp(backend(pending=3))

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        interrupted = screen_text(app)

        assert app.is_running, "还有没推上去的改动时，q 不该直接退"
        assert "3 处" in interrupted, "要说清有几处没推上去"


async def test_ctrl_c_quits_after_the_user_confirms():
    """确认之后真的退——拦截不是「退不出去」，是多问一句。"""
    app = DidaApp(backend(pending=2))

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app.is_running, "先确认它确实被拦住了"

        await pilot.press("y")
        await pilot.pause()
        assert_exited(app)


async def test_ctrl_c_cancelled_stays_and_the_prompt_can_be_raised_again():
    """``n`` / ``Esc`` 是留下，而且第二遍还得能拦（浮层不能只拦一次）。"""
    app = DidaApp(backend(pending=2))

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert app.is_running, "取消不是退出"

        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app.is_running, "再按一次还是拦"
        assert "2 处" in screen_text(app)


# ---------------------------------------------------------------- 没有待推送改动：两个键直接退


async def test_ctrl_c_quits_straight_away_when_nothing_is_pending():
    """没有待推送改动就照旧直接退——别把正常退出也变成一次盘问。"""
    app = DidaApp(backend(pending=0))

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert_exited(app)


async def test_q_quits_straight_away_when_nothing_is_pending():
    """``q`` 那一半照旧（本票不许把它改成要确认）。"""
    app = DidaApp(backend(pending=0))

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        assert_exited(app)


# ---------------------------------------------------------------- 措辞：不许说改动丢了


async def test_the_ctrl_c_prompt_does_not_claim_the_queue_is_lost():
    """``Ctrl+C`` 那条路说出来的是**同一句话**，而且同样不许说「就丢了」。

    待推送改动落在本地库的 ``pending_changes`` 表里，进程结束不等于它们没了：下次启动接着
    补推。吓唬用户说丢了，是拿一句不真的话换他一次犹豫。
    （``tests/test_sync_push.py::test_a_restart_still_has_the_queue_and_pushes_it`` 是这句话
    的事实依据；``tests/test_app_actions.py`` 那条钉的是 ``q`` 那一半。）
    """
    app = DidaApp(backend(pending=1))

    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()
        prompt = screen_text(app)

    assert "1 处" in prompt
    assert "下次打开" in prompt, "要说清它们去哪儿了：留在本地，下次接着补推"
    assert "丢了" not in prompt, "它们没丢——本地库里有，下次启动会接着推"

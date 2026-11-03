"""换绑成功之后的回声与无条件全量刷新（工单 #94）。

#92 把重启通道建好了：``_execute_rebind`` 交回一张
:class:`~dida.tui.app.RebindRestart`，``main`` 的启动循环重读配置、用
:func:`~dida.bootstrap.build_app` **新造一个** app 再 ``run()``。这一张票只补成功那张纸条上
的三项：落在个人主页、落地说一句「已换绑到新账号」、不管 ``refresh_on_start`` 配的是什么
都立刻跑一轮全量刷新——以及它们在新 app 上的样子。

纸条内容断在 ``tests/test_rebind_flow.py`` 的 ``yes`` 那条；这里按 ``main`` 的做法把新 app
装出来（``build_app(config=新配置, transport=FakeTransport(...), db_path=tmp,
config_file=tmp, start_layer=..., notice=..., force_refresh=True)``），再看屏幕、看请求头、
看刷新之后的清单与任务。失败那条路（#93）一个字都不在这份文件里。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from dida.bootstrap import build_app
from dida.config import Config, load_config, save_config
from dida.storage.store import Store
from dida.testing import FakeBackend, ManualClock
from dida.tui import theme
from dida.tui.app import DidaApp
from dida.tui.keys import LAYER_INDEX, LAYER_PROFILE
from support import screen_text

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 3, 14, 12, 3, tzinfo=TZ)
WIDE = (100, 30)

NEW_TOKEN = "new-token-5678"
NEW_LIST = "换绑后的清单"
NEW_TASK = "换绑后的任务"
OLD_LIST = "旧账号的清单"
OLD_TASK = "旧账号的任务"
ECHO = "已换绑到新账号"


class NewAccountServer:
    """换绑之后那个账号的假服务端：只有**一份**新清单，带着一条新任务。

    路径与 ``DidaApiClient`` 的调用一一对应（与 ``tests/test_composition_root.py`` 的
    ``Server`` 同一形态）：全量刷新先拉清单索引，再逐清单拉 data（收集箱总会被拉一次），
    最后拉已完成流。请求一条不落地记下来，好断「用的是哪一枚 token」。
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    async def send(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/open/v1/project":
            return httpx.Response(
                200, json=[{"id": "p-new", "name": NEW_LIST, "sortOrder": 0}]
            )
        if path == "/open/v1/project/p-new/data":
            return httpx.Response(
                200,
                json={
                    "project": {"id": "p-new", "name": NEW_LIST, "sortOrder": 0},
                    "tasks": [
                        {"id": "n1", "projectId": "p-new", "title": NEW_TASK, "status": 0}
                    ],
                },
            )
        if path == "/open/v1/project/inbox/data":
            return httpx.Response(
                200,
                json={"project": {"id": "inbox", "name": "收集箱"}, "tasks": []},
            )
        if path == "/open/v1/task/completed":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={})


def seed_old_account(db_path: Path) -> None:
    """先在本地副本里摆一份**旧账号**的东西。

    换绑本该在 #92 就把这个库删掉（ADR-0011 二第 5 条）；这里故意留一份，钉的是 #94 那一轮
    是**全量**刷新——它把本地副本整个换成新账号的，而不是只往上叠。所以刷新之后旧清单与旧
    任务都必须从屏幕与库里消失。
    """
    with Store(db_path) as store:
        store.apply_refresh(
            lists=[{"id": "p-old", "name": OLD_LIST, "sortOrder": 0}],
            tasks=[{"id": "o1", "projectId": "p-old", "title": OLD_TASK, "status": 0}],
        )


def rebound_app(tmp_path: Path, server: NewAccountServer) -> DidaApp:
    """按 ``main`` 换绑后重启的做法装一个新 app（三项来自那张 ``RebindRestart`` 纸条）。

    配置是换绑**已经写好**的那一份（新 token、``refresh_on_start=False``），库路径是换绑
    刚删掉的那个文件——新 app 打开时它是一条空库。三个重启参数照 ``run_startup_loop`` 的
    翻法给：``land_on_profile`` → ``start_layer``，``notice`` / ``force_refresh`` 原样。
    """
    config_file = tmp_path / "config.toml"
    save_config(Config(token=NEW_TOKEN, refresh_on_start=False), config_file)
    return build_app(
        config=load_config(config_file),
        transport=server,
        db_path=tmp_path / "cache.sqlite3",
        config_file=config_file,
        start_layer=LAYER_PROFILE,
        notice=ECHO,
        force_refresh=True,
    )


async def test_the_rebound_app_lands_on_the_profile_with_the_echo_and_refreshes_with_the_new_token(
    tmp_path,
):
    """判据一 / 二 / 四：落在个人主页、屏幕上有回声、``refresh_on_start=False`` 也刷新、
    请求头是新 token、刷新完清单列表页上是新账号的清单与任务（旧账号的一个都不剩）。
    """
    seed_old_account(tmp_path / "cache.sqlite3")
    server = NewAccountServer()
    app = rebound_app(tmp_path, server)

    async with app.run_test(size=WIDE, notifications=True) as pilot:
        await pilot.pause()
        assert app.layer == LAYER_PROFILE, "换绑成功要落在个人主页"
        landing = screen_text(app)
        assert ECHO in landing, f"落地那一刻屏幕上没有回声：\n{landing}"

        # ``refresh_on_start=False``，可这一轮必须发出去：库刚被清空，不刷新就是一片空白。
        await app.workers.wait_for_complete()

        after = screen_text(app)
        assert ECHO in after, "随后的那次刷新重画把回声抹掉了"
        assert server.requests, "refresh_on_start=False 时也必须无条件刷新一次"

        sent = {request.headers.get("Authorization") for request in server.requests}
        assert sent == {f"Bearer {NEW_TOKEN}"}, f"刷新没带新 token：{sent}"

        # 刷新结束：清单列表页上是新账号的东西，旧账号的名字一个都不在。
        await pilot.press("left")
        await pilot.pause()
        index_screen = screen_text(app)
        assert NEW_LIST in index_screen, f"清单列表页上没有新账号的清单：\n{index_screen}"
        assert OLD_LIST not in index_screen, f"旧账号的清单还留在屏幕上：\n{index_screen}"

        # 收集箱 → 今天 → 最近七天 → 所有 → 换绑后的清单（索引里只有这一份真实清单）。
        await pilot.press("j", "j", "j", "j")
        await pilot.press("right")
        await pilot.pause()
        tasks_screen = screen_text(app)
        assert NEW_TASK in tasks_screen, f"任务列表页上没有新账号的任务：\n{tasks_screen}"
        assert OLD_TASK not in tasks_screen, f"旧账号的任务还留在屏幕上：\n{tasks_screen}"


async def test_an_ordinary_start_still_obeys_refresh_on_start(tmp_path):
    """普通启动一个字都不变：没有 ``force_refresh`` / ``notice`` 时照旧听 ``refresh_on_start``。

    这一条是 #94 的**反面**：换绑之后那次是特例，不许把普通启动的闸门顺手拆掉。
    """
    config_file = tmp_path / "config.toml"
    save_config(Config(token=NEW_TOKEN, refresh_on_start=False), config_file)
    server = NewAccountServer()
    app = build_app(
        config=load_config(config_file),
        transport=server,
        db_path=tmp_path / "cache.sqlite3",
        config_file=config_file,
    )

    async with app.run_test(size=WIDE, notifications=True) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()

        assert app.layer == LAYER_INDEX, "普通启动还是落在清单列表页"
        assert server.requests == [], "refresh_on_start=False 时普通启动一轮都不跑"
        assert ECHO not in screen_text(app), "普通启动不该弹出换绑的回声"


class GatedRefreshBackend(FakeBackend):
    """``refresh()`` 卡在一道闸门上，直到测试放行——用来在**刷新进行中**截一帧。"""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def refresh(self):  # noqa: ANN201 - 测试替身
        self.started.set()
        await self.release.wait()
        return await super().refresh()


async def test_the_forced_refresh_shows_the_existing_sync_spinner_while_it_runs():
    """判据三：刷新进行中屏幕上看得见同步提示——用的就是状态栏那一个转圈。

    **不等真实时间**：刷新被闸门卡住，阈值那一步（``SPINNER_DELAY_MS`` 的定时器）直接交给
    app 自己那条路（``_maybe_spin``），截一帧之后再放行。断的是三件事：提示是既有那一句
    （``同步中 · `` + 一个 ``SPINNER_FRAMES`` 的帧），它只有一份措辞，刷新结束就收掉。
    """
    backend = GatedRefreshBackend(clock=ManualClock(T0))
    app = DidaApp(
        backend,
        refresh_on_start=False,
        force_refresh=True,
        start_layer=LAYER_PROFILE,
        notice=ECHO,
        account_bound=True,
    )

    async with app.run_test(size=WIDE, notifications=True) as pilot:
        await pilot.pause()  # 落地那一帧：个人主页 + 回声
        await backend.started.wait()
        # 阈值那一步不真等：先停掉还没到点的定时器，再走 app 自己的「到点了」回调。
        if app._spin_delay_timer is not None:
            app._spin_delay_timer.stop()
        if not app._spinning:
            app._maybe_spin()
        await pilot.pause()  # 让状态栏那一帧上屏

        during = screen_text(app)
        assert "同步中 · " in during, f"刷新进行中屏幕上看不到同步提示：\n{during}"
        assert any(frame in during for frame in theme.SPINNER_FRAMES), (
            f"同步提示里没有转圈那一帧：\n{during}"
        )
        assert during.count("同步中") == 1, f"同一件事出现了第二套措辞：\n{during}"
        assert ECHO in during, "转圈把落地的那句回声挤掉了"

        backend.release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()

        after = screen_text(app)
        assert "同步中" not in after, f"刷新结束之后转圈没有收掉：\n{after}"
        assert "已同步" in after, "刷新结束之后状态栏还是原来那一句"

"""换绑之后的**同进程重启**（工单 #92 建，与 #93 / #94 共用）。

一次换绑把旧库删了、把新 token 写进了配置，可这个进程里的引擎、周期泵、client 全都还握着
旧库 / 旧 token。ADR-0011 六选的是「app 退出 → 组合根重读配置 → **新造一个 app 实例**再
``run()``」，不是在原实例里换掉那几样东西（漏一处就是静默串号）。

这里的接缝是**注入的 build / run 假手**：不起真终端、不碰网络，断的是「重开几次、拿什么
参数重开、配置是什么时候重读的」。真正 ``dida`` 命令那条路也走 monkeypatch ``run``
（模板在 ``tests/test_live_day_boundary.py``）：#92 的「成功后重开」与 #93 的两条失败重启
（``test_main_restarts_after_*``）都在那里按真配置 / 真库断。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import dida.rebind
from dida.bootstrap import main, run_startup_loop
from dida.config import Config, load_config, save_config
from dida.storage.store import Store
from dida.sync.engine import DidaError
from dida.sync.views import ViewDefinition
from dida.tui import messages
from dida.tui.app import DidaApp, RebindRestart
from dida.tui.keys import LAYER_PROFILE

OLD_TOKEN = "old-token-1234"
NEW_TOKEN = "newtoken"
"""这一层只需要两枚凭据：旧的一直留在配置里，新的那次根本没写进去。"""


def make_config_file(tmp_path, token: str = "old-token"):
    path = tmp_path / "config.toml"
    save_config(Config(token=token, refresh_on_start=False), path)
    return path


def populate(db_path: Path) -> None:
    """旧本地副本里有东西可丢：一个清单、一条任务、一个自定义视图。"""
    store = Store(db_path)
    try:
        store.apply_refresh(
            lists=[{"id": "p1", "name": "工作", "sortOrder": 0}],
            tasks=[{"id": "t1", "projectId": "p1", "title": "写周报", "status": 0}],
        )
        store.save_view(ViewDefinition(id="v1", name="高优先级"))
    finally:
        store.close()


def content(db_path: Path) -> tuple[object, ...]:
    """本地副本里会随库一起消失的那些内容（内容对账，不是 mtime）。"""
    store = Store(db_path)
    try:
        return (store.lists(), store.tasks(), store.view_definitions())
    finally:
        store.close()


def failure_restart(app: DidaApp) -> RebindRestart:
    """第一次 ``run``：替 app 走一遍它的失败路，再把它会交回来的那张纸条交回来。

    换掉的只有 ``DidaApp.run`` 这个框架边界（真的会进 alt-screen 并阻塞）；里面调的是**真的**
    ``engine.account.execute``，失败注入在失败点。原句也走生产那一支
    :func:`~dida.tui.messages.rebind_execute_failed_message`，不在这里手抄一句。
    """
    account = app.engine.account
    assert account is not None, "组合根必须把换绑端口挂上"
    try:
        asyncio.run(account.execute(NEW_TOKEN))
    except DidaError as exc:
        return RebindRestart(
            land_on_profile=False,
            notice=messages.rebind_execute_failed_message(exc),
        )
    raise AssertionError("这一次换绑本该失败，结果成功了")


def run_with(results: list[object], built: list[dict]) -> object:
    """一个假的 run：按顺序交回摆好的结果；``build`` 收到的参数记在 ``built`` 里。"""

    def build(**kwargs: object) -> SimpleNamespace:
        built.append(kwargs)
        return SimpleNamespace(**kwargs)

    def run(app: object) -> object:
        return results[len(built) - 1]

    return build, run


def test_a_restart_builds_a_second_app_on_the_profile_page_and_rereads_the_config(tmp_path):
    """成功换绑：run 交回 ``land_on_profile`` → 重读配置、**新造**一个 app 落在个人主页。

    第二次装配拿到的必须是**重读过的**配置（换绑已经把新 token 写进去了）——不是第一次那
    一份。旧实例不重用（ADR-0011 六）。
    """
    config_file = make_config_file(tmp_path)
    built: list[dict] = []
    results: list[object] = [RebindRestart(land_on_profile=True), None]

    def build(**kwargs: object) -> SimpleNamespace:
        built.append(kwargs)
        if len(built) == 1:
            # 第一个 app 跑的时候「换绑」把新 token 写进了配置。
            save_config(Config(token="new-token", refresh_on_start=False), config_file)
        return SimpleNamespace(**kwargs)

    def run(app: object) -> object:
        return results[len(built) - 1]

    run_startup_loop(
        build=build, run=run, config=load_config(config_file), config_file=config_file
    )

    assert len(built) == 2, f"应当正好重开一次，实际 build 了 {len(built)} 次"
    assert built[0]["start_layer"] is None, "第一次装配是普通启动"
    assert built[1]["start_layer"] == LAYER_PROFILE, "换绑成功后新 app 落在个人主页"
    assert built[1]["notice"] is None and built[1]["force_refresh"] is False, (
        "纸条里没写的两项按默认值传过去——#94 的成功路会写上它们，循环自己不添油加醋"
    )
    assert built[1]["config"].token == "new-token", "第二次装配必须用重读过的配置"


def test_an_ordinary_exit_runs_exactly_one_app(tmp_path):
    """普通退出（run 返回 ``None``）：只 build 一次——没有 RebindRestart 就没有第二次。"""
    config_file = make_config_file(tmp_path)
    built: list[dict] = []
    build, run = run_with([None], built)

    run_startup_loop(
        build=build, run=run, config=load_config(config_file), config_file=config_file
    )

    assert len(built) == 1


def test_a_failed_restart_carries_its_notice_and_stays_off_the_profile_page(tmp_path):
    """失败（#93 那种重开）：``notice`` 原样交给新 app，而且不落在个人主页。"""
    config_file = make_config_file(tmp_path)
    built: list[dict] = []
    build, run = run_with(
        [
            RebindRestart(
                land_on_profile=False,
                notice="库已经删了，但写配置失败——配置里仍是旧账号，重开是旧账号 + 空库",
            ),
            None,
        ],
        built,
    )

    run_startup_loop(
        build=build, run=run, config=load_config(config_file), config_file=config_file
    )

    assert len(built) == 2
    assert built[1]["start_layer"] is None
    assert "库已经删了" in built[1]["notice"]


def test_force_refresh_is_handed_to_the_restarted_app(tmp_path):
    """#94 的「换完绑立刻重拉」：``force_refresh`` 原样传进第二次装配。"""
    config_file = make_config_file(tmp_path)
    built: list[dict] = []
    build, run = run_with(
        [RebindRestart(land_on_profile=True, force_refresh=True), None],
        built,
    )

    run_startup_loop(
        build=build, run=run, config=load_config(config_file), config_file=config_file
    )

    assert built[1]["force_refresh"] is True


def test_the_command_rebuilds_the_app_after_a_rebind(monkeypatch, tmp_path):
    """``dida`` 命令本身走那条循环：``run()`` 交回纸条就重开一个**新实例**（新引擎、新库连接）。

    ``run`` 换成假的（真的会进 alt-screen 并阻塞到用户按 ``q``）：换掉的是框架边界，不是被测
    的那一段接线。``HOME`` 指到 tmp，配置与库都落在那里。
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = tmp_path / ".config" / "dida-tui" / "config.toml"
    save_config(Config(token="tok-1234", refresh_on_start=False), config_file)

    composed: list[DidaApp] = []
    results: list[object] = [RebindRestart(land_on_profile=True), None]

    def fake_run(self: DidaApp) -> object:
        composed.append(self)
        return results.pop(0)

    monkeypatch.setattr(DidaApp, "run", fake_run)

    main()

    assert len(composed) == 2, f"换绑后应当重开一次，实际装了 {len(composed)} 个 app"
    assert composed[1] is not composed[0], "必须是新实例，不是原地换"
    assert composed[1].engine is not composed[0].engine, "新 app 要有新引擎"
    assert composed[1]._start_layer == LAYER_PROFILE, "新 app 开屏落在个人主页"


def test_main_restarts_after_a_delete_failure_with_the_old_copy_intact(monkeypatch, tmp_path):
    """删库失败后 ``dida`` 命令重开一个新 app：配置与旧库都没动，失败原句跟着走（判据一 / 二）。

    第一次 ``run`` 里走的是**真的** ``engine.account.execute``，失败注入在失败点（这个库文件的
    ``unlink``）——旧库还在、配置仍是旧 token、新实例带着失败原句，三件事一起断。
    ``DidaApp.run`` 换掉的只是框架边界（真的会进 alt-screen 并阻塞到用户按 ``q``）。
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = tmp_path / ".config" / "dida-tui" / "config.toml"
    save_config(Config(token=OLD_TOKEN, refresh_on_start=False), config_file)
    db_path = config_file.with_name("cache.sqlite3")
    populate(db_path)
    rows_before = content(db_path)
    config_before = load_config(config_file)

    real_unlink = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self == db_path:
            raise OSError("database is busy")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)

    composed: list[DidaApp] = []

    def fake_run(self: DidaApp) -> object:
        composed.append(self)
        return failure_restart(self) if len(composed) == 1 else None

    monkeypatch.setattr(DidaApp, "run", fake_run)

    main()

    assert len(composed) == 2, f"删库失败后也要重开一次，实际装了 {len(composed)} 个 app"
    assert composed[1] is not composed[0], "必须是新实例，不是原地换"
    assert composed[1]._start_layer is None, "失败那条路不回个人主页"
    assert load_config(config_file) == config_before, "配置仍是旧 token（别的一项都没动）"
    assert content(db_path) == rows_before, "旧库里的清单 / 任务 / 自定义视图一条都没少"
    notice = composed[1]._notice or ""
    assert "删库失败" in notice, f"新 app 没带上「哪一步失败」：{notice}"
    assert "database is busy" in notice, f"新 app 没带上失败原因：{notice}"


def test_main_restarts_after_a_config_write_failure_into_an_empty_copy(monkeypatch, tmp_path):
    """写配置失败后重开：配置是旧 token，库是一个刚建出来的空库，屏幕原句跟着走（判据三）。

    注入点是 ``RebindService`` 写配置那一下；关库与删库都真的发生了，所以「旧账号 + 空库」
    这条半截状态是执行出来的，不是测试摆出来的。
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = tmp_path / ".config" / "dida-tui" / "config.toml"
    save_config(Config(token=OLD_TOKEN, refresh_on_start=False), config_file)
    db_path = config_file.with_name("cache.sqlite3")
    populate(db_path)
    config_before = load_config(config_file)

    real_save = dida.rebind.save_config

    def save(config: Config, path: Path | None = None) -> None:
        if path == config_file:
            raise OSError("EACCES")
        real_save(config, path)

    monkeypatch.setattr(dida.rebind, "save_config", save)

    composed: list[DidaApp] = []
    gone_after_failure: list[bool] = []

    def fake_run(self: DidaApp) -> object:
        composed.append(self)
        if len(composed) == 1:
            result = failure_restart(self)
            gone_after_failure.append(not db_path.exists())
            return result
        return None

    monkeypatch.setattr(DidaApp, "run", fake_run)

    main()

    assert gone_after_failure == [True], "写配置失败之前库应当已经删掉"
    assert len(composed) == 2, f"写配置失败后也要重开一次，实际装了 {len(composed)} 个 app"
    assert composed[1]._start_layer is None
    assert load_config(config_file) == config_before, "重开之后配置仍是旧 token"
    assert content(db_path) == ((), (), ()), "重开的是一个空库：旧清单 / 任务 / 视图都没了"
    notice = composed[1]._notice or ""
    assert "库已删、配置仍是旧账号" in notice, f"新 app 没说出那句话：{notice}"
    assert "EACCES" in notice, f"新 app 没带上失败原因：{notice}"

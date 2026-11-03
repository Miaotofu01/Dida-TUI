"""换绑之后的**同进程重启**（工单 #92 建，与 #93 / #94 共用）。

一次换绑把旧库删了、把新 token 写进了配置，可这个进程里的引擎、周期泵、client 全都还握着
旧库 / 旧 token。ADR-0011 六选的是「app 退出 → 组合根重读配置 → **新造一个 app 实例**再
``run()``」，不是在原实例里换掉那几样东西（漏一处就是静默串号）。

这里的接缝是**注入的 build / run 假手**：不起真终端、不碰网络，断的是「重开几次、拿什么
参数重开、配置是什么时候重读的」。真正 ``dida`` 命令那条路另有一条 monkeypatch ``run`` 的
测试（模板在 ``tests/test_live_day_boundary.py``）。
"""

from __future__ import annotations

from types import SimpleNamespace

from dida.bootstrap import main, run_startup_loop
from dida.config import Config, load_config, save_config
from dida.tui.app import DidaApp, RebindRestart
from dida.tui.keys import LAYER_PROFILE


def make_config_file(tmp_path, token: str = "old-token"):
    path = tmp_path / "config.toml"
    save_config(Config(token=token, refresh_on_start=False), path)
    return path


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
        "成功那条路只置 land_on_profile；notice / force_refresh 是 #93 / #94 的"
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

"""换绑走真磁盘的那一条（工单 #92）：真 ``Store`` + 真配置文件 + 真 ``RebindService``。

`tests/test_rebind_flow.py` 用 ``FakeAccount`` 断界面行为；这一份把组合根也接上——app 里的
换绑端口是 ``dida.rebind.RebindService``，``cache.sqlite3`` 与 ``config.toml`` 都是 tmp 里
的真文件。判据里那些「**内容**没变」「旧数据一条都不在」只有在这条接缝上才断得到。

「磁盘没动」断的是**内容**（配置字节 / 库里的行），不是 mtime：``Store.__init__`` 一开库
就会清一次 ``id_aliases`` 再 commit，mtime 必变（工单 #92 的「注意」）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

import dida.rebind
from dida.bootstrap import build_app
from dida.config import Config, load_config, save_config
from dida.storage.store import ChangeKind, Store
from dida.sync.lists import ListWriteKind
from dida.sync.views import ViewDefinition
from dida.testing import FakeTransport
from dida.tui.app import REBIND_GROUP, DidaApp, RebindRestart
from dida.tui.keys import LAYER_PROFILE
from support import screen_text

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 3, 14, 12, 0, tzinfo=TZ)
WIDE = (100, 30)

OLD_TOKEN = "old-token-1234"
NEW_TOKEN = "newtoken"
SECOND_TOKEN = "secondtoken"


def project(id: str = "p1", name: str = "工作", **extra: object) -> dict:
    return {"id": id, "name": name, "sortOrder": 0, **extra}


def task(id: str = "t1", title: str = "写周报", project_id: str = "p1", **extra: object) -> dict:
    return {"id": id, "projectId": project_id, "title": title, "status": 0, **extra}


def populate(db_path: Path) -> None:
    """一份有东西可丢的本地副本：3 个清单（含收集箱）/ 4 条任务 / 2 个视图 / 2 笔待推送。"""
    store = Store(db_path)
    try:
        store.apply_refresh(
            lists=[project(), project(id="p2", name="生活"), project(id="inbox123", name="收集箱")],
            tasks=[
                task(),
                task(id="t2", title="已完成的", project_id="p2", status=2),
                task(id="t3", title="另一条", project_id="p1"),
                task(id="t4", title="收集箱里的", project_id="inbox123"),
            ],
        )
        store.save_view(ViewDefinition(id="v1", name="高优先级"))
        store.save_view(ViewDefinition(id="v2", name="家里的事"))
        store.enqueue(task_id="t1", kind=ChangeKind.UPDATE, payload={"title": "改了"}, now=NOW)
        store.enqueue_list(
            list_id="p2",
            kind=ListWriteKind.UPDATE,
            payload={"name": "新名字"},
            local={"id": "p2", "name": "新名字"},
            now=NOW,
        )
    finally:
        store.close()


def content(db_path: Path) -> tuple[object, ...]:
    """本地副本里会随库一起消失的那些内容（不是 SQL，也不是 mtime）。"""
    store = Store(db_path)
    try:
        return (
            store.lists(),
            store.tasks(),
            store.view_definitions(),
            store.pending(),
            store.pending_lists(),
        )
    finally:
        store.close()


class World:
    """tmp 里的一套真东西：配置 + 库 + app。

    周期泵的间隔被摆成一小时（``PUSH_TICK_SECONDS`` 是组合根的策略）：默认那一秒一次的泵会
    在这几个测试的 ``pilot.pause()`` 之间醒来，顺手推一笔待推送改动（``attempts`` /
    ``last_error`` 会变），而这几条断的是「换绑之外的东西一个字都没变」。泵本身有它自己的
    测试（``tests/test_app_sync.py`` / ``tests/test_pump_teardown.py``）。
    """

    def __init__(self, tmp_path: Path, *, transport: FakeTransport, monkeypatch=None) -> None:
        if monkeypatch is not None:
            monkeypatch.setattr("dida.bootstrap.PUSH_TICK_SECONDS", 3600.0)
        self.db_path = tmp_path / "cache.sqlite3"
        self.config_file = tmp_path / "config.toml"
        populate(self.db_path)
        save_config(
            Config(
                token=OLD_TOKEN,
                day_end="04:00",
                refresh_on_start=False,
                push_on_change=False,
                completed_window_hours=24,
            ),
            self.config_file,
        )
        self.transport = transport
        self.app: DidaApp = build_app(
            config=load_config(self.config_file),
            transport=transport,
            db_path=self.db_path,
            config_file=self.config_file,
        )


async def open_rebind(pilot) -> None:
    await pilot.press("A")
    await pilot.pause()
    await pilot.press("b")
    await pilot.pause()


async def submit_token(pilot, app: DidaApp, token: str = NEW_TOKEN) -> None:
    """填 token → 提交 → 等换绑的 worker 跑完（成功时确认页已经开出来）。"""
    await pilot.press(*token)
    await pilot.press("enter")
    for _ in range(200):
        await pilot.pause()
        busy = any(
            worker.group == REBIND_GROUP and not worker.is_finished for worker in app.workers
        )
        if not busy:
            return
    raise AssertionError("换绑的 worker 一直没跑完")


async def confirm_yes(pilot, app: DidaApp) -> RebindRestart:
    """在影响面确认页里输入 ``yes``，等 app 退出，交回它那张重启纸条。"""
    await pilot.press(*"yes")
    await pilot.press("enter")
    for _ in range(200):
        await pilot.pause()
        if app.return_value is not None:
            return app.return_value
    raise AssertionError("输入 yes 之后没有走到 exit()")


def later_app(world: World, notice: str | None) -> DidaApp:
    """按 ``main`` 那条路重开一个 app：重读配置、同一个库路径、带上失败原句。

    与 ``run_startup_loop`` 里 ``build(config=load_config(...), config_file=..., notice=...)``
    同一形状；这里只是不起启动循环，直接在测试里把它跑起来。
    """
    return build_app(
        config=load_config(world.config_file),
        transport=FakeTransport(json=[]),
        db_path=world.db_path,
        config_file=world.config_file,
        notice=notice,
    )


async def test_a_401_through_the_real_service_leaves_config_and_database_untouched(tmp_path, monkeypatch):
    """401：停在输入框、说清凭据问题，而且 ``config.toml`` 与 ``cache.sqlite3`` 的内容都没变。

    这是判据三在真磁盘上的那一半：验证失败时「先验证、后落盘」不许漏一个字节。
    """
    world = World(tmp_path, transport=FakeTransport(status_code=401), monkeypatch=monkeypatch)
    config_before = world.config_file.read_bytes()
    rows_before = content(world.db_path)

    async with world.app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_rebind(pilot)
        await submit_token(pilot, world.app, "badtoken")
        text = screen_text(world.app)

        assert "凭据" in text, f"401 要说清是凭据问题：\n{text}"
        assert "badtoken" not in text, "原始 token 不许出现在屏幕上"
        assert world.app.layer == LAYER_PROFILE
        assert world.app.return_value is None, "验证失败不该重启"

    assert world.config_file.read_bytes() == config_before, "验证失败时配置一个字节都不许变"
    assert content(world.db_path) == rows_before, "验证失败时库里的行一条都不许变"
    assert load_config(world.config_file).token == OLD_TOKEN


async def test_a_network_failure_through_the_real_service_says_network(tmp_path, monkeypatch):
    """断网：客户端把 ``httpx.TransportError`` 翻成 ``NetworkError``，界面说的是网络。"""
    transport = FakeTransport(json=[])
    transport.enqueue(httpx.ConnectError("no route to host"))
    world = World(tmp_path, transport=transport, monkeypatch=monkeypatch)
    config_before = world.config_file.read_bytes()

    async with world.app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_rebind(pilot)
        await submit_token(pilot, world.app)
        text = screen_text(world.app)

    assert "连不上服务端" in text, f"断网时要说网络：\n{text}"
    assert "凭据被拒绝" not in text
    assert world.config_file.read_bytes() == config_before


async def test_the_four_numbers_on_the_confirm_page_are_the_local_copies_own(tmp_path, monkeypatch):
    """确认页那四个数各自对得上本地副本里的东西——多一个少一个都断得出来。

    期望值从**库里读出来的行数**算，不从界面的实现算：`lists` 表的 3 行（含收集箱）、
    `tasks` 表的 4 行（含已完成）、`views` 表的 2 行、两张队列表的 2 笔。
    """
    world = World(tmp_path, transport=FakeTransport(json=[]), monkeypatch=monkeypatch)
    rows = content(world.db_path)
    expected = (
        len(rows[0]),
        len(rows[1]),
        len(rows[2]),
        len(rows[3]) + len(rows[4]),
    )
    assert expected == (3, 4, 2, 2), f"这份库的四个数变了：{expected}"

    async with world.app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_rebind(pilot)
        await submit_token(pilot, world.app)
        text = screen_text(world.app)

        assert f"{expected[0]} 个清单" in text
        assert f"{expected[1]} 条任务" in text
        assert f"{expected[2]} 个自定义视图" in text
        assert f"{expected[3]} 笔待推送" in text
        assert world.app.return_value is None, "确认之前什么都不该发生"


async def test_anything_but_yes_leaves_both_files_untouched(tmp_path, monkeypatch):
    """不输入 ``yes``：库与配置都不动——断的是**内容**（判据六）。"""
    world = World(tmp_path, transport=FakeTransport(json=[]), monkeypatch=monkeypatch)
    config_before = world.config_file.read_bytes()
    rows_before = content(world.db_path)

    async with world.app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_rebind(pilot)
        await submit_token(pilot, world.app)
        assert "yes" in screen_text(world.app), "确认页没开出来"

        await pilot.press(*"no")
        await pilot.press("enter")
        await pilot.pause()
        assert world.app.return_value is None, "没输入 yes 就重启了"

    assert world.config_file.read_bytes() == config_before, "没输入 yes，配置一个字都不许动"
    assert content(world.db_path) == rows_before, "没输入 yes，库里的行一条都不许动"
    assert load_config(world.config_file).token == OLD_TOKEN


async def test_yes_writes_the_new_token_deletes_the_old_copy_and_restarts_on_the_profile(tmp_path, monkeypatch):
    """``yes`` 之后：配置里是新 token；旧副本整个没了；重开的是新引擎 / 新库；落在个人主页。

    这就是判据七：一次走完「写配置 → 删库 → app 退出 → 用新配置重开」。重开那一步在这里
    **真的再 build 一个 app**（``dida`` 命令的循环在 ``tests/test_bootstrap_restart.py`` 里
    另有一条，用的是同一张 :class:`RebindRestart` 纸条）。
    """
    world = World(tmp_path, transport=FakeTransport(json=[]), monkeypatch=monkeypatch)
    old_engine = world.app.engine
    old_rows = content(world.db_path)
    assert old_rows[0] and old_rows[1] and old_rows[2], "换绑之前库里该是有东西的"

    async with world.app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_rebind(pilot)
        await submit_token(pilot, world.app)
        await pilot.press(*"yes")
        await pilot.press("enter")
        for _ in range(200):
            await pilot.pause()
            if world.app.return_value is not None:
                break
        else:
            raise AssertionError("输入 yes 之后没有走到 exit()")

    assert world.app.return_value == RebindRestart(land_on_profile=True), (
        f"成功后要交回「落在个人主页」那张纸条，实际是 {world.app.return_value!r}"
    )
    assert not world.db_path.exists(), "旧库必须整个删掉（不是删表）"
    written = load_config(world.config_file)
    assert written.token == NEW_TOKEN, "配置里是新 token"
    assert (written.day_end, written.refresh_on_start, written.push_on_change) == (
        "04:00",
        False,
        False,
    ), "换 token 只换 token，其余配置项原样保留"
    assert written.completed_window_hours == 24

    # 重开：main 的循环会用重读过的配置新造一个 app（新引擎、新库连接）。
    restarted = build_app(
        config=written,
        transport=FakeTransport(json=[]),
        db_path=world.db_path,
        config_file=world.config_file,
        # main 的启动循环把 ``RebindRestart.land_on_profile`` 翻成这一层（#92）。
        start_layer=LAYER_PROFILE,
    )
    assert restarted.engine is not old_engine, "重开必须是新引擎，不是原地换"
    async with restarted.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert restarted.layer == LAYER_PROFILE, "换绑成功后要落在个人主页"

    fresh = content(world.db_path)
    assert fresh[0] == (), "旧清单一条都不该剩下"
    assert fresh[1] == (), "旧任务一条都不该剩下"
    assert fresh[2] == (), "旧自定义视图一个都不该剩下"
    assert fresh[3] == () and fresh[4] == (), "旧待推送改动也不该剩下"
    assert world.db_path.stat().st_size > 0, "新库是一个真的、刚建出来的文件"


async def test_a_delete_failure_leaves_both_files_alone_and_the_restarted_app_can_rebind_again(
    tmp_path, monkeypatch
):
    """删库失败：配置一字未改、旧库一行未少，重启的 app 还能用，再换绑一次能成功（判据一 / 二）。

    失败是在**失败点**注入的：只让这个库文件的 ``unlink`` 抛 ``OSError``，其余每一步都是真的
    （真 ``Store``、真目录、真 ``RebindService``）。它模拟的正是「文件删不掉」——不是把正常
    路径假造一遍。

    之后的重开按 ``main`` 的接线走：重读配置（仍是旧 token）、同一个库路径、带上失败原句。
    旧库完好，所以新 app 应当照常读出旧清单 / 旧任务；再把注入关掉，同一份状态里第二次换绑
    必须成功。
    """
    world = World(tmp_path, transport=FakeTransport(json=[]), monkeypatch=monkeypatch)
    config_before = world.config_file.read_bytes()
    rows_before = content(world.db_path)
    failure = {"enabled": True}
    real_unlink = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if failure["enabled"] and self == world.db_path:
            raise OSError("database is busy")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)

    async with world.app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_rebind(pilot)
        await submit_token(pilot, world.app)
        result = await confirm_yes(pilot, world.app)

    assert isinstance(result, RebindRestart), f"失败也要交回重启纸条，实际 {result!r}"
    assert result.land_on_profile is False
    notice = result.notice or ""
    assert "删库失败" in notice, f"屏幕原句要说出是哪一步：{notice}"
    assert "database is busy" in notice, f"屏幕原句要带上原因：{notice}"
    assert world.config_file.read_bytes() == config_before, "删库失败时配置一个字节都不许变"
    assert content(world.db_path) == rows_before, (
        "旧库里的清单、任务、自定义视图、待推送改动一条都不许少"
    )

    # 重开：旧配置 + 旧库；失败原句用 toast 说出来。
    failure["enabled"] = False
    restarted = later_app(world, result.notice)
    async with restarted.run_test(size=WIDE, notifications=True) as pilot:
        await pilot.pause()
        text = screen_text(restarted)
        assert "删库失败" in text, f"重启后的屏幕没说出失败原因：\n{text}"
        assert "工作" in text, f"重启后旧清单不在了：\n{text}"
        assert "高优先级" in text and "家里的事" in text, f"重启后旧自定义视图不在了：\n{text}"
        # 旧库可读：收集箱那一行（光标默认停在它上面）里还躺着旧任务。
        await pilot.press("right")
        await pilot.pause()
        assert "收集箱里的" in screen_text(restarted), "重启后旧任务读不出来了——app 半死"
        await pilot.press("left")
        await pilot.pause()

        # 再换绑一次：这一次没有注入失败，应当走成功那条路。
        await open_rebind(pilot)
        await submit_token(pilot, restarted, SECOND_TOKEN)
        second = await confirm_yes(pilot, restarted)

    assert second == RebindRestart(land_on_profile=True), f"第二次换绑没成功：{second!r}"
    assert not world.db_path.exists(), "第二次换绑成功后旧库应当整个删掉"
    assert load_config(world.config_file).token == SECOND_TOKEN, "第二次换绑应当写进新 token"


async def test_a_config_write_failure_leaves_the_old_token_and_a_restart_opens_an_empty_copy(
    tmp_path, monkeypatch
):
    """写配置失败：库已删、配置仍是旧账号；重开是旧 token + 空库（判据三）。

    失败注入点就是 ``RebindService`` 写配置那一下（``dida.rebind.save_config``）：在它之前
    关库、删库都真的发生了，所以这条测试要断的「半截状态」是真的——不是伪造出来的。
    """
    world = World(tmp_path, transport=FakeTransport(json=[]), monkeypatch=monkeypatch)
    config_before = world.config_file.read_bytes()
    config_before_values = load_config(world.config_file)
    rows_before = content(world.db_path)
    assert rows_before[0] and rows_before[1], "换绑之前库里该是有东西的"
    failure = {"enabled": True}
    real_save = dida.rebind.save_config

    def save(config: Config, path: Path | None = None) -> None:
        if failure["enabled"] and path == world.config_file:
            raise OSError("EACCES")
        real_save(config, path)

    monkeypatch.setattr(dida.rebind, "save_config", save)

    async with world.app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await open_rebind(pilot)
        await submit_token(pilot, world.app)
        result = await confirm_yes(pilot, world.app)

    assert isinstance(result, RebindRestart), f"失败也要交回重启纸条，实际 {result!r}"
    assert result.land_on_profile is False
    notice = result.notice or ""
    assert "库已删、配置仍是旧账号" in notice, f"那句话必须原样说出来：{notice}"
    assert "EACCES" in notice, f"屏幕原句要带上原因：{notice}"
    assert NEW_TOKEN not in notice, "token 不许进屏幕原句"
    assert not world.db_path.exists(), "写配置失败发生在删库之后：库文件应当已经没了"
    assert world.config_file.read_bytes() == config_before, "配置内容一个字都不许变"
    assert load_config(world.config_file) == config_before_values, "配置仍是旧 token（其余项也没动）"

    # 重开：旧 token + 空库；屏幕上要把那句话原样说出来。
    failure["enabled"] = False
    restarted = later_app(world, result.notice)
    async with restarted.run_test(size=WIDE, notifications=True) as pilot:
        await pilot.pause()
        text = screen_text(restarted)
        assert "库已删、配置仍是旧账号" in text, f"重启后的屏幕没说出那句话：\n{text}"
        assert "EACCES" in text, f"重启后的屏幕没说出原因：\n{text}"
        assert "工作" not in text and "生活" not in text, f"旧清单还在屏幕上：\n{text}"
        assert "写周报" not in text and "收集箱里的" not in text, f"旧任务还在屏幕上：\n{text}"

    assert load_config(world.config_file).token == OLD_TOKEN, "重开之后配置仍是旧 token"
    fresh = content(world.db_path)
    assert fresh == ((), (), (), (), ()), "重开的是一个空库：旧清单 / 任务 / 视图 / 待推送都没了"

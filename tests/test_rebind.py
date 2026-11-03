"""换绑的端口（工单 #92）：验证 / 影响面 / 执行。

**接缝二**：真 ``Store``（临时文件）+ 真配置文件 + ``FakeTransport``。断的是外部行为——
磁盘上发生了什么、库里的行变了没有、抛出来的是哪一种错误，不碰私有方法，也不查 SQL。
界面上那一条流程在 ``tests/test_rebind_flow.py``（接缝一）。

这一票的要求有三条在这里看得最清楚：

- **验证绝不落盘**（ADR-0003）：一次 ``list_projects`` 之后配置与库的内容逐字节 / 逐行相同；
- **影响面的四个数在删库之前读**，而且各自对得上库里那几张表；
- **执行顺序是关连接 → 删库 → 写配置**（ADR-0011 五）：任何一步失败留下的东西都不一样。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

import dida.rebind
from dida.api.errors import AuthError, NetworkError
from dida.config import Config, load_config, save_config
from dida.rebind import RebindService
from dida.storage.store import ChangeKind, Store
from dida.sync.engine import (
    AccountPort,
    RebindCloseError,
    RebindConfigError,
    RebindDeleteError,
    RebindImpact,
)
from dida.sync.lists import ListWriteKind
from dida.sync.views import ViewDefinition
from dida.testing import FakeTransport

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 3, 14, 12, 0, tzinfo=TZ)

OLD_TOKEN = "old-token-1234"
NEW_TOKEN = "new-token-5678"
"""换绑前后的两枚凭据。它们一个都不许出现在错误消息里（Config.__repr__ 与这里的断言一起守）。"""


def project(id: str = "p1", name: str = "工作", **extra: object) -> dict:
    """一份 ``GET /open/v1/project`` 那样的清单原文。"""
    return {"id": id, "name": name, "sortOrder": 0, **extra}


def task(id: str = "t1", title: str = "写周报", project_id: str = "p1", **extra: object) -> dict:
    """一份 ``GET /open/v1/project/{id}/data`` 那样的任务原文。"""
    return {"id": id, "projectId": project_id, "title": title, "status": 0, **extra}


class World:
    """一份「换绑之前」的真实本地副本：三个清单（含收集箱）、四条任务、两个视图、两笔待推送。"""

    def __init__(
        self,
        tmp_path: Path,
        *,
        config: Config | None = None,
        transport: FakeTransport | None = None,
    ) -> None:
        self.db_path = tmp_path / "cache.sqlite3"
        self.config_file = tmp_path / "config.toml"
        self.store = Store(self.db_path)
        self.store.apply_refresh(
            lists=[project(), project(id="p2", name="生活"), project(id="inbox123", name="收集箱")],
            tasks=[
                task(),
                task(id="t2", title="已完成的", project_id="p2", status=2),
                task(id="t3", title="另一条", project_id="p1"),
                task(id="t4", title="收集箱里的", project_id="inbox123"),
            ],
        )
        self.store.save_view(ViewDefinition(id="v1", name="高优先级"))
        self.store.save_view(ViewDefinition(id="v2", name="家里的事"))
        self.store.enqueue(
            task_id="t1", kind=ChangeKind.UPDATE, payload={"title": "改了"}, now=NOW
        )
        self.store.enqueue_list(
            list_id="p2",
            kind=ListWriteKind.UPDATE,
            payload={"name": "新名字"},
            local={"id": "p2", "name": "新名字"},
            now=NOW,
        )
        self.config = config if config is not None else Config(
            token=OLD_TOKEN,
            day_end="04:00",
            refresh_on_start=False,
            push_on_change=False,
            completed_window_hours=24,
        )
        save_config(self.config, self.config_file)
        self.transport = FakeTransport(json=[]) if transport is None else transport
        self.service = RebindService(
            store=self.store,
            config=self.config,
            database=self.db_path,
            path=self.config_file,
            transport=self.transport,
        )

    def rows(self) -> tuple[object, ...]:
        """库里会被换绑一起带走的那几行（内容对账用，不是 SQL）。"""
        return (
            self.store.lists(),
            self.store.tasks(),
            self.store.view_definitions(),
            self.store.pending(),
            self.store.pending_lists(),
        )

    def close(self) -> None:
        try:
            self.store.close()
        except sqlite3.ProgrammingError:  # 已经关过了（execute 成功那条路）
            pass


@pytest.fixture
def world(tmp_path):
    made = World(tmp_path)
    yield made
    made.close()


def test_the_impact_counts_are_the_local_copies_own_rows(world):
    """四个数各自就是库里那几类东西的条数，一个不多一个不少（工单 #92 的判据）。

    口径在这一条上钉死（实现里也写着同一句）：清单 = ``lists`` 表的行（**含收集箱那一行**），
    任务 = ``tasks`` 表的全部行（含已完成），视图 = ``views`` 表的行，待推送 = 两张队列表一起
    数（与状态栏同一个 ``pending_count()``）。

    所以这份 World 的答案是 3 / 4 / 2 / 2：少算收集箱或者漏掉已完成任务，这里当场红。
    """
    assert world.service.impact() == RebindImpact(lists=3, tasks=4, views=2, pending=2)

    assert len(world.store.lists()) == 3, "清单那一项是对着 lists 表的行数的"
    assert len(world.store.tasks()) == 4, "任务那一项含已完成的那一条"
    assert len(world.store.view_definitions()) == 2
    assert world.store.pending_count() == 2, "任务队列与清单队列各一笔"


def test_an_empty_copy_with_only_the_inbox_row_still_counts_that_row(tmp_path):
    """只有收集箱一行的空库是「1 个清单 / 0 条任务」——收集箱那一行也在本地副本里，也会消失。"""
    store = Store(tmp_path / "cache.sqlite3")
    store.apply_refresh(lists=[project(id="inbox123", name="收集箱")], tasks=[])
    config = Config(token=OLD_TOKEN)
    config_file = tmp_path / "config.toml"
    save_config(config, config_file)
    service = RebindService(
        store=store,
        config=config,
        database=tmp_path / "cache.sqlite3",
        path=config_file,
        transport=FakeTransport(),
    )
    try:
        assert service.impact() == RebindImpact(lists=1, tasks=0, views=0, pending=0)
    finally:
        store.close()


def test_is_bound_is_the_one_fact_source(tmp_path):
    """「已绑定 / 未绑定」只有这一个出处：配置里有没有 token。"""
    store = Store(tmp_path / "cache.sqlite3")
    config_file = tmp_path / "config.toml"
    save_config(Config(), config_file)
    unbound = RebindService(
        store=store,
        config=load_config(config_file),
        database=tmp_path / "cache.sqlite3",
        path=config_file,
        transport=FakeTransport(),
    )
    bound = RebindService(
        store=store,
        config=Config(token=OLD_TOKEN),
        database=tmp_path / "cache.sqlite3",
        path=config_file,
        transport=FakeTransport(),
    )
    try:
        assert unbound.is_bound() is False
        assert bound.is_bound() is True
    finally:
        store.close()


def test_the_port_is_satisfied_by_the_real_service(world):
    """组合根注入的就是这一支：它满足引擎公开面上的那个端口。"""
    assert isinstance(world.service, AccountPort)


async def test_verifying_a_token_does_not_touch_the_disk(world):
    """先验证、后落盘：一次成功的 ``list_projects`` 之后配置与库一字不差（ADR-0003）。"""
    config_before = world.config_file.read_bytes()
    rows_before = world.rows()

    await world.service.verify(NEW_TOKEN)

    assert [request.url.path for request in world.transport.requests] == ["/open/v1/project"], (
        "验证就是一次「列清单」"
    )
    assert world.transport.requests[0].headers["Authorization"] == f"Bearer {NEW_TOKEN}"
    assert world.config_file.read_bytes() == config_before, "验证不改配置（token 没落盘）"
    assert world.rows() == rows_before, "验证不改本地副本"
    assert world.db_path.exists(), "验证连库文件都不该动"


async def test_a_rejected_token_is_an_auth_error_and_the_disk_is_untouched(tmp_path):
    """401：结构化地说是凭据问题，配置与库的内容都没变（工单 #92 的判据）。"""
    world = World(tmp_path, transport=FakeTransport(status_code=401))
    config_before = world.config_file.read_bytes()
    rows_before = world.rows()
    try:
        with pytest.raises(AuthError) as caught:
            await world.service.verify("bad-token-9999")

        assert caught.value.status_code == 401
        assert "bad-token-9999" not in str(caught.value), "token 不许进错误消息"
        assert world.config_file.read_bytes() == config_before
        assert world.rows() == rows_before
        assert world.db_path.exists()
    finally:
        world.close()


async def test_a_network_failure_is_not_reported_as_a_bad_token(world):
    """断网是断网：``NetworkError``，不是 ``AuthError``——两句话让用户做两件不同的事。"""
    world.transport.enqueue(httpx.ConnectError("no route to host"))

    with pytest.raises(NetworkError) as caught:
        await world.service.verify(NEW_TOKEN)

    assert not isinstance(caught.value, AuthError), "网络失败不许被说成凭据失效"
    assert NEW_TOKEN not in str(caught.value)


async def test_an_empty_token_is_refused_before_any_request(world):
    """空粘贴本地就拦下，不浪费一次请求，也不落盘。"""
    with pytest.raises(AuthError):
        await world.service.verify("   ")

    assert world.transport.requests == []


async def test_execute_closes_the_old_connection_then_deletes_the_database_then_writes_the_token(
    world, monkeypatch
):
    """执行的顺序是**关连接 → 删库 → 写配置**，而且写配置只换 token（ADR-0011 五）。

    顺序这样钉：关连接那一刻库文件必须还在（说明删库在它后面），写配置那一刻库文件必须已经
    没了（说明删库在它前面）。三件事的相对次序因此是确定的，不是碰巧。
    """
    events: list[tuple[str, bool]] = []
    real_close = world.store.close

    def close() -> None:
        events.append(("close", world.db_path.exists()))
        real_close()

    real_save = dida.rebind.save_config

    def save(config: Config, path: Path | None = None) -> None:
        events.append(("save", world.db_path.exists()))
        real_save(config, path)

    monkeypatch.setattr(world.store, "close", close)
    monkeypatch.setattr(dida.rebind, "save_config", save)

    await world.service.execute(NEW_TOKEN)

    assert events == [("close", True), ("save", False)], (
        "顺序必须是：连接先关（那时库还在）→ 删库 → 写配置（那时库已经没了）"
    )
    assert not world.db_path.exists(), "旧库必须整个消失（不是删表）"
    with pytest.raises(sqlite3.ProgrammingError):
        world.store.tasks()  # close 之后任何一次读都会炸——连接真的关了

    written = load_config(world.config_file)
    assert written.token == NEW_TOKEN, "配置里是新 token"
    assert (written.day_end, written.refresh_on_start, written.push_on_change) == (
        "04:00",
        False,
        False,
    ), "其余配置项原样保留（写配置只换 token）"
    assert written.completed_window_hours == 24
    assert OLD_TOKEN not in world.config_file.read_text(encoding="utf-8"), "旧 token 不许留在文件里"
    assert world.service.is_bound() is True


async def test_a_close_failure_names_the_close_step_and_leaves_the_file_alone(world, monkeypatch):
    """关连接失败：说的是「关连接」而不是「删库」——库文件一个字节都没动（工单 #93 的诚实口径）。

    失败点注入在 ``Store.close``：``unlink`` 根本没走到，所以把这一步说成「删库失败」就是在
    说一件没发生的事。旧连接此刻是什么状态不可知，句子因此只承诺文件与配置没动，不承诺
    「连接还开着」。
    """
    config_before = world.config_file.read_bytes()
    rows_before = world.rows()

    def close() -> None:
        raise sqlite3.ProgrammingError("Cannot operate on a closed database")

    monkeypatch.setattr(world.store, "close", close)

    with pytest.raises(RebindCloseError) as caught:
        await world.service.execute(NEW_TOKEN)

    message = str(caught.value)
    assert "关连接失败" in message, "要说清失败的是关连接这一步"
    assert "删库" not in message, "关连接失败不许把没发生的那一步说成失败"
    assert NEW_TOKEN not in message
    assert world.config_file.read_bytes() == config_before, "关连接失败时配置一动不动"
    assert world.db_path.exists(), "库文件还在（unlink 根本没走到）"
    monkeypatch.undo()
    reopened = Store(world.db_path)
    try:
        assert reopened.lists() == rows_before[0], "旧库的内容一条都没少"
        assert reopened.tasks() == rows_before[1]
    finally:
        reopened.close()


async def test_a_delete_failure_leaves_the_old_database_and_config_alone(world, monkeypatch):
    """删库失败：配置没动，旧库完好，报的是「删库失败」这一类错（ADR-0011 五）。"""
    config_before = world.config_file.read_bytes()
    rows_before = world.rows()

    def unlink(self: Path, missing_ok: bool = False) -> None:
        raise OSError("database is busy")

    monkeypatch.setattr(Path, "unlink", unlink)

    with pytest.raises(RebindDeleteError) as caught:
        await world.service.execute(NEW_TOKEN)

    assert NEW_TOKEN not in str(caught.value), "token 不许进错误消息"
    assert world.config_file.read_bytes() == config_before, "删库失败时配置一动不动"
    assert world.db_path.exists(), "旧库还在"
    monkeypatch.undo()
    reopened = Store(world.db_path)
    try:
        assert reopened.lists() == rows_before[0], "旧库的内容一条都没少"
        assert reopened.tasks() == rows_before[1]
    finally:
        reopened.close()


async def test_a_config_write_failure_says_the_database_is_already_gone(world, monkeypatch):
    """写配置失败：库已经删了，但配置仍是旧账号——这是唯一会留下半截状态的失败点。"""

    def save(config: Config, path: Path | None = None) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(dida.rebind, "save_config", save)

    with pytest.raises(RebindConfigError) as caught:
        await world.service.execute(NEW_TOKEN)

    message = str(caught.value)
    assert "库" in message, "这句话必须说出来：库已经删了"
    assert "旧账号" in message, "而且配置里仍是旧账号"
    assert NEW_TOKEN not in message
    assert not world.db_path.exists(), "库确实已经删了（失败点在这之后）"
    written = load_config(world.config_file)
    assert written.token == OLD_TOKEN, "配置还是旧的"

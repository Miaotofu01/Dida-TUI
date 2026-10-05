"""组合根接线（t21）：``config`` → 本地副本 → 客户端 → 引擎，各走各的路。

这个文件是**搬过来的**（工单 #32 的「先搬后删」）：这些结论原本钉在 ``test_sync_session.py``
的接缝一里——起一个真 app、按 ``r``、再看屏幕。搬过来的是**引擎那一半**：配置有没有真的落到
引擎上、请求有没有真的带上凭据、库里那条路径对不对。``build_app()`` 装出来的东西里
``engine`` 是唯一的读写入出口（也是 TUI 唯一看得见的那个对象），所以这些断言一个字都不用开屏。

组合根**不许**读用户真实的 ``~/.config``：每一条都显式传 ``config=`` 与 ``db_path=``。
"""

from __future__ import annotations

import asyncio
import importlib
import tomllib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from dida.api.errors import NetworkError
from dida.api.transport import Transport
from dida.bootstrap import build_app, default_transport
from dida.config import Config
from dida.storage.store import Store
from dida.testing import ManualClock

ROOT = Path(__file__).resolve().parents[1]

TZ = timezone(timedelta(hours=8))


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻（带时区）。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


T0 = at(14, 2, 0)
"""凌晨两点：``day_end = "04:00"`` 时它仍属于逻辑日 03-13。"""


def inbox(id: str = "inbox", name: str = "收集箱") -> dict:
    return {"id": id, "name": name, "sortOrder": 0}


def inbox_task(id: str = "t1", title: str = "写周报", **extra: object) -> dict:
    return {"id": id, "projectId": "inbox", "title": title, "status": 0, **extra}


class Server:
    """接缝二的假服务端：按路径应答，并记下每一个请求（与 ``test_sync_session`` 同一形态）。"""

    def __init__(self, *, tasks: list[dict] | None = None) -> None:
        self.lists = [inbox()]
        self.tasks = list(tasks or [])
        self.requests: list[httpx.Request] = []

    @property
    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    async def send(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/open/v1/project":
            return httpx.Response(200, json=self.lists)
        if path.endswith("/data"):
            return httpx.Response(200, json={"project": inbox(), "tasks": self.tasks})
        if path == "/open/v1/task/completed":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={})


def seeded(db: Path, *tasks: dict) -> None:
    """先在库里摆一份缓存：``build_app`` 打开的是同一个文件。"""
    with Store(db) as store:
        store.apply_refresh(lists=[inbox()], tasks=list(tasks))


def make_app(
    db: Path,
    server: Server,
    *,
    clock: ManualClock | None = None,
    **config: object,
):
    """组合根装出来的 app：配置与库路径都显式给，绝不碰 ``~/.config``。"""
    return build_app(
        clock=clock if clock is not None else ManualClock(T0),
        config=Config(token="tok-1234", **config),  # type: ignore[arg-type]
        transport=server,
        db_path=db,
    )


def test_the_console_script_points_at_an_importable_main():
    """``pyproject`` 里 ``dida`` 那个入口指向的东西真的能拿到：导入它、取出属性、可调用。

    这是整个包唯一一条「敲 ``dida`` 会启动」的守卫，而它原本与界面测试住在同一个文件里
    （``test_bootstrap.py`` 也 import ``DidaApp``，属于 v1 那 19 个要被删的文件之一）。
    界面重写最容易踩坏的就是它：改了模块路径、或者删了 ``main``，这里当场红。
    """
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    target = pyproject["project"]["scripts"]["dida"]
    module_name, _, attribute = target.partition(":")

    assert callable(getattr(importlib.import_module(module_name), attribute))


def test_the_day_end_from_the_config_reaches_the_engine(tmp_path):
    """``day_end = "04:00"`` 进了引擎：凌晨两点看到的逻辑日还是前一天。

    「现在」是注入的，所以这一条不依赖跑测试的钟点。原本钉在 ``test_sync_session.py`` 的
    组合根那条测试里（#32 搬出来的）。
    """
    db = tmp_path / "cache.sqlite3"
    seeded(db, inbox_task())

    app = make_app(db, Server(), day_end="04:00", refresh_on_start=False)

    assert app.engine.status().logical_day == date(2026, 3, 13)


def test_the_completed_window_from_the_config_reaches_the_engine(tmp_path):
    """``completed_window_hours`` 进了引擎：30 小时前完成的那条只有 48 小时的窗口收得住。

    两边都断言（24 挡住、48 收住）：只断「48 里有」的话，窗口那一项接没接上根本看不出来。
    """
    db = tmp_path / "cache.sqlite3"
    seeded(
        db,
        inbox_task(
            id="done1",
            title="手机上做完的",
            status=2,
            completedTime="2026-03-12T20:00:00+0800",  # 距 T0 三十小时
        ),
    )

    narrow = make_app(db, Server(), completed_window_hours=24, refresh_on_start=False)
    wide = make_app(db, Server(), completed_window_hours=48, refresh_on_start=False)

    assert [item.title for item in narrow.engine.view().completed.items] == []
    assert [item.title for item in wide.engine.view().completed.items] == ["手机上做完的"]


def test_the_cache_lives_at_the_path_the_composition_root_was_given(tmp_path):
    """库落在给的那条路径上：写一笔，换一个连接照样读得到（不是内存里的假货）。"""
    db = tmp_path / "cache.sqlite3"
    seeded(db, inbox_task(id="t1", title="写周报"))
    app = make_app(db, Server(), push_on_change=False, refresh_on_start=False)

    app.engine.cycle_priority("t1")

    assert db.exists()
    with Store(db) as reopened:
        payload = reopened.task_payload("t1")
        assert payload is not None and payload["priority"] == 1, "写落在给的那个文件里"


async def test_the_token_from_the_config_rides_in_the_authorization_header(tmp_path):
    """凭据用在请求头里：刷新发出去的第一个请求带着 ``Bearer`` 那一份配置。"""
    db = tmp_path / "cache.sqlite3"
    seeded(db, inbox_task(id="t1", title="写周报"))
    server = Server(tasks=[inbox_task(id="t1", title="写周报")])
    app = make_app(db, server, refresh_on_start=False)

    await app.engine.refresh()

    assert server.requests, "刷新确实发了请求"
    assert server.requests[0].headers["Authorization"] == "Bearer tok-1234"


async def test_push_on_change_false_from_the_config_queues_instead_of_pushing(tmp_path):
    """``push_on_change = false`` 进了引擎：写只入队，一个请求都不发。"""
    db = tmp_path / "cache.sqlite3"
    seeded(db, inbox_task(id="t1", title="写周报"))
    server = Server()
    app = make_app(db, server, push_on_change=False, refresh_on_start=False)

    app.engine.cycle_priority("t1")
    await app.engine.wait_for_pushes()

    assert server.requests == [], "配置关掉了「改动立即推送」"
    assert app.engine.status().pending_count == 1, "改动确实进了队列"


async def test_a_transport_that_cannot_be_built_still_reads_the_cache(tmp_path):
    """建不出真传输也不该把 app 挡在门外：缓存照读，真要发请求时按**网络失败**报出来。

    真实场景就是某些机器：``all_proxy=socks5://…`` 而没装 ``socksio`` 时，
    ``httpx.AsyncClient()`` 当场抛 ``ImportError``。那不是「启动失败」，是「现在连不上」。
    原本钉在 ``test_sync_session.py`` 里（#32 搬出来的）。
    """

    def boom() -> Transport:
        raise ImportError("Using SOCKS proxy, but the 'socksio' package is not installed.")

    db = tmp_path / "cache.sqlite3"
    seeded(db, inbox_task(id="t1", title="写周报"))
    app = build_app(
        clock=ManualClock(T0),
        config=Config(token="tok", refresh_on_start=False),
        transport=default_transport(factory=boom),
        db_path=db,
    )

    assert [item.title for group in app.engine.view().groups for item in group.items] == ["写周报"], (
        "建不出客户端不影响读缓存"
    )

    with pytest.raises(NetworkError):
        await app.engine.refresh()


def test_a_transport_that_cannot_be_built_is_a_structured_error_not_an_import_error(tmp_path):
    """降级之后的传输层抛的是结构化网络错误：界面那一层只认 ``DidaError``。

    ``_UnusableTransport.send`` 抛 ``NetworkError`` 而不是把建不出来时的 ``ImportError``
    留到第一次请求——后者会一路冒到 ``main``，被糊成一句「凭据没验证通过」。
    """
    def no_socksio() -> Transport:
        raise ImportError("Using SOCKS proxy, but the 'socksio' package is not installed.")

    transport = default_transport(factory=no_socksio)

    async def send_once() -> None:
        await transport.send(httpx.Request("GET", "https://api.dida365.com/open/v1/project"))

    with pytest.raises(NetworkError):
        asyncio.run(send_once())

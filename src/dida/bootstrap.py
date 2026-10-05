"""组合根：把七个模块拼成一个 app。

TUI 自己不 import 存储与 API 客户端，只拿 :class:`~dida.sync.engine.SyncEngine`；
接线发生在这一层。``dida`` 命令与 ``python -m dida`` 都走这里。

接线图（t21 落地）：

``config.toml`` → :class:`~dida.config.Config` → ``Store``（本地副本）+ ``DidaApiClient``
（真传输）→ ``SyncEngine``（``day_end`` / ``completed_window_hours`` / ``push_on_change``
三项配置从这里进去）→ ``DidaApp``（``refresh_on_start`` 与周期泵从这里进去）。

首次运行（``needs_token``）走 :func:`paste_token`：请用户粘贴一次，拿它真的列一次清单，
服务端认了才落盘——token 不进日志、不进屏幕、也不进这个仓库。
"""

from __future__ import annotations

import asyncio
import getpass
from collections.abc import Callable
from pathlib import Path

import httpx

from dida.api.client import DEFAULT_BASE_URL, DidaApiClient
from dida.api.errors import NetworkError
from dida.api.transport import HttpxTransport, Transport
from dida.clock import Clock, SystemClock
from dida.config import Config, Credentials, config_path, load_config, needs_token
from dida.storage.store import Store
from dida.sync.engine import SyncEngine
from dida.tui.app import PUSH_TICK_SECONDS, DidaApp
from dida.tui.escape import open_in_browser

STORE_FILENAME = "cache.sqlite3"
"""本地副本的文件名：与 ``config.toml`` 同一个目录（spec 只写死了配置的位置）。"""

PASTE_PROMPT = "粘贴滴答清单的 API Token（输入不回显；网页版「设置 > 账户 > API Token」）："
"""首次运行的提示语。token 本身由用户输入，这一句里没有任何凭据。"""


class _UnusableTransport:
    """建不出来的真传输：第一次真要发请求时按**网络失败**报出去。

    传输层建不出来是有真实场景的：这台机器上 ``all_proxy=socks5://…`` 而没装 ``socksio``
    时，``httpx.AsyncClient()`` 当场抛 ``ImportError``。那不是一个「启动失败」——用户故事
    3/62 要的是「先读缓存、能分诊」，连不上就按连不上说（结构化错误），界面照旧。
    """

    def __init__(self, reason: str) -> None:
        self._reason = reason

    async def send(self, request: httpx.Request) -> httpx.Response:
        raise NetworkError(f"连不上服务端：{self._reason}")

    async def aclose(self) -> None:
        """没有连接池可关。``_first_run`` 的 ``finally`` 会调它，所以这里必须有。"""
        return None


def default_transport(factory: Callable[[], Transport] | None = None) -> Transport:
    """真传输；建不出来就退化成 :class:`_UnusableTransport`（见那里的理由）。

    ``factory`` 可注入：测试用它模拟「这台机器建不出传输层」，不必去动真环境变量。
    默认在**调用时**才取 :class:`HttpxTransport`，不写成参数默认值——那样会在定义时就把
    类绑死，patch 模块属性一律看不见它（这也是它可测性的一部分）。
    """
    if factory is None:
        factory = HttpxTransport
    try:
        return factory()
    except Exception as exc:  # noqa: BLE001 - 建不出来的原因不该把整个 app 挡在门外
        return _UnusableTransport(str(exc))


def _unreachable_message(error: NetworkError) -> str:
    """「连不上服务端」时要说的话：真实原因 + 两条能照着做的出路。

    首次运行这一步**必须**联网（token 要真的被服务端认一次才落盘，用户故事 2），所以这里
    不能糊过去。但也不能把环境问题说成凭据问题——用户在这一屏能做的事只有两件：让代理
    能用，或者绕开代理。

    两条触发路径共用这一段话：传输层根本建不出来（本机 ``all_proxy`` 缺 ``socksio``），
    以及建出来了但请求发失败（断网、超时）。两者都是「这台机器发不出请求」。
    """
    return (
        f"连不上服务端：{error}\n"
        "这不是 token 的问题，再粘一次也一样——是这台机器发不出请求。\n"
        "两条出路：\n"
        "  1. 让代理能用（本机 all_proxy 指向 socks5，需要 socksio）：\n"
        "     uv tool install --with socksio dida-tui\n"
        "  2. 绕开代理再跑（只在不需要代理也能上外网时有用）：\n"
        "     env -u all_proxy -u ALL_PROXY -u http_proxy -u https_proxy dida"
    )


def store_path() -> Path:
    """本地副本的位置：``~/.config/dida-tui/cache.sqlite3``。"""
    return config_path().with_name(STORE_FILENAME)


def build_client(config: Config, transport: Transport | None = None) -> DidaApiClient:
    """按配置造客户端；``transport`` 省略就是真的 HTTP。

    没有 token 时用空串：请求会被服务端按 401 拒绝，UI 因此给出「凭据失效，请重新粘贴
    token」——比一个裸 ``RuntimeError`` 诚实得多（首次运行本该在 :func:`main` 里就补上
    token，走到这里说明用户把配置文件里的 token 删了）。
    """
    return DidaApiClient(
        token=config.token or "",
        transport=transport if transport is not None else default_transport(),
        base_url=DEFAULT_BASE_URL,
    )


def build_engine(
    clock: Clock | None = None,
    *,
    config: Config | None = None,
    transport: Transport | None = None,
    db_path: Path | None = None,
) -> SyncEngine:
    """组装同步引擎：配置 → 本地副本 → 客户端 → 引擎。

    配置里那三项都在这里落到引擎上：``day_end``（逻辑日边界）、``completed_window_hours``
    （已完成区的窗口）、``push_on_change``（改动要不要立刻推）。省略 ``config`` 就按
    ``~/.config/dida-tui/config.toml`` 来（缺文件时由 :func:`~dida.config.load_config`
    生成一份默认的，那份没有 token——首次运行的信号）。
    """
    config = load_config() if config is None else config
    return SyncEngine(
        clock=clock if clock is not None else SystemClock(),
        day_end=config.day_end,
        source=Store(db_path if db_path is not None else store_path()),
        client=build_client(config, transport),
        completed_window_hours=config.completed_window_hours,
        push_on_change=config.push_on_change,
    )


def build_app(
    clock: Clock | None = None,
    *,
    config: Config | None = None,
    transport: Transport | None = None,
    db_path: Path | None = None,
    open_url: Callable[[str], bool] = open_in_browser,
) -> DidaApp:
    """组装 app：引擎 + 两项启动策略（``refresh_on_start``、周期泵的间隔）。

    策略放在组合根而不是 ``DidaApp`` 的默认值里：产品行为由 ``config.toml`` 决定，
    而直接 new 一个 app 的测试不该被迫先接上客户端与存储。
    """
    config = load_config() if config is None else config
    return DidaApp(
        build_engine(clock, config=config, transport=transport, db_path=db_path),
        open_url=open_url,
        refresh_on_start=config.refresh_on_start,
        push_tick_seconds=PUSH_TICK_SECONDS,
    )


async def paste_token(
    *,
    transport: Transport,
    prompt: Callable[[str], str] = getpass.getpass,
    path: Path | None = None,
    base_url: str = DEFAULT_BASE_URL,
) -> Config:
    """首次运行：请用户粘贴 token，验证通过才落盘（用户故事 1 + 2）。

    ``prompt`` 是注入的（默认 :func:`getpass.getpass`，输入不回显）：测试塞一个假的进来，
    于是「粘了什么、落盘了没有」能当场断言，而终端上不会留下任何凭据。失败原样抛出去
    ——:class:`~dida.config.CredentialsError`（凭据被拒）或网络错误，**磁盘一动不动**，
    而且这些消息里不会有 token。
    """
    return await Credentials(transport=transport, path=path, base_url=base_url).verify_and_store(
        prompt(PASTE_PROMPT)
    )


async def _first_run(transport: Transport | None = None) -> Config:
    """首次运行的那一次网络调用：用完就关掉自己的传输层。

    单独造一个传输、并且关掉它，是因为它与随后那个 app 的传输不共用连接池：``httpx``
    的异步连接池绑在创建它的那个事件循环上，而这里跑在 ``asyncio.run`` 自己那个循环里。

    走 :func:`default_transport` 而不是裸 ``HttpxTransport()``：建不出传输层的那台机器
    在**这一步也会**建不出来，而降级之后的 :meth:`_UnusableTransport.send` 抛的是
    :class:`_TransportUnavailable`，:func:`main` 据此把「发不出请求」与「token 被拒」
    分开说。以前这里是裸构造，``ImportError`` 直接冒到 ``main``，被糊成一句
    「凭据没验证通过，再运行一次重新粘贴」——两句都是错的。
    """
    if transport is None:
        transport = default_transport()
    try:
        return await paste_token(transport=transport)
    finally:
        await transport.aclose()


def main() -> None:
    """``dida`` 命令的入口：单进程，按 ``q`` 退出。

    首次运行先补 token（验证通过才落盘）；之后每次启动都直接起界面——本地缓存先上屏，
    全量刷新按 ``refresh_on_start`` 在后台跑（用户故事 3 + 4）。

    「连不上」与「token 被拒」分开报：前者是环境问题，说「重新粘贴」帮不上任何忙。
    """
    config = load_config()
    if needs_token(config):
        try:
            config = asyncio.run(_first_run())
        except NetworkError as exc:
            raise SystemExit(_unreachable_message(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - 首次运行只有这一条出口：说清楚再退
            raise SystemExit(f"凭据没验证通过：{exc}\n再运行一次 dida 重新粘贴。") from exc
    build_app(config=config).run()

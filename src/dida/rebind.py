"""换绑的真实实现（工单 #92）：引擎公开面上那个 :class:`~dida.sync.engine.AccountPort` 的生产一支。

它住在 ``dida.tui`` **之外**，由组合根（:func:`dida.bootstrap.build_engine`）注入，因为界面不许
碰存储、配置与 API 客户端（``tests/test_architecture.py`` 的允许表）。它握着**引擎正在用的
那一个** ``Store`` 实例——删库删的就是同一个连接底下的那个文件，不会出现「删一个还开着的
SQLite」那种半截状态。

四件事（与端口一一对应）：

- ``is_bound()``：「已绑定 / 未绑定」的**唯一事实来源**；
- ``verify(token)``：一次 ``list_projects``，**一个字节都不落盘**（ADR-0003）；
- ``impact()``：清库会带走什么——**必须在执行之前读**；
- ``execute(token)``：关连接 → 删库 → 只换 token 写配置（ADR-0011 五）。

``execute`` 的顺序不是随便排的：先关连接，是因为删一个还开着的 SQLite 只会解链一个 inode，
旧连接照样能写；先删库后写配置，是因为写配置失败是三个失败点里唯一会留下「旧账号 + 空库」
的那个——把它排在最后，失败时要说的话只有这一种。关连接与删库**分开**抛
:class:`~dida.sync.engine.RebindCloseError` / :class:`~dida.sync.engine.RebindDeleteError`
（``unlink`` 根本没走到时不能把「删库失败」说成发生过），写配置失败是
:class:`~dida.sync.engine.RebindConfigError`。三者留下的东西不同，界面分得开（#93 定屏幕原句）。

**token 的三条禁令**（与 ``Config.__repr__`` 同一条）：不进日志、不进屏幕、不进异常消息。
所以这里的每一条失败消息都只描述「哪一步失败了、留下了什么」，不带凭据。
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Callable

from dida.api.client import DEFAULT_BASE_URL
from dida.api.errors import AuthError
from dida.api.transport import Transport
from dida.config import (
    Config,
    ConfigError,
    load_config,
    needs_token,
    save_config,
    verify_token,
)
from dida.storage.store import Store
from dida.sync.engine import (
    RebindCloseError,
    RebindConfigError,
    RebindDeleteError,
    RebindImpact,
)

__all__ = ["RebindService"]


class RebindService:
    """换绑端口的生产实现。

    ``config`` 是**装配时**读到的那一份（``build_app`` 也用它算「已绑定 / 未绑定」）；
    ``path`` 是配置文件的落点；``database`` 是本地副本那个文件（与 ``Store`` 是同一个路径）。

    ``transport`` 可注入（测试用 ``FakeTransport``）；生产省略它，交给
    ``transport_factory``（组合根传 :func:`dida.bootstrap.default_transport`，于是「这台机器
    建不出传输层」与第一次运行那条路一样按**网络失败**报，而不是把 app 带走）。自己造出来的
    传输在验证结束时关掉——一次换绑不必留下一个连接池。
    """

    def __init__(
        self,
        *,
        store: Store,
        config: Config,
        database: Path,
        path: Path | None = None,
        transport: Transport | None = None,
        transport_factory: Callable[[], Transport] | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self._store = store
        self._config = config
        self._database = database
        self._path = path
        self._transport = transport
        self._transport_factory = transport_factory
        self._base_url = base_url

    # ---------------------------------------------------------------- 端口：绑没绑

    def is_bound(self) -> bool:
        """「已绑定 / 未绑定」的唯一出处：配置里有没有可用 token。

        官方 API 没有「当前用户」端点，所以这里能说的**只有**这一位事实（GLOSSARY 的
        「账号绑定」）：说不清绑到了谁身上。
        """
        return not needs_token(self._config)

    # ---------------------------------------------------------------- 端口：验一下这个 token

    async def verify(self, token: str) -> None:
        """只验一次 ``list_projects``：**不关库、不删库、不写配置**（ADR-0003）。

        校验口径与首次运行那条路一致（同一个 :func:`dida.config.verify_token`）：401/403 由
        客户端抛 :class:`~dida.api.errors.AuthError`，连不上抛
        :class:`~dida.api.errors.NetworkError`，两者**不互相冒充**。空粘贴在本地就拦下
        （建传输层之前），不浪费一次请求。
        """
        stripped = await verify_token(
            token,
            transport=self._transport,
            transport_factory=self._transport_factory,
            base_url=self._base_url,
        )
        if stripped is None:
            raise AuthError("token 不能为空，请重新粘贴")

    # ---------------------------------------------------------------- 端口：会丢多少

    def impact(self) -> RebindImpact:
        """清库会带走什么，四个数各数各的（工单 #92 的判据；ADR-0011 四）。

        口径（与确认页逐字对得上，改这里就要一起改测试）：

        - **清单** = ``lists`` 表的行数，**含收集箱那一行**——它也在本地副本里，也会跟着消失；
        - **任务** = ``tasks`` 表的全部行：含已完成的、含还有待推送改动的那几条；
        - **自定义视图** = ``views`` 表的行数（换绑是它们唯一会死的场景）；
        - **待推送** = 任务队列 + 清单队列，直接读 ``Store.pending_count()``（与状态栏同一个数）。

        **必须在 :meth:`execute` 之前调用**：那之后库已经没了，这些读都会抛。
        """
        return RebindImpact(
            lists=len(self._store.lists()),
            tasks=len(self._store.tasks()),
            views=len(self._store.view_definitions()),
            pending=self._store.pending_count(),
        )

    # ---------------------------------------------------------------- 端口：执行

    async def execute(self, token: str) -> None:
        """执行换绑：**关连接 → 删库 → 只换 token 写配置**（ADR-0011 五）。

        写配置用 ``replace(load_config(path), token=token)``：日界、已完成窗口、两个开关
        原样保留——手写一份 ``Config(token=...)`` 会把它们全冲回默认值。

        失败留下什么：

        - 关连接失败 → :class:`RebindCloseError`：``unlink`` 还没走到，配置与库**文件**都没动；
        - 删库（``unlink``）失败 → :class:`RebindDeleteError`：配置没动，旧库文件完好；
        - 写配置失败 → :class:`RebindConfigError`：库已经删了，配置仍是旧账号。

        两条消息里都没有 token。关连接与删库**分开**捕获：把没有发生的 ``unlink`` 说成
        「删库失败」就是在屏幕上说一件假的步骤（#93 的判据是「说出是哪一步」）。
        """
        token = token.strip()
        if not token:
            raise AuthError("token 不能为空，请重新粘贴")
        try:
            self._store.close()
        except (OSError, sqlite3.Error) as exc:
            raise RebindCloseError(
                f"关连接失败，配置与旧库文件都没动（可以重开再试一次）：{exc}"
            ) from exc
        try:
            self._remove_database()
        except OSError as exc:
            raise RebindDeleteError(
                f"删库失败，配置与旧库都没动（可以再来一次）：{exc}"
            ) from exc
        try:
            config = replace(load_config(self._path), token=token)
            save_config(config, self._path)
        except (OSError, ConfigError) as exc:
            raise RebindConfigError(
                "库已经删了，但写配置失败——配置里仍是旧账号，重开是旧账号 + 空库"
                f"（{exc}）"
            ) from exc
        # 同一个进程里这支端口还会被读到（`is_bound`），所以把它自己记的那一份也换掉。
        self._config = config

    def _remove_database(self) -> None:
        """把本地副本那个文件整个删掉（不是删表）。

        文件本来就不在不算失败：目标状态是「没有旧库」。
        """
        try:
            self._database.unlink()
        except FileNotFoundError:
            pass

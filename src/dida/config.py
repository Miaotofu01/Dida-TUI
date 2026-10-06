"""配置与凭据（第 1 个深模块）。

配置文件 ``~/.config/dida-tui/config.toml``，权限 0600。五个键：
``token``、``day_end``（默认 ``"24:00"``）、``refresh_on_start``、
``push_on_change``、``completed_window_hours``；文件缺失时生成默认配置，
生成出来的那份没有 token —— 那就是「首次运行」的信号。

``day_end`` 在这一层就被校验并规范化：``"24:00"`` 与 ``"00:00"`` 表示同一件事
（不偏移），规范形式统一写成 ``"00:00"``，这样 t04（逻辑日）只需要处理一种写法。
非法取值（``"25:00"``、``"04:30:30"``、``"banana"`` ……）在构造 :class:`Config`
时就被拒绝，绝不会落盘。

公开接口：

- :class:`Config` —— 上面五个键的数据类，字段名就是文件里的键名。
- :class:`Credentials` —— 「先验证、后落盘」的入口：一次成功的「列清单」调用
  是 token 落盘的唯一途径。
- :func:`config_path` / :func:`load_config` / :func:`save_config` —— 位置、读、写。
- :func:`needs_token` —— 首次运行的信号。
- :class:`DayEndReader` —— 配置里那个「一天结束的时刻」，跟着文件走（工单 #46 的重读路径）。

细节与理由见 ``docs/adr/0003-config-canonical-day-end-and-verified-token.md``。
本模块由 t03 落地；t07 扩展客户端时不要动这里的失败分类。
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

import tomli_w

from dida.api.client import DEFAULT_BASE_URL, DidaApiClient
from dida.api.errors import AuthError, DidaError
from dida.api.transport import Transport


class ConfigError(Exception):
    """配置文件的内容不合法：坏 TOML、不认识的键、类型或取值不对。"""


class CredentialsError(DidaError):
    """服务端不接受这个 token：用户得重新粘贴一个。

    继承 ``DidaError``：UI 对「凭据失效」和「网络失败」用同一套结构化错误接，
    但仍然分得清是哪一种（看类型与 ``status_code``）。
    """


_PASTE_HINT = "请在滴答清单网页版「设置 > 账户 > API Token」里复制后粘贴"


class InvalidDayEnd(ConfigError):
    """``day_end`` 不是 ``00:00``–``24:00`` 之间的整分钟时刻（类型不对也算）。"""


_DAY_END_PATTERN = re.compile(r"^(\d{1,2}):(\d{2})$")
_NO_OFFSET = "00:00"


def _check_field_types(config: Config) -> None:
    """手写的 TOML 很容易把类型写错；这里给出可读提示，而不是 ``TypeError``。

    ``day_end`` 不在这里管：它的问题（包括类型不对）统一由
    :func:`_normalise_day_end` 抛 :class:`InvalidDayEnd`。
    """
    if config.token is not None and not isinstance(config.token, str):
        raise ConfigError(f"token 应该是字符串，收到 {config.token!r}")
    for name in ("refresh_on_start", "push_on_change"):
        value = getattr(config, name)
        if not isinstance(value, bool):
            raise ConfigError(f"{name} 应该是布尔值，收到 {value!r}")
    window = config.completed_window_hours
    if not isinstance(window, int) or isinstance(window, bool):
        raise ConfigError(f"completed_window_hours 应该是整数，收到 {window!r}")


def _normalise_day_end(value: object) -> str:
    """校验并规范化 ``day_end``；非法取值抛 :class:`InvalidDayEnd`。"""
    match = _DAY_END_PATTERN.match(value) if isinstance(value, str) else None
    if match is None:
        raise InvalidDayEnd(f'day_end 必须是 "HH:MM"（00:00–24:00），收到 {value!r}')
    hours, minutes = int(match.group(1)), int(match.group(2))
    if minutes > 59 or hours > 24 or (hours == 24 and minutes):
        raise InvalidDayEnd(f"day_end 必须在 00:00–24:00 之间，收到 {value!r}")
    if hours == 24:  # 24:00 就是 00:00：两者都表示不偏移
        return _NO_OFFSET
    return f"{hours:02d}:{minutes:02d}"


@dataclass(frozen=True)
class Config:
    """``config.toml`` 的内容。字段名就是文件里的键名。"""

    token: str | None = None
    day_end: str = "24:00"
    refresh_on_start: bool = True
    push_on_change: bool = True
    completed_window_hours: int = 168

    def __post_init__(self) -> None:
        _check_field_types(self)
        # 粘贴来的 token 常带换行或空格；全空就等于「还没有 token」（首次运行）。
        token = self.token.strip() if isinstance(self.token, str) else self.token
        object.__setattr__(self, "token", token or None)
        object.__setattr__(self, "day_end", _normalise_day_end(self.day_end))

    def __repr__(self) -> str:
        """token 不进 repr：pytest 的失败输出、日志、终端回滚都不该留下它。"""
        token = "'***'" if self.token is not None else "None"
        return (
            f"Config(token={token}, day_end={self.day_end!r}, "
            f"refresh_on_start={self.refresh_on_start!r}, "
            f"push_on_change={self.push_on_change!r}, "
            f"completed_window_hours={self.completed_window_hours!r})"
        )


CONFIG_RELATIVE_PATH = Path(".config") / "dida-tui" / "config.toml"


def config_path() -> Path:
    """配置文件位置：``~/.config/dida-tui/config.toml``（按 spec 写死，不认 XDG）。"""
    return Path.home() / CONFIG_RELATIVE_PATH


def load_config(path: Path | None = None) -> Config:
    """读配置。文件缺失时先生成一份默认配置再返回它（这就是首次运行的信号）。"""
    path = config_path() if path is None else path
    if not path.exists():
        config = Config()
        save_config(config, path)
        return config
    with path.open("rb") as stream:
        try:
            data = tomllib.load(stream)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"配置文件不是合法的 TOML：{path}（{exc}）") from exc
    unknown = sorted(set(data) - {field.name for field in fields(Config)})
    if unknown:
        raise ConfigError(f"配置文件里有不认识的键：{'、'.join(unknown)}（{path}）")
    return Config(**data)


def needs_token(config: Config) -> bool:
    """首次运行（或 token 被清掉）的信号：还没有可用的 token，该请用户粘贴一个。"""
    return config.token is None


def save_config(config: Config, path: Path | None = None) -> None:
    """落盘：0600，写入前先建父目录；``token`` 为 ``None`` 时不写这个键。

    权限显式设成 0600：``os.open`` 的 mode 会被 umask 削掉，而且对已经存在的文件
    它根本不会改权限，所以覆盖写一个旧的宽松文件也要能收紧。
    """
    path = config_path() if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {key: value for key, value in asdict(config).items() if value is not None}
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(tomli_w.dumps(data))
    os.chmod(path, 0o600)


class DayEndReader:
    """配置文件里那个「一天结束的时刻」，**跟着文件走**（工单 #46 的重读路径）。

    启动时读一次配置、把 ``day_end`` 烤进引擎，从前的下场是：用户在配置里改完边界值，界面
    一直按旧的算，只能重启。重读需要一个**每次都问得出当前值**的口子，就是这里。

    ``current()`` 回答「现在文件里写的是什么」：每次都重新读一遍。这不是浪费——要读的是一个
    一百来字节的本地文件，而调用方是 app 那条 1 秒一次的心跳；相比之下「先比 ``stat`` 再决定
    要不要解析」省下的那点时间，换来的是一类静默的漏读（改动落在上一次 ``stat`` 的同一个
    时钟粒度里就再也看不见了）。**值有没有变**由下游判断（``SyncEngine.set_day_end`` 会说），
    这里只管读。
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = config_path() if path is None else path

    def current(self) -> str | None:
        """现在配置里写着的 ``day_end``；读不了就是 ``None``（调用方沿用上一次那个日界）。

        「读不了」的几种情形都算 ``None``，而且**都不出声**：文件不在（不替用户建一份默认的
        ——那是启动时 :func:`load_config` 的事，不是一秒问一次的读手该干的）、TOML 写坏了、
        值非法或键不认识（:class:`ConfigError` 一族）、打开就失败（``OSError``：权限、悬空的
        软链、路径上根本不是个文件）。抛出去的下场是：用户手改到一半保存一次，界面就当场崩
        掉——而这东西是挂在定时器上的。
        """
        if not self._path.exists():
            return None
        try:
            return load_config(self._path).day_end
        except (ConfigError, OSError):
            return None


class Credentials:
    """凭据的验证入口：一次成功的「列清单」调用是 token 落盘的唯一途径。

    UI 只跟这个对象打交道，不碰传输层。失败都是结构化的：

    - :class:`CredentialsError` —— 服务端不接受这个 token（401/403），
      消息直接引导用户重新粘贴；
    - 网络失败与其它服务端拒绝原样抛出（``NetworkError`` / ``ServerRejectionError``），
      不能把它们说成「凭据失效」。

    任何失败都不改动磁盘上的配置 —— 先验证，通过了才写。
    """

    def __init__(
        self,
        *,
        transport: Transport,
        path: Path | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self._transport = transport
        self._path = path
        self._base_url = base_url

    async def verify_and_store(self, token: str) -> Config:
        """验证 token 并落盘，返回写好的配置；失败抛错且磁盘不变。"""
        token = token.strip()
        if not token:  # 空粘贴本地就拦下，不浪费一次请求
            raise CredentialsError(f"token 不能为空，请重新粘贴：{_PASTE_HINT}")
        client = DidaApiClient(token=token, transport=self._transport, base_url=self._base_url)
        try:
            await client.list_projects()
        except AuthError as exc:
            raise CredentialsError(
                f"凭据失效，请重新粘贴 token（HTTP {exc.status_code}）：{_PASTE_HINT}",
                status_code=exc.status_code,
            ) from exc
        config = replace(load_config(self._path), token=token)
        save_config(config, self._path)
        return config

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
    completed_window_hours: int = 24

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

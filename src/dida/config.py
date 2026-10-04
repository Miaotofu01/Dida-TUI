"""配置与凭据（第 1 个深模块）。t03 填充。

配置文件 ``~/.config/dida-tui/config.toml``，权限 0600。五个键：
``token``、``day_end``（默认 ``"24:00"``）、``refresh_on_start``、
``push_on_change``、``completed_window_hours``。

公开接口：

- :class:`Config` —— 上面五个键的数据类；``day_end`` 原样保留文件里的写法
  （``"24:00"`` 这种 24 点写法怎么解析归 t04）。
- :func:`config_path` —— 配置文件位置。
- :func:`load_config` / :func:`save_config` —— 读与写（0600、首次粘贴、验证后落盘）归 t03。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    """``config.toml`` 的内容。字段名就是文件里的键名。"""

    token: str
    day_end: str = "24:00"
    refresh_on_start: bool = True
    push_on_change: bool = True
    completed_window_hours: int = 24


def config_path() -> Path:
    """配置文件位置。t03 实现。"""
    raise NotImplementedError("配置路径由 t03 实现")


def load_config(path: Path | None = None) -> Config:
    """读配置。缺文件、坏 TOML、权限不对都要给出可读的提示——t03 实现。"""
    raise NotImplementedError("配置读取由 t03 实现")


def save_config(config: Config, path: Path | None = None) -> None:
    """落盘（0600）。t03 实现。"""
    raise NotImplementedError("配置写入由 t03 实现")

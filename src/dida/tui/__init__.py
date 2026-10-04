"""TUI：三栏外壳 + 状态栏。只通过同步引擎读写，不碰存储与 API 客户端。"""

from dida.tui.app import DidaApp

__all__ = ["DidaApp"]

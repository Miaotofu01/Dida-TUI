"""日界改了立刻生效（工单 #46）：配置文件 → 引擎 → 屏幕，**一条线**测穿。

工单的验收标准写得很直白——「有测试覆盖『改配置 → 逻辑日变化 → 视图跟着变』」——三个各自通过、
而接线断了的单元测试满足不了它。所以这个文件里三层都有，而且界面那一层走的是**生产真的会走的
那两条触发**（周期泵的 1 秒心跳、``r``），不是直接叫内部方法：

- 配置层：``DayEndReader``——每次都重新读，读不了给 ``None``，**从不写盘**；
- 引擎层：``SyncEngine.set_day_end`` 之后，「今天」视图、逾期判定、顺延一起按新的逻辑日重算；
- 界面层：**接缝一**（真 ``DidaApp`` + ``FakeBackend`` + Textual 的 Pilot）——改临时目录里的
  配置文件，看屏幕上写着哪个逻辑日、哪条任务还在「今天」里、光标还在不在那一行。

「现在」与「配置在哪」都在测试手里（``ManualClock`` + ``tmp_path``）。真实的
``~/.config/dida-tui/config.toml`` 一次都不读——它握着用户的 token。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dida.config import Config, DayEndReader, load_config, save_config

TZ = timezone(timedelta(hours=8))
WIDE = (100, 30)
"""一栏三层不需要降级，一个普通的终端尺寸就够。"""


def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
    """2026-03 里的一个时刻（带时区）。"""
    return datetime(2026, 3, day, hour, minute, tzinfo=TZ)


def write_day_end(path: Path, day_end: str) -> None:
    """改配置里的边界值：**只换这一个键**，其余原样（用户就是这么改的）。

    读写都走 ``dida.config`` 的公开 API，不手拼 TOML、也不碰真的 ``~/.config``。
    """
    save_config(replace(load_config(path), day_end=day_end), path)


# ------------------------------------------------------------------ 配置层：读手


def test_the_reader_gives_the_boundary_that_is_on_disk(tmp_path):
    """读手给的日界就是文件里写着的那个（规范化过的形式：``24:00`` → ``00:00``）。"""
    path = tmp_path / "config.toml"
    save_config(Config(day_end="24:00"), path)

    assert DayEndReader(path).current() == "00:00"


def test_the_reader_re_reads_after_the_file_changes(tmp_path):
    """改完文件再问一次，给的是**新的**那个——这就是「重读」这两个字。"""
    path = tmp_path / "config.toml"
    save_config(Config(day_end="24:00"), path)
    reader = DayEndReader(path)
    assert reader.current() == "00:00"

    save_config(Config(day_end="04:00"), path)

    assert reader.current() == "04:00"


def test_a_missing_config_file_is_no_value_and_is_not_created(tmp_path):
    """文件不在了就什么都不给，也**不替用户生成**一份默认配置。

    启动时 ``load_config()`` 有「缺文件就建一份默认的」这个行为（首次运行的信号），但一个
    每秒问一次的读手不能有副作用：用户把配置删了，不该由界面悄悄按默认日界继续跑。
    """
    path = tmp_path / "config.toml"

    assert DayEndReader(path).current() is None
    assert not path.exists(), "读手不写盘"


@pytest.mark.parametrize(
    "content",
    [
        'day_end = "0',  # 手改到一半的 TOML
        'day_end = "banana"',  # 合法 TOML，非法的值
        'day_end = "04:00"\nnope = 1',  # 多了一个不认识的键
    ],
)
def test_a_config_that_cannot_be_read_is_no_value_not_an_exception(tmp_path, content):
    """读不了的配置给 ``None``，不抛：一秒问一次的读手不允许把界面带走（沿用上一次那个日界）。"""
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")

    assert DayEndReader(path).current() is None

"""时钟接缝：注入的「现在」。"""

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dida.sync.engine import SyncEngine
from dida.testing import ManualClock

T0 = datetime(2026, 3, 14, 2, 0, tzinfo=timezone(timedelta(hours=8)))

ROOT = Path(__file__).resolve().parent.parent
CLOCK_MODULE = ROOT / "src" / "dida" / "clock.py"
"""唯一允许读真实时钟的模块：``SystemClock`` 就是「真实时钟」这个适配器本身。"""

REAL_CLOCK_CALLS = ("now", "utcnow", "today")
"""真实时钟上那几个读法：``datetime.now()`` / ``datetime.utcnow()`` / ``date.today()``。

注入的那只钟（``Clock.now()``）不在这一列——它正是**该**被调的那一个。
"""


def _clock_names(tree: ast.Module) -> set[str]:
    """这份文件里指向 ``datetime`` 那一家子的名字。

    先认名字再认调用，是这条守卫唯一站得住的做法：``self._clock.now()`` 与
    ``datetime.now()`` 长得一样（都是 ``X.now()``），差别全在 ``X`` 是谁——前者是注入进来的
    那只钟，后者是真实时钟。名字从 import 语句上取（含 ``as`` 别名），所以
    ``from datetime import datetime as dt`` 也认。
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "datetime":
                    names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "datetime":
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _receiver_name(node: ast.expr) -> str | None:
    """``self._clock`` → ``self``；``datetime`` → ``datetime``；``dt.datetime`` → ``dt``。

    取的是最外面那个名字（表达式链的根），因为「谁引进来的」这件事只挂在根上。
    """
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def test_status_reports_the_injected_now():
    engine = SyncEngine(clock=ManualClock(T0))

    assert engine.status().checked_at == T0


def test_status_follows_the_injected_clock():
    clock = ManualClock(T0)
    engine = SyncEngine(clock=clock)

    clock.advance(timedelta(minutes=90))

    assert engine.status().checked_at == T0 + timedelta(minutes=90)


def test_nothing_outside_the_clock_adapter_reads_the_real_clock():
    """「现在」只能来自注入的 ``Clock``（README 的硬规则）——用 AST 守着，规矩是绝对的。

    这条守卫是**全量**的，因为「只是查一次时区、不是读『现在几点』」正是一个真实的漏网：
    ``detail.py`` 曾经用 ``datetime.now().astimezone().tzinfo`` 取本地时区，而当时没有任何
    测试拦它（工单 #58 的 T1）。真实时钟只有 ``clock.py`` 那个适配器能碰，业务代码一律从
    注入的那只钟问时间——``self._clock.now()`` 是**允许**的，它问的正是那只钟。
    """
    offenders: dict[str, list[str]] = {}
    for path in sorted((ROOT / "src" / "dida").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        real = _clock_names(tree)
        found = [
            f"{node.func.attr}() 第 {node.lineno} 行"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in REAL_CLOCK_CALLS
            and _receiver_name(node.func.value) in real
        ]
        if found and path != CLOCK_MODULE:
            offenders[str(path.relative_to(ROOT))] = found

    assert not offenders, (
        f"这几处绕开了注入的钟（只有 {CLOCK_MODULE.name} 可以读真实时钟）：{offenders}"
    )
    # 守卫自己也要有据可依：那个允许的适配器里确实读着真实时钟，否则这条可能只是在扫空目录。
    adapter = ast.parse(CLOCK_MODULE.read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in REAL_CLOCK_CALLS
        and _receiver_name(node.func.value) in _clock_names(adapter)
        for node in ast.walk(adapter)
    ), f"没在 {CLOCK_MODULE.name} 里读到真实时钟——守卫扫错地方了？"

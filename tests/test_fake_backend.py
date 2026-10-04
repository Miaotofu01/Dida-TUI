"""假后端自己的契约。

它是接缝一的地基，t12/t16/t17/t20 都要在上面按键，所以这里钉住两件事：
它一直满足引擎接口（t11/t13 往 ``Engine`` 上加方法时要立刻发现），
以及写操作真的被记下来了。
"""

from datetime import datetime, timedelta, timezone

from dida.sync.engine import Engine
from dida.testing import FakeBackend, ManualClock

T0 = datetime(2026, 3, 14, 12, 3, tzinfo=timezone(timedelta(hours=8)))


def test_fake_backend_satisfies_the_engine_interface():
    assert isinstance(FakeBackend(clock=ManualClock(T0)), Engine)


def test_fake_backend_records_the_write_calls_later_tickets_assert_on():
    backend = FakeBackend(clock=ManualClock(T0))

    backend.refresh()
    backend.complete("t1")
    backend.defer("t2")

    assert (backend.refreshes, backend.completed, backend.deferred) == (1, ["t1"], ["t2"])

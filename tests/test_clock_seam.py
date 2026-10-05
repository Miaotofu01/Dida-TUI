"""时钟接缝：注入的「现在」。"""

from datetime import datetime, timedelta, timezone

from dida.sync.engine import SyncEngine
from dida.testing import ManualClock

T0 = datetime(2026, 3, 14, 2, 0, tzinfo=timezone(timedelta(hours=8)))


def test_status_reports_the_injected_now():
    engine = SyncEngine(clock=ManualClock(T0))

    assert engine.status().checked_at == T0


def test_status_follows_the_injected_clock():
    clock = ManualClock(T0)
    engine = SyncEngine(clock=clock)

    clock.advance(timedelta(minutes=90))

    assert engine.status().checked_at == T0 + timedelta(minutes=90)

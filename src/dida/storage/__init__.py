"""本地存储：SQLite（stdlib）本地副本。

生产实现是 :class:`~dida.storage.store.Store`（同时是 ``sync.view.ViewSource`` 的实现）；
其余几个类型是它的公开词汇表，t09 / t10 直接用。
"""

from dida.storage.store import (
    ChangeKind,
    FieldOverride,
    PendingChange,
    RefreshReport,
    Store,
    StoredSyncState,
)

__all__ = [
    "ChangeKind",
    "FieldOverride",
    "PendingChange",
    "RefreshReport",
    "Store",
    "StoredSyncState",
]

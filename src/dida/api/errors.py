"""客户端对外的结构化错误。

UI 只会看到这几类失败，绝不会看到裸异常。第 4 类（字段被静默忽略）由 t07 的
本地守卫在发送前抛出——服务端不会报错，只会默默丢掉那个字段。
"""

from __future__ import annotations


class DidaError(Exception):
    """本客户端对外失败的基类。"""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class NetworkError(DidaError):
    """连不上、超时、TLS 失败等传输层问题。"""


class AuthError(DidaError):
    """凭据不被接受（401/403）：token 过期或写错。"""


class ServerRejectionError(DidaError):
    """服务端拒绝（其它 4xx/5xx）。"""


class FieldIgnoredError(DidaError):
    """服务端会静默忽略的字段（非法日期、无日期任务上的重复规则等）。

    t07 的本地守卫在发送前抛这个错，绝不把这种请求发出去。
    """

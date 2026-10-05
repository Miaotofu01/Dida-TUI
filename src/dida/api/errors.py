"""客户端对外的结构化错误。

UI 只会看到这几类失败，绝不会看到裸异常。第 4 类（字段被静默忽略）由 t07 的
本地守卫在发送前抛出——服务端不会报错，只会默默丢掉那个字段。
"""

from __future__ import annotations

from typing import Mapping


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

    t07 的本地守卫在发送前抛这个错，绝不把这种请求发出去。``field`` 指出是哪个字段
    出的问题，UI 可以直接把它显示给用户。
    """

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.field = field


class InvalidDateError(FieldIgnoredError):
    """日期字段不是文档要求的形式。

    服务端对写不进去的日期一声不吭（既不改也不报错），所以本地严格校验：
    ``yyyy-MM-dd'T'HH:mm:ssZ``，例 ``2019-11-13T03:00:00+0000``。
    """


class DatelessRepeatError(FieldIgnoredError):
    """没有截止/开始时间的任务上写了重复规则。

    文档没写、但服务端的实测行为是：这种任务的 ``repeatFlag`` 被**静默清空**。
    用户会以为「每天都提醒」设好了，几天后才发现只做过一次。所以不写。
    """


class MalformedResponseError(DidaError):
    """服务端回了 2xx，但响应体不是文档说的那个形状。

    两种都算：不是 JSON（代理的登录页、被截断的响应），或者是 JSON 但形状不对——
    该是对象的地方给了数组、该是数组的地方给了对象、缺必需字段。``json.JSONDecodeError``
    / ``AttributeError`` / ``KeyError`` 这种裸异常一个都不许抛给 UI。
    """


class BatchRejectedError(DidaError):
    """批量更新（``POST /open/v1/task/batch``）里**每个任务**的失败。

    它藏在 ``200 OK`` 里：响应体是 ``{id2etag, id2error}``（openapi :567），HTTP 状态码
    对逐条结果一个字都没说——整批全失败也是 200。所以「没抛异常」不等于「改成了」，
    这个类就是那道分界线：:attr:`errors` 是任务 id → 文档给的错误码（``NOT_EXISTED`` /
    ``DELETED`` / ``EXCEED_QUOTA`` ……）。

    取消完成走的就是这条路，而它**一个字都不能静默**：批量更新没成而用户以为成了，
    那条任务在屏幕上就不再是已完成的样子，重开却还在已完成区里——两边各说各话。
    """

    def __init__(
        self,
        message: str,
        *,
        errors: Mapping[str, str] | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.errors: Mapping[str, str] = dict(errors or {})
        """哪个任务、哪一个错误码（文档 :567 的那张码表里的一档）。"""

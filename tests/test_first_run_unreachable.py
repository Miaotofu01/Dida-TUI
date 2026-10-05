"""首次运行碰到「传输层建不出来」时说的话（评审后续修）。

背景是这台机器上的真实一幕：``all_proxy=socks5://…`` 而没装 ``socksio``，于是
``httpx.AsyncClient()`` 当场抛 ``ImportError``。这条路径原来在 :func:`dida.bootstrap._first_run`
里是**裸的** ``HttpxTransport()``，异常一路冒到 :func:`main` 的通配 ``except``，被糊成：

    凭据没验证通过：Using SOCKS proxy, but the 'socksio' package is not installed.
    再运行一次 dida 重新粘贴。

两句都是错的：token 一个字符都没错（用户会开始怀疑自己），而「重新粘贴」再粘一百次也
一样——传输层不是靠粘 token 建出来的。

这里钉住三件事：报的是环境问题、给出两条能照着做的出路、以及**成功路径不许被污染**
（能连上时仍然走「验证通过才落盘」）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dida.bootstrap import main
from dida.api.errors import NetworkError
from dida.config import Config, load_config
from dida.api.transport import Transport
from dida.testing import FakeTransport

SOCKS_ERROR = "Using SOCKS proxy, but the 'socksio' package is not installed."


def _boom() -> Transport:
    """模拟「这台机器建不出传输层」。"""
    raise ImportError(SOCKS_ERROR)


def _no_token_yet(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """把配置指到一个还没有 token 的文件上：那就是首次运行的信号。"""
    cfg = tmp_path / "config.toml"
    load_config(cfg)  # 生成默认配置（没有 token）
    monkeypatch.setattr("dida.bootstrap.load_config", lambda *a, **k: Config(token=None))
    return cfg


async def test_first_run_uses_the_degrading_transport_factory(monkeypatch):
    """``_first_run`` 必须走 ``default_transport``，不能裸构造。

    裸构造就是这条缺陷的根：降级只发生在 app 那条路径上，首次运行绕过了它。

    做法是 patch ``HttpxTransport`` 这个**全局名**：``default_transport`` 的默认参数
    ``factory=HttpxTransport`` 在该函数**定义时**就绑定了，所以从它自己的身体里读不到
    patch——除非它当初写的就是裸构造（那正是缺陷的形态）。因此这里观察「有没有走工厂」的
    办法是：一个带 ``aclose`` 的替身被调用了，就说明确实经过了 ``default_transport``。
    """
    from dida import bootstrap

    calls: list[str] = []

    class _SpyTransport:
        def __init__(self) -> None:
            calls.append("constructed")
            self._inner = FakeTransport(json=[{"id": "inbox", "name": "收集箱"}])

        async def send(self, request):
            return await self._inner.send(request)

        async def aclose(self) -> None:
            calls.append("closed")

    monkeypatch.setattr(bootstrap, "HttpxTransport", _SpyTransport)
    monkeypatch.setattr(bootstrap, "paste_token", _stub_paste_token(Config(token="tok")))

    await bootstrap._first_run()

    assert "constructed" in calls, "首次运行也要经过可降级的工厂，而不是裸构造"
    assert "closed" in calls, "用完要关掉连接池（这次运行自己的那个事件循环）"


def _stub_paste_token(result: Config):
    async def _paste(*, transport, **kwargs):
        return result

    return _paste


def test_unreachable_transport_is_reported_as_an_environment_problem(monkeypatch, tmp_path):
    """建不出传输层时：说环境、别赖 token、给两条出路。"""
    _no_token_yet(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "dida.bootstrap._first_run",
        _raising_first_run(NetworkError(f"连不上服务端：{SOCKS_ERROR}")),
    )

    with pytest.raises(SystemExit) as caught:
        main()

    message = str(caught.value)
    assert SOCKS_ERROR in message, "要说清真实原因（用户才知道去查代理）"
    assert "不是 token 的问题" in message, "别让用户去怀疑自己的 token"
    assert "socksio" in message, "给出路一：让代理能用"
    assert "all_proxy" in message, "给出路二：绕开代理"
    assert "重新粘贴" not in message, "「再粘一次」在这条路径上帮不上忙，不许作为建议出现"


def test_a_rejected_token_still_says_credentials(monkeypatch, tmp_path):
    """凭据被拒仍然是凭据问题：两条路径不许合并成一句笼统的话。"""
    from dida.config import CredentialsError

    _no_token_yet(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "dida.bootstrap._first_run", _raising_first_run(CredentialsError("token 被服务端拒绝"))
    )

    with pytest.raises(SystemExit) as caught:
        main()

    message = str(caught.value)
    assert "凭据没验证通过" in message, "凭据问题照旧按凭据说"
    assert "重新粘贴" in message
    assert "socksio" not in message, "凭据问题不该把用户往代理上引"


def test_a_network_failure_mid_validation_says_environment(monkeypatch, tmp_path):
    """真正发出请求之后才失败，也是环境问题，不该说成凭据问题。"""
    from dida.api.errors import NetworkError

    _no_token_yet(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "dida.bootstrap._first_run", _raising_first_run(NetworkError("连不上服务端：超时"))
    )

    with pytest.raises(SystemExit) as caught:
        main()

    message = str(caught.value)
    assert "凭据没验证通过" not in message, "网络失败不是凭据失败"


def _raising_first_run(exc: BaseException):
    async def _first_run(*args, **kwargs):
        raise exc

    return _first_run

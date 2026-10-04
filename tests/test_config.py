"""配置与凭据（第 1 个深模块）：配置文件、``day_end`` 规范形式、验证后落盘。

测试落在两个地方：文件系统行为直接对 ``tmp_path`` 断言（这就是本模块的公开行为），
「列清单」验证走接缝二（注入 ``FakeTransport``，只钉请求形状）。
绝不触碰真实的 ``~/.config``：凡是会写盘的调用都显式传 ``path``。
"""

from __future__ import annotations

import stat
import tomllib
from pathlib import Path

import httpx
import pytest

from dida.api.errors import NetworkError, ServerRejectionError
from dida.config import (
    Config,
    ConfigError,
    Credentials,
    CredentialsError,
    InvalidDayEnd,
    config_path,
    load_config,
    needs_token,
    save_config,
)
from dida.testing import FakeTransport


def test_config_path_is_the_documented_location(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    assert config_path() == Path(tmp_path) / ".config" / "dida-tui" / "config.toml"


def test_day_end_defaults_to_no_offset():
    assert Config().day_end == "00:00"


def test_24_00_and_00_00_are_one_and_the_same_representation():
    assert Config(day_end="24:00").day_end == "00:00"
    assert Config(day_end="00:00").day_end == "00:00"


def test_day_end_is_normalised_to_a_zero_padded_hh_mm():
    assert Config(day_end="4:05").day_end == "04:05"


@pytest.mark.parametrize(
    "value",
    ["25:00", "24:30", "04:30:30", "banana", "", "4", "04:5", "04:60", "-1:00", 4, None],
)
def test_illegal_day_end_is_rejected(value):
    with pytest.raises(InvalidDayEnd):
        Config(day_end=value)


def test_missing_file_generates_a_default_config_without_a_token(tmp_path):
    path = tmp_path / "dida-tui" / "config.toml"

    config = load_config(path)

    assert config == Config()
    assert config.token is None
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "token" not in path.read_text(encoding="utf-8")
    assert tomllib.loads(path.read_text(encoding="utf-8"))["day_end"] == "00:00"


def test_existing_file_is_read_exactly_as_written(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'token = "tok-1"\n'
        'day_end = "04:00"\n'
        "refresh_on_start = false\n"
        "push_on_change = false\n"
        "completed_window_hours = 72\n",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config == Config(
        token="tok-1",
        day_end="04:00",
        refresh_on_start=False,
        push_on_change=False,
        completed_window_hours=72,
    )


def test_saving_tightens_the_permissions_of_an_existing_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('token = "old"\n', encoding="utf-8")
    path.chmod(0o644)

    save_config(Config(token="tok-2"), path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_pasted_token_round_trips_through_the_file(tmp_path):
    path = tmp_path / "config.toml"

    save_config(Config(token="tok-2", day_end="04:00"), path)

    assert load_config(path) == Config(token="tok-2", day_end="04:00")


@pytest.mark.parametrize("pasted", ["tok-1", "  tok-1", "tok-1\n", "\ttok-1  "])
def test_a_pasted_token_is_stripped(pasted):
    assert Config(token=pasted).token == "tok-1"


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_a_blank_token_counts_as_no_token(blank):
    assert Config(token=blank).token is None


def test_broken_toml_is_a_readable_config_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("token = \n", encoding="utf-8")

    with pytest.raises(ConfigError) as caught:
        load_config(path)

    assert str(path) in str(caught.value)


def test_an_unknown_key_is_rejected_by_name(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('day_endd = "04:00"\n', encoding="utf-8")

    with pytest.raises(ConfigError) as caught:
        load_config(path)

    assert "day_endd" in str(caught.value)


@pytest.mark.parametrize(
    "line",
    [
        "token = 1",
        'refresh_on_start = "yes"',
        "push_on_change = 1",
        'completed_window_hours = "24"',
        "completed_window_hours = true",
    ],
)
def test_a_wrongly_typed_value_is_rejected(tmp_path, line):
    path = tmp_path / "config.toml"
    path.write_text(line + "\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(path)


def test_a_config_without_a_token_is_the_first_run_signal():
    assert needs_token(Config()) is True
    assert needs_token(Config(token="tok-1")) is False


async def test_a_pasted_token_is_stored_after_a_successful_list_projects(tmp_path):
    path = tmp_path / "config.toml"
    transport = FakeTransport(json=[{"id": "inbox", "name": "收集箱"}])
    credentials = Credentials(transport=transport, path=path)

    config = await credentials.verify_and_store("  tok-9\n")

    assert config.token == "tok-9"
    assert transport.last_request.method == "GET"
    assert str(transport.last_request.url) == "https://api.dida365.com/open/v1/project"
    assert transport.last_request.headers["Authorization"] == "Bearer tok-9"
    assert load_config(path).token == "tok-9"


@pytest.mark.parametrize("status_code", [401, 403])
async def test_a_rejected_token_is_never_written(tmp_path, status_code):
    path = tmp_path / "config.toml"
    path.write_text('day_end = "04:00"\n', encoding="utf-8")
    before = path.read_bytes()
    transport = FakeTransport(status_code=status_code, json={})
    credentials = Credentials(transport=transport, path=path)

    with pytest.raises(CredentialsError) as caught:
        await credentials.verify_and_store("wrong-token")

    assert caught.value.status_code == status_code
    assert "重新粘贴" in str(caught.value)
    assert transport.last_request.headers["Authorization"] == "Bearer wrong-token"
    assert path.read_bytes() == before


async def test_re_pasting_a_token_keeps_the_rest_of_the_config(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('day_end = "04:00"\ncompleted_window_hours = 72\n', encoding="utf-8")
    credentials = Credentials(transport=FakeTransport(json=[]), path=path)

    config = await credentials.verify_and_store("tok-9")

    assert config == Config(token="tok-9", day_end="04:00", completed_window_hours=72)
    assert load_config(path) == config


async def test_a_network_failure_is_not_reported_as_a_credential_failure(tmp_path):
    path = tmp_path / "config.toml"
    transport = FakeTransport()
    transport.enqueue(httpx.ConnectError("连不上"))
    credentials = Credentials(transport=transport, path=path)

    with pytest.raises(NetworkError) as caught:
        await credentials.verify_and_store("tok-9")

    assert not isinstance(caught.value, CredentialsError)
    assert not path.exists()


async def test_a_server_rejection_is_not_reported_as_a_credential_failure(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('day_end = "04:00"\n', encoding="utf-8")
    before = path.read_bytes()
    credentials = Credentials(transport=FakeTransport(status_code=500, json={}), path=path)

    with pytest.raises(ServerRejectionError):
        await credentials.verify_and_store("tok-9")

    assert path.read_bytes() == before


async def test_an_empty_paste_never_reaches_the_network(tmp_path):
    path = tmp_path / "config.toml"
    transport = FakeTransport(json=[])
    credentials = Credentials(transport=transport, path=path)

    with pytest.raises(CredentialsError) as caught:
        await credentials.verify_and_store("  \n")

    assert "重新粘贴" in str(caught.value)
    assert transport.requests == []
    assert not path.exists()


def test_the_token_never_leaks_into_a_repr():
    assert "tok-1" not in repr(Config(token="tok-1"))
    assert "None" in repr(Config())  # 没 token 时仍然看得出「还没有」
    assert "day_end='04:00'" in repr(Config(token="tok-1", day_end="04:00"))

"""Every path by which a credential could escape the redaction boundary.

README and output.py both promise a credential cannot escape through an error
message. These are the tests that hold that promise to account, including on
stderr -- which nothing asserted before, and which is why two escapes shipped.
"""

from __future__ import annotations

import pytest

from conftest import FAKE_TOKEN
from hass_axi import output
from hass_axi.cli import main


def test_a_cross_origin_redirect_is_refused_rather_than_followed(run_cli, rest_server):
    """urllib copies Authorization onto a redirect, including across schemes."""
    rest_server.redirect_to = "http://other.example.com/login"
    code, out = run_cli(["state", "list"], {"HA_URL": rest_server.url, "HA_TOKEN": FAKE_TOKEN})
    assert code == 1
    assert "refusing to follow a redirect" in out
    assert "other.example.com" in out
    assert FAKE_TOKEN not in out
    assert "Traceback" not in out


def test_a_scheme_downgrade_on_the_same_host_is_refused(run_cli, rest_server):
    host = rest_server.url.split("://", 1)[1]
    rest_server.redirect_to = f"https://{host}/"
    code, out = run_cli(["state", "list"], {"HA_URL": rest_server.url, "HA_TOKEN": FAKE_TOKEN})
    assert code == 1
    assert "refusing to follow a redirect" in out


def test_a_same_origin_redirect_is_still_followed(run_cli, rest_server):
    """A proxy normalising a path must keep working; only cross-origin is unsafe."""
    rest_server.redirect_to = f"{rest_server.url}/api/states"
    code, out = run_cli(["state", "list"], {"HA_URL": rest_server.url, "HA_TOKEN": FAKE_TOKEN})
    assert code == 0
    assert "light.example_lamp" in out


def test_an_unexpected_exception_becomes_a_structured_error_on_stdout(monkeypatch, capsys):
    """No raw traceback, nothing on stderr, and stdout carries the contract shape."""

    def explode(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("hass_axi.commands.state.run", explode)
    code = main(
        ["state", "list"], environ={"HA_URL": "https://ha.example.com", "HA_TOKEN": FAKE_TOKEN}
    )
    captured = capsys.readouterr()

    assert code == 1
    assert "error: " in captured.out
    assert "INTERNAL_ERROR" in captured.out
    assert "RuntimeError: boom" in captured.out
    assert "Traceback" not in captured.out
    assert captured.err == ""


def test_an_unexpected_exception_never_prints_the_token(monkeypatch, capsys):
    """The reproducer for the escape: the token inside the raised message."""

    def explode(*_args, **_kwargs):
        raise ValueError(f"Invalid header value b'Bearer {FAKE_TOKEN}'")

    monkeypatch.setattr("hass_axi.commands.state.run", explode)
    code = main(
        ["state", "list"], environ={"HA_URL": "https://ha.example.com", "HA_TOKEN": FAKE_TOKEN}
    )
    captured = capsys.readouterr()

    assert code == 1
    assert FAKE_TOKEN not in captured.out
    assert FAKE_TOKEN not in captured.err
    assert output.REDACTED in captured.out


def test_debug_tracebacks_on_stderr_are_redacted(monkeypatch, capsys):
    def explode(*_args, **_kwargs):
        raise ValueError(f"Bearer {FAKE_TOKEN}")

    monkeypatch.setattr("hass_axi.commands.state.run", explode)
    code = main(
        ["--debug", "state", "list"],
        environ={"HA_URL": "https://ha.example.com", "HA_TOKEN": FAKE_TOKEN},
    )
    captured = capsys.readouterr()

    assert code == 1
    assert "Traceback" in captured.err  # the diagnostic is present...
    assert FAKE_TOKEN not in captured.err  # ...but carries no credential
    assert FAKE_TOKEN not in captured.out


def test_debug_actually_enables_diagnostics(run_cli, rest_env, capsys):
    """--debug was advertised but set the variable on a copy of the environment."""
    output.set_debug(False)
    main(["state", "list"], environ=dict(rest_env))
    assert capsys.readouterr().err == ""

    output.set_debug(False)
    main(["--debug", "state", "list"], environ=dict(rest_env))
    err = capsys.readouterr().err
    assert "hass-axi:" in err
    assert "GET" in err


def test_debug_diagnostics_never_carry_the_token(rest_env, capsys):
    output.set_debug(False)
    main(["--debug", "state", "list"], environ=dict(rest_env))
    captured = capsys.readouterr()
    assert FAKE_TOKEN not in captured.err
    assert FAKE_TOKEN not in captured.out


@pytest.fixture(autouse=True)
def _reset_debug():
    yield
    output.set_debug(False)

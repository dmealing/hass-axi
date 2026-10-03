"""Environment-driven configuration, and the errors when it is absent."""

from __future__ import annotations

import pytest

from conftest import FAKE_TOKEN
from hass_axi import config, output
from hass_axi.errors import ConfigError


def test_loads_from_the_primary_variables():
    resolved = config.load({"HA_URL": "https://ha.example.com", "HA_TOKEN": FAKE_TOKEN})
    assert resolved.base_url == "https://ha.example.com"
    assert resolved.token == FAKE_TOKEN


def test_falls_back_to_the_hass_cli_variable_names():
    resolved = config.load({"HASS_SERVER": "https://ha.example.com", "HASS_TOKEN": FAKE_TOKEN})
    assert resolved.base_url == "https://ha.example.com"


def test_primary_variable_wins_over_the_fallback():
    resolved = config.load(
        {
            "HA_URL": "https://primary.example.com",
            "HASS_SERVER": "https://fallback.example.com",
            "HA_TOKEN": FAKE_TOKEN,
        }
    )
    assert resolved.base_url == "https://primary.example.com"


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, "HA_URL and HA_TOKEN"),
        ({"HA_URL": "https://ha.example.com"}, "HA_TOKEN"),
        ({"HA_TOKEN": FAKE_TOKEN}, "HA_URL"),
        ({"HA_URL": "  ", "HA_TOKEN": FAKE_TOKEN}, "HA_URL"),
    ],
)
def test_missing_configuration_names_what_is_absent(environ, expected):
    with pytest.raises(ConfigError) as caught:
        config.load(environ)
    assert expected in caught.value.message
    assert caught.value.help_lines


def test_a_bare_host_defaults_to_https_not_cleartext():
    """A silent http:// default would send the access token in the clear."""
    assert config.normalize_base_url("ha.example.com") == "https://ha.example.com"
    assert config.normalize_base_url("ha.example.com:8123") == "https://ha.example.com:8123"
    # An explicit scheme is always honoured.
    assert config.normalize_base_url("http://ha.example.com") == "http://ha.example.com"


def test_base_url_normalization_trims_noise():
    assert config.normalize_base_url("https://ha.example.com/") == "https://ha.example.com"
    assert config.normalize_base_url("https://ha.example.com/api") == "https://ha.example.com"


def test_url_credentials_are_stripped_and_registered_as_secrets():
    """Basic-auth userinfo must not survive into anything printable.

    The no-argument home view prints the base URL, and `setup hooks` runs that
    view into every agent session, so userinfo there would land in transcripts.
    """
    resolved = config.normalize_base_url("https://someone:hunter2@ha.example.com")
    assert resolved == "https://ha.example.com"
    assert "someone" not in resolved and "hunter2" not in resolved
    # The property that matters is that the password cannot survive, however
    # the surrounding text is shaped.
    assert "hunter2" not in output.redact("password is hunter2")
    assert "hunter2" not in output.redact("pair is someone:hunter2")
    assert output.redact("pair is someone:hunter2") == "pair is <redacted>"


def test_the_home_view_never_prints_url_credentials(run_cli, rest_server):
    host = rest_server.url.split("://", 1)[1]
    env = {"HA_URL": f"http://someone:hunter2@{host}", "HA_TOKEN": FAKE_TOKEN}
    code, out = run_cli([], env)
    assert code == 0
    assert "hunter2" not in out
    assert "someone" not in out


@pytest.mark.parametrize("bad", ["with space", "with\nnewline", "with\ttab", "trailing\r"])
def test_a_token_that_cannot_be_a_header_is_rejected_before_use(bad):
    """http.client raises a ValueError embedding the whole Bearer header.

    That is a credential inside a traceback, so the token is validated at the
    point it is read rather than at the point it is encoded.
    """
    with pytest.raises(ConfigError) as caught:
        config.load({"HA_URL": "https://ha.example.com", "HA_TOKEN": f"abc{bad}def"})
    assert caught.value.code == "BAD_TOKEN"
    assert bad.strip() not in caught.value.message
    assert "abc" not in caught.value.message


def test_loading_registers_the_token_as_a_secret():
    config.load({"HA_URL": "https://ha.example.com", "HA_TOKEN": FAKE_TOKEN})
    assert output.redact(f"leaked {FAKE_TOKEN}") == "leaked <redacted>"


def test_base_url_rejects_an_unusable_scheme():
    with pytest.raises(ConfigError, match="http or https"):
        config.normalize_base_url("ftp://ha.example.com")


def test_websocket_url_follows_the_scheme():
    plain = config.load({"HA_URL": "http://ha.example.com", "HA_TOKEN": FAKE_TOKEN})
    secure = config.load({"HA_URL": "https://ha.example.com", "HA_TOKEN": FAKE_TOKEN})
    assert plain.ws_url == "ws://ha.example.com/api/websocket"
    assert secure.ws_url == "wss://ha.example.com/api/websocket"


def test_websocket_url_keeps_a_subpath():
    resolved = config.load({"HA_URL": "https://proxy.example.com/ha", "HA_TOKEN": FAKE_TOKEN})
    assert resolved.ws_url == "wss://proxy.example.com/ha/api/websocket"


def test_describe_environment_never_reveals_the_token():
    described = config.describe_environment(
        {"HA_URL": "https://ha.example.com", "HA_TOKEN": FAKE_TOKEN}
    )
    assert described == {
        "url_var": "HA_URL",
        "url_set": True,
        "token_var": "HA_TOKEN",
        "token_set": True,
        "read_only": False,
        "read_only_var": "HASS_AXI_READ_ONLY",
    }
    assert FAKE_TOKEN not in repr(described)

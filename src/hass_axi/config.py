"""Connection settings, read from the environment and never from a file.

Home Assistant credentials are long-lived JWTs. This tool deliberately has no
``--token`` flag and reads no credential file inside the repository: a token
passed on a command line leaks into shell history and the process table, and a
token in a file leaks into commits. The environment is the only channel.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from .errors import ConfigError
from .output import register_secret
from .readonly import ENV_VAR as READ_ONLY_VAR
from .readonly import active_var as read_only_var
from .readonly import enabled as read_only_enabled

#: Primary variable names, with the ``hass-cli`` names accepted as fallbacks so
#: an existing Home Assistant shell environment works unchanged.
URL_VARS = ("HA_URL", "HASS_SERVER")
TOKEN_VARS = ("HA_TOKEN", "HASS_TOKEN")

DEFAULT_TIMEOUT = 30.0

_SETUP_HELP = [
    "Set HA_URL to your Home Assistant base URL, e.g. export HA_URL=https://homeassistant.example.com",
    "Set HA_TOKEN to a long-lived access token from your Home Assistant profile page, under Security",
    "Run `hass-axi doctor` to verify the connection once both are set",
]


#: Anything a token must not contain. A header value cannot carry a line break,
#: and http.client raises a ValueError embedding the whole `Bearer ...` header
#: when it finds one -- which is a credential in a traceback.
_ILLEGAL_TOKEN = re.compile(r"[\s\x00-\x1f\x7f]")


@dataclass(frozen=True)
class Config:
    """A resolved, ready-to-use connection configuration.

    ``read_only`` rides along with the credentials because both transports hold
    a :class:`Config` and both have to refuse a write of their own accord. It
    defaults to ``False`` only because a configuration assembled by hand in a
    test has no environment to read; every configuration the CLI builds goes
    through :func:`load`, which reads it.
    """

    base_url: str
    token: str
    timeout: float = DEFAULT_TIMEOUT
    read_only: bool = False

    @property
    def rest_root(self) -> str:
        return f"{self.base_url}/api"

    @property
    def ws_url(self) -> str:
        parts = urlsplit(self.base_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        return urlunsplit((scheme, parts.netloc, f"{parts.path}/api/websocket", "", ""))

    @property
    def auth_header(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}


def _first_env(names: tuple, environ) -> tuple:
    for name in names:
        value = environ.get(name)
        if value and value.strip():
            return name, value.strip()
    return None, None


def split_userinfo(netloc: str) -> tuple:
    """Separate any ``user:password@`` prefix from a network location.

    Credentials in a URL are never sent by this tool -- Home Assistant
    authenticates with the bearer token -- but they must not survive into the
    base URL either, because the no-argument home view prints it.
    """
    if "@" not in netloc:
        return "", netloc
    userinfo, _, host = netloc.rpartition("@")
    return userinfo, host


def normalize_base_url(raw: str) -> str:
    """Accept a bare host, add a scheme if missing, and drop any trailing path noise."""
    value = raw.strip().rstrip("/")
    if "://" not in value:
        # Default to TLS: a bare host that silently became http:// would send
        # the access token in cleartext.
        value = f"https://{value}"
    parts = urlsplit(value)
    if not parts.netloc:
        raise ConfigError(
            f"{URL_VARS[0]} is not a usable URL: {value!r}",
            help_lines=[_SETUP_HELP[0]],
            code="BAD_URL",
        )
    if parts.scheme not in ("http", "https"):
        raise ConfigError(
            f"{URL_VARS[0]} must use http or https, got {parts.scheme!r}",
            help_lines=[_SETUP_HELP[0]],
            code="BAD_URL",
        )
    path = parts.path.rstrip("/")
    # A base URL that already points at the API root is a common paste mistake.
    if path.endswith("/api"):
        path = path[: -len("/api")]
    userinfo, host = split_userinfo(parts.netloc)
    if userinfo:
        # Register before returning: from here on the value can only be
        # printed through the redacting output boundary.
        register_secret(userinfo, min_length=4)
        _, _, password = userinfo.partition(":")
        register_secret(password, min_length=4)
    return urlunsplit((parts.scheme, host, path, "", ""))


def load(environ=None, *, timeout: float | None = None) -> Config:
    """Resolve a :class:`Config` or raise :class:`ConfigError` naming what is absent."""
    environ = os.environ if environ is None else environ
    _, raw_url = _first_env(URL_VARS, environ)
    _, token = _first_env(TOKEN_VARS, environ)

    missing = []
    if not raw_url:
        missing.append(URL_VARS[0])
    if not token:
        missing.append(TOKEN_VARS[0])
    if missing:
        names = " and ".join(missing)
        plural = "are" if len(missing) > 1 else "is"
        raise ConfigError(
            f"{names} {plural} not set in the environment",
            help_lines=_SETUP_HELP,
            code="NOT_CONFIGURED",
        )

    if _ILLEGAL_TOKEN.search(token):
        raise ConfigError(
            f"{TOKEN_VARS[0]} contains whitespace or a control character",
            help_lines=[
                "A long-lived access token is a single unbroken string; check for a line break",
                "If it was read from a file, strip the trailing newline, e.g. HA_TOKEN=$(tr -d '\\n' < token.txt)",
            ],
            code="BAD_TOKEN",
        )

    # Registered at the moment it is read, so no later code path can print it.
    register_secret(token)
    return Config(
        base_url=normalize_base_url(raw_url),
        token=token,
        timeout=DEFAULT_TIMEOUT if timeout is None else timeout,
        read_only=read_only_enabled(environ),
    )


def missing_env_vars(environ=None) -> list:
    """The primary variable names that are absent, in the order to report them."""
    described = describe_environment(environ)
    missing = []
    if not described["url_set"]:
        missing.append(URL_VARS[0])
    if not described["token_set"]:
        missing.append(TOKEN_VARS[0])
    return missing


def setup_help(*, include_doctor: bool = True) -> list:
    """The guidance printed wherever configuration is found to be absent."""
    lines = list(_SETUP_HELP[:2])
    if include_doctor:
        lines.append(_SETUP_HELP[2])
    return lines


def describe_environment(environ=None) -> dict:
    """Report which variables are set without ever revealing the token."""
    environ = os.environ if environ is None else environ
    url_var, raw_url = _first_env(URL_VARS, environ)
    token_var, token = _first_env(TOKEN_VARS, environ)
    return {
        "url_var": url_var or "",
        "url_set": bool(raw_url),
        "token_var": token_var or "",
        "token_set": bool(token),
        "read_only": read_only_enabled(environ),
        # The variable that is actually set, so `doctor` names the one to unset.
        "read_only_var": read_only_var(environ) or READ_ONLY_VAR,
    }

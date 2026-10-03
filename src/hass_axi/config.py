"""Connection settings, read from the environment and never from a file.

Home Assistant credentials are long-lived JWTs. This tool deliberately has no
``--token`` flag and reads no credential file inside the repository: a token
passed on a command line leaks into shell history and the process table, and a
token in a file leaks into commits. The environment is the only channel.
"""

from __future__ import annotations

import os
import re
import socket
import ssl
from dataclasses import dataclass, replace
from urllib.parse import urlsplit, urlunsplit

from .errors import ConfigError
from .output import debug, register_secret
from .readonly import ENV_VAR as READ_ONLY_VAR
from .readonly import active_var as read_only_var
from .readonly import enabled as read_only_enabled

#: Primary variable names, with the ``hass-cli`` names accepted as fallbacks so
#: an existing Home Assistant shell environment works unchanged.
URL_VARS = ("HA_URL", "HASS_SERVER")
TOKEN_VARS = ("HA_TOKEN", "HASS_TOKEN")

DEFAULT_TIMEOUT = 30.0

#: How long one candidate URL gets to answer before the next is tried. Shorter
#: than the request timeout on purpose: a candidate that is merely slow to
#: refuse would otherwise cost the whole timeout before the one that works is
#: even attempted.
CANDIDATE_CONNECT_TIMEOUT = 5.0

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
    #: Every base URL the environment named, in the order to try them. One
    #: entry for the ordinary single-URL configuration; ``base_url`` is the one
    #: in use, which :func:`select_reachable` moves along this list.
    candidates: tuple = ()

    @property
    def fell_back(self) -> bool:
        """Whether a candidate other than the first is the one in use."""
        return bool(self.candidates) and self.base_url != self.candidates[0]

    def candidate_note(self) -> str:
        """``candidate 2 of 3`` when a fallback happened, else ``""``."""
        if not self.fell_back:
            return ""
        return f"candidate {self.candidates.index(self.base_url) + 1} of {len(self.candidates)}"

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


def parse_base_urls(raw: str) -> tuple:
    """Split ``HA_URL`` into the candidate base URLs it names, in order.

    One URL is the ordinary case. Several, separated by commas, are tried in
    order -- typically the address on the local network first and a remote one
    second -- and the first that answers is used for the rest of the run. Each
    is normalised exactly as a lone URL is, so a userinfo prefix on any of them
    is stripped and registered as a secret before anything can print it.
    """
    urls: list = []
    for part in raw.split(","):
        if not part.strip():
            continue
        url = normalize_base_url(part)
        if url not in urls:
            urls.append(url)
    if not urls:
        raise ConfigError(
            f"{URL_VARS[0]} names no URL: {raw!r}",
            help_lines=[_SETUP_HELP[0]],
            code="BAD_URL",
        )
    return tuple(urls)


def _answers(url: str, timeout: float) -> bool:
    """Whether a TCP connection -- and, for https, a TLS handshake -- succeeds.

    The handshake is part of the probe because a certificate this machine will
    not accept is as much a dead end as a refused port: the request that
    follows would fail with `TLS_ERROR` on that candidate however long it was
    given. The default context is the one urllib verifies with, so the probe
    and the request agree about which certificates are acceptable.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout) as sock:
            if parts.scheme == "https":
                context = ssl.create_default_context()
                with context.wrap_socket(sock, server_hostname=host):
                    pass
        return True
    except (OSError, ValueError):
        return False


def select_reachable(config: Config) -> Config:
    """Move ``base_url`` to the first candidate that answers at the transport level.

    Fallback is decided by reachability alone and never by what Home Assistant
    says: a 401, a 404 or a 500 is an answer, and the next candidate is the same
    installation reached another way, so retrying it there would only repeat the
    refusal. A single candidate is never probed -- the ordinary configuration
    pays nothing for this -- and when no candidate answers the first is kept,
    so the request that follows reports the failure in the usual taxonomy
    rather than in a vocabulary of its own.
    """
    if len(config.candidates) < 2:
        return config
    timeout = min(config.timeout, CANDIDATE_CONNECT_TIMEOUT)
    for url in config.candidates:
        if _answers(url, timeout):
            if url != config.base_url:
                debug(f"falling back to {url}: the candidates before it did not answer")
            return replace(config, base_url=url)
    return replace(config, base_url=config.candidates[0])


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
    candidates = parse_base_urls(raw_url)
    return Config(
        base_url=candidates[0],
        token=token,
        timeout=DEFAULT_TIMEOUT if timeout is None else timeout,
        read_only=read_only_enabled(environ),
        candidates=candidates,
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

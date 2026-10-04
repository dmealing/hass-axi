"""Home Assistant WebSocket API client.

The entity, area, device, floor and label registries have no REST equivalent --
they are reachable only over the WebSocket API. That gap is the main reason this
tool exists.

Adding a command is a one-line entry in :data:`REGISTRY`; the transport, the
auth handshake and the error translation below are shared by every command.
"""

from __future__ import annotations

import contextlib
import json
import socket
import ssl
from dataclasses import dataclass
from typing import Any

from .config import Config
from .errors import (
    UNAVAILABLE_STATUSES,
    ApiError,
    AuthFailed,
    AxiError,
    ConnectionFailed,
    Forbidden,
    NotFound,
    UsageError,
)
from .output import debug
from .readonly import READ, WRITE, guard

#: Cap on a single frame. Registry payloads on large installations comfortably
#: exceed the library default of 1 MiB.
MAX_FRAME_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class WsCommand:
    """One WebSocket command, declared once and reused by every caller.

    ``access`` is the read-only classification, and it defaults to ``None`` for
    the same reason :class:`hass_axi.argspec.Sub` does: an unclassified command
    is refused rather than sent, and the sweep in ``tests/test_read_only.py``
    fails on the absence. ``DYNAMIC`` has no meaning here -- the type is fixed
    by the declaration, so no argument is left to decide anything.
    """

    name: str
    type: str
    summary: str
    required: tuple = ()
    optional: tuple = ()
    access: str | None = None

    @property
    def params(self) -> tuple:
        return self.required + self.optional


def _cmd(name, type_, summary, required=(), optional=(), access=None) -> WsCommand:
    return WsCommand(
        name=name,
        type=type_,
        summary=summary,
        required=required,
        optional=optional,
        access=access,
    )


#: The command table. Every registry operation the CLI exposes routes through
#: here, and so does `hass-axi ws <name>`, which is what makes the surface
#: extensible without touching the transport.
#:
#: Every entry classifies itself for the read-only gate. The classification is
#: deliberate, not derived from the command's name or from the shape of its
#: type: nothing in `config/entity_registry/update` is structurally different
#: from `config/entity_registry/list`, and a rule that read one from the other
#: would be a rule about spelling.
REGISTRY: dict = {
    c.name: c
    for c in (
        _cmd("entity.list", "config/entity_registry/list", "Read the entity registry", access=READ),
        _cmd(
            "entity.get",
            "config/entity_registry/get",
            "Read one entity registry entry",
            required=("entity_id",),
            access=READ,
        ),
        _cmd(
            "entity.update",
            "config/entity_registry/update",
            "Update an entity registry entry",
            required=("entity_id",),
            optional=(
                "name",
                "area_id",
                "icon",
                "new_entity_id",
                "disabled_by",
                "hidden_by",
                "labels",
                "aliases",
            ),
            access=WRITE,
        ),
        _cmd("area.list", "config/area_registry/list", "Read the area registry", access=READ),
        _cmd(
            "area.create",
            "config/area_registry/create",
            "Create an area",
            required=("name",),
            optional=("icon", "floor_id", "aliases", "labels", "picture"),
            access=WRITE,
        ),
        _cmd(
            "area.update",
            "config/area_registry/update",
            "Update an area",
            required=("area_id",),
            optional=("name", "icon", "floor_id", "aliases", "labels", "picture"),
            access=WRITE,
        ),
        _cmd(
            "area.delete",
            "config/area_registry/delete",
            "Delete an area",
            required=("area_id",),
            access=WRITE,
        ),
        _cmd("device.list", "config/device_registry/list", "Read the device registry", access=READ),
        _cmd(
            "device.update",
            "config/device_registry/update",
            "Update a device registry entry",
            required=("device_id",),
            optional=("name_by_user", "area_id", "disabled_by", "labels"),
            access=WRITE,
        ),
        _cmd("floor.list", "config/floor_registry/list", "Read the floor registry", access=READ),
        _cmd("label.list", "config/label_registry/list", "Read the label registry", access=READ),
        _cmd("config.get", "get_config", "Read the instance configuration", access=READ),
        _cmd("service.list", "get_services", "Read every registered service", access=READ),
        _cmd("state.list", "get_states", "Read every entity state", access=READ),
        _cmd(
            "statistics.list",
            "recorder/list_statistic_ids",
            "List the statistics the recorder keeps",
            optional=("statistic_type",),
            access=READ,
        ),
        _cmd(
            "statistics.metadata",
            "recorder/get_statistics_metadata",
            "Read the metadata of named statistics",
            optional=("statistic_ids",),
            access=READ,
        ),
        _cmd(
            "statistics.during_period",
            "recorder/statistics_during_period",
            "Read statistics buckets over a window",
            required=("start_time", "statistic_ids", "period"),
            optional=("end_time", "types", "units"),
            access=READ,
        ),
    )
}


#: Home Assistant's own WebSocket error vocabulary, transcribed from
#: ``homeassistant/components/websocket_api/const.py`` at 2026.8.3, and mapped
#: to this tool's codes. Transcribed rather than derived, for the reason the
#: doubles are: the twelve entries are a fixed published set and a rule that
#: guessed at them -- uppercasing whatever arrived, as this did -- produces a
#: vocabulary nobody wrote down and no caller can switch over.
#:
#: Two mappings are the point of the exercise:
#:
#: - ``unknown_command`` is a **not_found**, and it gets a code of its own.
#:   Uppercased it became ``UNKNOWN_COMMAND``, which is what `hass-axi` already
#:   calls a command *this CLI* does not have -- one string for "read `--help`"
#:   and for "this Home Assistant version has no such command", which are
#:   opposite next moves.
#: - ``unauthorized`` is a **permission**, not an auth failure. It can only
#:   arrive after ``auth_ok``: it is what ``@require_admin`` raises for a
#:   non-administrator, so the token is valid and a new one changes nothing.
WS_ERROR_CODES: dict = {
    "unauthorized": "FORBIDDEN",
    "unknown_command": "NO_SUCH_WS_COMMAND",
    "not_found": "NOT_FOUND",
    "invalid_format": "INVALID_FORMAT",
    "not_allowed": "NOT_ALLOWED",
    "not_supported": "NOT_SUPPORTED",
    "home_assistant_error": "HOME_ASSISTANT_ERROR",
    "service_validation_error": "SERVICE_VALIDATION_ERROR",
    "template_error": "TEMPLATE_ERROR",
    "timeout": "TIMEOUT",
    "id_reuse": "ID_REUSE",
    "unknown_error": "API_ERROR",
}


def access_for_type(type_: str) -> str:
    """The read-only classification of one raw API type.

    Read from :data:`REGISTRY` at the moment it is asked rather than from a map
    built at import, so a command added to the table -- by a later release or by
    a test proving the fail-closed default -- is classified by the same rule as
    every other. A type no declaration names is a write: `ws --raw` hands an
    arbitrary string to the API, and the safe reading of an unknown one is that
    it changes something.
    """
    for command in REGISTRY.values():
        if command.type == type_ and command.access == READ:
            return READ
    return WRITE


def _handshake_status(exc) -> int | None:
    """The HTTP status carried by a refused upgrade, if there is one.

    Read by attribute rather than by catching ``websockets.exceptions``:
    ``InvalidStatus`` carries ``.response.status_code`` and the ``InvalidStatusCode``
    it replaced carried ``.status_code``, and this tool declares
    ``websockets>=13`` rather than pinning it. Duck-typing here means a version
    bump cannot silently downgrade a classified failure to an unclassified one.
    """
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def _connect_error(exc: Exception) -> AxiError:
    """Classify a failure to open the WebSocket, by the same rules REST uses.

    The two transports used to disagree about one fault. ``ssl.SSLError`` and
    ``socket.timeout`` are both ``OSError`` subclasses, so a certificate this
    machine will not accept and a host that never answered both arrived as
    ``WS_UNREACHABLE`` here while REST called them ``TLS_ERROR`` and
    ``TIMEOUT`` -- an agent that learnt the vocabulary on one transport was
    wrong on the other, for a fault that has one cause and one fix. The codes
    are shared now; which transport met the fault is in the message, where it
    belongs, because it is not what the caller has to change.

    ``WS_HANDSHAKE`` survives as the fault genuinely specific to this
    transport: the TCP connection was made and the HTTP upgrade was refused,
    which is what a proxy that does not forward WebSockets does, and the fix is
    on that proxy rather than on the host or the token.
    """
    if isinstance(exc, ssl.SSLError):
        return ConnectionFailed(
            f"TLS handshake with Home Assistant failed: {exc}",
            help_lines=["Confirm HA_URL uses the scheme your instance actually serves"],
            code="TLS_ERROR",
        )
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return ConnectionFailed(
            f"timed out opening a WebSocket to Home Assistant: {exc}",
            help_lines=["Raise the limit with `hass-axi --timeout 60 <command>`"],
            code="TIMEOUT",
        )
    status = _handshake_status(exc)
    if status == 401:
        return AuthFailed(
            "the WebSocket upgrade was rejected as unauthenticated (HTTP 401)",
            help_lines=[
                "Check HA_TOKEN holds a current long-lived access token",
                "Home Assistant authenticates after the upgrade, so a 401 here is "
                "usually a proxy in front of it demanding its own credentials",
            ],
            code="UNAUTHORIZED",
        )
    if status == 403:
        return Forbidden(
            "the WebSocket upgrade was refused (HTTP 403): the token was not the problem",
            help_lines=[
                "A new token will not help; the upgrade was refused before Home Assistant saw it",
                "Check whether a proxy in front of Home Assistant, or an IP ban, is refusing it",
            ],
            code="FORBIDDEN",
        )
    if status == 404:
        return NotFound(
            "there is no WebSocket API at this URL (HTTP 404)",
            help_lines=[
                "Confirm HA_URL points at the Home Assistant root, not at /api",
                "Run `hass-axi doctor` to see whether the REST API answers at the same URL",
            ],
            code="NO_WEBSOCKET_API",
        )
    if status in UNAVAILABLE_STATUSES:
        return ConnectionFailed(
            f"Home Assistant is not serving the WebSocket API right now (HTTP {status})",
            help_lines=[
                "Retry the command; an instance answers this way while it restarts",
                "Run `hass-axi doctor` if it keeps answering this way",
            ],
            code="UNAVAILABLE",
        )
    if status is not None:
        return ConnectionFailed(
            f"the WebSocket upgrade was refused with HTTP {status}",
            help_lines=[
                "Confirm HA_URL points at the Home Assistant root, not at /api",
                "Check whether a proxy in front of Home Assistant forwards WebSocket upgrades",
            ],
            code="WS_HANDSHAKE",
        )
    if isinstance(exc, OSError):
        return ConnectionFailed(
            f"could not open a WebSocket to Home Assistant: {exc}",
            help_lines=[
                "Check HA_URL points at a reachable Home Assistant instance",
                "Run `hass-axi doctor` to test both the REST and WebSocket connections",
            ],
            code="UNREACHABLE",
        )
    return ConnectionFailed(
        f"WebSocket handshake with Home Assistant failed: {exc}",
        help_lines=["Confirm HA_URL points at the Home Assistant root, not at /api"],
        code="WS_HANDSHAKE",
    )


class WsClient:
    """A synchronous WebSocket session: connect, authenticate, send commands.

    Used as a context manager so one connection serves several commands, which
    matters because the auth handshake costs a round trip.

    **One WebSocket connection, held open by entering it.** This project
    declares ``websockets>=13.0`` and supports four Pythons, so one call has to
    be correct against every release that range resolves to. Only one form is:
    *enter the object* ``websockets.sync.client.connect()`` returns, rather
    than assigning it.

    - Through 16.x, ``connect()`` returns a ``ClientConnection`` and entering
      it returns that same object -- ``Connection.__enter__`` is ``return
      self``.
    - 17.1 still returns the connection, but marks it, and the first ``send``
      or ``recv`` on an unentered one raises a ``DeprecationWarning`` naming
      ``legacy=True``. Entering it clears the mark.
    - When upstream flips that default, ``connect()`` returns a ``reconnect``
      whose ``__enter__`` performs the connection and returns it. Entering is
      then the only form that yields something with ``send`` and ``recv`` at
      all.

    ``legacy=True`` is *not* the portable answer: the parameter does not exist
    before 17.1, where it reaches ``socket.create_connection()`` through
    ``**kwargs`` and raises ``TypeError: create_connection() got an unexpected
    keyword argument 'legacy'``. Entering needs no version check, so there is
    no shim here to go stale.

    The connection outlives the call that opens it, so the entered context is
    held in an :class:`~contextlib.ExitStack` and unwound by :meth:`close`,
    which keeps the lifetime property the comment in :meth:`connect` names: a
    failure after the socket is open still closes it.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self._socket: Any = None
        self._open: contextlib.ExitStack | None = None
        self._next_id = 1
        self.ha_version = ""

    # ------------------------------------------------------------- lifecycle

    def __enter__(self) -> WsClient:
        self.connect()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def connect(self) -> None:
        try:
            from websockets.sync.client import connect
        except ImportError:  # pragma: no cover - dependency is declared in pyproject
            raise ConnectionFailed(
                "the websockets package is required for registry commands",
                help_lines=["Install it with `pip install 'hass-axi'` or `pip install websockets`"],
                code="MISSING_DEPENDENCY",
            ) from None

        url = self.config.ws_url
        debug(f"websocket connect {url}")
        # `connect()` is *entered*, never assigned. Assigning it is the one
        # form that does not span the declared `websockets` range: see "One
        # WebSocket connection, held open by entering it" above. `ExitStack`
        # is what holds the entered connection open past this method, since a
        # `with` block would close it on the way out of the very call that
        # opened it.
        opening = contextlib.ExitStack()
        try:
            self._socket = opening.enter_context(
                connect(
                    url,
                    open_timeout=self.config.timeout,
                    close_timeout=self.config.timeout,
                    max_size=MAX_FRAME_BYTES,
                )
            )
        except Exception as exc:
            raise _connect_error(exc) from None
        self._open = opening
        try:
            self._authenticate()
        except Exception:
            # __exit__ never runs when __enter__ raises, so the socket would
            # stay open for the life of the process.
            self.close()
            raise

    def close(self) -> None:
        if self._open is not None:
            with contextlib.suppress(Exception):
                # Unwinds to the connection's own `__exit__`, which is what
                # closes the socket -- the same call the old direct
                # `self._socket.close()` made, reached the supported way.
                self._open.close()
            self._open = None
        self._socket = None

    # ------------------------------------------------------------- handshake

    def _authenticate(self) -> None:
        message = self._receive()
        if message.get("type") != "auth_required":
            raise ConnectionFailed(
                f"unexpected greeting from the WebSocket API: {message.get('type')!r}",
                code="WS_PROTOCOL",
            )
        self.ha_version = message.get("ha_version", "")
        self._send({"type": "auth", "access_token": self.config.token})

        while True:
            message = self._receive()
            kind = message.get("type")
            if kind == "auth_ok":
                self.ha_version = message.get("ha_version", self.ha_version)
                return
            if kind == "auth_invalid":
                raise AuthFailed(
                    "Home Assistant rejected the access token over the WebSocket API",
                    help_lines=[
                        "Check HA_TOKEN holds a current long-lived access token",
                        "Create a new one on your Home Assistant profile page, under Security",
                    ],
                    code="UNAUTHORIZED",
                )
            if kind != "auth_required":
                raise ConnectionFailed(
                    f"unexpected message during authentication: {kind!r}", code="WS_PROTOCOL"
                )

    # ------------------------------------------------------------- transport

    def _closed(self, exc: Exception) -> ConnectionFailed:
        return ConnectionFailed(
            f"WebSocket connection to Home Assistant closed: {exc}", code="WS_CLOSED"
        )

    def _send(self, payload: dict) -> None:
        try:
            self._socket.send(json.dumps(payload))
        except Exception as exc:
            raise self._closed(exc) from None

    def _receive(self) -> dict:
        try:
            raw = self._socket.recv(timeout=self.config.timeout)
        except Exception as exc:
            raise self._closed(exc) from None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            raise ConnectionFailed(
                "the WebSocket API sent a message that is not JSON", code="WS_PROTOCOL"
            ) from None
        if not isinstance(message, dict):
            raise ConnectionFailed(
                "the WebSocket API sent a message that is not an object", code="WS_PROTOCOL"
            )
        return message

    # -------------------------------------------------------------- commands

    def send_command(self, type_: str, params: dict | None = None) -> Any:
        """Send one command and return its ``result``, translating failures.

        The read-only gate is applied here, ahead of the connection, because
        this is the one place every WebSocket command passes through: a command
        added later is guarded whether or not its author knew there was a gate.
        Refusing before :meth:`connect` also keeps the token off the wire for a
        write that was never going to be sent.
        """
        guard(self.config.read_only, access_for_type(type_), type_)
        if self._socket is None:
            self.connect()
        message_id = self._next_id
        self._next_id += 1
        payload = {"id": message_id, "type": type_}
        # Null is meaningful here: sending `name: null` is how the registry
        # clears a user override. Callers decide what to include; nothing is
        # filtered out on the way past.
        payload.update(params or {})
        debug(f"websocket command {type_}")
        self._send(payload)

        while True:
            message = self._receive()
            if message.get("id") != message_id or message.get("type") != "result":
                # Event and pong frames may interleave; they are not our answer.
                continue
            if message.get("success"):
                return message.get("result")
            raise self._command_error(type_, message.get("error") or {})

    def run(self, name: str, params: dict | None = None) -> Any:
        """Send a command declared in :data:`REGISTRY` by its friendly name."""
        command = REGISTRY.get(name)
        if command is None:
            # Exit 2: the command table is static, so this is a malformed
            # invocation, matching how `hass-axi ws <name>` rejects the same thing.
            raise UsageError(
                f"unknown websocket command: {name}",
                help_lines=[
                    f"declared commands: {', '.join(sorted(REGISTRY))}",
                    "Run `hass-axi ws --list` to see each command's parameters",
                ],
                code="NO_SUCH_COMMAND",
            )
        return self.send_command(command.type, params)

    def _command_error(self, type_: str, error: dict):
        """Classify one refused command, for every WebSocket command there is.

        The classification is by Home Assistant's published error code, mapped
        through :data:`WS_ERROR_CODES`. A code that table does not name is not
        passed through: it becomes ``API_ERROR`` and the name Home Assistant
        used is written into the message, where it is readable without
        pretending to be a code this tool has ever documented. That is the
        whole of the difference from what stood here before, and it is the
        difference between a closed vocabulary and an open one.
        """
        reported = str(error.get("code") or "unknown_error")
        message = str(error.get("message") or "the WebSocket API refused the command")
        code = WS_ERROR_CODES.get(reported)

        if code == "FORBIDDEN":
            return Forbidden(
                f"the token is not permitted to run {type_}: {message}",
                help_lines=[
                    "Check HA_TOKEN belongs to an account with administrator rights",
                    "A new token for the same account will not help; the account is the limit",
                ],
                code="FORBIDDEN",
            )
        if code == "NO_SUCH_WS_COMMAND":
            return NotFound(
                f"this Home Assistant has no WebSocket command `{type_}`",
                help_lines=[
                    "Run `hass-axi ws --list` to see the commands this CLI declares",
                    "A command removed or not yet added upstream answers this way; "
                    "check the instance version with `hass-axi doctor`",
                ],
                code="NO_SUCH_WS_COMMAND",
            )
        if code == "NOT_FOUND":
            return NotFound(
                f"{type_}: {message}",
                help_lines=[
                    "The id passed names nothing in this installation",
                    "Run `hass-axi entity list --search <text>`, `hass-axi device list` or "
                    "`hass-axi area list` to find the id",
                ],
                code="NOT_FOUND",
            )
        if code == "INVALID_FORMAT":
            return ApiError(
                f"{type_} rejected the arguments: {message}",
                help_lines=["Run `hass-axi ws --list` to see each command's parameters"],
                code="INVALID_FORMAT",
            )
        if code == "TIMEOUT":
            return ConnectionFailed(
                f"{type_} timed out inside Home Assistant: {message}",
                help_lines=["Retry the command; the instance did not finish in time"],
                code="TIMEOUT",
            )
        if code == "ID_REUSE":
            return ApiError(
                f"{type_} reused a message id: {message}",
                help_lines=[
                    "This is a bug in hass-axi; the connection cannot be reused after it",
                    "Report it at https://github.com/dmealing/hass-axi/issues",
                ],
                code="ID_REUSE",
            )
        refused = [
            "Home Assistant reached the command and refused it; change the arguments "
            "rather than retrying",
            "Run `hass-axi ws --list` to see each command's parameters",
        ]
        if code is not None:
            return ApiError(f"{type_} failed: {message}", help_lines=refused, code=code)
        return ApiError(
            f"{type_} failed with an error Home Assistant named `{reported}`: {message}",
            help_lines=refused,
            code="API_ERROR",
        )

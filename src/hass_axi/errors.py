"""Error types, the declared fault classes, and the closed code vocabulary.

Every failure this tool reports carries a ``code``. A code names one specific
thing that went wrong; a **fault class** names the kind of thing it is, and
that is the fact a caller has to switch on before it can decide what to do
next. Three of the classes are the ones that matter most in practice, and
collapsing them is a defect rather than a simplification:

- a rejected credential (``auth``) is fixed by minting a new token;
- a command or entity this installation does not have (``not_found``) is fixed
  by asking for something else, and never by a new token;
- a host that could not be reached (``transport``) is fixed by nothing at all
  -- it is the one class where retrying the identical command is the correct
  next move.

An agent that cannot tell them apart retries a wrong token forever, or reports
that Home Assistant is down when the real answer is that it asked for a command
this version does not have.

Two more classes fall out of what Home Assistant actually returns, rather than
out of a wish for symmetry. ``permission`` is separate from ``auth`` because
Home Assistant answers "your credential is fine, you are not allowed" on both
transports and the fix is a different account, not a different token -- see
:func:`hass_axi.rest.RestClient._http_error` and
:func:`hass_axi.ws.WsClient._command_error`. ``refused`` is separate from
``not_found`` because a service that exists and rejected these arguments is
fixed by changing the arguments.

**The vocabulary is closed.** :data:`CODES` is the whole of it, every code in
it names its class, and ``tests/test_error_codes.py`` sweeps the source for
code literals and fails on one this table does not declare. That sweep is the
deliverable in the same way the read-only sweep is: the taxonomy is complete
because a test enumerates it, not because everybody remembered.

**A code is always a literal.** The two places that used to build one --
``f"HTTP_{status}"`` and a WebSocket error code passed through ``.upper()`` --
minted vocabulary from whatever the server said, so no caller could switch on
the result and no table could ever be complete. The same sweep rejects a
computed ``code=``.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


# ------------------------------------------------------------- fault classes

#: The invocation is wrong and nothing was sent. Change the command line.
CLASS_USAGE = "usage"
#: This machine is not set up to talk to Home Assistant. Change the environment.
CLASS_CONFIG = "config"
#: Home Assistant was not reached, or was reached and is not serving. Change
#: nothing: this is the one class where retrying as-is is the right next move.
CLASS_TRANSPORT = "transport"
#: The credential was rejected. A new long-lived access token is the fix.
CLASS_AUTH = "auth"
#: The credential was accepted and this account or client is not permitted. A
#: new token for the same account does not help.
CLASS_PERMISSION = "permission"
#: Reached, permitted, and the subject named does not resolve to exactly one
#: thing that exists here -- absent, or ambiguous. Look it up and ask again.
CLASS_NOT_FOUND = "not_found"
#: Reached, permitted, the subject exists, and this request was refused. Change
#: the arguments.
CLASS_REFUSED = "refused"
#: A bug in hass-axi. Nothing the caller did causes it and nothing it does fixes it.
CLASS_INTERNAL = "internal"
#: The fail-closed answer for a code this table does not declare. It is never
#: reachable from a released build -- the sweep in ``tests/test_error_codes.py``
#: fails first -- and it exists so that a code that escaped the sweep says so
#: out loud rather than being silently filed under a class it does not belong
#: to. This is the same instinct as the read-only gate's unclassified default:
#: being visibly wrong beats being quietly wrong.
CLASS_UNCLASSIFIED = "unclassified"

#: Every class a reachable code may declare. ``CLASS_UNCLASSIFIED`` is
#: deliberately not a member: it is the absence of an answer, not an answer.
CLASSES = (
    CLASS_USAGE,
    CLASS_CONFIG,
    CLASS_TRANSPORT,
    CLASS_AUTH,
    CLASS_PERMISSION,
    CLASS_NOT_FOUND,
    CLASS_REFUSED,
    CLASS_INTERNAL,
)


# --------------------------------------------------------- the code vocabulary

#: Every code this tool can print, and the class it belongs to. One table, one
#: source of truth: the README's error-code section is checked against it, and
#: so is the source. Adding a code without adding it here fails the sweep;
#: leaving one here after its last raise site is deleted fails it too.
CODES: dict = {
    # -- usage: the invocation is wrong, and nothing reached Home Assistant.
    "UNKNOWN_COMMAND": CLASS_USAGE,
    "UNKNOWN_SUBCOMMAND": CLASS_USAGE,
    "MISSING_SUBCOMMAND": CLASS_USAGE,
    "UNKNOWN_FLAG": CLASS_USAGE,
    "MISSING_VALUE": CLASS_USAGE,
    "MISSING_ARGUMENT": CLASS_USAGE,
    "UNEXPECTED_ARGUMENT": CLASS_USAGE,
    "CONFLICTING_FLAGS": CLASS_USAGE,
    "UNKNOWN_FIELD": CLASS_USAGE,
    "BAD_LIMIT": CLASS_USAGE,
    "BAD_TIMEOUT": CLASS_USAGE,
    "BAD_TIME": CLASS_USAGE,
    "BAD_WINDOW": CLASS_USAGE,
    "BAD_PERIOD": CLASS_USAGE,
    "BAD_KIND": CLASS_USAGE,
    "BAD_JSON": CLASS_USAGE,
    "BAD_PAIR": CLASS_USAGE,
    "BAD_SERVICE": CLASS_USAGE,
    "MISSING_PATH": CLASS_USAGE,
    "MISSING_NAME": CLASS_USAGE,
    "MISSING_TEMPLATE": CLASS_USAGE,
    "MISSING_COMMAND": CLASS_USAGE,
    "MISSING_PARAM": CLASS_USAGE,
    "NO_CHANGES": CLASS_USAGE,
    "NO_SUCH_COMMAND": CLASS_USAGE,
    "UNREADABLE": CLASS_USAGE,
    "UNREADABLE_FILE": CLASS_USAGE,
    "UNWRITABLE": CLASS_USAGE,
    "READ_ONLY": CLASS_USAGE,
    # -- config: this machine is not set up.
    "NOT_CONFIGURED": CLASS_CONFIG,
    "BAD_URL": CLASS_CONFIG,
    "BAD_TOKEN": CLASS_CONFIG,
    "MISSING_DEPENDENCY": CLASS_CONFIG,
    "REDIRECT_REFUSED": CLASS_CONFIG,
    # -- transport: not reached, or reached and not serving.
    "UNREACHABLE": CLASS_TRANSPORT,
    "TIMEOUT": CLASS_TRANSPORT,
    "TLS_ERROR": CLASS_TRANSPORT,
    "CONNECTION_DROPPED": CLASS_TRANSPORT,
    "UNAVAILABLE": CLASS_TRANSPORT,
    "WS_HANDSHAKE": CLASS_TRANSPORT,
    "WS_CLOSED": CLASS_TRANSPORT,
    "WS_PROTOCOL": CLASS_TRANSPORT,
    # -- auth: the credential was rejected.
    "UNAUTHORIZED": CLASS_AUTH,
    # -- permission: the credential was accepted, the caller is not permitted.
    "FORBIDDEN": CLASS_PERMISSION,
    # -- not_found: the subject does not resolve to one thing that exists here.
    "NOT_FOUND": CLASS_NOT_FOUND,
    "NO_SUCH_ENTITY": CLASS_NOT_FOUND,
    "NO_SUCH_AREA": CLASS_NOT_FOUND,
    "AMBIGUOUS_AREA": CLASS_NOT_FOUND,
    "NO_SUCH_DEVICE": CLASS_NOT_FOUND,
    "AMBIGUOUS_DEVICE": CLASS_NOT_FOUND,
    "NO_SUCH_DOMAIN": CLASS_NOT_FOUND,
    "NO_SUCH_SERVICE": CLASS_NOT_FOUND,
    "NO_SUCH_STATISTIC": CLASS_NOT_FOUND,
    "NO_ENTITIES_TARGETED": CLASS_NOT_FOUND,
    "NO_SUCH_WS_COMMAND": CLASS_NOT_FOUND,
    "NO_WEBSOCKET_API": CLASS_NOT_FOUND,
    # -- refused: it exists, and this request was refused.
    "BAD_REQUEST": CLASS_REFUSED,
    "METHOD_NOT_ALLOWED": CLASS_REFUSED,
    "SERVER_ERROR": CLASS_REFUSED,
    "API_ERROR": CLASS_REFUSED,
    "INVALID_FORMAT": CLASS_REFUSED,
    "NOT_ALLOWED": CLASS_REFUSED,
    "NOT_SUPPORTED": CLASS_REFUSED,
    "HOME_ASSISTANT_ERROR": CLASS_REFUSED,
    "SERVICE_VALIDATION_ERROR": CLASS_REFUSED,
    "TEMPLATE_ERROR": CLASS_REFUSED,
    "UNKNOWN_SERVICE_FIELD": CLASS_REFUSED,
    "MISSING_SERVICE_FIELD": CLASS_REFUSED,
    "UNSUPPORTED_CAPABILITY": CLASS_REFUSED,
    "RESPONSE_REQUIRED": CLASS_REFUSED,
    "RESPONSE_NOT_SUPPORTED": CLASS_REFUSED,
    # -- internal: a bug in hass-axi.
    "INTERNAL_ERROR": CLASS_INTERNAL,
    "ID_REUSE": CLASS_INTERNAL,
}


#: HTTP statuses that mean the request was never seen rather than refused, and
#: therefore classify as ``transport`` on whichever transport meets them.
#: ``helpers/http.py`` answers every request with a bodyless 503 while
#: ``hass.is_stopping``, and a reverse proxy in front of a restarting instance
#: answers 502 or 504 for the same window. Declared here rather than in either
#: transport because both meet them -- REST as a response status, WebSocket as
#: the status on a refused upgrade -- and two copies of one rule drift.
UNAVAILABLE_STATUSES = frozenset({502, 503, 504})


def fault_class(code: str | None) -> str:
    """The declared class of ``code``, or ``CLASS_UNCLASSIFIED``.

    Fails closed on an unknown code rather than guessing from the exception
    type it arrived on: the type is a coarser fact than the code -- one
    ``ConnectionFailed`` is a missing Python package and another is a dropped
    socket -- and a guess that is usually right is exactly what this table
    exists to replace.
    """
    return CODES.get(code or "", CLASS_UNCLASSIFIED)


class AxiError(Exception):
    """An error the agent should be able to read, understand and act on.

    ``help_lines`` carry the specific command that fixes the problem, per the
    AXI standard: on errors, suggest the fix rather than pointing at --help.
    ``code`` is one of :data:`CODES`, always, and always a literal.
    """

    exit_code = EXIT_ERROR

    def __init__(
        self,
        message: str,
        *,
        help_lines: list[str] | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.help_lines = help_lines or []
        self.code = code

    @property
    def fault_class(self) -> str:
        """The declared class of this error's code."""
        return fault_class(self.code)


class UsageError(AxiError):
    """A malformed invocation: unknown flag, missing argument, bad value."""

    exit_code = EXIT_USAGE


class ConfigError(AxiError):
    """Required environment configuration is missing or unusable."""


class ConnectionFailed(AxiError):
    """The Home Assistant instance could not be reached."""


class AuthFailed(AxiError):
    """Home Assistant rejected the token."""


class Forbidden(AxiError):
    """Home Assistant accepted the credential and refused the caller anyway.

    Deliberately not an :class:`AuthFailed` and deliberately not an
    :class:`ApiError`. Not the former because a new token from the same account
    does not help, and telling an agent to mint one sends it to fail a login
    against an installation that may already have banned its address. Not the
    latter because ``service call`` explains an :class:`ApiError` from the
    service model, and a refusal that never reached a service has nothing there
    to explain it.
    """


class NotFound(AxiError):
    """The requested entity, area, path or command does not exist."""


class ApiError(AxiError):
    """Home Assistant answered, but refused the request."""

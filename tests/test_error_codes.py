"""The error-code taxonomy: one closed vocabulary, classified at the transport.

Three failures used to be one answer, and they demand opposite next moves:

- a rejected credential, fixed by minting a token;
- a command or entity this installation does not have, which no token fixes;
- a host that was not reached, which is the one case where retrying the
  identical command is right.

Two more fall out of what Home Assistant actually returns rather than out of a
wish for symmetry, and both are pinned below against the shapes the real server
sends: a **permission** refusal, where the credential was accepted and the
account is the limit, and a **refused** request, where the subject exists and
these arguments were rejected.

Two conventions in this file are deliberate, and both are borrowed from
``tests/test_read_only.py`` for the reasons written down there.

**The codes and the class names are written out as literals**, not imported
from :mod:`hass_axi.errors`. They are the contract an agent switches on; a test
that imported them would agree with a rename that broke every caller, the same
way a double that imports the client's own table can only prove the client
agrees with itself.

**Everything that needs the new module members imports them inside the test
body.** At the commit before this change :data:`hass_axi.errors.CODES` does not
exist, so a module-level import would collapse the whole file into one
collection error and hide how much of it the change is actually responsible
for. :mod:`hass_axi.cli` predates the change and is imported normally, so the
parametrised sweeps still enumerate the real command table at collection time.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import FAKE_TOKEN
from hass_axi import cli

SOURCE_ROOT = pathlib.Path(__file__).resolve().parent.parent / "src" / "hass_axi"
README = pathlib.Path(__file__).resolve().parent.parent / "README.md"

#: The error types whose construction must name a code. Written out rather than
#: discovered by walking `hass_axi.errors`, so a new subclass has to be added here
#: on purpose and cannot slip past the sweep by being new.
ERROR_TYPES = frozenset(
    {
        "AxiError",
        "UsageError",
        "ConfigError",
        "ConnectionFailed",
        "AuthFailed",
        "Forbidden",
        "NotFound",
        "ApiError",
        "ReadOnlyRefused",
    }
)

#: The classes, spelled as they are printed.
USAGE = "usage"
CONFIG = "config"
TRANSPORT = "transport"
AUTH = "auth"
PERMISSION = "permission"
NOT_FOUND = "not_found"
REFUSED = "refused"
INTERNAL = "internal"


def errors():
    """The module under test, imported late -- see this file's docstring."""
    from hass_axi import errors as module

    return module


# ------------------------------------------------------- reading the source


#: How a ``code=`` argument was written. ``absent`` is an error that names no
#: code at all; ``computed`` is one built from something -- an f-string, a
#: concatenation, a method call -- which is the shape both removed bugs had.
ABSENT, LITERAL, NAME, COMPUTED = "absent", "literal", "name", "computed"


def _code_arguments() -> list:
    """Every error construction in the shipped source: ``(where, kind, code)``."""
    found = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name not in ERROR_TYPES:
                continue
            where = f"{path.name}:{node.lineno}"
            keywords = {kw.arg: kw.value for kw in node.keywords}
            if "code" not in keywords:
                found.append((where, ABSENT, None))
                continue
            value = keywords["code"]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                found.append((where, LITERAL, value.value))
            elif isinstance(value, ast.Name):
                found.append((where, NAME, None))
            else:
                found.append((where, COMPUTED, None))
    return found


def _quoted_codes() -> set:
    """Every code named as a bare string anywhere in the source.

    Wider than the constructor sweep on purpose: the WebSocket translation
    table names its codes as dictionary values, and a code that is declared,
    mapped and never raised is still a code this tool can print.
    """
    declared = set(errors().CODES)
    seen = set()
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if path.name == "errors.py":
            # The table itself would otherwise vouch for every entry in it.
            text = text.split("def fault_class", 1)[-1]
        for candidate in re.findall(r'"([A-Z][A-Z0-9_]{2,})"', text):
            if candidate in declared:
                seen.add(candidate)
    return seen


# ------------------------------------------------------- the completeness bar


def test_every_error_the_source_constructs_names_a_code():
    """The deliverable, first half: no failure may arrive unclassified.

    Swept from the source rather than from a list somebody maintains, so a
    command added later is covered without anybody remembering this file
    exists -- the same bar, and the same reason for it, as the read-only gate's
    sweep over `cli._MODULES`.
    """
    missing = [where for where, kind, _ in _code_arguments() if kind == ABSENT]
    assert not missing, f"error raised without a code at {', '.join(missing)}"


def test_no_error_code_is_computed_from_what_a_server_said():
    """The deliverable, second half: the vocabulary has to be closed.

    A *computed* code is not a code. `f"HTTP_{status}"` made the vocabulary the
    set of HTTP statuses and `error["code"].upper()` made it whatever Home
    Assistant might ever name, so no caller could switch over either and no
    table could ever claim to be complete. Both are gone; this is what keeps
    them gone.

    A bare name is allowed, because one place genuinely has to hand a code
    along -- the WebSocket translation reads Home Assistant's error code and
    answers with this tool's -- and the table it reads is pinned separately
    below. What is refused is any expression that *builds* a code: an f-string,
    a concatenation, a method call. That is the line between a closed
    vocabulary and an open one.
    """
    computed = [where for where, kind, _ in _code_arguments() if kind == COMPUTED]
    assert not computed, f"error code is built rather than named at {', '.join(computed)}"


def test_the_websocket_translation_table_only_names_declared_codes():
    """The other half of the rule above.

    `ws.WS_ERROR_CODES` is the one table a code is read out of rather than
    written at the raise site, so it is the one place a code could re-enter the
    source without the constructor sweep seeing it.
    """
    from hass_axi import ws

    declared = set(errors().CODES)
    assert set(ws.WS_ERROR_CODES.values()) <= declared
    assert all(isinstance(value, str) for value in ws.WS_ERROR_CODES.values())


def test_every_code_the_source_names_is_declared():
    declared = set(errors().CODES)
    raised = {code for _, kind, code in _code_arguments() if kind == LITERAL}
    assert raised - declared == set(), (
        f"undeclared error codes: {sorted(raised - declared)}; add them to errors.CODES"
    )


def test_every_declared_code_is_reachable():
    """No dead entries.

    A table nobody prunes drifts into a list of codes that used to exist, which
    is the same defect as a leak-scanner allowance that has outlived its cause:
    it reads as coverage and is not.
    """
    declared = set(errors().CODES)
    unreachable = declared - _quoted_codes()
    assert unreachable == set(), (
        f"declared but never raised: {sorted(unreachable)}; delete them or raise them"
    )


def test_every_declared_code_belongs_to_a_declared_class():
    module = errors()
    for code, klass in module.CODES.items():
        assert klass in module.CLASSES, f"{code} declares an unknown class {klass!r}"


def test_every_class_has_at_least_one_code():
    """A class nothing reaches is a distinction that was never drawn."""
    module = errors()
    used = set(module.CODES.values())
    assert set(module.CLASSES) - used == set()


def test_an_unknown_code_is_visibly_unclassified_rather_than_quietly_filed():
    """Fail closed, and fail loudly.

    Guessing the class from the exception type is the tempting alternative and
    it is wrong: one `ConnectionFailed` is a missing Python package and another
    is a dropped socket. A code the table does not name says so.
    """
    module = errors()
    assert module.fault_class("NO_SUCH_CODE") == "unclassified"
    assert module.fault_class(None) == "unclassified"
    assert "unclassified" not in module.CLASSES


def test_the_readme_documents_exactly_the_declared_codes():
    """Documented means documented, not intended-to-be.

    The README carries the table an agent reads; `errors.CODES` carries the one
    the code reads. Two tables that can disagree is one table nobody can trust,
    so this pins them together the way the generated skill is pinned to the
    command table.
    """
    declared = set(errors().CODES)
    section = README.read_text(encoding="utf-8").split("<!-- error-codes:start -->", 1)[-1]
    section = section.split("<!-- error-codes:end -->", 1)[0]
    documented = set(re.findall(r"`([A-Z][A-Z0-9_]+)`", section))
    assert documented == declared, (
        f"missing from README: {sorted(declared - documented)}; "
        f"stale in README: {sorted(documented - declared)}"
    )


def test_the_readme_documents_every_class():
    section = README.read_text(encoding="utf-8").split("<!-- error-codes:start -->", 1)[-1]
    section = section.split("<!-- error-codes:end -->", 1)[0]
    for klass in errors().CLASSES:
        assert f"`{klass}`" in section, f"class {klass} is not documented"


# ------------------------------------------------- what a real server returns


def test_a_rejected_token_is_an_auth_failure_over_rest(run_cli, rest_env):
    code, out = run_cli(["state", "list"], {**rest_env, "HA_TOKEN": "not-the-right-token"})
    assert code == 1
    assert "code: UNAUTHORIZED" in out
    assert f"class: {AUTH}" in out


def test_a_rejected_token_is_an_auth_failure_over_websocket(run_cli, ws_env):
    code, out = run_cli(["entity", "list"], {**ws_env, "HA_TOKEN": "not-the-right-token"})
    assert code == 1
    assert "code: UNAUTHORIZED" in out
    assert f"class: {AUTH}" in out


def test_a_banned_address_is_a_permission_failure_and_not_an_auth_one(
    run_cli, rest_env, rest_server
):
    """403 is not 401, and the difference is the whole point of the split.

    `components/http/ban.py` answers a banned address with a bare
    `HTTPForbidden` from a middleware, before any view reads the token. Telling
    an agent to mint a new token there sends it to fail another login against
    an instance that already banned it -- which is how the ban got deeper in
    the first place.
    """
    rest_server.forbidden = True
    code, out = run_cli(["state", "list"], rest_env)
    assert code == 1
    assert "code: FORBIDDEN" in out
    assert f"class: {PERMISSION}" in out
    assert "UNAUTHORIZED" not in out


def test_a_non_admin_account_is_a_permission_failure_over_websocket(run_cli, ws_env, ws_server):
    """`unauthorized` over the WebSocket can only arrive after `auth_ok`.

    It is what `@require_admin` raises for an account that is not an
    administrator, so the token is valid and a new one for the same account
    changes nothing. Filing it under the code that means "the token was
    rejected" sent an agent to fix the one thing that was already right.
    """
    ws_server.fail_all = {"code": "unauthorized", "message": "Unauthorized"}
    code, out = run_cli(["area", "create", "--name", "Example Room Two"], ws_env)
    assert code == 1
    assert "code: FORBIDDEN" in out
    assert f"class: {PERMISSION}" in out


def test_a_command_this_instance_does_not_have_is_not_the_cli_not_having_one(
    run_cli, ws_env, ws_server
):
    """The collision this change exists to remove.

    Home Assistant answers a command it does not know with
    `{"code": "unknown_command", "message": "Unknown command."}`. Uppercasing
    that gave `UNKNOWN_COMMAND`, which is already what this CLI calls a command
    *it* does not have -- one string for "read `--help`" and for "this Home
    Assistant version cannot do that", which are opposite next moves.
    """
    ws_server.fail_all = {"code": "unknown_command", "message": "Unknown command."}
    code, out = run_cli(["entity", "list"], ws_env)
    assert code == 1
    assert "code: NO_SUCH_WS_COMMAND" in out
    assert f"class: {NOT_FOUND}" in out

    typo = run_cli(["ws", "entity.lst"], ws_env)
    assert typo[0] == 2
    assert "code: UNKNOWN_COMMAND" in typo[1]
    assert f"class: {USAGE}" in typo[1]
    assert "NO_SUCH_WS_COMMAND" not in typo[1]


def test_an_unrouted_path_is_not_found_rather_than_a_dead_instance(run_cli, rest_env, rest_server):
    rest_server.unrouted = True
    code, out = run_cli(["state", "list"], rest_env)
    assert code == 1
    assert "code: NOT_FOUND" in out
    assert f"class: {NOT_FOUND}" in out


def test_a_missing_entity_is_not_found_and_says_which_entity(run_cli, rest_env):
    code, out = run_cli(["state", "get", "light.no_such_lamp"], rest_env)
    assert code == 1
    assert "code: NO_SUCH_ENTITY" in out
    assert f"class: {NOT_FOUND}" in out


def test_a_restarting_instance_is_transport_and_therefore_retryable(run_cli, rest_env, rest_server):
    """503 means the request was never seen.

    `helpers/http.py` answers every request with a bodyless
    `web.Response(status=SERVICE_UNAVAILABLE)` while `hass.is_stopping`, and a
    proxy in front of a restarting instance answers 502 or 504 for the same
    window. Calling that a refusal tells an agent to change its arguments; it
    is the one class where changing nothing and trying again is correct.
    """
    rest_server.stopping = True
    code, out = run_cli(["state", "list"], rest_env)
    assert code == 1
    assert "code: UNAVAILABLE" in out
    assert f"class: {TRANSPORT}" in out
    assert "Retry" in out


def test_an_unreachable_host_is_transport_on_both_transports(run_cli, closed_port):
    """One fault, one code, whichever transport meets it.

    `ssl.SSLError` and `socket.timeout` are both `OSError` subclasses, so the
    WebSocket client called a refused connection, an expired certificate and a
    timeout all `WS_UNREACHABLE` while REST called them `UNREACHABLE`,
    `TLS_ERROR` and `TIMEOUT`. An agent that learnt the vocabulary on one
    transport was wrong on the other, for a fault with one cause and one fix.
    """
    env = {"HA_URL": f"http://127.0.0.1:{closed_port}", "HA_TOKEN": FAKE_TOKEN}
    rest = run_cli(["state", "list"], env)
    websocket = run_cli(["entity", "list"], env)
    assert rest[0] == websocket[0] == 1
    for out in (rest[1], websocket[1]):
        assert "code: UNREACHABLE" in out
        assert f"class: {TRANSPORT}" in out


def test_a_refused_upgrade_names_the_status_it_was_refused_with(run_cli, refusing_server):
    """The fault genuinely specific to the WebSocket transport.

    The TCP connection was made and the HTTP upgrade was refused -- what a
    proxy that does not forward WebSockets does, and what a URL that is not
    Home Assistant's root does. A 404 there is `not_found`: there is no
    WebSocket API at this address, which no retry and no token can change.
    """
    env = {"HA_URL": refusing_server(404), "HA_TOKEN": FAKE_TOKEN}
    code, out = run_cli(["entity", "list"], env)
    assert code == 1
    assert "code: NO_WEBSOCKET_API" in out
    assert f"class: {NOT_FOUND}" in out

    banned = run_cli(["entity", "list"], {"HA_URL": refusing_server(403), "HA_TOKEN": FAKE_TOKEN})
    assert "code: FORBIDDEN" in banned[1]
    assert f"class: {PERMISSION}" in banned[1]

    restarting = run_cli(
        ["entity", "list"], {"HA_URL": refusing_server(503), "HA_TOKEN": FAKE_TOKEN}
    )
    assert "code: UNAVAILABLE" in restarting[1]
    assert f"class: {TRANSPORT}" in restarting[1]

    proxy = run_cli(["entity", "list"], {"HA_URL": refusing_server(426), "HA_TOKEN": FAKE_TOKEN})
    assert "code: WS_HANDSHAKE" in proxy[1]
    assert f"class: {TRANSPORT}" in proxy[1]


def test_a_timeout_is_one_fault_however_the_exchange_ran_out_of_time():
    """urllib reports the two halves differently; the caller must not see that.

    A timeout while sending is wrapped into a `URLError` by
    `AbstractHTTPHandler.do_open` and used to be reported as `UNREACHABLE`,
    while a timeout waiting for the response propagates as a bare
    `TimeoutError` and was reported as `TIMEOUT`. Which half ran out of time is
    not something a caller can act on.
    """
    import urllib.error

    from hass_axi.config import load
    from hass_axi.rest import RestClient

    client = RestClient(load({"HA_URL": "https://homeassistant.example.com", "HA_TOKEN": "x" * 40}))
    wrapped = client._url_error(urllib.error.URLError(TimeoutError("timed out")))
    assert wrapped.code == "TIMEOUT"


def test_a_websocket_error_home_assistant_never_declared_stays_inside_the_vocabulary(
    run_cli, ws_env, ws_server
):
    """An unrecognised server code must not become a CLI code.

    Home Assistant is free to add one, and uppercasing it published a code this
    project had never documented and no caller could have been ready for. The
    name it used is still readable -- it is in the message, where a fact that
    is not a contract belongs.
    """
    ws_server.fail_all = {"code": "some_future_code", "message": "Something new happened"}
    code, out = run_cli(["entity", "list"], ws_env)
    assert code == 1
    assert "code: API_ERROR" in out
    assert f"class: {REFUSED}" in out
    assert "some_future_code" in out


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ("invalid_format", "INVALID_FORMAT"),
        ("not_allowed", "NOT_ALLOWED"),
        ("not_supported", "NOT_SUPPORTED"),
        ("home_assistant_error", "HOME_ASSISTANT_ERROR"),
        ("service_validation_error", "SERVICE_VALIDATION_ERROR"),
        ("template_error", "TEMPLATE_ERROR"),
        ("id_reuse", "ID_REUSE"),
        ("timeout", "TIMEOUT"),
        ("unknown_error", "API_ERROR"),
    ],
)
def test_every_declared_websocket_error_maps_to_a_declared_code(
    run_cli, ws_env, ws_server, reported, expected
):
    """Home Assistant's twelve, transcribed from its own `const.py`.

    Parametrised over the published vocabulary rather than over the ones that
    happened to be interesting, because the point of a closed mapping is that
    it is closed: an entry that stopped resolving would otherwise be found by
    whoever hit it in production.
    """
    ws_server.fail_all = {"code": reported, "message": "refused"}
    _, out = run_cli(["entity", "list"], ws_env)
    assert f"code: {expected}" in out
    assert "class: " in out


# ------------------------------------------------ the per-command sweep


#: One runnable invocation per subcommand, and the transports it can reach.
#: Maintained by hand because only a human knows what arguments a command
#: needs -- but never *enumerated* by hand: the test below reconciles it
#: against `cli._MODULES`, so a noun added later fails here until it is
#: covered, exactly as an unclassified read-only declaration does.
INVOCATIONS = {
    ("api", "api"): ["api", "/config"],
    ("area", "list"): ["area", "list"],
    ("area", "get"): ["area", "get", "example_room"],
    ("area", "create"): ["area", "create", "--name", "Example Room Two"],
    ("area", "update"): ["area", "update", "example_room", "--name", "Renamed"],
    ("device", "list"): ["device", "list"],
    ("device", "get"): ["device", "get", "device_two"],
    ("device", "update"): ["device", "update", "device_two", "--name", "Renamed"],
    ("doctor", "doctor"): ["doctor"],
    ("entity", "list"): ["entity", "list"],
    ("entity", "get"): ["entity", "get", "light.example_ceiling"],
    ("entity", "update"): ["entity", "update", "light.example_ceiling", "--name", "Renamed"],
    ("home", "home"): [],
    ("service", "list"): ["service", "list"],
    ("service", "get"): ["service", "get", "light.turn_on"],
    ("service", "call"): [
        "service",
        "call",
        "light.turn_on",
        "--target-entity",
        "light.example_ceiling",
    ],
    ("state", "list"): ["state", "list"],
    ("state", "get"): ["state", "get", "light.example_ceiling"],
    ("template", "render"): ["template", "render", "--template", "{{ 1 + 1 }}"],
    ("ws", "ws"): ["ws", "entity.list"],
}

#: The subcommands that never reach Home Assistant. Named rather than inferred,
#: and asserted below, so "it does not touch a transport" has to be a claim
#: somebody made rather than a gap nobody noticed.
LOCAL_ONLY = {("setup", "hooks"), ("setup", "skill"), ("context", "context")}

CLI_SUBCOMMANDS = [
    (name, sub.name) for name, module in sorted(cli._MODULES.items()) for sub in module.COMMAND.subs
]


def test_the_sweep_covers_every_subcommand_the_cli_dispatches():
    """The bar this file is built to hold.

    A per-command classification that some commands opt into is the partial
    guard again: it converts an understood risk into a false assurance about
    the commands it missed. Enumerated from the dispatch table, so a noun added
    later arrives here on its own.
    """
    covered = set(INVOCATIONS) | LOCAL_ONLY
    assert set(CLI_SUBCOMMANDS) - covered == set(), (
        "these subcommands reach a transport and no fault sweep covers them: "
        f"{sorted(set(CLI_SUBCOMMANDS) - covered)}"
    )
    assert covered - set(CLI_SUBCOMMANDS) == set(), "the sweep names a subcommand that is gone"


#: Each fault, the way both doubles produce it, and the class it must report.
#: The two transports are faulted together because a subcommand is free to use
#: either -- and `doctor` and `state list --area` use both.
FAULTS = {
    AUTH: "a token neither transport accepts",
    PERMISSION: "403 from the ban middleware, `unauthorized` from require_admin",
    NOT_FOUND: "nothing routed under /api, `unknown_command` over the WebSocket",
    REFUSED: "a bodyless 500, `home_assistant_error` over the WebSocket",
    TRANSPORT: "nothing listening on the port",
}


def _apply(fault, rest_server, ws_server, env, closed_port):
    if fault == AUTH:
        return {**env, "HA_TOKEN": "not-the-right-token"}
    if fault == PERMISSION:
        rest_server.forbidden = True
        ws_server.fail_all = {"code": "unauthorized", "message": "Unauthorized"}
        return env
    if fault == NOT_FOUND:
        rest_server.unrouted = True
        ws_server.fail_all = {"code": "unknown_command", "message": "Unknown command."}
        return env
    if fault == REFUSED:
        rest_server.status_override = (500, b"")
        ws_server.fail_all = {"code": "home_assistant_error", "message": "Home Assistant error"}
        return env
    return {**env, "HA_URL": f"http://127.0.0.1:{closed_port}"}


@pytest.mark.parametrize("fault", sorted(FAULTS), ids=sorted(FAULTS))
@pytest.mark.parametrize(
    ("command", "sub"), sorted(INVOCATIONS), ids=lambda v: v if isinstance(v, str) else str(v)
)
def test_every_command_classifies_every_fault(
    run_cli, installation, rest_server, ws_server, closed_port, command, sub, fault
):
    """The deliverable: every command, every fault, on both transports.

    Classification lives at the transport boundary -- `RestClient.request` and
    `WsClient.send_command` -- rather than in any command body, which is what
    makes this sweep pass for a command whose author never heard of it. That is
    the same argument the read-only gate makes, and it is checked the same way:
    by running every command rather than by trusting that they all call a
    helper.
    """
    env = _apply(fault, rest_server, ws_server, installation.environ, closed_port)
    code, out = run_cli(INVOCATIONS[(command, sub)], env)
    assert code != 0, f"`{command} {sub}` succeeded against {FAULTS[fault]}"
    assert "class: unclassified" not in out
    assert f"class: {fault}" in out, (
        f"`{command} {sub}` reported no `{fault}` class against {FAULTS[fault]}:\n{out}"
    )


@pytest.mark.parametrize("fault", sorted(FAULTS), ids=sorted(FAULTS))
def test_every_fault_carries_a_declared_code_as_well_as_a_class(
    run_cli, installation, rest_server, ws_server, closed_port, fault
):
    declared = set(errors().CODES)
    env = _apply(fault, rest_server, ws_server, installation.environ, closed_port)
    _, out = run_cli(["state", "list"], env)
    reported = re.findall(r"^\s*code: ([A-Z][A-Z0-9_]+)$", out, re.MULTILINE)
    assert reported, f"no code reported for {FAULTS[fault]}:\n{out}"
    assert set(reported) <= declared


# ------------------------------------------------------------------- doctor


def test_doctor_reports_the_fault_of_each_transport_separately(
    run_cli, installation, rest_server, ws_server
):
    """Strictly better, and the reason the split is worth having at all.

    `doctor` already told the two transports apart. Now each row says *why*,
    so the case a reverse proxy actually produces -- REST answering and the
    WebSocket upgrade refused -- is two readable facts rather than one
    unhealthy instance.
    """
    ws_server.fail_all = {"code": "unauthorized", "message": "Unauthorized"}
    code, out = run_cli(["doctor"], installation.environ)
    assert code == 1
    assert "check: rest\n    status: ok" in out
    assert f"class: {PERMISSION}" in out
    assert "code: FORBIDDEN" in out


def test_doctor_still_answers_healthy_in_tabular_form(run_cli, installation_env):
    """No regression: a run where nothing failed has nothing extra to say."""
    code, out = run_cli(["doctor"], installation_env)
    assert code == 0
    assert "rest,ok" in out
    assert "class:" not in out


def test_doctor_codes_a_missing_environment(run_cli):
    """A failing check that never reaches a transport is coded like any other.

    `home` answers an unset HA_URL with `NOT_CONFIGURED`; folding the identical
    condition into a prose-only check row was the one remaining failing check a
    caller could not switch on -- and reachable on the most common
    misconfiguration there is.
    """
    code, out = run_cli(["doctor"], {})
    assert code == 1
    assert "code: NOT_CONFIGURED" in out
    assert f"class: {CONFIG}" in out


def test_the_home_view_carries_a_code_like_every_other_command(run_cli, rest_env, rest_server):
    """The most-read error surface the tool has, and it had no code at all.

    `setup hooks` puts this view in front of every agent session, and until now
    it reported a failed instance with prose and a help block and nothing to
    switch on.
    """
    rest_server.forbidden = True
    code, out = run_cli([], rest_env)
    assert code == 1
    assert "code: FORBIDDEN" in out
    assert f"class: {PERMISSION}" in out


def test_the_home_view_codes_a_missing_environment(run_cli):
    code, out = run_cli([], {})
    assert code == 1
    assert "code: NOT_CONFIGURED" in out
    assert f"class: {CONFIG}" in out


# ----------------------------------------------- the local classes, in passing


def test_a_usage_error_is_a_usage_class(run_cli, rest_env):
    code, out = run_cli(["state", "list", "--nope"], rest_env)
    assert code == 2
    assert "code: UNKNOWN_FLAG" in out
    assert f"class: {USAGE}" in out


def test_a_read_only_refusal_is_a_usage_class_and_keeps_its_own_code(run_cli, ws_env):
    """Unchanged, and named here so a later edit cannot quietly move it.

    The refusal is decided without touching the installation and no argument to
    the same command changes the verdict, which is what puts it on the exit-2
    side of the line -- and `READ_ONLY` stays distinct from `FORBIDDEN`,
    because "this session forbids writes" and "that account may not" have
    different fixes.
    """
    code, out = run_cli(
        ["entity", "update", "light.example_ceiling", "--name", "Renamed"],
        {**ws_env, "HASS_AXI_READ_ONLY": "1"},
    )
    assert code == 2
    assert "code: READ_ONLY" in out
    assert f"class: {USAGE}" in out


def test_a_missing_configuration_is_a_config_class(run_cli):
    code, out = run_cli(["state", "list"], {})
    assert code == 1
    assert "code: NOT_CONFIGURED" in out
    assert f"class: {CONFIG}" in out


def test_an_unexpected_exception_is_an_internal_class(run_cli, rest_env, monkeypatch):
    from hass_axi.commands import state as state_command

    def explode(*args, **kwargs):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(state_command, "run", explode)
    code, out = run_cli(["state", "list"], rest_env)
    assert code == 1
    assert "code: INTERNAL_ERROR" in out
    assert f"class: {INTERNAL}" in out


def test_the_class_is_rendered_in_json_mode_too(run_cli, rest_env, rest_server):
    """The field is data, not decoration, so it survives `--json`."""
    rest_server.forbidden = True
    code, out = run_cli(["--json", "state", "list"], rest_env)
    assert code == 1
    doc = json.loads(out)
    assert (doc["code"], doc["class"]) == ("FORBIDDEN", PERMISSION)


# ----------------------------------------------------------------- fixtures


@pytest.fixture
def closed_port():
    """A port on loopback with nothing listening on it."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return port


@pytest.fixture
def refusing_server():
    """An HTTP server that refuses every request with a chosen status.

    Stands in for what is in front of Home Assistant rather than for Home
    Assistant: a proxy that does not forward upgrades, a URL that is not the
    instance root, a gateway answering while the instance restarts. The
    WebSocket client meets a real HTTP response with a real status here, which
    is the only way to prove the handshake classifier reads one.
    """
    servers = []

    def make(status: int) -> str:
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                body = f"{status}: refused".encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.daemon = True
        thread.start()
        servers.append((server, thread))
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield make
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

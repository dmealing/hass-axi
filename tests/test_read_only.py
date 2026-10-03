"""The read-only gate: ``HASS_AXI_READ_ONLY``, fail-closed, on both transports.

Two conventions in this file are deliberate.

**The variable name and the error code are written out as literals here**, not
imported from :mod:`hass_axi.readonly`. They are the contract an operator types
into a shell and an agent reads out of a document; a test that imported them
would agree with a rename that broke every caller, the same way a double that
imports the client's own table can only prove the client agrees with itself.

**Everything that needs the new module imports it inside the test body.** At the
commit before this gate existed the module is absent, so a module-level import
would collapse the whole file into one collection error. Function-local imports
make each test fail on its own account there, which is what lets the change be
reported as "N tests fail before, all pass after" rather than "the file does not
load". :mod:`hass_axi.cli` and :mod:`hass_axi.ws` predate the change and are
imported normally, so the parametrised sweeps below still enumerate the real
command tables at collection time.
"""

from __future__ import annotations

import types

import pytest

from hass_axi import cli, ws
from hass_axi.argspec import Command, Sub

#: The switch, spelled as an operator spells it.
ENV_VAR = "HASS_AXI_READ_ONLY"

#: The code a refusal carries, distinct from UNAUTHORIZED and from any
#: transport failure so an agent can tell "forbidden here" from "rejected
#: there" without reading prose.
CODE = "READ_ONLY"

#: Every subcommand the CLI dispatches, across both transports, enumerated from
#: the dispatch table itself rather than from a list somebody maintains. A
#: command added without a classification arrives here automatically.
CLI_SUBCOMMANDS = [
    (name, sub.name) for name, module in sorted(cli._MODULES.items()) for sub in module.COMMAND.subs
]

#: Every WebSocket command the CLI ships, same principle.
WS_COMMANDS = sorted(ws.REGISTRY)


def readonly():
    """The module under test, imported late -- see this file's docstring."""
    from hass_axi import readonly as module

    return module


def enabled(environ: dict) -> dict:
    return {**environ, ENV_VAR: "1"}


# ------------------------------------------------------- the completeness bar


@pytest.mark.parametrize(("command", "sub"), CLI_SUBCOMMANDS, ids=lambda v: v)
def test_every_cli_subcommand_carries_an_explicit_read_only_classification(command, sub):
    """The deliverable: no subcommand may be unclassified.

    Enumerated from `cli._MODULES`, so a noun added later is covered without
    anybody remembering to extend a list. `None` is the unclassified state and
    is treated as a write everywhere else in this file; here it is a failure,
    because a guard whose coverage depends on memory is the partial guard this
    change exists to avoid.
    """
    declared = cli._MODULES[command].COMMAND.find(sub)
    assert declared is not None
    assert declared.access in readonly().CLASSIFICATIONS, (
        f"`{command} {sub}` has no read_only classification; "
        f"declare one of {readonly().CLASSIFICATIONS}"
    )


@pytest.mark.parametrize("name", WS_COMMANDS)
def test_every_websocket_command_carries_an_explicit_read_only_classification(name):
    """The same bar on the other transport.

    `DYNAMIC` is deliberately not permitted here: a WebSocket command's type is
    fixed by its declaration, so there is nothing left for an argument to
    decide.
    """
    module = readonly()
    assert ws.REGISTRY[name].access in (module.READ, module.WRITE), (
        f"websocket command `{name}` has no read_only classification"
    )


def test_every_dynamic_subcommand_can_resolve_its_own_classification():
    """`DYNAMIC` is a promise that the module answers per invocation.

    A subcommand whose verdict depends on its arguments -- the two escape
    hatches, and `setup skill --check` -- declares `DYNAMIC` and supplies an
    `access()` resolver. Declaring `DYNAMIC` without one would be an
    unclassified command wearing a classification, so it is a failure here.
    """
    module = readonly()
    dynamic = [
        (name, sub.name)
        for name, command_module in sorted(cli._MODULES.items())
        for sub in command_module.COMMAND.subs
        if sub.access == module.DYNAMIC
    ]
    assert dynamic, "expected at least the two escape hatches to be dynamic"
    for name, sub in dynamic:
        resolver = getattr(cli._MODULES[name], "access", None)
        assert callable(resolver), f"`{name} {sub}` declares DYNAMIC but exposes no access()"


def test_an_unclassified_declaration_is_the_default_so_forgetting_is_visible():
    """The default has to be absent, not `WRITE`, or the sweep above cannot see it."""
    assert Sub(name="example").access is None
    assert ws.WsCommand(name="example", type="example/type", summary="").access is None


# ------------------------------------------------------------ fail closed


def test_a_subcommand_with_no_classification_is_refused_rather_than_dispatched(
    run_cli, installation_env, rest_server, ws_server
):
    """Forgetting must refuse, not mutate.

    A command registered with no classification at all -- the shape a future
    contributor produces by copying an existing module and not reading this
    file -- is refused under the variable, and its body never runs.
    """
    dispatched = []
    unclassified = types.SimpleNamespace(
        COMMAND=Command(
            name="example",
            summary="A command nobody classified",
            default_sub="write",
            subs=(Sub(name="write", summary="Mutate something"),),
        ),
        run=lambda ctx, sub, parsed: dispatched.append(sub) or {"ok": True},
    )
    cli._MODULES["example"] = unclassified
    try:
        code, out = run_cli(["example", "write"], enabled(installation_env))
    finally:
        del cli._MODULES["example"]

    assert code == 2
    assert f"code: {CODE}" in out
    assert dispatched == []
    assert rest_server.requests == []
    assert ws_server.received == []


def test_a_websocket_command_with_no_classification_is_refused(
    run_cli, ws_env, ws_server, monkeypatch
):
    """The same fail-closed default on the WebSocket command table."""
    monkeypatch.setitem(
        ws.REGISTRY,
        "example.write",
        ws.WsCommand(name="example.write", type="example/registry/update", summary="Mutate"),
    )
    code, out = run_cli(["ws", "example.write"], enabled(ws_env))
    assert code == 2
    assert f"code: {CODE}" in out
    assert ws_server.received == []


def test_a_command_classified_read_still_cannot_write(run_cli, rest_env, rest_server):
    """The two enforcement points compose, and the transport has the last word.

    A classification is a claim a module makes about itself, and a wrong one is
    exactly the shape of the partial guard this change exists to avoid. So the
    transport refuses the write whatever the dispatch gate was told, which is
    what makes the guard hold for a command whose author never read any of it.
    """
    misclassified = types.SimpleNamespace(
        COMMAND=Command(
            name="example",
            summary="A command that says it reads and does not",
            default_sub="read",
            subs=(Sub(name="read", summary="Claims to read", access="read"),),
        ),
        run=lambda ctx, sub, parsed: ctx.rest().request(
            "POST", "/services/light/turn_on", body={"entity_id": "light.example_lamp"}
        ),
    )
    cli._MODULES["example"] = misclassified
    try:
        code, out = run_cli(["example", "read"], enabled(rest_env))
    finally:
        del cli._MODULES["example"]

    assert code == 2
    assert f"code: {CODE}" in out
    assert rest_server.requests == []


def test_an_undeclared_websocket_type_is_refused(run_cli, ws_env, ws_server):
    """`--raw` hands an arbitrary type to the API, so an unknown one is a write."""
    code, out = run_cli(["ws", "--raw", "example/registry/update"], enabled(ws_env))
    assert code == 2
    assert f"code: {CODE}" in out
    assert ws_server.received == []


# --------------------------------------------- the same write, both transports


def test_a_write_over_rest_is_refused_and_nothing_reaches_the_installation(
    run_cli, rest_env, rest_server
):
    """A service call is the REST write, and it is refused before any request.

    `service call` reads the service model before it posts, so an empty request
    log proves the refusal landed ahead of the whole command rather than at the
    last moment.
    """
    code, out = run_cli(
        ["service", "call", "light.turn_on", "--target-entity", "light.example_lamp"],
        enabled(rest_env),
    )
    assert code == 2
    assert f"code: {CODE}" in out
    assert ENV_VAR in out
    assert "service call" in out
    assert rest_server.requests == []


def test_a_write_over_the_websocket_is_refused_and_nothing_reaches_the_installation(
    run_cli, ws_env, ws_server
):
    """A registry update is the WebSocket write, refused the same way.

    The counterpart of the REST test above, and it is a separate test on
    purpose: the two transports are separate code routes into one server, and a
    guard proven on one says nothing about the other.
    """
    code, out = run_cli(
        ["entity", "update", "light.example_ceiling", "--name", "Reading Lamp"],
        enabled(ws_env),
    )
    assert code == 2
    assert f"code: {CODE}" in out
    assert ENV_VAR in out
    assert "entity update" in out
    assert ws_server.received == []


def test_the_same_rest_write_succeeds_with_the_variable_unset(run_cli, rest_env, rest_server):
    code, _ = run_cli(
        ["service", "call", "light.turn_on", "--target-entity", "light.example_lamp"], rest_env
    )
    assert code == 0
    assert [r for r in rest_server.requests if r["path"] == "/api/services/light/turn_on"]


def test_the_same_websocket_write_succeeds_with_the_variable_unset(run_cli, ws_env, ws_server):
    code, _ = run_cli(
        ["entity", "update", "light.example_ceiling", "--name", "Reading Lamp"], ws_env
    )
    assert code == 0
    assert [c for c in ws_server.received if c["type"] == "config/entity_registry/update"]


def test_a_device_registry_write_is_refused_before_it_reaches_the_installation(
    run_cli, ws_env, ws_server
):
    """The device registry is a second write surface on the same transport.

    A classification is a claim a module makes about itself, so a newly declared
    `WRITE` sub proves nothing until the gate is seen refusing it: a write that
    is not actually gated is the failure this whole file exists to prevent, and
    it is invisible from the declaration alone.
    """
    code, out = run_cli(
        ["device", "update", "device_two", "--name", "Hall Ceiling"], enabled(ws_env)
    )
    assert code == 2
    assert f"code: {CODE}" in out
    assert ENV_VAR in out
    assert "device update" in out
    assert ws_server.received == []


def test_the_same_device_write_succeeds_with_the_variable_unset(run_cli, ws_env, ws_server):
    code, _ = run_cli(["device", "update", "device_two", "--name", "Hall Ceiling"], ws_env)
    assert code == 0
    assert [c for c in ws_server.received if c["type"] == "config/device_registry/update"]


def test_clearing_a_device_field_is_a_write_too(run_cli, ws_env, ws_server):
    """`--clear-name` and `--clear-area` remove data, so neither is a read."""
    for flag in ("--clear-name", "--clear-area"):
        code, out = run_cli(["device", "update", "device_two", flag], enabled(ws_env))
        assert code == 2, flag
        assert f"code: {CODE}" in out
    assert ws_server.received == []


# ------------------------------------------- the transports guard themselves


def test_the_rest_client_refuses_a_write_with_no_dispatch_gate_in_front_of_it(rest_server):
    """The backstop: a command that never consulted the gate still cannot write.

    Enforcement lives at the point of dispatch rather than in each command
    body, so a new command reaches the server through this method whether or
    not its author read any of this.
    """
    from conftest import FAKE_TOKEN
    from hass_axi.config import load
    from hass_axi.rest import RestClient

    config = load({"HA_URL": rest_server.url, "HA_TOKEN": FAKE_TOKEN, ENV_VAR: "1"})
    client = RestClient(config)
    with pytest.raises(readonly().ReadOnlyRefused) as raised:
        client.request("POST", "/services/light/turn_on", body={})
    assert raised.value.code == CODE
    assert rest_server.requests == []


def test_the_websocket_client_refuses_a_write_with_no_dispatch_gate_in_front_of_it(ws_server):
    from conftest import FAKE_TOKEN
    from hass_axi.config import load
    from hass_axi.ws import WsClient

    config = load(
        {"HA_URL": f"http://127.0.0.1:{ws_server.port}", "HA_TOKEN": FAKE_TOKEN, ENV_VAR: "1"}
    )
    client = WsClient(config)
    with pytest.raises(readonly().ReadOnlyRefused) as raised:
        client.send_command("config/entity_registry/update", {"entity_id": "light.example_lamp"})
    assert raised.value.code == CODE
    assert ws_server.received == []


def test_the_websocket_client_refuses_before_it_opens_a_connection(ws_server):
    """No socket, no handshake, no credential on the wire for a refused write."""
    from conftest import FAKE_TOKEN
    from hass_axi.config import load
    from hass_axi.ws import WsClient

    config = load(
        {"HA_URL": f"http://127.0.0.1:{ws_server.port}", "HA_TOKEN": FAKE_TOKEN, ENV_VAR: "1"}
    )
    client = WsClient(config)
    with pytest.raises(readonly().ReadOnlyRefused):
        client.run("entity.update", {"entity_id": "light.example_lamp", "name": "Reading Lamp"})
    assert client._socket is None


# ------------------------------------------------------------ escape hatches


def test_the_rest_escape_hatch_refuses_an_unsafe_method(run_cli, rest_env, rest_server):
    code, out = run_cli(
        ["api", "POST", "/services/light/turn_on", "--field", "entity_id=light.example_lamp"],
        enabled(rest_env),
    )
    assert code == 2
    assert f"code: {CODE}" in out
    assert rest_server.requests == []


def test_the_rest_escape_hatch_still_reads(run_cli, rest_env):
    code, out = run_cli(["api", "/states/light.example_lamp"], enabled(rest_env))
    assert code == 0
    assert "light.example_lamp" in out


def test_the_websocket_escape_hatch_refuses_a_declared_write_reached_by_raw_type(
    run_cli, ws_env, ws_server
):
    """`--raw` is judged by the type it names, not by the name it was given."""
    code, out = run_cli(
        ["ws", "--raw", "config/entity_registry/update", "--param", "entity_id=light.example_lamp"],
        enabled(ws_env),
    )
    assert code == 2
    assert f"code: {CODE}" in out
    assert ws_server.received == []


def test_the_websocket_escape_hatch_still_reads_by_raw_type(run_cli, ws_env):
    code, _ = run_cli(["ws", "--raw", "config/floor_registry/list"], enabled(ws_env))
    assert code == 0


def test_the_websocket_escape_hatch_still_lists_its_commands(run_cli, ws_env):
    code, out = run_cli(["ws", "--list"], enabled(ws_env))
    assert code == 0
    assert "entity.update" in out


# ---------------------------------------------------------- reads still work


READ_INVOCATIONS = [
    ["state", "list"],
    ["state", "get", "light.example_lamp"],
    ["service", "list"],
    ["service", "get", "light.turn_on"],
    ["template", "render", "--template", "{{ 1 + 1 }}"],
    ["api", "/config"],
]


@pytest.mark.parametrize("argv", READ_INVOCATIONS, ids=lambda a: " ".join(a[:2]))
def test_a_rest_read_still_works_under_read_only(run_cli, rest_env, argv):
    """A read-only session is still a working session, or nobody sets it.

    `template render` is the one that matters most here: it is a POST, and a
    guard that judged the method alone would refuse the tool's most useful
    read.
    """
    code, _ = run_cli(argv, enabled(rest_env))
    assert code == 0


WS_READ_INVOCATIONS = [
    ["entity", "list"],
    ["entity", "get", "light.example_lamp"],
    ["area", "list"],
    ["area", "get", "example_room"],
    ["device", "list"],
    ["device", "get", "device_two"],
    ["ws", "entity.list"],
]


@pytest.mark.parametrize("argv", WS_READ_INVOCATIONS, ids=lambda a: " ".join(a[:2]))
def test_a_websocket_read_still_works_under_read_only(run_cli, ws_env, argv):
    code, _ = run_cli(argv, enabled(ws_env))
    assert code == 0


def test_the_home_view_still_works_and_announces_the_mode(run_cli, rest_env):
    """The landing view is what `setup hooks` puts in front of every session.

    An agent that cannot see the mode plans writes it will never be allowed to
    make, and reads the refusals as a broken installation.
    """
    code, out = run_cli([], enabled(rest_env))
    assert code == 0
    assert "read_only: on" in out


def test_the_home_view_says_nothing_about_the_mode_when_it_is_off(run_cli, rest_env):
    code, out = run_cli([], rest_env)
    assert code == 0
    assert "read_only" not in out


def test_doctor_reports_the_mode_when_it_is_on(run_cli, installation_env):
    code, out = run_cli(["doctor"], enabled(installation_env))
    assert code == 0
    assert "read_only,ok" in out
    assert ENV_VAR in out


def test_doctor_reports_the_mode_when_it_is_off(run_cli, installation_env):
    code, out = run_cli(["doctor"], installation_env)
    assert code == 0
    assert "read_only,ok" in out


# ------------------------------------------------- visible, and still refused


def test_a_refused_command_is_still_listed_in_the_command_table(run_cli, rest_env):
    """Deliberately unlike the sibling project's playback gate.

    There the capability was hidden, because a second tool offered the same
    thing and an agent could pick the wrong one. Nothing else here reaches
    Home Assistant, so hiding `entity update` would leave an agent unable to
    work out why its plan is impossible. Visible and refused is the answer.
    """
    code, out = run_cli(["entity", "--help"], enabled(rest_env))
    assert code == 0
    assert "update" in out


def test_help_for_a_refused_command_still_renders(run_cli, rest_env):
    code, out = run_cli(["service", "call", "--help"], enabled(rest_env))
    assert code == 0
    assert "--target-entity" in out


def test_root_help_documents_the_variable(run_cli):
    code, out = run_cli(["--help"], {})
    assert code == 0
    assert ENV_VAR in out


# --------------------------------------------------------- reading the switch


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "0", "false", "off"])
def test_any_non_empty_value_enables_the_gate(run_cli, ws_env, ws_server, value):
    """It is a switch, not a boolean.

    `HASS_AXI_READ_ONLY=false` enabling read-only is the safe way to be wrong:
    the alternative is a spelling this tool fails to recognise leaving the
    house writable while an operator believes it is not.
    """
    code, _ = run_cli(
        ["entity", "update", "light.example_ceiling", "--name", "Reading Lamp"],
        {**ws_env, ENV_VAR: value},
    )
    assert code == 2
    assert ws_server.received == []


@pytest.mark.parametrize("value", ["", "   ", "\t"])
def test_an_empty_value_is_the_same_as_unset(run_cli, ws_env, value):
    code, _ = run_cli(
        ["entity", "update", "light.example_ceiling", "--name", "Reading Lamp"],
        {**ws_env, ENV_VAR: value},
    )
    assert code == 0


# ----------------------------------------------------------- the refusal text


def test_the_refusal_names_the_command_the_variable_and_a_way_forward(run_cli, ws_env):
    code, out = run_cli(
        ["entity", "update", "light.example_ceiling", "--name", "Reading Lamp"], enabled(ws_env)
    )
    assert code == 2
    assert "hass-axi entity update" in out
    assert ENV_VAR in out
    assert "help[" in out


def test_a_refusal_is_distinguishable_from_a_rejected_token(run_cli, rest_env, rest_server):
    """Two different failures must not answer with one code.

    An agent that cannot tell "this session forbids writes" from "this token is
    not accepted" retries the wrong fix.
    """
    rest_server.status_override = (401, {"message": "Unauthorized"})
    code, out = run_cli(["service", "call", "light.turn_on"], rest_env)
    assert (code, "code: UNAUTHORIZED" in out) == (1, True)
    assert CODE not in out


def test_the_refusal_is_reported_on_stdout_and_stderr_stays_clean(run_cli, ws_env, capsys):
    run_cli(
        ["entity", "update", "light.example_ceiling", "--name", "Reading Lamp"], enabled(ws_env)
    )
    assert capsys.readouterr().err == ""


# ------------------------------------------------------------- local writes


def test_setup_hooks_is_a_write(run_cli, rest_env, tmp_path):
    """`setup` writes to this machine rather than to Home Assistant, and still counts.

    The variable says this tool does not write. Splitting that into "does not
    write your house" and "does not write your dotfiles" is a distinction
    nobody asked for, and the safe half of it is refusing both.
    """
    code, out = run_cli(["setup", "hooks", "--home", str(tmp_path)], enabled(rest_env))
    assert code == 2
    assert f"code: {CODE}" in out
    assert list(tmp_path.iterdir()) == []


def test_setup_skill_check_is_a_read(run_cli, rest_env):
    code, out = run_cli(["setup", "skill", "--check"], enabled(rest_env))
    assert code == 0
    assert "status: current" in out


# --------------------------------------------- the name before the rename


LEGACY_VAR = "HA_AXI_READ_ONLY"


def test_the_pre_rename_variable_still_enables_the_gate(run_cli, ws_env, ws_server, capsys):
    """An upgrade must never be the thing that switched the guard off.

    Before the rename the switch was spelled `HA_AXI_READ_ONLY`. A session that
    still sets it is still read-only, and is told once on stderr to move to the
    new name -- stdout stays the document an agent parses.
    """
    capsys.readouterr()
    from hass_axi.cli import main

    code = main(
        ["entity", "update", "light.example_ceiling", "--name", "Renamed"],
        environ={**ws_env, LEGACY_VAR: "1"},
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "code: READ_ONLY" in captured.out
    assert f"{LEGACY_VAR} is deprecated; rename it to {ENV_VAR}" in captured.err
    assert captured.err.count("deprecated") == 1
    assert not any(c.get("type") == "config/entity_registry/update" for c in ws_server.received)


def test_doctor_names_the_variable_that_is_actually_set(run_cli, installation_env):
    code, out = run_cli(["doctor"], {**installation_env, LEGACY_VAR: "1"})
    assert code == 0
    assert f"{LEGACY_VAR} is set: every write is refused" in out


def test_the_new_name_wins_silently_when_both_are_set(ws_env, capsys):
    from hass_axi.readonly import active_var

    capsys.readouterr()
    assert active_var({**ws_env, ENV_VAR: "1", LEGACY_VAR: "1"}) == ENV_VAR
    assert capsys.readouterr().err == ""

"""The exit-code contract, written down as a table and swept over every flag.

Three exits, and the line between two of them is the one a caller acts on:

| exit | meaning | decided |
| --- | --- | --- |
| 0 | the command answered | by the answer |
| 1 | the installation, or a lookup against it, said no | only by asking |
| 2 | the invocation is wrong (class `usage`) | without asking |

The third column is the contract this file adds. A usage error is decided
before any transport is built, so it is the *same* error whether the
installation answers, refuses the connection, or is not configured at all. An
agent with a typo and a server that happens to be down was told to check the
network; `--limit abc` answered `UNREACHABLE`.

The sweep is derived from the dispatch table rather than listed by hand, so a
flag added later is held to the contract without anybody remembering it.
"""

from __future__ import annotations

import json

import pytest

from conftest import FAKE_TOKEN
from hass_axi import cli
from test_error_codes import INVOCATIONS

#: Reached, and not reached. A usage error is the same in all three.
UNREACHABLE = {"HA_URL": "http://127.0.0.1:9", "HA_TOKEN": FAKE_TOKEN}
UNCONFIGURED: dict = {}

#: argv, exit, code. One row per kind of fault the parser and the commands
#: decide statically. The class is always `usage`, derived from the code.
USAGE_FAULTS = [
    (["nosuchcommand"], "UNKNOWN_COMMAND"),
    (["states"], "UNKNOWN_COMMAND"),
    (["--nosuchglobal", "state", "list"], "UNKNOWN_FLAG"),
    (["state"], "MISSING_SUBCOMMAND"),
    (["state", "lst"], "UNKNOWN_SUBCOMMAND"),
    (["device", "updat", "x"], "UNKNOWN_SUBCOMMAND"),
    (["state", "list", "--nosuch"], "UNKNOWN_FLAG"),
    (["state", "list", "--room", "x"], "UNKNOWN_FLAG"),
    (["sensor", "list", "--format", "json"], "UNKNOWN_FLAG"),
    (["state", "get"], "MISSING_ARGUMENT"),
    (["state", "get", "light.a", "light.b"], "UNEXPECTED_ARGUMENT"),
    (["ping", "extra"], "UNEXPECTED_ARGUMENT"),
    (["area", "create", "Example Annex"], "UNEXPECTED_ARGUMENT"),
    (["state", "list", "--domain"], "MISSING_VALUE"),
    (["state", "list", "--domain", "--json"], "MISSING_VALUE"),
    (["state", "list", "--limit", "abc"], "BAD_LIMIT"),
    (["state", "list", "--limit", "0"], "BAD_LIMIT"),
    (["state", "list", "--limit", "-3"], "BAD_LIMIT"),
    (["state", "list", "--limit", "2.5"], "BAD_LIMIT"),
    (["state", "list", "--fields", "nope"], "UNKNOWN_FIELD"),
    (["state", "list", "--stale", "soon"], "BAD_TIME"),
    (["--timeout", "abc", "state", "list"], "BAD_TIMEOUT"),
    (["--timeout", "0", "state", "list"], "BAD_TIMEOUT"),
    (["state", "list", "--timeout"], "BAD_TIMEOUT"),
    (["history", "get", "light.example_lamp", "--start", "soon"], "BAD_TIME"),
    (["history", "get", "light.example_lamp", "--start", "1h", "--end", "2h"], "BAD_WINDOW"),
    (["statistics", "get", "sensor.x", "--period", "fortnight"], "BAD_PERIOD"),
    (["statistics", "list", "--kind", "total"], "BAD_KIND"),
    (["service", "call", "turn_on"], "BAD_SERVICE"),
    (["service", "call", "light.turn_on", "--data", "novalue"], "BAD_PAIR"),
    (["service", "call", "light.turn_on", "--data-json", "{"], "BAD_JSON"),
    (["service", "call", "light.turn_on", "--data-json", "[1]"], "BAD_JSON"),
    (["template", "render"], "MISSING_TEMPLATE"),
    (["template", "render", "--template", "a", "--template-file", "b"], "CONFLICTING_FLAGS"),
    (["template", "render", "--template-file", "/no/such/template.j2"], "UNREADABLE_FILE"),
    (["entity", "update", "light.example_lamp"], "NO_CHANGES"),
    (
        ["entity", "update", "light.example_lamp", "--name", "a", "--clear-name"],
        "CONFLICTING_FLAGS",
    ),
    (["entity", "update", "light.example_lamp", "--icon", "sofa"], "BAD_ICON"),
    (["area", "create"], "MISSING_NAME"),
    (["area", "update", "example_room"], "NO_CHANGES"),
    (["device", "update", "device_one"], "NO_CHANGES"),
    (["ws", "nosuch.command"], "UNKNOWN_COMMAND"),
    (["ws", "entity.update"], "MISSING_PARAM"),
    (["ws", "entity.list", "--param", "novalue"], "BAD_PAIR"),
    (["ws", "entity.list", "--params-json", "{"], "BAD_JSON"),
    (["api"], "MISSING_ARGUMENT"),
    (["api", "POST"], "MISSING_PATH"),
    (["api", "BOGUS", "/config"], "UNEXPECTED_ARGUMENT"),
    (["api", "/config", "--body", "{"], "BAD_JSON"),
    (["api", "/config", "--query", "novalue"], "BAD_PAIR"),
    (["setup"], "MISSING_SUBCOMMAND"),
    (["setup", "nosuch"], "UNKNOWN_SUBCOMMAND"),
    (["context", "bogus"], "UNKNOWN_SUBCOMMAND"),
]

ENVIRONMENTS = ["reachable", "unreachable", "unconfigured"]


def as_json(run_cli, argv, env):
    code, out = run_cli(["--json", *argv], env)
    return code, json.loads(out)


def _environment(name: str, installation_env) -> dict:
    return {
        "reachable": installation_env,
        "unreachable": UNREACHABLE,
        "unconfigured": UNCONFIGURED,
    }[name]


@pytest.mark.parametrize(
    ("argv", "code"), USAGE_FAULTS, ids=lambda v: " ".join(v) if isinstance(v, list) else v
)
@pytest.mark.parametrize("where", ENVIRONMENTS)
def test_a_usage_error_is_the_same_error_with_or_without_the_installation(
    run_cli, installation_env, argv, code, where
):
    exit_code, doc = as_json(run_cli, argv, _environment(where, installation_env))
    assert (exit_code, doc["code"], doc["class"]) == (2, code, "usage")
    assert doc["help"], "an error that suggests nothing is a dead end"


def _flag_cases():
    """Every value-taking flag of every subcommand, given with no value at all."""
    for (command, sub_name), argv in sorted(INVOCATIONS.items()):
        if command == "home":
            continue
        sub = cli.command_specs()[command].find(sub_name)
        for flag in sub.flags:
            if flag.takes_value:
                yield pytest.param(
                    [*argv, flag.name], flag.name, id=f"{command} {sub_name} {flag.name}"
                )


@pytest.mark.parametrize(("argv", "flag"), list(_flag_cases()))
@pytest.mark.parametrize("env", [UNREACHABLE, UNCONFIGURED], ids=["unreachable", "unconfigured"])
def test_every_flag_given_without_its_value_is_refused_without_a_transport(
    run_cli, argv, flag, env
):
    exit_code, doc = as_json(run_cli, argv, env)
    assert (exit_code, doc["code"]) == (2, "MISSING_VALUE"), doc
    assert flag in doc["error"]


def _listing_cases():
    """`--limit` and `--fields`, on every subcommand that declares either."""
    for (command, sub_name), argv in sorted(INVOCATIONS.items()):
        if command == "home":
            continue
        declared = {flag.name for flag in cli.command_specs()[command].find(sub_name).flags}
        if "--limit" in declared:
            yield pytest.param(
                [*argv, "--limit", "abc"], "BAD_LIMIT", id=f"{command} {sub_name} --limit"
            )
        if "--fields" in declared:
            yield pytest.param(
                [*argv, "--fields", "no_such_field"],
                "UNKNOWN_FIELD",
                id=f"{command} {sub_name} --fields",
            )


@pytest.mark.parametrize(("argv", "code"), list(_listing_cases()))
@pytest.mark.parametrize("env", [UNREACHABLE, UNCONFIGURED], ids=["unreachable", "unconfigured"])
def test_a_bad_limit_or_field_list_is_refused_without_a_transport(run_cli, argv, code, env):
    exit_code, doc = as_json(run_cli, argv, env)
    assert (exit_code, doc["code"], doc["class"]) == (2, code, "usage"), doc


def test_every_unknown_flag_is_refused_without_a_transport(run_cli):
    for (command, _sub), argv in sorted(INVOCATIONS.items()):
        if command == "home":
            continue
        exit_code, doc = as_json(run_cli, [*argv, "--no-such-flag-anywhere"], UNREACHABLE)
        assert (exit_code, doc["code"]) == (2, "UNKNOWN_FLAG"), (argv, doc)


#: The other side of the line: a well-formed command that only the
#: installation can answer exits 1, never 2, and never with class `usage`.
LOOKUPS = [
    (["state", "get", "light.no_such_entity"], "NO_SUCH_ENTITY"),
    (["state", "get", "Example Reading Lamp"], "NO_SUCH_ENTITY"),
    (["entity", "get", "light.no_such_entity"], "NO_SUCH_ENTITY"),
    (["area", "get", "nowhere"], "NO_SUCH_AREA"),
    (["entity", "list", "--area", "nowhere"], "NO_SUCH_AREA"),
    (["device", "get", "nothing"], "NO_SUCH_DEVICE"),
    (["entity", "list", "--device", "nothing"], "NO_SUCH_DEVICE"),
    (["service", "get", "light.no_such_service"], "NO_SUCH_SERVICE"),
    (["service", "get", "nodomain.turn_on"], "NO_SUCH_DOMAIN"),
    (["statistics", "get", "sensor.no_such_statistic"], "NO_SUCH_STATISTIC"),
    (["area", "update", "example_room", "--floor", "nowhere"], "NO_SUCH_FLOOR"),
    (["api", "/no/such/path"], "NOT_FOUND"),
]


@pytest.mark.parametrize(
    ("argv", "code"), LOOKUPS, ids=lambda v: " ".join(v) if isinstance(v, list) else v
)
def test_a_failed_lookup_exits_1_and_is_never_a_usage_error(run_cli, installation_env, argv, code):
    exit_code, doc = as_json(run_cli, argv, installation_env)
    assert (exit_code, doc["code"]) == (1, code), doc
    assert doc["class"] != "usage"
    assert doc["help"]


def test_a_well_formed_command_against_nothing_exits_1(run_cli):
    exit_code, doc = as_json(run_cli, ["state", "list"], UNREACHABLE)
    assert (exit_code, doc["class"]) == (1, "transport")
    exit_code, doc = as_json(run_cli, ["state", "list"], UNCONFIGURED)
    assert (exit_code, doc["class"]) == (1, "config")

"""P4 and P5: the error contract, for faults decided here and for lookups only the server can fail."""

from __future__ import annotations

import pytest

from .harness import contract, three_modes

#: Static faults, each exit 2 and class `usage`, with nothing about any installation in them.
USAGE = [
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
    (["ping", "extra"], "UNEXPECTED_ARGUMENT"),
    (["area", "create", "Scratch"], "UNEXPECTED_ARGUMENT"),
    (["state", "list", "--domain"], "MISSING_VALUE"),
    (["state", "list", "--domain", "--limit", "3"], "MISSING_VALUE"),
    (["state", "list", "--limit", "abc"], "BAD_LIMIT"),
    (["sensor", "list", "--limit", "0"], "BAD_LIMIT"),
    (["entity", "list", "--fields", "nope"], "UNKNOWN_FIELD"),
    (["device", "list", "--fields", "nope"], "UNKNOWN_FIELD"),
    (["statistics", "list", "--limit", "abc"], "BAD_LIMIT"),
    (["logbook", "get", "--fields", "nope"], "UNKNOWN_FIELD"),
    (["service", "get", "light.turn_on", "--fields", "nope"], "UNKNOWN_FIELD"),
    (["state", "list", "--stale", "soon"], "BAD_TIME"),
    (["--timeout", "abc", "state", "list"], "BAD_TIMEOUT"),
    (["history", "get", "light.x", "--start", "soon"], "BAD_TIME"),
    (["history", "get", "light.x", "--start", "1h", "--end", "2h"], "BAD_WINDOW"),
    (["statistics", "get", "sensor.x", "--period", "fortnight"], "BAD_PERIOD"),
    (["statistics", "list", "--kind", "total"], "BAD_KIND"),
    (["service", "call", "turn_on"], "BAD_SERVICE"),
    (["service", "call", "light.turn_on", "--data", "novalue"], "BAD_PAIR"),
    (["service", "call", "light.turn_on", "--data-json", "{"], "BAD_JSON"),
    (["template", "render"], "MISSING_TEMPLATE"),
    (["entity", "update", "light.x"], "NO_CHANGES"),
    (["entity", "update", "light.x", "--name", "a", "--clear-name"], "CONFLICTING_FLAGS"),
    (["area", "update", "x", "--icon", "not-an-icon"], "BAD_ICON"),
    (["area", "create"], "MISSING_NAME"),
    (["ws", "nosuch.command"], "UNKNOWN_COMMAND"),
    (["ws", "entity.update"], "MISSING_PARAM"),
    (["api", "POST"], "MISSING_PATH"),
    (["api", "BOGUS", "/config"], "UNEXPECTED_ARGUMENT"),
    (["setup"], "MISSING_SUBCOMMAND"),
    (["context", "bogus"], "UNKNOWN_SUBCOMMAND"),
]
IDS = [" ".join(argv) for argv, _ in USAGE]

DEAD = {"HA_URL": "http://127.0.0.1:9"}


@pytest.mark.parametrize(("argv", "code"), USAGE, ids=IDS)
def test_a_usage_error_keeps_the_contract_in_every_mode(house, argv, code):
    three_modes(house, argv, code=code, exit_code=2)


@pytest.mark.parametrize(("argv", "code"), USAGE, ids=IDS)
def test_a_usage_error_is_the_same_without_the_installation(house, argv, code):
    """Decided before any transport: unreachable and unconfigured answer identically."""
    unreachable = house.run(*argv, "--json", env=DEAD)
    contract(unreachable, "json", code=code, exit_code=2)
    unconfigured = house.run(*argv, "--json", unset=("HA_URL", "HA_TOKEN"))
    contract(unconfigured, "json", code=code, exit_code=2)


def test_lookups_that_fail_exit_1_with_a_next_step(house, snapshot):
    light = snapshot.light()
    domain = light.split(".")[0]
    cases = [
        (["state", "get", f"{domain}.zz_no_such_entity_zz"], "NO_SUCH_ENTITY"),
        (["state", "get", "nodothere"], "NO_SUCH_ENTITY"),
        (["entity", "get", f"{domain}.zz_no_such_entity_zz"], "NO_SUCH_ENTITY"),
        (["area", "get", "zz no such area zz"], "NO_SUCH_AREA"),
        (["entity", "list", "--area", "zz no such area zz"], "NO_SUCH_AREA"),
        (["state", "list", "--area", "zz no such area zz"], "NO_SUCH_AREA"),
        (["device", "list", "--area", "zz no such area zz"], "NO_SUCH_AREA"),
        (["sensor", "list", "--area", "zz no such area zz"], "NO_SUCH_AREA"),
        (["device", "get", "zz no such device zz"], "NO_SUCH_DEVICE"),
        (["entity", "list", "--device", "zznosuchdevice"], "NO_SUCH_DEVICE"),
        (["service", "get", f"{domain}.zz_no_such_service"], "NO_SUCH_SERVICE"),
        (["service", "get", "zznodomain.turn_on"], "NO_SUCH_DOMAIN"),
        (
            ["service", "call", f"{domain}.zz_no_such_service", "--target-entity", light],
            "NO_SUCH_SERVICE",
        ),
        (["statistics", "get", "sensor.zz_no_such_statistic_zz"], "NO_SUCH_STATISTIC"),
        (["history", "get", "Not An Entity Id"], "NO_SUCH_ENTITY"),
        (["logbook", "get", "--entity", "Not An Entity Id", "--start", "1h"], "NO_SUCH_ENTITY"),
        (["api", "/zz/no/such/path"], "NOT_FOUND"),
        (["template", "render", "--template", "{{ zz_no_such_function() }}"], "TEMPLATE_ERROR"),
    ]
    for argv, code in cases:
        three_modes(house, argv, code=code, exit_code=1)


def test_reads_of_awkward_subjects_still_answer(house, snapshot):
    """Unavailable, unregistered and upper-case subjects are answers, not errors."""
    light = snapshot.light()
    for argv in (["state", "get", light.upper()], ["area", "get", snapshot.biggest_area()]):
        assert house.run(*argv).code == 0, argv
    for finder in (snapshot.unavailable, snapshot.unregistered):
        try:
            subject = finder()
        except pytest.skip.Exception:
            continue
        assert house.run("state", "get", subject).code == 0
    try:
        disabled = snapshot.disabled()
    except pytest.skip.Exception:
        return
    assert house.ok("entity", "get", disabled)["entity"]["disabled"] is True
    result, document = house.doc("state", "get", disabled)
    assert (result.code, document["code"]) == (1, "NO_SUCH_ENTITY")
    # The next step leads to the registry, where a disabled entity can be found.
    assert any(f"entity get {disabled}" in line for line in document["help"])


def test_an_ambiguous_device_name_is_an_error_and_not_a_guess(house, snapshot):
    name = snapshot.duplicate_device_name()
    result, document = house.doc("device", "get", name)
    assert (result.code, document["code"]) == (1, "AMBIGUOUS_DEVICE")

"""P2: `--fields` and `--limit` on every list command, and what `count:` promises."""

from __future__ import annotations

import re

import pytest

from .harness import contract, counts

#: command, the key its rows are under, a filter that leaves it more than a few rows.
LISTS = [
    (["state", "list"], "states"),
    (["sensor", "list"], "sensors"),
    (["entity", "list"], "entities"),
    (["device", "list"], "devices"),
    (["statistics", "list"], "statistics"),
    (["logbook", "get", "--start", "6h"], "entries"),
]
IDS = [" ".join(command) for command, _ in LISTS]


def declared_fields(house, command) -> list:
    """The field list as `--help` prints it, so this suite cannot drift from the tool."""
    text = house.run(command[0], "--help").out
    match = re.search(r"--fields <a,b,c> \(from ([a-z_|]+)\)", text)
    assert match, f"{command[0]} --help declares no field list"
    return match.group(1).split("|")


@pytest.mark.parametrize(("command", "key"), LISTS, ids=IDS)
def test_each_declared_field_can_be_asked_for(house, command, key):
    fields = declared_fields(house, command)
    for selection in ([field] for field in fields):
        document = house.ok(*command, "--limit", "2", "--fields", ",".join(selection))
        for row in document[key]:
            assert list(row) == selection
    for selection in (fields, list(reversed(fields))):
        document = house.ok(*command, "--limit", "2", "--fields", ",".join(selection))
        for row in document[key]:
            assert list(row) == selection


@pytest.mark.parametrize(("command", "key"), LISTS, ids=IDS)
def test_an_unknown_field_lists_the_valid_ones(house, command, key):
    fields = declared_fields(house, command)
    result = house.run(*command, "--fields", "no_such_field", "--json")
    document = contract(result, "json", code="UNKNOWN_FIELD", exit_code=2)
    assert all(field in " ".join(document["help"]) for field in fields)


@pytest.mark.parametrize(("command", "key"), LISTS, ids=IDS)
@pytest.mark.parametrize("value", ["0", "-1", "abc", "2.5", "", "0x10"])
def test_a_bad_limit_is_a_usage_error(house, command, key, value):
    result = house.run(*command, f"--limit={value}", "--json")
    contract(result, "json", code="BAD_LIMIT", exit_code=2)


@pytest.mark.parametrize(("command", "key"), LISTS[:5], ids=IDS[:5])
def test_the_count_line_is_true(house, command, key):
    everything = house.ok(*command, "--limit", "100000")
    full = everything[key]
    if not full:
        pytest.skip("nothing to list")
    shown, matched, total = counts(everything["count"])
    assert shown == len(full) == matched <= total

    limited = house.ok(*command, "--limit", "3")
    shown, matched, total = counts(limited["count"])
    assert shown == len(limited[key]) <= matched <= total
    # A limit is a prefix of the full order, and the order is stable.
    assert limited[key] == [
        {name: row[name] for name in limited[key][0]} for row in full[: len(limited[key])]
    ]


def test_the_see_all_suggestion_lists_what_it_counted(house, snapshot):
    """The suggestion carries the filter, so running it returns the rows the count named."""
    import shlex

    domain = max(
        snapshot.domains(), key=lambda d: sum(1 for i in snapshot.state if i.startswith(d + "."))
    )
    area = snapshot.biggest_area()
    cases = [
        (["state", "list", "--domain", domain, "--limit", "2"], "states"),
        (["entity", "list", "--area", area, "--limit", "2"], "entities"),
        (["entity", "list", "--domain", domain, "--limit", "2"], "entities"),
        (["device", "list", "--area", area, "--limit", "1"], "devices"),
    ]
    checked = 0
    for argv, key in cases:
        document = house.ok(*argv)
        line = next((entry for entry in document["help"] if "to see all" in entry), None)
        if line is None:
            continue
        matched = int(line.rsplit(" ", 1)[1])
        suggested = shlex.split(line.split("`")[1])[1:]
        everything = house.ok(*suggested)
        assert len(everything[key]) == matched, f"{argv}: the suggestion returned other rows"
        assert everything[key][: len(document[key])] == document[key]
        checked += 1
    assert checked, "no list here was long enough to be limited"

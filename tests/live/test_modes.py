"""P1: every read, in every output mode, and its TOON read back by an independent decoder."""

from __future__ import annotations

import json

import pytest

from .harness import stable, toon_decode


def reads(snapshot) -> dict:
    """Read shapes for whatever this installation holds, keyed by a name with no data in it."""
    light = snapshot.light()
    area = snapshot.biggest_area()
    device = snapshot.devices[0]["id"]
    domain = light.split(".")[0]
    shapes = {
        "home": [],
        "state list": ["state", "list"],
        "state list domain": ["state", "list", "--domain", domain],
        "state list area": ["state", "list", "--area", area],
        "state list none": ["state", "list", "--domain", "no_such_domain"],
        "state list all fields": [
            "state",
            "list",
            "--fields",
            "entity_id,name,state,domain,last_changed,last_updated",
        ],
        "state get": ["state", "get", light],
        "state get full": ["state", "get", light, "--full"],
        "sensor list": ["sensor", "list"],
        "sensor list all": ["sensor", "list", "--all", "--limit", "500"],
        "service list": ["service", "list"],
        "service list domain": ["service", "list", "--domain", domain],
        "service get": ["service", "get", f"{domain}.turn_on"],
        "service call preview": ["service", "call", f"{domain}.turn_on", "--target-entity", light],
        "template": ["template", "render", "--template", "{{ 1 + 1 }}"],
        "entity list": ["entity", "list"],
        "entity list area": ["entity", "list", "--area", area],
        "entity list none": ["entity", "list", "--area", "none", "--limit", "5"],
        "entity get": ["entity", "get", light],
        "area list": ["area", "list"],
        "area get": ["area", "get", area],
        "device list": ["device", "list"],
        "device get": ["device", "get", device],
        "entity list device": ["entity", "list", "--device", device],
        "statistics list": ["statistics", "list"],
        "logbook": ["logbook", "get", "--start", "1h"],
        "history": ["history", "get", light, "--start", "6h"],
        "ws list": ["ws", "--list"],
        "ws entity list": ["ws", "entity.list"],
        "ws entity list full": ["ws", "entity.list", "--full"],
        "ws device list full": ["ws", "device.list", "--full"],
        "ws service list full": ["ws", "service.list", "--full"],
        "api config": ["api", "/config"],
        "api states full": ["api", "/states", "--full"],
        "api services full": ["api", "/services", "--full"],
        "doctor": ["doctor"],
        "context": ["context"],
        "version": ["--version"],
    }
    return shapes


#: Shapes whose two runs are not one answer: they time something.
TIMED = {"ping"}


def test_every_read_agrees_with_itself_across_the_modes(house, snapshot):
    failures = []
    for name, argv in reads(snapshot).items():
        decoded, document = stable(house, argv)
        if decoded != document:
            failures.append(name)
        human = house.run(*argv, "--human")
        if human.code != 0 or not human.out.strip() or human.err:
            failures.append(f"{name} (human)")
    assert not failures, f"TOON and JSON disagree, or --human failed, for: {failures}"


def test_the_mode_flag_works_wherever_it_is_put(house, snapshot):
    light = snapshot.light()
    trailing = house.run("state", "get", light, "--json")
    leading = house.run("--json", "state", "get", light)
    assert trailing.code == leading.code == 0
    assert (
        json.loads(trailing.out)["state"]["entity_id"]
        == json.loads(leading.out)["state"]["entity_id"]
    )


def test_output_ends_in_exactly_one_newline_and_stderr_is_empty(house, snapshot):
    for argv in (
        ["state", "list", "--limit", "3"],
        ["area", "list"],
        ["state", "get", snapshot.light()],
    ):
        for mode in ([], ["--json"], ["--human"]):
            result = house.run(*argv, *mode)
            assert result.code == 0
            assert result.out.endswith("\n") and not result.out.endswith("\n\n"), result.cmd
            assert result.err == "", result.cmd


def test_ping_answers_in_every_mode(house):
    for mode in ([], ["--json"], ["--human"]):
        result = house.run("ping", *mode)
        assert result.code == 0 and result.err == ""
    document = toon_decode(house.run("ping").out)
    assert document["ok"] is True and isinstance(document["latency_ms"], int)


def test_help_is_text_by_default_and_a_document_in_json(house):
    for argv in (["--help"], ["state", "--help"], ["service", "--help"]):
        text = house.run(*argv)
        human = house.run(*argv, "--human")
        assert text.code == 0 and text.out == human.out
        document = json.loads(house.run(*argv, "--json").out)
        assert document["usage"] and document["description"]


@pytest.mark.parametrize(
    "empty",
    [
        ["state", "list", "--domain", "no_such_domain"],
        ["entity", "list", "--search", "zz no such name zz"],
    ],
)
def test_an_empty_listing_is_a_list_in_json(house, empty):
    document = house.ok(*empty)
    rows = next(value for key, value in document.items() if key in ("states", "entities"))
    assert rows == []
    assert document["count"].startswith("0 ")

"""P12: the commands this tool tells an agent to run, run as written.

Documented examples include writes, and suggestions can too, so every one of
them runs with `HASS_AXI_READ_ONLY=1`: a write is refused rather than sent, and
a refusal is an acceptable answer here. What is not acceptable is a usage
error -- a documented or suggested command the parser itself rejects.
"""

from __future__ import annotations

import json
import re
import shlex

READ_ONLY = {"HASS_AXI_READ_ONLY": "1"}
COMMAND = re.compile(r"`(hass-axi[^`]*)`")
PLACEHOLDER = re.compile(r"<[^>]+>|\.\.\.")


def runnable(line: str):
    """The argv of a complete command, or ``None`` for a template or a shell pipeline."""
    if PLACEHOLDER.search(line) or "|" in line or "--template-file" in line or "--path" in line:
        return None
    try:
        argv = shlex.split(line)
    except ValueError:
        return None
    return argv[1:] if argv and argv[0] == "hass-axi" else None


def verdict(house, argv):
    result = house.run(*argv, "--json", env=READ_ONLY)
    try:
        document = json.loads(result.out)
    except ValueError:
        document = {}
    return result, document if isinstance(document, dict) else {}


def test_no_documented_example_is_a_usage_error(house):
    from hass_axi import cli

    examples = [line.strip() for line in cli.render_root_help().split("examples:")[1].splitlines()]
    for name in cli.COMMAND_ORDER:
        examples.extend(cli.command_specs()[name].examples)
    ran, rejected = 0, []
    for example in examples:
        argv = runnable(example)
        if argv is None or argv[:1] == ["setup"]:
            continue
        result, document = verdict(house, argv)
        ran += 1
        if result.code == 2 and document.get("code") != "READ_ONLY":
            rejected.append(example)
    assert ran > 30
    assert not rejected, f"documented examples the parser rejects: {rejected}"


def test_every_suggestion_the_tool_prints_can_be_run(house, snapshot):
    """Harvest the commands printed in help lines across reads and failures, and run each."""
    light = snapshot.light()
    name = snapshot.state[snapshot.multi_word_name()]["attributes"]["friendly_name"]
    area = snapshot.biggest_area()
    sources = [
        [],
        ["state", "list", "--limit", "2"],
        ["state", "list", "--domain", "light", "--limit", "1"],
        ["state", "get", light],
        ["state", "get", name],
        ["state", "get", "light.zz_no_such_entity_zz"],
        ["sensor", "list", "--limit", "2"],
        ["entity", "list", "--limit", "2"],
        ["entity", "list", "--area", area, "--limit", "1"],
        ["entity", "get", "light.zz_no_such_entity_zz"],
        ["entity", "get", name],
        ["area", "list"],
        ["area", "get", area],
        ["area", "get", snapshot.area[area]["name"][:-1] + "x"],
        ["entity", "list", "--area", snapshot.area[area]["name"][:-1] + "x"],
        ["device", "list", "--limit", "2"],
        ["device", "get", snapshot.devices[0]["id"]],
        ["device", "get", snapshot.devices[0]["id"][:8]],
        ["device", "get", "zz no such device zz"],
        ["service", "list"],
        ["service", "get", "light.turn_on"],
        ["service", "get", "light.turn_onn"],
        ["service", "get", "zznodomain.turn_on"],
        ["service", "call", "light.turn_on"],
        ["service", "call", "light.turn_on", "--target-entity", light],
        ["statistics", "list", "--limit", "2"],
        ["statistics", "get", "sensor.zz_no_such_statistic_zz"],
        ["history", "get", light],
        ["history", "get", name],
        ["logbook", "get", "--start", "1h"],
        ["ping"],
        ["doctor"],
        ["context"],
        ["ping", "extra"],
        ["area", "create", "Scratch Name"],
        ["sensor", "list", "--format", "json"],
        ["api", "BOGUS", "/config"],
        ["state", "list", "--domain"],
        ["state", "lst"],
        ["entity", "update", light],
    ]
    suggested: dict = {}
    for argv in sources:
        _, document = verdict(house, argv)
        for line in document.get("help") or []:
            for command in COMMAND.findall(line):
                parsed = runnable(command)
                if parsed is not None and parsed[:1] != ["setup"]:
                    suggested.setdefault(tuple(parsed), argv)
    assert len(suggested) > 25
    rejected = []
    for argv, source in suggested.items():
        result, document = verdict(house, list(argv))
        if result.code == 2 and document.get("code") != "READ_ONLY":
            rejected.append((" ".join(argv), document.get("code"), " ".join(source)))
    assert not rejected, f"suggestions the parser rejects: {rejected}"


def test_a_search_suggested_for_a_name_finds_that_name(house, snapshot):
    """`state get <a name>` suggests a search; running it returns the entity that was meant."""
    entity_id = snapshot.multi_word_name()
    name = snapshot.state[entity_id]["attributes"]["friendly_name"]
    result, document = house.doc("state", "get", name)
    assert (result.code, document["code"]) == (1, "NO_SUCH_ENTITY")
    argv = shlex.split(COMMAND.findall(document["help"][0])[0])[1:]
    found = house.ok(*argv, "--limit", "100000")
    assert entity_id in [row["entity_id"] for row in found["states"]]


def test_a_name_with_a_typographic_mark_is_found_by_typing_a_plain_one(house, snapshot):
    entity_id = snapshot.typographic()
    name = snapshot.state[entity_id]["attributes"]["friendly_name"]
    typed = name.translate(str.maketrans({"‘": "'", "’": "'", "ʼ": "'", "“": '"', "”": '"'}))
    assert typed != name
    found = house.ok("state", "list", "--search", typed, "--limit", "100000")
    assert entity_id in [row["entity_id"] for row in found["states"]]
    if entity_id in snapshot.entity:
        registry = house.ok("entity", "list", "--search", typed, "--limit", "100000")
        assert entity_id in [row["entity_id"] for row in registry["entities"]]


def test_an_area_with_a_typographic_mark_is_found_by_typing_a_plain_one(house, snapshot):
    marks = {"‘": "'", "’": "'", "ʼ": "'", "“": '"', "”": '"'}
    area = snapshot.first(
        lambda a: any(m in a["name"] for m in marks),
        snapshot.areas,
        "area name with a typographic mark",
    )
    typed = area["name"].translate(str.maketrans(marks))
    assert house.ok("area", "get", typed)["area"]["area_id"] == area["area_id"]
    by_typed = house.ok(
        "entity", "list", "--area", typed, "--limit", "100000", "--fields", "entity_id"
    )
    by_id = house.ok(
        "entity", "list", "--area", area["area_id"], "--limit", "100000", "--fields", "entity_id"
    )
    assert by_typed["entities"] == by_id["entities"]


def test_a_mistyped_area_is_answered_with_the_nearest_one(house, snapshot):
    area = snapshot.area[snapshot.biggest_area()]
    typo = area["name"][:-1] if len(area["name"]) > 4 else area["name"] + "x"
    for argv in (
        ["entity", "list", "--area", typo],
        ["state", "list", "--area", typo],
        ["device", "list", "--area", typo],
        ["sensor", "list", "--area", typo],
    ):
        result, document = house.doc(*argv)
        if result.code == 0:
            continue  # the shortened name is itself another area's
        assert document["code"] == "NO_SUCH_AREA"
        assert document["help"][0].startswith("did you mean: ")
        assert f"(id {area['area_id']})" in document["help"][0]
        assert not any("area create" in line for line in document["help"])


def test_the_home_view_names_counts_its_commands_return(house):
    document = house.ok()
    line = next((entry for entry in document.get("help", []) if "not reporting" in entry), None)
    if line is None:
        return
    unavailable = house.ok("state", "list", "--state", "unavailable", "--limit", "100000")
    unknown = house.ok("state", "list", "--state", "unknown", "--limit", "100000")
    numbers = [int(n) for n in re.findall(r"\d+", line)]
    # Entities come and go between three reads; the sentence is held to within a few.
    assert abs(numbers[1] - len(unavailable["states"])) <= 3, line
    assert abs(numbers[3] - len(unknown["states"])) <= 3, line

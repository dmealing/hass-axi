"""`floor list`, `label list`, and `--target-floor` / `--target-label` on `service call`.

The registries are the double's own, written from Home Assistant's: the `ground`
floor holds the hall, the hall's one device carries `example_label`, the lamp
carries it itself, and a third label is on nothing. A floor reaches entities
through the areas on it and a label through whatever carries it, which is the
reading `entities_targeted` in `tests/conftest.py` applies independently of the
client.
"""

from __future__ import annotations

import json


def test_floor_list_counts_the_areas_on_each_floor(run_cli, ws_env):
    code, out = run_cli(["floor", "list", "--json"], ws_env)
    assert code == 0
    doc = json.loads(out)
    assert [(f["floor_id"], f["areas"]) for f in doc["floors"]] == [
        ("ground", 1),
        ("example_upper_floor", 0),
    ]
    # `example_room` is on no floor, and the two counts account for every area.
    assert doc["areas_without_floor"] == 1


def test_label_list_counts_what_carries_each_label(run_cli, ws_env):
    code, out = run_cli(["label", "list", "--json"], ws_env)
    assert code == 0
    counts = {
        row["label_id"]: (row["entities"], row["devices"], row["areas"])
        for row in json.loads(out)["labels"]
    }
    assert counts == {
        "example_label": (1, 1, 0),
        "hall_label": (0, 0, 1),
        "unused_label": (0, 0, 0),
    }


def test_a_floor_target_reaches_the_entities_in_the_areas_on_it(
    run_cli, installation_env, rest_server
):
    code, out = run_cli(
        ["service", "call", "light.turn_on", "--target-floor", "ground", "--write"],
        installation_env,
    )
    assert code == 0
    assert "light.example_ceiling" in out
    assert "light.example_lamp" not in out
    posted = [r for r in rest_server.requests if r["path"] == "/api/services/light/turn_on"]
    assert posted[-1]["body"]["floor_id"] == ["ground"]


def test_a_label_target_reaches_an_entity_its_device_and_its_area(run_cli, installation_env):
    """The lamp carries `example_label` itself and the ceiling light's device does."""
    code, out = run_cli(
        ["service", "call", "light.turn_on", "--target-label", "example_label"],
        installation_env,
    )
    assert code == 0
    assert "would reach 2 entities" in out

    # `hall_label` is on the hall, which holds the ceiling light and nothing else.
    code, out = run_cli(
        ["service", "call", "light.turn_on", "--target-label", "hall_label"],
        installation_env,
    )
    assert code == 0
    assert "would reach 1 entity" in out


def test_a_floor_or_label_target_is_previewed_without_sending(
    run_cli, installation_env, rest_server
):
    for flag, value in (("--target-floor", "ground"), ("--target-label", "example_label")):
        code, _ = run_cli(["service", "call", "light.turn_on", flag, value], installation_env)
        assert code == 0
    assert not [r for r in rest_server.requests if r["path"] == "/api/services/light/turn_on"]


def test_a_floor_or_label_that_does_not_exist_is_reported_by_id(run_cli, installation_env):
    code, out = run_cli(
        ["service", "call", "light.turn_on", "--target-floor", "nowhere", "--write"],
        installation_env,
    )
    assert code == 1
    assert "no floor with id 'nowhere'" in out
    assert "hass-axi floor list" in out

    code, out = run_cli(
        ["service", "call", "light.turn_on", "--target-label", "nowhere", "--write"],
        installation_env,
    )
    assert code == 1
    assert "no label with id 'nowhere'" in out
    assert "hass-axi label list" in out


def test_a_floor_or_label_that_holds_nothing_is_not_a_success(run_cli, installation_env):
    for flag, value in (
        ("--target-floor", "example_upper_floor"),
        ("--target-label", "unused_label"),
    ):
        code, out = run_cli(
            ["service", "call", "light.turn_on", flag, value, "--write"], installation_env
        )
        assert code == 1
        assert "0 entities" in out

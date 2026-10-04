"""The typed registry writes: previewed by default, sent only with ``--write``.

`entity update`, `area create`, `area update` and `device update` follow the
rule `service call` set: nothing in this tool changes Home Assistant without
``--write``. The first half pins the preview -- it reads, shows what it would
send, and sends nothing. The second half runs the destructive cases the real
house must never be asked for -- create, rename, collide, delete, clear --
against a double that refuses the way Home Assistant's own handlers do.
"""

from __future__ import annotations

import json

import pytest

WRITE = "--write"
WRITE_TYPES = (
    "config/entity_registry/update",
    "config/area_registry/create",
    "config/area_registry/update",
    "config/device_registry/update",
)


def as_json(run_cli, argv, env):
    code, out = run_cli(["--json", *argv], env)
    return code, json.loads(out)


def writes(ws_server) -> list:
    return [c for c in ws_server.received if c["type"] in WRITE_TYPES]


def snapshot(ws_server) -> str:
    return json.dumps([ws_server.entities, ws_server.areas, ws_server.devices, ws_server.floors])


PREVIEWS = [
    (["entity", "update", "light.example_lamp", "--name", "Reading Lamp"], "would_change"),
    (["entity", "update", "light.example_lamp", "--area", "Example Hall"], "would_change"),
    (
        ["entity", "update", "light.example_lamp", "--new-id", "light.example_reading"],
        "would_change",
    ),
    (["area", "create", "--name", "Example Annex"], "would_create"),
    (["area", "update", "example_room", "--name", "Example Study"], "would_change"),
    (["area", "update", "example_room", "--floor", "ground"], "would_change"),
    (["device", "update", "device_one", "--name", "Example Fitting"], "would_change"),
    (["device", "update", "device_one", "--clear-area"], "would_change"),
]


@pytest.mark.parametrize(("argv", "key"), PREVIEWS)
def test_a_typed_write_without_the_write_flag_sends_nothing(run_cli, ws_env, ws_server, argv, key):
    before = snapshot(ws_server)
    code, doc = as_json(run_cli, argv, ws_env)
    assert code == 0
    assert doc["preview"] == "nothing was sent to Home Assistant"
    assert doc[key]
    assert any(WRITE in line for line in doc["help"])
    assert writes(ws_server) == []
    assert snapshot(ws_server) == before


@pytest.mark.parametrize(("argv", "key"), PREVIEWS)
def test_the_same_command_with_the_write_flag_sends_it(run_cli, ws_env, ws_server, argv, key):
    before = snapshot(ws_server)
    code, doc = as_json(run_cli, [*argv, WRITE], ws_env)
    assert code == 0
    assert "preview" not in doc
    assert len(writes(ws_server)) == 1
    assert snapshot(ws_server) != before


def test_a_preview_shows_the_stored_value_beside_the_new_one(run_cli, ws_env):
    code, doc = as_json(
        run_cli,
        ["area", "update", "example_hall", "--name", "Example Lobby", "--clear-icon"],
        ws_env,
    )
    assert code == 0
    assert doc["would_change"] == [
        {"field": "icon", "from": "mdi:door", "to": ""},
        {"field": "name", "from": "Example Hall", "to": "Example Lobby"},
    ]


def test_a_preview_resolves_what_the_write_would(run_cli, ws_env):
    """The area is looked up before the preview, so a typo fails there too."""
    code, doc = as_json(
        run_cli, ["entity", "update", "light.example_lamp", "--area", "Example Hall"], ws_env
    )
    assert doc["would_change"] == [
        {"field": "area_id", "from": "example_room", "to": "example_hall"}
    ]
    code, doc = as_json(
        run_cli, ["entity", "update", "light.example_lamp", "--area", "nowhere"], ws_env
    )
    assert (code, doc["code"]) == (1, "NO_SUCH_AREA")


def test_a_request_for_what_is_already_stored_needs_no_flag(run_cli, ws_env, ws_server):
    """Nothing to send, so nothing to preview: the answer is the same either way."""
    for extra in ([], [WRITE]):
        code, doc = as_json(
            run_cli, ["area", "update", "example_hall", "--icon", "mdi:door", *extra], ws_env
        )
        assert code == 0
        assert "already matches" in doc["updated"]
        assert "preview" not in doc
    assert writes(ws_server) == []


def test_a_read_only_session_can_preview_and_cannot_write(run_cli, ws_env, ws_server):
    env = {**ws_env, "HASS_AXI_READ_ONLY": "1"}
    argv = ["entity", "update", "light.example_lamp", "--name", "Reading Lamp"]
    code, doc = as_json(run_cli, argv, env)
    assert code == 0
    assert "read-only" in doc["preview"]
    assert any("Unset HASS_AXI_READ_ONLY" in line for line in doc["help"])

    code, doc = as_json(run_cli, [*argv, WRITE], env)
    assert (code, doc["code"]) == (2, "READ_ONLY")
    assert writes(ws_server) == []


@pytest.mark.parametrize(
    "argv",
    [
        ["entity", "update", "light.example_lamp", "--name", "X", WRITE],
        ["area", "create", "--name", "Example Annex", WRITE],
        ["area", "update", "example_room", "--name", "X", WRITE],
        ["device", "update", "device_one", "--name", "X", WRITE],
    ],
)
def test_a_read_only_write_is_refused_before_any_transport(run_cli, argv):
    code, doc = as_json(run_cli, argv, {"HASS_AXI_READ_ONLY": "1"})
    assert (code, doc["code"]) == (2, "READ_ONLY")


# -------------------------------------------------- destructive, and offline


def test_an_area_can_be_created_changed_and_deleted(run_cli, ws_env, ws_server):
    """The whole life of a scratch area, read back from the registry at every step."""
    before = json.dumps(ws_server.areas)

    code, doc = as_json(run_cli, ["area", "create", "--name", "Example Annex", WRITE], ws_env)
    assert (code, doc["created"]) == (0, True)
    area_id = doc["area"]["area_id"]
    assert area_id == "example_annex"

    # A second create reports the area that exists rather than making another.
    code, doc = as_json(run_cli, ["area", "create", "--name", "example annex", WRITE], ws_env)
    assert code == 0
    assert "already exists" in doc["created"]
    # One that differs only by a space is another name here and the same name
    # to Home Assistant, which refuses it: nothing is created and it says why.
    code, doc = as_json(run_cli, ["area", "create", "--name", "ExampleAnnex", WRITE], ws_env)
    assert code == 1
    assert "is already in use" in doc["error"]
    assert len([a for a in ws_server.areas if a["area_id"] == area_id]) == 1

    code, doc = as_json(
        run_cli,
        ["area", "update", area_id, "--icon", "mdi:sofa", "--floor", "ground", WRITE],
        ws_env,
    )
    assert (code, doc["updated"]) == (0, ["floor_id", "icon"])
    stored = next(a for a in ws_server.areas if a["area_id"] == area_id)
    assert (stored["icon"], stored["floor_id"]) == ("mdi:sofa", "ground")

    code, doc = as_json(
        run_cli, ["area", "update", area_id, "--name", 'Example, "Annex": [2]', WRITE], ws_env
    )
    assert code == 0
    code, doc = as_json(run_cli, ["area", "get", area_id], ws_env)
    assert doc["area"]["name"] == 'Example, "Annex": [2]'

    code, doc = as_json(
        run_cli, ["entity", "update", "light.example_lamp", "--area", area_id, WRITE], ws_env
    )
    assert (code, doc["area_id"]) == (0, area_id)

    # Deleting is only reachable through the escape hatch, previewed first.
    code, doc = as_json(run_cli, ["ws", "area.delete", "--param", f"area_id={area_id}"], ws_env)
    assert code == 0 and "preview" in doc
    assert any(a["area_id"] == area_id for a in ws_server.areas)
    code, doc = as_json(
        run_cli, ["ws", "area.delete", "--param", f"area_id={area_id}", WRITE], ws_env
    )
    assert code == 0
    # Home Assistant clears the deleted area from what pointed at it.
    code, doc = as_json(run_cli, ["entity", "get", "light.example_lamp"], ws_env)
    assert doc["entity"]["area_id"] != area_id
    code, doc = as_json(run_cli, ["area", "get", area_id], ws_env)
    assert (code, doc["code"]) == (1, "NO_SUCH_AREA")

    # Deleting it again is refused the way Home Assistant refuses it.
    code, doc = as_json(
        run_cli, ["ws", "area.delete", "--param", f"area_id={area_id}", WRITE], ws_env
    )
    assert (code, doc["code"]) == (1, "API_ERROR")
    assert "invalid_info" in doc["error"] and "Area ID doesn't exist" in doc["error"]
    assert doc["help"]

    assert json.dumps(ws_server.areas) == before


def test_renaming_an_area_onto_another_is_refused(run_cli, ws_env, ws_server):
    before = json.dumps(ws_server.areas)
    code, doc = as_json(
        run_cli, ["area", "update", "example_room", "--name", "example hall", WRITE], ws_env
    )
    assert code == 1
    assert "is already in use" in doc["error"]
    assert json.dumps(ws_server.areas) == before


def test_an_entity_can_be_renamed_moved_and_put_back(run_cli, ws_env, ws_server):
    before = json.dumps(ws_server.entities)
    code, doc = as_json(
        run_cli,
        [
            "entity",
            "update",
            "light.example_lamp",
            "--new-id",
            "light.example_reading",
            "--name",
            "Example’s Reading, Lamp",
            WRITE,
        ],
        ws_env,
    )
    assert (code, doc["entity"]) == (0, "light.example_reading")
    assert doc["name"] == "Example’s Reading, Lamp"
    code, doc = as_json(run_cli, ["entity", "list", "--search", "example's reading"], ws_env)
    assert [row["entity_id"] for row in doc["entities"]] == ["light.example_reading"]

    stored = json.loads(before)
    original = next(e for e in stored if e["entity_id"] == "light.example_lamp")
    restore = ["entity", "update", "light.example_reading", "--new-id", "light.example_lamp"]
    restore += ["--name", original["name"]] if original.get("name") else ["--clear-name"]
    code, doc = as_json(run_cli, [*restore, WRITE], ws_env)
    assert code == 0
    assert json.dumps(ws_server.entities) == before


@pytest.mark.parametrize(
    ("new_id", "reason"),
    [
        ("light.example_ceiling", "already registered"),
        ("switch.example_lamp", "same domain"),
        ("light.Bad Id", "Invalid entity ID"),
    ],
)
def test_an_entity_id_change_home_assistant_refuses_is_refused(
    run_cli, ws_env, ws_server, new_id, reason
):
    before = json.dumps(ws_server.entities)
    code, doc = as_json(
        run_cli, ["entity", "update", "light.example_lamp", "--new-id", new_id, WRITE], ws_env
    )
    assert code == 1
    assert reason in doc["error"]
    assert doc["help"]
    assert json.dumps(ws_server.entities) == before


def test_an_entity_can_be_removed_through_the_escape_hatch(run_cli, ws_env, ws_server):
    argv = ["ws", "--raw", "config/entity_registry/remove", "--param"]
    target = "entity_id=sensor.example_disabled_probe"
    code, doc = as_json(run_cli, [*argv, target], ws_env)
    assert code == 0 and "preview" in doc
    assert any(e["entity_id"] == "sensor.example_disabled_probe" for e in ws_server.entities)

    code, doc = as_json(run_cli, [*argv, target, WRITE], ws_env)
    assert code == 0
    assert not any(e["entity_id"] == "sensor.example_disabled_probe" for e in ws_server.entities)

    code, doc = as_json(run_cli, [*argv, target, WRITE], ws_env)
    assert (code, doc["code"]) == (1, "NOT_FOUND")
    assert doc["help"]


def test_a_floor_can_be_created_used_and_deleted(run_cli, ws_env, ws_server):
    raw = ["ws", "--raw"]
    code, doc = as_json(
        run_cli,
        [
            *raw,
            "config/floor_registry/create",
            "--param",
            "name=Example Attic",
            "--param",
            "level=2",
            WRITE,
        ],
        ws_env,
    )
    assert code == 0
    floor_id = doc["result"]["floor_id"]
    assert floor_id == "example_attic"

    code, doc = as_json(
        run_cli,
        [*raw, "config/floor_registry/create", "--param", "name=example attic", WRITE],
        ws_env,
    )
    assert code == 1 and "is already in use" in doc["error"]

    code, doc = as_json(
        run_cli, ["area", "update", "example_room", "--floor", "Example Attic", WRITE], ws_env
    )
    assert (code, doc["area"]["floor_id"]) == (0, floor_id)

    code, doc = as_json(
        run_cli,
        [*raw, "config/floor_registry/delete", "--param", f"floor_id={floor_id}", WRITE],
        ws_env,
    )
    assert code == 0
    # The area registry drops a deleted floor from every area that was on it.
    code, doc = as_json(run_cli, ["area", "get", "example_room"], ws_env)
    assert doc["area"]["floor_id"] == ""

    code, doc = as_json(
        run_cli,
        [*raw, "config/floor_registry/delete", "--param", f"floor_id={floor_id}", WRITE],
        ws_env,
    )
    assert code == 1 and "Floor ID doesn't exist" in doc["error"]


def test_a_device_can_be_renamed_moved_and_put_back(run_cli, ws_env, ws_server):
    before = json.dumps(ws_server.devices)
    code, doc = as_json(
        run_cli,
        [
            "device",
            "update",
            "device_one",
            "--name",
            "Example Fitting",
            "--area",
            "Example Hall",
            WRITE,
        ],
        ws_env,
    )
    assert (code, doc["name"], doc["area_id"]) == (0, "Example Fitting", "example_hall")
    code, doc = as_json(
        run_cli,
        ["device", "update", "Example Fitting", "--clear-name", "--area", "example_room", WRITE],
        ws_env,
    )
    assert code == 0
    assert json.dumps(ws_server.devices) == before


def test_updating_a_device_that_does_not_exist_answers_as_home_assistant_does(
    run_cli, ws_env, ws_server
):
    """An uncaught `KeyError` upstream: a fixed `unknown_error`, and nothing stored."""
    before = snapshot(ws_server)
    code, doc = as_json(
        run_cli,
        ["ws", "device.update", "--param", "device_id=nowhere", "--param", "name_by_user=X", WRITE],
        ws_env,
    )
    assert (code, doc["code"]) == (1, "API_ERROR")
    assert doc["help"]
    assert snapshot(ws_server) == before


def test_a_write_of_the_wrong_type_is_refused_by_the_schema(run_cli, ws_env, ws_server):
    before = snapshot(ws_server)
    code, doc = as_json(
        run_cli,
        ["ws", "area.update", "--param", "area_id=example_room", "--param", "name=7", WRITE],
        ws_env,
    )
    assert (code, doc["code"]) == (1, "INVALID_FORMAT")
    assert snapshot(ws_server) == before


def test_a_service_call_changes_state_and_a_second_one_has_nothing_to_do(
    run_cli, installation_env, rest_server
):
    argv = ["service", "call", "light.turn_off", "--target-entity", "light.example_lamp"]
    before = next(s for s in rest_server.state["states"] if s["entity_id"] == "light.example_lamp")
    assert before["state"] == "on"

    code, doc = as_json(run_cli, argv, installation_env)
    assert code == 0 and "preview" in doc
    assert before["state"] == "on"

    code, doc = as_json(run_cli, [*argv, WRITE], installation_env)
    assert code == 0
    assert doc["changed"][0]["state"] == "off"

    code, doc = as_json(run_cli, [*argv, WRITE], installation_env)
    assert code == 0
    assert "0 states changed" in doc["changed"]

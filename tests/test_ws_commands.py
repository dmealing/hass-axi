"""Registry commands, exercised against a real local WebSocket server.

These run the full protocol: the auth handshake, id correlation, and the
command/result exchange. Nothing is mocked, so the client's framing and error
translation are actually covered.
"""

from __future__ import annotations

import time

import pytest

from conftest import FAKE_TOKEN


def test_entity_list_resolves_area_names_in_the_default_view(run_cli, ws_env):
    code, out = run_cli(["entity", "list"], ws_env)
    assert code == 0
    assert "entities[11]{entity_id,name,area}:" in out
    assert "light.example_lamp,Example Lamp,Example Room" in out


def test_entity_list_reports_a_name_for_an_entry_that_carries_none(run_cli, ws_env):
    # There is no `original_name` to fall back to here, which is the ordinary
    # case: the name is the device's, and reading the entity row alone leaves
    # this column empty on most of a real registry.
    _, out = run_cli(["entity", "list"], ws_env)
    assert "light.example_ceiling,Example Ceiling," in out


def test_entity_inherits_its_device_area_when_it_has_none_of_its_own(run_cli, ws_env):
    code, out = run_cli(["entity", "list", "--fields", "entity_id,area,area_id"], ws_env)
    assert code == 0
    # light.example_ceiling has no area_id but its device sits in Example Hall.
    assert "light.example_ceiling,Example Hall,example_hall" in out


def test_entity_list_filters_by_area_name_or_id(run_cli, ws_env):
    _, by_name = run_cli(["entity", "list", "--area", "Example Room"], ws_env)
    _, by_id = run_cli(["entity", "list", "--area", "example_room"], ws_env)
    assert "light.example_lamp" in by_name and "light.example_ceiling" not in by_name
    assert by_name.splitlines()[1:] == by_id.splitlines()[1:]


def test_entity_list_finds_entities_with_no_area(run_cli, ws_env):
    code, out = run_cli(["entity", "list", "--area", "none"], ws_env)
    assert code == 0
    assert "sensor.example_temperature" in out
    assert "light.example_lamp" not in out


def test_entity_list_rejects_an_unknown_area_with_a_way_forward(run_cli, ws_env):
    code, out = run_cli(["entity", "list", "--area", "Nowhere"], ws_env)
    assert code == 1
    assert "no area with id or name 'Nowhere'" in out
    assert "hass-axi area list" in out


def test_entity_list_filters_by_domain_and_platform_and_search(run_cli, ws_env):
    _, out = run_cli(["entity", "list", "--domain", "sensor"], ws_env)
    assert "sensor.example_temperature" in out and "light.example_lamp" not in out
    _, out = run_cli(["entity", "list", "--platform", "demo"], ws_env)
    assert "count: 6 of 6 matched (11 total)" in out
    _, out = run_cli(["entity", "list", "--search", "lamp"], ws_env)
    assert "light.example_lamp" in out and "sensor.example" not in out


def test_entity_list_states_the_zero_explicitly(run_cli, ws_env):
    code, out = run_cli(["entity", "list", "--domain", "vacuum"], ws_env)
    assert code == 0
    assert "entities: 0 registry entries found in domain vacuum" in out


def test_entity_get_shows_where_the_area_came_from(run_cli, ws_env):
    code, out = run_cli(["entity", "get", "light.example_lamp"], ws_env)
    assert code == 0
    assert "area_source: entity" in out
    code, out = run_cli(["entity", "get", "light.example_ceiling"], ws_env)
    assert "area_source: device" in out


def test_entity_get_on_a_missing_entry_suggests_a_search(run_cli, ws_env):
    code, out = run_cli(["entity", "get", "light.absent"], ws_env)
    assert code == 1
    assert "no registry entry for light.absent" in out
    assert "--search absent" in out


def test_entity_update_sets_the_name_and_the_area(run_cli, ws_env, ws_server):
    code, out = run_cli(
        [
            "entity",
            "update",
            "light.example_ceiling",
            "--name",
            "Reading Lamp",
            "--area",
            "Example Room",
        ],
        ws_env,
    )
    assert code == 0
    assert "updated[2]: area_id,name" in out
    updates = [c for c in ws_server.received if c["type"] == "config/entity_registry/update"]
    assert updates[0]["name"] == "Reading Lamp"
    assert updates[0]["area_id"] == "example_room"
    assert ws_server.entities[1]["name"] == "Reading Lamp"


def test_entity_update_reports_the_area_inherited_from_the_device(run_cli, ws_env):
    # light.example_ceiling has no area of its own; its device sits in Example
    # Hall. The update response is what an agent reads to decide whether the
    # entity still needs placing, so an empty area here reads as "unassigned"
    # and invites a helpful reassignment of an entity that was never homeless.
    code, out = run_cli(
        ["entity", "update", "light.example_ceiling", "--name", "Reading Lamp"], ws_env
    )
    assert code == 0
    assert "updated[1]: name" in out
    assert "name: Reading Lamp" in out
    assert "area: Example Hall" in out
    assert "area_id: example_hall" in out
    assert "area_source: device" in out


def test_entity_update_and_entity_get_agree_about_the_area(run_cli, ws_env):
    """The two views are built from the same row, so they cannot drift apart."""
    _, updated = run_cli(
        ["entity", "update", "light.example_ceiling", "--icon", "mdi:lamp"], ws_env
    )
    _, fetched = run_cli(["entity", "get", "light.example_ceiling"], ws_env)

    def area_lines(text):
        wanted = ("area:", "area_id:", "area_source:")
        return [line.strip() for line in text.splitlines() if line.strip().startswith(wanted)]

    assert (
        area_lines(updated)
        == area_lines(fetched)
        == ["area: Example Hall", "area_id: example_hall", "area_source: device"]
    )


def test_entity_update_reports_the_inherited_area_in_json_too(run_cli, ws_env):
    import json

    code, out = run_cli(
        ["--json", "entity", "update", "light.example_ceiling", "--name", "Reading Lamp"], ws_env
    )
    assert code == 0
    doc = json.loads(out)
    assert doc["area"] == "Example Hall"
    assert doc["area_id"] == "example_hall"


def test_a_no_op_update_reports_the_inherited_area_as_well(run_cli, ws_env, ws_server):
    # --clear-name on an entity that has no name override changes nothing, so
    # this takes the no-op branch, which made the same wrong area claim.
    code, out = run_cli(["entity", "update", "light.example_ceiling", "--clear-name"], ws_env)
    assert code == 0
    assert "no change made" in out
    assert "area: Example Hall" in out
    assert "area_id: example_hall" in out
    assert "area_source: device" in out
    assert [c for c in ws_server.received if c["type"] == "config/entity_registry/update"] == []


def test_entity_update_reports_no_area_when_there_genuinely_is_none(run_cli, ws_env):
    # sensor.example_temperature has neither an area nor a device, so an empty
    # area is the truth here rather than a lost inheritance.
    code, out = run_cli(
        ["entity", "update", "sensor.example_temperature", "--name", "Hall Sensor"], ws_env
    )
    assert code == 0
    assert 'area: ""' in out
    assert 'area_source: ""' in out


def test_the_double_answers_an_update_with_the_stored_entry_not_the_request(run_cli, ws_env):
    """A double that echoed the request could not contradict a wrong client.

    The answer carries fields the request never mentioned, and an `area_id`
    that is still null because this entity's area belongs to its device.
    """
    import json

    code, out = run_cli(
        [
            "--json",
            "ws",
            "entity.update",
            "--param",
            "entity_id=light.example_ceiling",
            "--param",
            "name=Reading Lamp",
        ],
        ws_env,
    )
    assert code == 0
    entry = json.loads(out)["result"]["entity_entry"]
    assert entry["name"] == "Reading Lamp"
    assert entry["platform"] == "demo"
    assert entry["unique_id"] == "unique-two"
    assert entry["device_id"] == "device_two"
    assert entry["area_id"] is None


def test_the_double_rejects_a_key_the_api_does_not_declare(run_cli, ws_env):
    # Home Assistant validates every command against a PREVENT_EXTRA schema.
    code, out = run_cli(
        [
            "ws",
            "--raw",
            "config/entity_registry/update",
            "--param",
            "entity_id=light.example_lamp",
            "--param",
            "nickname=Nope",
        ],
        ws_env,
    )
    assert code == 1
    assert "rejected the arguments" in out
    assert "INVALID_FORMAT" in out
    assert "nickname" in out


def test_entity_update_is_idempotent(run_cli, ws_env, ws_server):
    code, out = run_cli(
        ["entity", "update", "light.example_lamp", "--name", "Example Lamp"], ws_env
    )
    assert code == 0
    assert "no change made" in out
    assert [c for c in ws_server.received if c["type"] == "config/entity_registry/update"] == []


def test_entity_update_can_clear_the_name_and_the_area(run_cli, ws_env, ws_server):
    code, _ = run_cli(
        ["entity", "update", "light.example_lamp", "--clear-name", "--clear-area"], ws_env
    )
    assert code == 0
    update = next(c for c in ws_server.received if c["type"] == "config/entity_registry/update")
    assert update["name"] is None and update["area_id"] is None


def test_entity_update_needs_something_to_change(run_cli, ws_env):
    code, out = run_cli(["entity", "update", "light.example_lamp"], ws_env)
    assert code == 2
    assert "nothing to update" in out


def test_entity_update_rejects_conflicting_area_flags(run_cli, ws_env):
    code, out = run_cli(
        ["entity", "update", "light.example_lamp", "--area", "example_room", "--clear-area"], ws_env
    )
    assert code == 2
    assert "mutually exclusive" in out


@pytest.mark.parametrize(
    "argv",
    [
        ["entity", "update", "light.example_lamp", "--name", "Reading Lamp", "--clear-name"],
        ["entity", "update", "light.example_lamp", "--icon", "mdi:lamp", "--clear-icon"],
        ["area", "update", "example_room", "--icon", "mdi:sofa", "--clear-icon"],
        ["area", "update", "example_room", "--floor", "ground", "--clear-floor"],
        ["device", "update", "device_two", "--name", "Renamed", "--clear-name"],
        ["device", "update", "device_two", "--area", "example_room", "--clear-area"],
    ],
)
def test_update_rejects_a_set_flag_paired_with_its_clear(run_cli, ws_env, argv):
    code, out = run_cli(argv, ws_env)
    assert code == 2
    assert "mutually exclusive" in out
    assert "CONFLICTING_FLAGS" in out


def test_area_list_counts_entities_including_device_inheritance(run_cli, ws_env):
    code, out = run_cli(["area", "list"], ws_env)
    assert code == 0
    assert "areas[2]{area_id,name,entities,devices,floor_id}:" in out
    assert "example_hall,Example Hall,2,1,ground" in out
    assert "example_room,Example Room,3,2," in out


def test_area_get_accepts_a_name_as_well_as_an_id(run_cli, ws_env):
    _, by_id = run_cli(["area", "get", "example_room"], ws_env)
    _, by_name = run_cli(["area", "get", "Example Room"], ws_env)
    assert by_id == by_name
    assert "name: Example Room" in by_id


def test_area_create_makes_a_new_area(run_cli, ws_env, ws_server):
    code, out = run_cli(["area", "create", "--name", "Example Study"], ws_env)
    assert code == 0
    assert "area_id: example_study" in out
    assert any(a["name"] == "Example Study" for a in ws_server.areas)


def test_area_create_is_idempotent(run_cli, ws_env, ws_server):
    code, out = run_cli(["area", "create", "--name", "Example Room"], ws_env)
    assert code == 0
    assert "already exists" in out
    assert [c for c in ws_server.received if c["type"] == "config/area_registry/create"] == []


def test_area_create_requires_a_name(run_cli, ws_env):
    code, out = run_cli(["area", "create"], ws_env)
    assert code == 2
    assert "--name is required" in out


def test_area_update_renames(run_cli, ws_env, ws_server):
    code, out = run_cli(["area", "update", "Example Room", "--name", "Example Study"], ws_env)
    assert code == 0
    assert "updated[1]: name" in out
    assert ws_server.areas[0]["name"] == "Example Study"


def test_area_update_is_idempotent(run_cli, ws_env, ws_server):
    code, out = run_cli(["area", "update", "example_room", "--name", "Example Room"], ws_env)
    assert code == 0
    assert "no change made" in out
    assert [c for c in ws_server.received if c["type"] == "config/area_registry/update"] == []


def test_area_update_needs_something_to_change(run_cli, ws_env):
    code, out = run_cli(["area", "update", "example_room"], ws_env)
    assert code == 2
    assert "nothing to update" in out


def test_device_list_shows_areas_and_entity_counts(run_cli, ws_env):
    code, out = run_cli(["device", "list"], ws_env)
    assert code == 0
    assert "devices[4]{device_id,name,area}:" in out
    assert "device_two,Example Ceiling,Example Hall" in out


def test_device_list_works_without_the_subcommand_name(run_cli, ws_env):
    code, out = run_cli(["device"], ws_env)
    assert code == 0
    assert "devices[4]" in out


def test_ws_list_needs_no_connection(run_cli):
    code, out = run_cli(["ws", "--list"], {})
    assert code == 0
    assert "entity.update,config/entity_registry/update" in out


def test_ws_sends_a_declared_command(run_cli, ws_env, ws_server):
    code, out = run_cli(["ws", "area.list"], ws_env)
    assert code == 0
    assert "type: config/area_registry/list" in out
    assert ws_server.received[0]["type"] == "config/area_registry/list"


def test_ws_passes_parameters_through(run_cli, ws_env, ws_server):
    code, _ = run_cli(
        ["ws", "area.update", "--param", "area_id=example_room", "--param", "name=Example Study"],
        ws_env,
    )
    assert code == 0
    assert ws_server.received[0]["name"] == "Example Study"


def test_ws_requires_declared_parameters_up_front(run_cli, ws_env, ws_server):
    code, out = run_cli(["ws", "area.update"], ws_env)
    assert code == 2
    assert "area.update needs area_id" in out
    assert ws_server.received == []


def test_ws_rejects_an_undeclared_name_and_lists_what_exists(run_cli, ws_env):
    code, out = run_cli(["ws", "nope"], ws_env)
    assert code == 2
    assert "unknown websocket command: nope" in out
    assert "entity.list" in out


def test_ws_points_a_raw_type_at_the_raw_flag(run_cli, ws_env):
    code, out = run_cli(["ws", "config/floor_registry/list"], ws_env)
    assert code == 2
    assert "--raw config/floor_registry/list" in out


def test_ws_raw_sends_an_undeclared_type(run_cli, ws_env, ws_server):
    code, _out = run_cli(["ws", "--raw", "config/floor_registry/list"], ws_env)
    assert code == 0
    assert ws_server.received[0]["type"] == "config/floor_registry/list"


def test_a_command_error_is_translated_not_leaked(run_cli, ws_env, ws_server):
    ws_server.fail_next = {"code": "invalid_format", "message": "expected str for name"}
    code, out = run_cli(["area", "list"], ws_env)
    assert code == 1
    assert "rejected the arguments" in out
    assert "Traceback" not in out


def test_an_unauthorized_command_names_the_permission_problem(run_cli, ws_env, ws_server):
    ws_server.fail_next = {"code": "unauthorized", "message": "nope"}
    code, out = run_cli(["area", "list"], ws_env)
    assert code == 1
    assert "not permitted" in out
    assert "administrator" in out


def test_a_rejected_token_fails_the_handshake_cleanly(run_cli, ws_server):
    env = {"HA_URL": f"http://127.0.0.1:{ws_server.port}", "HA_TOKEN": "wrong-token-value"}
    code, out = run_cli(["entity", "list"], env)
    assert code == 1
    assert "rejected the access token" in out
    assert "wrong-token-value" not in out


def test_an_unreachable_websocket_reports_the_transport(run_cli):
    env = {"HA_URL": "http://127.0.0.1:1", "HA_TOKEN": FAKE_TOKEN}
    code, out = run_cli(["entity", "list"], env)
    assert code == 1
    assert "could not open a WebSocket" in out
    assert "Traceback" not in out


def test_a_socket_closed_between_commands_reports_the_transport_not_a_traceback(
    run_cli, ws_env, ws_server
):
    # The server answers entity.list and area.list, then drops the connection;
    # whatever the client does next has to fail as structured output.
    ws_server.close_after = 2
    code, out = run_cli(
        ["entity", "update", "light.example_lamp", "--name", "Renamed Lamp"], ws_env
    )
    assert code == 1
    assert "error:" in out
    assert "WebSocket connection to Home Assistant closed" in out
    assert "WS_CLOSED" in out
    assert "Traceback" not in out


def test_writing_to_a_closed_connection_is_a_structured_failure(ws_env, ws_server):
    from hass_axi.cli import Context
    from hass_axi.errors import ConnectionFailed

    ws_server.close_after = 1
    ctx = Context(ws_env)
    with ctx.ws() as client:
        assert len(client.run("entity.list")) == 11
        # Give the client's reader time to observe the reset, so the write is
        # the first operation to touch the dead socket.
        time.sleep(0.1)
        with pytest.raises(ConnectionFailed) as raised:
            client.run("area.list")
    assert raised.value.code == "WS_CLOSED"


def test_device_list_filters_by_area(run_cli, ws_env):
    code, out = run_cli(["device", "list", "--area", "Example Room"], ws_env)
    assert code == 0
    assert "device_one" in out and "device_two" not in out
    assert "count: 2 of 2 matched (4 total)" in out


def test_device_list_finds_devices_with_no_area(run_cli, ws_env):
    # device_three is in no area, which is why the entities it supplies are in
    # none either -- the fixture set carries the case rather than inventing it.
    code, out = run_cli(["device", "list", "--area", "none"], ws_env)
    assert code == 0
    assert "device_three" in out and "device_one" not in out


def test_device_list_searches_name_manufacturer_and_model(run_cli, ws_env):
    _, by_model = run_cli(["device", "list", "--search", "Model Y"], ws_env)
    assert "device_two" in by_model and "device_one" not in by_model

    _, by_maker = run_cli(["device", "list", "--search", "Example Co"], ws_env)
    assert "device_one" in by_maker and "device_two" in by_maker


def test_device_list_states_the_zero_explicitly(run_cli, ws_env):
    code, out = run_cli(["device", "list", "--search", "nothing-matches"], ws_env)
    assert code == 0
    assert "0 devices found" in out
    assert "4 devices in the device registry" in out


def test_device_list_rejects_an_unknown_area(run_cli, ws_env):
    code, out = run_cli(["device", "list", "--area", "Nowhere"], ws_env)
    assert code == 1
    assert "no area with id or name" in out


# ------------------------------------------- the device registry as a write surface


#: `device list` with no flags, pinned in full. The list surface predates the
#: write surface and scripts already read it, so `get` and `update` were added
#: by routing all three through one row builder rather than by reshaping this.
DEFAULT_DEVICE_LIST = """count: 4 of 4 total
devices[4]{device_id,name,area}:
  device_one,Example Lamp Fitting,Example Room
  device_two,Example Ceiling,Example Hall
  device_three,Example Hub,""
  device_four,Example Doorway,Example Room
help[1]:
  Run `hass-axi entity list --area <id|name>` to see the entities in an area
"""


def test_device_list_is_unchanged_by_the_write_surface(run_cli, ws_env):
    code, out = run_cli(["device", "list"], ws_env)
    assert code == 0
    assert out == DEFAULT_DEVICE_LIST


def test_device_get_accepts_an_id_or_the_displayed_name(run_cli, ws_env):
    _, by_id = run_cli(["device", "get", "device_two"], ws_env)
    _, by_name = run_cli(["device", "get", "Example Ceiling"], ws_env)
    assert by_id == by_name
    assert "device_id: device_two" in by_id
    assert "entities: 1" in by_id


def test_device_get_resolves_a_name_case_insensitively(run_cli, ws_env):
    code, out = run_cli(["device", "get", "example ceiling"], ws_env)
    assert code == 0
    assert "device_id: device_two" in out


def test_device_get_reports_both_names_and_which_one_is_showing(run_cli, ws_env):
    """`name` and `name_by_user` are different fields and only one is writable.

    device_two carries a user rename, so what Home Assistant displays is not the
    integration's `Ceiling Fitting` at all; device_three carries none.
    """
    _, renamed = run_cli(["device", "get", "device_two"], ws_env)
    assert "name: Example Ceiling" in renamed
    assert "name_by_user: Example Ceiling" in renamed
    assert "name_source: user" in renamed

    _, untouched = run_cli(["device", "get", "device_three"], ws_env)
    assert "name: Example Hub" in untouched
    assert 'name_by_user: ""' in untouched
    assert "name_source: integration" in untouched


def test_device_get_says_where_the_area_stands(run_cli, ws_env):
    _, placed = run_cli(["device", "get", "device_two"], ws_env)
    assert "area: Example Hall" in placed
    assert "area_source: device" in placed

    # device_three is in no area, which is why the entities it supplies are in
    # none either.
    _, unplaced = run_cli(["device", "get", "device_three"], ws_env)
    assert 'area: ""' in unplaced
    assert 'area_source: ""' in unplaced


def test_device_get_names_a_dangling_area_id_rather_than_implying_a_placement(
    run_cli, ws_env, ws_server
):
    # Home Assistant takes `ws device.update --param area_id=<typo>` without
    # complaint. `area` is empty either way, and "in no area" and "holding an id
    # nothing answers to" are different facts with different fixes.
    ws_server.devices[0]["area_id"] = "no_such_area"
    code, out = run_cli(["device", "get", "device_one"], ws_env)
    assert code == 0
    assert "area_id: no_such_area" in out
    assert "area_source: no area has this id" in out


def test_device_get_on_a_missing_device_offers_a_way_to_find_it(run_cli, ws_env):
    code, out = run_cli(["device", "get", "Example Absent"], ws_env)
    assert code == 1
    assert "no device with id or name 'Example Absent'" in out
    assert "NO_SUCH_DEVICE" in out
    assert 'Run `hass-axi device list --search "Example Absent"` to find it' in out
    assert "Traceback" not in out


def test_device_get_refuses_a_name_two_devices_answer_to(run_cli, ws_env, ws_server):
    """An ambiguous name is an error rather than a guess, as it is on an area."""
    ws_server.devices.append(
        {
            "id": "device_five",
            "name": "Example Ceiling",
            "name_by_user": None,
            "disabled_by": None,
            "area_id": None,
            "manufacturer": "Example Co",
            "model": "Model W",
        }
    )
    code, out = run_cli(["device", "get", "Example Ceiling"], ws_env)
    assert code == 1
    assert "matches more than one device" in out
    assert "AMBIGUOUS_DEVICE" in out
    assert "device_two" in out and "device_five" in out


def test_an_id_wins_over_a_name_that_happens_to_look_like_one(run_cli, ws_env, ws_server):
    # A device whose displayed name is another device's id must not shadow it.
    ws_server.devices.append(
        {
            "id": "device_five",
            "name": "device_two",
            "name_by_user": None,
            "disabled_by": None,
            "area_id": None,
            "manufacturer": "Example Co",
            "model": "Model W",
        }
    )
    code, out = run_cli(["device", "get", "device_two"], ws_env)
    assert code == 0
    assert "device_id: device_two" in out


def test_device_update_sets_name_by_user_and_the_area(run_cli, ws_env, ws_server):
    code, out = run_cli(
        ["device", "update", "device_two", "--name", "Hall Ceiling", "--area", "Example Room"],
        ws_env,
    )
    assert code == 0
    assert "updated[2]: area_id,name_by_user" in out
    update = next(c for c in ws_server.received if c["type"] == "config/device_registry/update")
    # The user override is what is written; the integration's own name is not a
    # field Home Assistant accepts here at all.
    assert update["name_by_user"] == "Hall Ceiling"
    assert "name" not in update
    assert update["area_id"] == "example_room"
    assert ws_server.devices[1]["name_by_user"] == "Hall Ceiling"
    assert ws_server.devices[1]["name"] == "Ceiling Fitting"


def test_device_update_is_visible_in_a_following_device_get(run_cli, ws_env):
    assert run_cli(["device", "update", "device_one", "--name", "Reading Fitting"], ws_env)[0] == 0
    code, out = run_cli(["device", "get", "device_one"], ws_env)
    assert code == 0
    assert "name: Reading Fitting" in out
    assert "name_by_user: Reading Fitting" in out
    assert "name_source: user" in out


def test_device_update_and_device_get_agree_about_the_area(run_cli, ws_env):
    """The two views are built from the same row, so they cannot drift apart."""
    _, updated = run_cli(["device", "update", "device_three", "--area", "Example Hall"], ws_env)
    _, fetched = run_cli(["device", "get", "device_three"], ws_env)

    def area_lines(text):
        wanted = ("area:", "area_id:", "area_source:")
        return [line.strip() for line in text.splitlines() if line.strip().startswith(wanted)]

    assert (
        area_lines(updated)
        == area_lines(fetched)
        == ["area: Example Hall", "area_id: example_hall", "area_source: device"]
    )


def test_device_update_accepts_the_displayed_name_as_the_subject(run_cli, ws_env, ws_server):
    code, out = run_cli(["device", "update", "Example Doorway", "--area", "Example Hall"], ws_env)
    assert code == 0
    assert "device: device_four" in out
    update = next(c for c in ws_server.received if c["type"] == "config/device_registry/update")
    assert update["device_id"] == "device_four"


def test_device_update_can_clear_the_name_and_the_area(run_cli, ws_env, ws_server):
    code, out = run_cli(["device", "update", "device_two", "--clear-name", "--clear-area"], ws_env)
    assert code == 0
    update = next(c for c in ws_server.received if c["type"] == "config/device_registry/update")
    assert update["name_by_user"] is None and update["area_id"] is None
    # Clearing the override falls back to the integration's name, not to blank.
    assert "name: Ceiling Fitting" in out
    assert 'name_by_user: ""' in out
    assert 'area: ""' in out


def test_device_update_is_idempotent(run_cli, ws_env, ws_server):
    code, out = run_cli(["device", "update", "device_two", "--name", "Example Ceiling"], ws_env)
    assert code == 0
    assert "no change made" in out
    assert "name: Example Ceiling" in out
    assert [c for c in ws_server.received if c["type"] == "config/device_registry/update"] == []


def test_device_update_needs_something_to_change(run_cli, ws_env, ws_server):
    code, out = run_cli(["device", "update", "device_two"], ws_env)
    assert code == 2
    assert "nothing to update" in out
    assert "NO_CHANGES" in out
    assert ws_server.received == []


def test_device_update_rejects_an_unknown_device(run_cli, ws_env, ws_server):
    code, out = run_cli(["device", "update", "Example Absent", "--name", "Renamed"], ws_env)
    assert code == 1
    assert "no device with id or name 'Example Absent'" in out
    assert "NO_SUCH_DEVICE" in out
    assert [c for c in ws_server.received if c["type"] == "config/device_registry/update"] == []


def test_device_update_rejects_an_unknown_area_with_a_way_forward(run_cli, ws_env, ws_server):
    code, out = run_cli(["device", "update", "device_two", "--area", "Nowhere"], ws_env)
    assert code == 1
    assert "no area with id or name 'Nowhere'" in out
    assert "NO_SUCH_AREA" in out
    assert "hass-axi area list" in out
    assert [c for c in ws_server.received if c["type"] == "config/device_registry/update"] == []


def test_device_update_answers_from_the_stored_entry_not_from_the_request(run_cli, ws_env):
    """The double answers with the device it now holds, fields and all.

    Reporting from the payload instead is the defect `entity update` shipped:
    an update that never read the registry claimed an area the entity did not
    have, and an agent reads the update response.
    """
    import json

    code, out = run_cli(
        ["--json", "device", "update", "device_two", "--name", "Hall Ceiling"], ws_env
    )
    assert code == 0
    doc = json.loads(out)
    assert doc["name"] == "Hall Ceiling"
    # Never asked for, and reported anyway, because it comes from the registry.
    assert doc["area"] == "Example Hall"
    assert doc["area_id"] == "example_hall"


def test_a_device_rename_reaches_every_entity_the_device_names(run_cli, ws_env):
    """The reason the write belongs at this level rather than on each entity.

    light.example_ceiling has no name of its own: everything it is called comes
    from device_two, so one device-level correction moves it and leaves nothing
    behind that still says the old name.
    """
    _, before = run_cli(["entity", "list", "--domain", "light"], ws_env)
    assert "light.example_ceiling,Example Ceiling," in before

    assert run_cli(["device", "update", "device_two", "--name", "Hall Ceiling"], ws_env)[0] == 0

    _, after = run_cli(["entity", "list", "--domain", "light"], ws_env)
    assert "light.example_ceiling,Hall Ceiling," in after
    assert run_cli(["entity", "list", "--search", "Hall Ceiling"], ws_env)[1].count(
        "light.example_ceiling"
    )


def test_a_mistyped_device_subcommand_is_not_swallowed_by_the_default_one(run_cli, ws_env):
    """`device` defaults to `list`, and that must not hide a spelling mistake.

    A bare leading token that `list` has no positional to hold can only be a
    subcommand name, and `unexpected argument 'updat' for `device list`` names a
    subcommand nobody typed.
    """
    code, out = run_cli(["device", "updat", "device_two"], ws_env)
    assert code == 2
    assert "unknown subcommand `updat` for `device`" in out
    assert "subcommands: list, get, update" in out


def test_the_default_subcommand_still_answers_with_no_subcommand_at_all(run_cli, ws_env):
    code, out = run_cli(["device", "--fields", "device_id,name"], ws_env)
    assert code == 0
    assert "devices[4]{device_id,name}:" in out


# ------------------------------- the name Home Assistant actually displays


def test_an_entry_that_names_nothing_takes_its_whole_name_from_its_device(run_cli, ws_env):
    # light.example_ceiling has no `name` and no `original_name`: everything it
    # is called comes from device_two. Reading the entity row by itself renders
    # it blank, which is what four registry entries in five looked like.
    _, out = run_cli(["entity", "list", "--domain", "light"], ws_env)
    assert "light.example_ceiling,Example Ceiling,Example Hall" in out


def test_an_entry_that_names_its_own_half_keeps_the_device_prefix(run_cli, ws_env):
    # sensor.example_temperature is `Temperature` on its own row and
    # `Example Hub Temperature` everywhere Home Assistant shows it.
    _, out = run_cli(["entity", "list", "--fields", "entity_id,name,original_name"], ws_env)
    assert "sensor.example_temperature,Example Hub Temperature,Temperature" in out


def test_a_name_somebody_set_wins_outright_over_the_device_prefix(run_cli, ws_env):
    # `use_legacy_naming=True` means a user override is the whole name: Home
    # Assistant does not compose `Example Lamp Fitting` in front of it.
    _, out = run_cli(["entity", "list", "--domain", "light"], ws_env)
    assert "light.example_lamp,Example Lamp,Example Room" in out
    assert "Example Lamp Fitting Lamp" not in out


def test_composition_is_not_gated_on_has_entity_name(run_cli, ws_env):
    # sensor.example_legacy_meter sets `has_entity_name` false and still shows
    # the device prefix, because the flag decides only whether Home Assistant
    # stripped that prefix out of `original_name` before publishing it -- which
    # it does on the way out, so both kinds of entry compose the same way here.
    _, out = run_cli(["entity", "list", "--domain", "sensor"], ws_env)
    assert "sensor.example_legacy_meter,Example Doorway Legacy Meter,Example Room" in out


def test_search_finds_an_entity_by_the_name_a_user_actually_sees(run_cli, ws_env):
    # The headline. `Example Doorway` is what Home Assistant displays and what
    # an agent has to go on, and it lives in the *device* registry: matching
    # only the entity row answered `0 registry entries found` for it.
    code, out = run_cli(["entity", "list", "--search", "Example Doorway"], ws_env)
    assert code == 0
    assert "binary_sensor.example_doorway" in out
    assert "0 registry entries found" not in out


def test_search_still_matches_the_entity_half_of_a_composed_name(run_cli, ws_env):
    _, out = run_cli(["entity", "list", "--search", "Legacy Meter"], ws_env)
    assert "sensor.example_legacy_meter" in out


def test_entity_get_reports_the_displayed_name(run_cli, ws_env):
    code, out = run_cli(["entity", "get", "binary_sensor.example_doorway"], ws_env)
    assert code == 0
    assert "name: Example Doorway" in out
    assert "area_source: device" in out


def test_entity_update_reports_the_displayed_name_it_leaves_behind(run_cli, ws_env):
    # Setting an icon changes no name, so what comes back has to be the composed
    # one -- the same answer `entity get` gives, from the same row builder.
    code, out = run_cli(
        ["entity", "update", "binary_sensor.example_doorway", "--icon", "mdi:door"], ws_env
    )
    assert code == 0
    assert "name: Example Doorway" in out

    # And clearing an override falls back to the composed name, not to blank.
    code, out = run_cli(["entity", "update", "light.example_lamp", "--clear-name"], ws_env)
    assert code == 0
    assert "name: Example Lamp Fitting Lamp" in out


# ------------------------------------------- reaching a device's entities


def test_entity_list_filters_by_device(run_cli, ws_env):
    code, out = run_cli(["entity", "list", "--device", "device_four"], ws_env)
    assert code == 0
    assert "binary_sensor.example_doorway" in out
    assert "sensor.example_legacy_meter" in out
    assert "light.example_lamp" not in out
    assert "count: 2 of 2 matched (11 total)" in out


def test_entity_list_rejects_a_device_id_no_device_has(run_cli, ws_env):
    """A truncated id is a failed lookup, not a filter that matched nothing.

    `--area` on the same command resolves against the live registry and exits
    1. A device id is opaque, so an agent holding a mistyped one has no other
    spelling to try, and a zero-row answer used to send it round the filter
    variations -- the same dead end the flag itself was added to remove.
    """
    code, out = run_cli(["entity", "list", "--device", "device_nine"], ws_env)
    assert code == 1
    assert "no device with id 'device_nine'" in out
    assert "NO_SUCH_DEVICE" in out
    assert "Run `hass-axi device list --fields device_id,name` to see each device's id" in out


def test_entity_list_keeps_the_zero_for_a_device_that_supplies_nothing(run_cli, ws_env, ws_server):
    """A real device with no entities is an empty result, not a lookup failure.

    `this device has no entities` and `this id is not a device` are different
    facts, and only the second is an error.
    """
    ws_server.devices.append(
        {
            "id": "device_five",
            "name": "Example Stereo",
            "name_by_user": None,
            "area_id": None,
            "manufacturer": "Example Co",
            "model": "Model S",
        }
    )
    code, out = run_cli(["entity", "list", "--device", "device_five"], ws_env)
    assert code == 0
    assert "0 registry entries found supplied by device device_five" in out
    assert "total: 11 entries in the entity registry" in out


# ---------------------------------------------- an area_id nothing answers to


def _strand(ws_server, entity_id: str) -> None:
    """Point one entry at an area that does not exist, as a typo would."""
    for entry in ws_server.entities:
        if entry["entity_id"] == entity_id:
            entry["area_id"] = "no_such_area"


def test_an_area_id_no_area_claims_leaves_the_entity_unassigned(run_cli, ws_env, ws_server):
    # Home Assistant accepts `entity.update --param area_id=<typo>` without
    # complaint, and the entity then belongs to no area anybody can name. It was
    # missing from `--area <id>` and from `--area none` alike, so there was no
    # filter in the tool that could find it at all.
    _strand(ws_server, "light.example_lamp")
    code, out = run_cli(["entity", "list", "--area", "none", "--domain", "light"], ws_env)
    assert code == 0
    assert "light.example_lamp" in out


def test_a_stranded_entity_still_counts_somewhere(run_cli, ws_env, ws_server):
    _strand(ws_server, "light.example_lamp")
    code, out = run_cli(["area", "list"], ws_env)
    assert code == 0
    # 2 + 2 in the two areas and the rest unassigned is the whole registry.
    # Counting the stranded entity into an area nothing prints made the totals
    # stop summing, with nothing said about the entity that had gone missing.
    assert "unassigned_entities: 7" in out
    assert "example_room,Example Room,2,2," in out
    assert "example_hall,Example Hall,2,1,ground" in out


def test_entity_get_names_a_dangling_area_id_rather_than_implying_a_placement(
    run_cli, ws_env, ws_server
):
    _strand(ws_server, "light.example_lamp")
    code, out = run_cli(["entity", "get", "light.example_lamp"], ws_env)
    assert code == 0
    assert "area_id: no_such_area" in out
    assert "area_source: no area has this id" in out


# --------------------------------------- a registry entry with no state at all


def test_a_disabled_entry_is_listed_and_flagged(run_cli, ws_env):
    code, out = run_cli(
        ["entity", "list", "--search", "probe", "--fields", "entity_id,name,disabled"], ws_env
    )
    assert code == 0
    assert "sensor.example_disabled_probe,Example Hub Probe,true" in out


def test_a_disabled_entry_has_no_state_and_state_list_says_so(run_cli, installation_env):
    # The registry is the only view a disabled entity appears in: REST never
    # publishes a state for it. A fixture set where every entry had one never
    # exercised the difference between the two views the tool exists to bridge.
    code, out = run_cli(["state", "get", "sensor.example_disabled_probe"], installation_env)
    assert code == 1
    assert "no entity with id sensor.example_disabled_probe" in out

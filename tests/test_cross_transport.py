"""Commands that need both transports at once, on one origin.

Home Assistant serves REST and WebSocket from the same base URL, so a command
is free to use both. `installation_env` is the fixture that reproduces that
topology; `rest_env` and `ws_env` stay the cheaper choice elsewhere.
"""

from __future__ import annotations

import json


def test_state_list_filters_by_area_name(run_cli, installation_env):
    code, out = run_cli(["state", "list", "--area", "Example Room"], installation_env)
    assert code == 0
    assert "light.example_lamp,Example Lamp,on" in out
    assert "light.example_ceiling" not in out
    # Three: the lamp by its own area_id, and two more through the device that
    # sits in Example Room and gives them no area of their own.
    assert "count: 3 of 3 matched (11 total)" in out


def test_state_list_filters_by_area_id_as_well_as_by_name(run_cli, installation_env):
    id_code, by_id = run_cli(["state", "list", "--area", "example_room"], installation_env)
    name_code, by_name = run_cli(["state", "list", "--area", "Example Room"], installation_env)
    assert id_code == name_code == 0
    assert by_id == by_name
    assert "light.example_lamp" in by_id


def test_state_list_area_includes_an_entity_that_inherits_its_device_area(
    run_cli, installation_env
):
    # light.example_ceiling has no area of its own; its device sits in Example
    # Hall. A filter that ignored the device fallback would miss it entirely.
    code, out = run_cli(["state", "list", "--area", "example_hall"], installation_env)
    assert code == 0
    assert "light.example_ceiling,Example Ceiling,off" in out
    assert "light.example_lamp" not in out


def test_state_list_area_none_finds_entities_with_no_area_at_all(run_cli, installation_env):
    # switch.example_outlet has no registry entry, so it has no area either.
    code, out = run_cli(["state", "list", "--area", "none"], installation_env)
    assert code == 0
    assert "sensor.example_temperature" in out
    assert "switch.example_outlet" in out
    assert "light.example_lamp" not in out


def test_state_list_area_combines_with_the_other_filters(run_cli, installation_env):
    code, out = run_cli(
        ["state", "list", "--area", "Example Hall", "--domain", "light"], installation_env
    )
    assert code == 0
    assert "light.example_ceiling" in out
    assert "count: 1 of 1 matched (11 total)" in out

    code, out = run_cli(
        ["state", "list", "--area", "Example Room", "--domain", "climate"], installation_env
    )
    assert code == 0
    assert "states: 0 entity states found in area Example Room in domain climate" in out


def test_state_list_rejects_an_unknown_area_the_way_entity_list_does(run_cli, installation_env):
    code, out = run_cli(["state", "list", "--area", "Nowhere"], installation_env)
    # Exit 1, not 2: the invocation was well formed and only the live registry
    # could reveal that no such area exists.
    assert code == 1
    assert "no area with id or name 'Nowhere'" in out
    assert "hass-axi area list" in out


def test_state_list_without_area_never_opens_a_websocket(run_cli, installation_env, ws_server):
    """The registry round-trip is paid only by the flag that needs it."""
    code, _ = run_cli(["state", "list", "--domain", "light"], installation_env)
    assert code == 0
    assert ws_server.received == []


def test_state_list_area_reads_the_registry_once(run_cli, installation_env, ws_server):
    code, _ = run_cli(["state", "list", "--area", "Example Room"], installation_env)
    assert code == 0
    assert [command["type"] for command in ws_server.received] == [
        "config/entity_registry/list",
        "config/area_registry/list",
        "config/device_registry/list",
    ]


def test_state_list_area_is_reported_in_json_mode_too(run_cli, installation_env):
    code, out = run_cli(["--json", "state", "list", "--area", "example_hall"], installation_env)
    assert code == 0
    doc = json.loads(out)
    assert [row["entity_id"] for row in doc["states"]] == [
        "light.example_ceiling",
        "media_player.example_speaker",
    ]


def test_doctor_is_healthy_when_both_transports_answer(run_cli, installation_env):
    code, out = run_cli(["doctor"], installation_env)
    assert code == 0
    assert "healthy: true" in out
    assert "environment,ok" in out
    assert "rest,ok" in out
    assert 'websocket,ok,"authenticated, 11 registry entries in 2 areas"' in out
    assert "version: 2026.1.0" in out


def test_a_service_call_and_a_registry_read_share_one_base_url(
    run_cli, installation_env, rest_server, ws_server
):
    """A POST body survives the routing, so the origin is genuinely shared."""
    code, _ = run_cli(
        ["service", "call", "light.turn_on", "--target-entity", "light.example_lamp"],
        installation_env,
    )
    assert code == 0
    posted = [r for r in rest_server.requests if r["path"] == "/api/services/light/turn_on"]
    assert posted[0]["body"] == {"entity_id": ["light.example_lamp"]}

    code, _ = run_cli(["entity", "list"], installation_env)
    assert code == 0
    assert ws_server.received[0]["type"] == "config/entity_registry/list"

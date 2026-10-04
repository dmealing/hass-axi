"""Writes preview until `--write`, and a raw response is shortened until `--full`.

Two AXI rules, on the three commands whose subject is in their arguments rather
than their declaration: `service call`, a write-method `api` request and a write
`ws` command.

**The preview claim is asserted on the doubles' request logs, not on the
output.** A version that printed "nothing was sent" and sent it anyway would
pass any assertion about the text, so each case below checks that the request
that would have mutated never arrived -- and, beside it, that the same command
with `--write` does arrive. One flag name on all three is part of the contract,
which is why it is written here as a literal rather than imported.
"""

from __future__ import annotations

import json

WRITE = "--write"
READ_ONLY = {"HASS_AXI_READ_ONLY": "1"}


def posts(rest_server) -> list:
    return [r["path"] for r in rest_server.requests if r["method"] != "GET"]


def as_json(run_cli, argv, env):
    code, out = run_cli(["--json", *argv], env)
    return code, json.loads(out)


# ------------------------------------------------------------- service call


def test_a_service_call_without_the_write_flag_sends_nothing(run_cli, rest_env, rest_server):
    code, doc = as_json(
        run_cli,
        ["service", "call", "light.turn_off", "--target-entity", "light.example_lamp"],
        rest_env,
    )
    assert code == 0
    assert posts(rest_server) == []
    assert doc["preview"].startswith("nothing was sent to Home Assistant")
    assert doc["request"] == {
        "method": "POST",
        "path": "/api/services/light/turn_off",
        "body": {"entity_id": ["light.example_lamp"]},
    }
    assert [row["entity_id"] for row in doc["would_reach"]] == ["light.example_lamp"]
    assert "would reach 1 entity" in doc["target"]
    assert any(WRITE in line for line in doc["help"])


def test_the_same_service_call_with_the_write_flag_is_sent(run_cli, rest_env, rest_server):
    code, out = run_cli(
        ["service", "call", "light.turn_off", "--target-entity", "light.example_lamp", WRITE],
        rest_env,
    )
    assert code == 0
    assert posts(rest_server) == ["/api/services/light/turn_off"]
    assert "preview" not in out


def test_a_preview_fails_where_the_call_would_for_a_service_that_does_not_exist(
    run_cli, rest_env, rest_server
):
    code, out = run_cli(["service", "call", "light.turn_onn"], rest_env)
    assert code == 1
    assert "code: NO_SUCH_SERVICE" in out
    assert "light.turn_on" in out
    assert posts(rest_server) == []


def test_a_preview_says_a_target_reaches_nothing(run_cli, installation_env, rest_server):
    """The verdict the call itself only reaches after it has been sent."""
    code, out = run_cli(
        ["service", "call", "light.turn_on", "--target-area", "nowhere"], installation_env
    )
    assert code == 1
    assert "code: NO_ENTITIES_TARGETED" in out
    assert posts(rest_server) == []


def test_a_preview_resolves_an_area_to_the_entities_it_would_reach(
    run_cli, installation_env, rest_server
):
    code, doc = as_json(
        run_cli,
        ["service", "call", "light.turn_off", "--target-area", "example_room"],
        installation_env,
    )
    assert code == 0
    assert posts(rest_server) == []
    assert doc["would_reach"], doc
    assert all(row["entity_id"].startswith("light.") for row in doc["would_reach"])
    assert doc["capability_check"].startswith("nothing to check")


def test_a_preview_reports_the_capability_pre_check_refusing(
    run_cli, installation_env, rest_server
):
    code, out = run_cli(
        ["service", "call", "media_player.media_next_track", "--target-area", "example_hall"],
        installation_env,
    )
    assert code == 1
    assert "code: UNSUPPORTED_CAPABILITY" in out
    assert posts(rest_server) == []


def test_a_preview_reports_the_capability_pre_check_passing(run_cli, installation_env):
    code, doc = as_json(
        run_cli,
        ["service", "call", "media_player.volume_up", "--target-area", "example_hall"],
        installation_env,
    )
    assert code == 0
    assert doc["capability_check"].startswith("passed - 1 of 1 available can do this")
    assert [row["entity_id"] for row in doc["would_reach"]] == ["media_player.example_speaker"]


def test_a_preview_says_when_the_capability_pre_check_was_skipped(run_cli, installation_env):
    code, doc = as_json(
        run_cli,
        [
            "service",
            "call",
            "media_player.media_next_track",
            "--target-area",
            "example_hall",
            "--no-check",
        ],
        installation_env,
    )
    assert code == 0
    assert doc["capability_check"].startswith("skipped by --no-check")


def test_a_preview_names_a_field_the_service_does_not_publish_without_refusing(
    run_cli, rest_env, rest_server
):
    """A published field list is an integration's claim about itself, and a
    script takes variables it never declared -- so before the call this is a
    warning, and only after a refusal is it the explanation."""
    code, doc = as_json(
        run_cli, ["service", "call", "light.turn_on", "--data", "brightnes=180"], rest_env
    )
    assert code == 0
    assert "does not accept field brightnes" in doc["fields"]
    assert any("brightness" in line for line in doc["help"])
    assert posts(rest_server) == []


def test_a_preview_carries_the_response_mismatch_the_call_would_be_refused_for(run_cli, rest_env):
    code, out = run_cli(["service", "call", "calendar.list_events"], rest_env)
    assert code == 1
    assert "code: RESPONSE_REQUIRED" in out
    assert "return_response" not in out


def test_a_read_only_session_can_still_preview_and_is_told_the_write_would_be_refused(
    run_cli, rest_env, rest_server
):
    env = {**rest_env, **READ_ONLY}
    code, out = run_cli(
        ["service", "call", "light.turn_off", "--target-entity", "light.example_lamp"], env
    )
    assert code == 0
    assert "this session is read-only" in out
    assert "Unset HASS_AXI_READ_ONLY" in out

    code, out = run_cli(
        ["service", "call", "light.turn_off", "--target-entity", "light.example_lamp", WRITE], env
    )
    assert code == 2
    assert "code: READ_ONLY" in out
    assert posts(rest_server) == []


def test_a_preview_says_so_when_the_service_model_cannot_be_read(run_cli, rest_env, rest_server):
    """Unchecked is an answer; a list of checks that never ran is not."""
    rest_server.state["services"] = "not a list at all"
    code, out = run_cli(["service", "call", "light.turn_on"], rest_env)
    assert code == 0
    assert "checks: not run - the service model could not be read" in out
    assert posts(rest_server) == []


# ---------------------------------------------------------------------- api


def test_an_unsafe_api_method_is_previewed_and_not_sent(run_cli, rest_env, rest_server):
    code, doc = as_json(
        run_cli,
        ["api", "POST", "/services/light/turn_off", "--field", "entity_id=light.example_lamp"],
        rest_env,
    )
    assert code == 0
    assert rest_server.requests == []
    assert doc["request"] == {
        "method": "POST",
        "path": "/api/services/light/turn_off",
        "body": {"entity_id": "light.example_lamp"},
    }
    assert doc["preview"].startswith("nothing was sent to Home Assistant")


def test_the_same_api_request_with_the_write_flag_is_sent(run_cli, rest_env, rest_server):
    code, _ = run_cli(
        [
            "api",
            "POST",
            "/services/light/turn_off",
            "--field",
            "entity_id=light.example_lamp",
            WRITE,
        ],
        rest_env,
    )
    assert code == 0
    assert posts(rest_server) == ["/api/services/light/turn_off"]


def test_an_api_read_needs_no_write_flag(run_cli, rest_env, rest_server):
    code, out = run_cli(["api", "/config"], rest_env)
    assert code == 0
    assert "preview" not in out
    assert [r["path"] for r in rest_server.requests] == ["/api/config"]


# ----------------------------------------------------------------------- ws


def test_a_write_websocket_command_is_previewed_and_not_sent(run_cli, ws_env, ws_server):
    code, doc = as_json(
        run_cli,
        ["ws", "area.update", "--param", "area_id=example_room", "--param", "name=Example Study"],
        ws_env,
    )
    assert code == 0
    assert ws_server.received == []
    assert doc["command"] == {
        "name": "area.update",
        "type": "config/area_registry/update",
        "access": "write",
    }
    assert doc["params"] == {"area_id": "example_room", "name": "Example Study"}
    assert doc["preview"].startswith("nothing was sent to Home Assistant")


def test_the_same_websocket_command_with_the_write_flag_is_sent(run_cli, ws_env, ws_server):
    code, _ = run_cli(
        [
            "ws",
            "area.update",
            "--param",
            "area_id=example_room",
            "--param",
            "name=Example Study",
            WRITE,
        ],
        ws_env,
    )
    assert code == 0
    assert [c["type"] for c in ws_server.received] == ["config/area_registry/update"]


def test_a_raw_type_no_declaration_names_is_previewed_as_a_write(run_cli, ws_env, ws_server):
    """Fail-closed, as the read-only gate is: an unknown type might change something."""
    code, out = run_cli(["ws", "--raw", "example/registry/update"], ws_env)
    assert code == 0
    assert "access: write" in out
    assert ws_server.received == []


def test_a_read_websocket_command_needs_no_write_flag(run_cli, ws_env, ws_server):
    code, out = run_cli(["ws", "area.list"], ws_env)
    assert code == 0
    assert "preview" not in out
    assert [c["type"] for c in ws_server.received] == ["config/area_registry/list"]


def test_the_command_table_says_which_commands_need_the_write_flag(run_cli):
    code, doc = as_json(run_cli, ["ws", "--list"], {})
    assert code == 0
    access = {row["command"]: row["access"] for row in doc["commands"]}
    assert access["area.list"] == "read"
    assert access["area.update"] == "write"


# ------------------------------------------------------------- truncation


def _many_states(rest_server, count: int) -> None:
    rest_server.state["states"].extend(
        {"entity_id": f"sensor.example_filler_{index}", "state": "1", "attributes": {}}
        for index in range(count)
    )


def test_a_long_api_response_is_shortened_and_says_how_much_there_is(
    run_cli, rest_env, rest_server
):
    _many_states(rest_server, 60)
    total = len(rest_server.state["states"])
    code, doc = as_json(run_cli, ["api", "/states"], rest_env)
    assert code == 0
    assert len(doc["result"]) == 25
    assert f"first 25 of {total} items shown" in doc["truncated"]
    assert "chars total" in doc["truncated"]
    assert any("--full" in line for line in doc["help"])


def test_the_full_flag_prints_the_whole_api_response(run_cli, rest_env, rest_server):
    _many_states(rest_server, 60)
    code, doc = as_json(run_cli, ["api", "/states", "--full"], rest_env)
    assert code == 0
    assert len(doc["result"]) == len(rest_server.state["states"])
    assert "truncated" not in doc


def test_a_short_api_response_is_printed_whole_with_no_hint(run_cli, rest_env):
    code, out = run_cli(["api", "/config"], rest_env)
    assert code == 0
    assert "truncated" not in out
    assert "--full" not in out


def test_a_long_string_in_a_response_is_previewed_with_its_length(run_cli, rest_env, rest_server):
    rest_server.state["states"][0].setdefault("attributes", {})["example_note"] = "x" * 5000
    entity_id = rest_server.state["states"][0]["entity_id"]
    code, doc = as_json(run_cli, ["api", f"/states/{entity_id}"], rest_env)
    assert code == 0
    note = doc["result"]["attributes"]["example_note"]
    assert note.endswith("... (truncated, 5000 chars total)")
    assert "1 string cut to 1200 chars" in doc["truncated"]


def test_a_long_websocket_result_is_shortened_and_full_restores_it(run_cli, ws_env, ws_server):
    template = ws_server.entities[0]
    for index in range(40):
        ws_server.entities.append({**template, "entity_id": f"sensor.example_filler_{index}"})
    total = len(ws_server.entities)

    code, doc = as_json(run_cli, ["ws", "entity.list"], ws_env)
    assert code == 0
    assert len(doc["result"]) == 25
    assert f"first 25 of {total} items shown" in doc["truncated"]

    code, doc = as_json(run_cli, ["ws", "entity.list", "--full"], ws_env)
    assert code == 0
    assert len(doc["result"]) == total
    assert "truncated" not in doc

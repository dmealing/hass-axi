"""Commands backed by the REST API, exercised against a real local HTTP server."""

from __future__ import annotations

import socket
import threading

from conftest import FAKE_TOKEN


def test_state_list_defaults_to_three_fields_and_reports_the_total(run_cli, rest_env):
    code, out = run_cli(["state", "list"], rest_env)
    assert code == 0
    assert "states[11]{entity_id,name,state}:" in out
    assert "count: 11 of 11 total" in out
    assert "light.example_lamp,Example Lamp,on" in out


def test_state_list_filters_by_domain_and_keeps_the_total_visible(run_cli, rest_env):
    code, out = run_cli(["state", "list", "--domain", "light"], rest_env)
    assert code == 0
    assert "count: 2 of 2 matched (11 total)" in out
    assert "sensor.example_temperature" not in out


def test_state_list_filters_by_state_and_by_search(run_cli, rest_env):
    _, out = run_cli(["state", "list", "--state", "unavailable"], rest_env)
    assert "switch.example_outlet" in out and "light.example_lamp" not in out
    _, out = run_cli(["state", "list", "--search", "ceiling"], rest_env)
    assert "light.example_ceiling" in out and "light.example_lamp,Example" not in out


def test_state_list_states_the_zero_explicitly(run_cli, rest_env):
    code, out = run_cli(["state", "list", "--domain", "vacuum"], rest_env)
    assert code == 0
    assert "states: 0 entity states found in domain vacuum" in out
    assert "total: 11 entities in this installation" in out


def test_state_list_limit_suggests_how_to_see_the_rest(run_cli, rest_env):
    code, out = run_cli(["state", "list", "--limit", "2"], rest_env)
    assert code == 0
    assert "count: 2 of 11 total" in out
    assert "Run `hass-axi state list --limit 11` to see all 11" in out


def test_state_list_honours_requested_fields(run_cli, rest_env):
    code, out = run_cli(["state", "list", "--fields", "entity_id,domain"], rest_env)
    assert code == 0
    assert "states[11]{entity_id,domain}:" in out


def test_state_list_rejects_an_unknown_field_and_lists_the_valid_ones(run_cli, rest_env):
    code, out = run_cli(["state", "list", "--fields", "nope"], rest_env)
    assert code == 2
    assert "unknown field: nope" in out
    assert "available fields: entity_id, name, state" in out


def test_state_get_returns_attributes(run_cli, rest_env):
    code, out = run_cli(["state", "get", "light.example_lamp"], rest_env)
    assert code == 0
    assert "entity_id: light.example_lamp" in out
    assert "brightness: 180" in out


def test_state_get_truncates_a_long_attribute_and_offers_the_escape_hatch(run_cli, rest_server):
    rest_server.state["states"][0]["attributes"]["blob"] = "y" * 5000
    env = {"HA_URL": rest_server.url, "HA_TOKEN": FAKE_TOKEN}
    code, out = run_cli(["state", "get", "light.example_lamp"], env)
    assert code == 0
    assert "truncated, 5000 chars total" in out
    assert "--full` to see complete attributes" in out

    code, out = run_cli(["state", "get", "light.example_lamp", "--full"], env)
    assert code == 0
    assert "truncated" not in out


def test_state_get_on_a_missing_entity_suggests_how_to_find_it(run_cli, rest_env):
    code, out = run_cli(["state", "get", "light.absent"], rest_env)
    assert code == 1
    assert "no entity with id light.absent" in out
    assert "--search absent" in out


def test_service_list_summarizes_domains(run_cli, rest_env):
    code, out = run_cli(["service", "list"], rest_env)
    assert code == 0
    assert "domains[5]{domain,services}:" in out
    assert "light,2" in out


def test_service_list_for_one_domain(run_cli, rest_env):
    code, out = run_cli(["service", "list", "--domain", "light"], rest_env)
    assert code == 0
    assert "light.turn_on,Turn on,3" in out


def test_service_list_rejects_an_unknown_domain(run_cli, rest_env):
    code, out = run_cli(["service", "list", "--domain", "nope"], rest_env)
    assert code == 1
    assert "no service domain named 'nope'" in out


def test_service_call_sends_targets_flat_as_home_assistant_requires(run_cli, rest_env, rest_server):
    """Targets go flat in the body, not nested under `target`.

    The REST endpoint hands the body to the service as its data and never
    unwraps a `target` key, so a nested one is rejected as an extra key. The
    double enforces that, so this asserts the shape a real instance accepts.
    """
    code, out = run_cli(
        [
            "service",
            "call",
            "light.turn_on",
            "--target-entity",
            "light.example_lamp",
            "--data",
            "brightness=180",
            "--write",
        ],
        rest_env,
    )
    assert code == 0
    posted = [r for r in rest_server.requests if r["path"] == "/api/services/light/turn_on"]
    assert posted[0]["body"] == {"brightness": 180, "entity_id": ["light.example_lamp"]}
    assert "target" not in posted[0]["body"]
    assert "service: light.turn_on" in out
    assert "changed[1]{entity_id,name,state}:" in out


def test_service_call_sends_every_target_kind_flat(run_cli, rest_env, rest_server):
    code, _ = run_cli(
        [
            "service",
            "call",
            "light.turn_on",
            "--target-entity",
            "light.example_lamp",
            "--target-area",
            "example_room",
            "--target-device",
            "device_one",
            "--write",
        ],
        rest_env,
    )
    assert code == 0
    body = next(r for r in rest_server.requests if r["path"] == "/api/services/light/turn_on")[
        "body"
    ]
    assert body == {
        "entity_id": ["light.example_lamp"],
        "area_id": ["example_room"],
        "device_id": ["device_one"],
    }


def test_the_rest_double_rejects_a_nested_target(run_cli, rest_env):
    """The regression guard itself: a nested target must fail loudly.

    Home Assistant answers an extra key with an empty 400 -- `vol.Invalid`
    raised as `HTTPBadRequest`, no body -- so which key was wrong can only come
    from the service model, read on the failure path. The refusal is what this
    guards; naming the key is what stops it being a dead end.
    """
    code, out = run_cli(
        [
            "service",
            "call",
            "light.turn_on",
            "--data-json",
            '{"target": {"entity_id": "x"}}',
            "--write",
        ],
        rest_env,
    )
    assert code == 1
    assert "does not accept field target" in out
    assert "fields for light.turn_on: brightness, transition, profile" in out


def test_service_call_merges_a_json_object_over_key_value_data(run_cli, rest_env, rest_server):
    run_cli(
        [
            "service",
            "call",
            "light.turn_on",
            "--data",
            "brightness=1",
            "--data-json",
            '{"brightness": 9}',
            "--write",
        ],
        rest_env,
    )
    posted = [r for r in rest_server.requests if r["path"] == "/api/services/light/turn_on"]
    assert posted[-1]["body"]["brightness"] == 9


def test_service_call_reports_an_empty_change_set_definitively(run_cli, rest_env, rest_server):
    rest_server.state["service_result"] = []
    code, out = run_cli(["service", "call", "light.turn_off", "--write"], rest_env)
    assert code == 0
    assert "changed: light.turn_off accepted with 0 states changed" in out


def test_service_call_surfaces_a_service_response(run_cli, rest_env, rest_server):
    rest_server.state["service_result"] = {"changed_states": [], "service_response": {"answer": 42}}
    code, out = run_cli(
        [
            "service",
            "call",
            "calendar.get_events",
            "--response",
            "--data",
            "start_date_time=2026-01-01 00:00:00",
            "--write",
        ],
        rest_env,
    )
    assert code == 0
    assert "answer: 42" in out


def test_service_call_rejects_a_malformed_service_name(run_cli, rest_env):
    code, out = run_cli(["service", "call", "turn_on", "--write"], rest_env)
    assert code == 2
    assert "expected <domain>.<service>" in out


def test_service_call_rejects_malformed_data(run_cli, rest_env):
    code, out = run_cli(["service", "call", "light.turn_on", "--data", "oops", "--write"], rest_env)
    assert code == 2
    assert "--data needs key=value" in out


def test_template_render_returns_the_rendered_text(run_cli, rest_env, rest_server):
    rest_server.state["template"] = "on"
    code, out = run_cli(
        ["template", "render", "--template", '{{ states("light.example_lamp") }}'], rest_env
    )
    assert code == 0
    assert "result: on" in out
    posted = [r for r in rest_server.requests if r["path"] == "/api/template"]
    assert posted[0]["body"] == {"template": '{{ states("light.example_lamp") }}'}


def test_template_render_works_without_the_subcommand_name(run_cli, rest_env, rest_server):
    rest_server.state["template"] = "42"
    code, out = run_cli(["template", "--template", "{{ 42 }}"], rest_env)
    assert code == 0
    assert 'result: "42"' in out


def test_template_render_truncates_a_long_result(run_cli, rest_env, rest_server):
    rest_server.state["template"] = "z" * 4000
    code, out = run_cli(["template", "render", "--template", "{{ x }}"], rest_env)
    assert code == 0
    assert "truncated, 4000 chars total" in out
    assert "--full" in out


def test_template_render_reads_a_file(run_cli, rest_env, tmp_path, rest_server):
    rest_server.state["template"] = "from file"
    path = tmp_path / "t.j2"
    path.write_text("{{ now() }}", encoding="utf-8")
    code, out = run_cli(["template", "render", "--template-file", str(path)], rest_env)
    assert code == 0
    assert "result: from file" in out


def test_template_render_requires_a_source(run_cli, rest_env):
    code, out = run_cli(["template", "render"], rest_env)
    assert code == 2
    assert "--template or --template-file is required" in out


def test_template_render_rejects_two_sources(run_cli, rest_env):
    code, out = run_cli(["template", "render", "--template", "a", "--template-file", "b"], rest_env)
    assert code == 2
    assert "mutually exclusive" in out


def test_api_defaults_to_get(run_cli, rest_env):
    code, out = run_cli(["api", "/config"], rest_env)
    assert code == 0
    assert "version: 2026.1.0" in out
    assert "method: GET" in out


def test_api_posts_fields(run_cli, rest_env, rest_server):
    code, _ = run_cli(
        [
            "api",
            "POST",
            "/services/light/turn_on",
            "--field",
            "entity_id=light.example_lamp",
            "--write",
        ],
        rest_env,
    )
    assert code == 0
    posted = [r for r in rest_server.requests if r["path"] == "/api/services/light/turn_on"]
    assert posted[0]["body"] == {"entity_id": "light.example_lamp"}


def test_api_requires_a_path(run_cli, rest_env):
    code, out = run_cli(["api"], rest_env)
    assert code == 2
    assert "needs <method-or-path>" in out

    code, out = run_cli(["api", "POST"], rest_env)
    assert code == 2
    assert "a path is required after POST" in out


def test_api_query_values_keep_their_json_spelling(run_cli, rest_env, rest_server):
    code, _ = run_cli(
        [
            "api",
            "/config",
            "--query",
            "return_config=true",
            "--query",
            "absent=null",
            "--query",
            "level=10",
        ],
        rest_env,
    )
    assert code == 0
    query = rest_server.requests[-1]["query"]
    assert "return_config=true" in query
    assert "absent=null" in query
    assert "level=10" in query
    assert "True" not in query
    assert "None" not in query


def test_a_rejected_token_produces_an_actionable_error(run_cli, rest_server):
    code, out = run_cli(
        ["state", "list"], {"HA_URL": rest_server.url, "HA_TOKEN": "wrong-token-value"}
    )
    assert code == 1
    assert "rejected the access token" in out
    assert "HA_TOKEN" in out


def test_an_unreachable_instance_reports_the_transport_not_a_traceback(run_cli):
    env = {"HA_URL": "http://127.0.0.1:1", "HA_TOKEN": FAKE_TOKEN}
    code, out = run_cli(["state", "list"], env)
    assert code == 1
    assert "could not reach Home Assistant" in out
    assert "Traceback" not in out


def test_a_response_cut_off_mid_body_reports_the_transport_not_a_traceback(run_cli):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve_a_truncated_body():
        conn, _ = listener.accept()
        conn.recv(65536)
        body = b'{"message": "API run'
        conn.sendall(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body) + 64}\r\n\r\n".encode()
            + body
        )
        conn.close()
        listener.close()

    threading.Thread(target=serve_a_truncated_body, daemon=True).start()
    env = {"HA_URL": f"http://127.0.0.1:{listener.getsockname()[1]}", "HA_TOKEN": FAKE_TOKEN}
    code, out = run_cli(["state", "list"], env)
    assert code == 1
    assert "dropped mid-response" in out
    assert "CONNECTION_DROPPED" in out
    assert "Traceback" not in out


# ------------------------------------------------- error translation paths


def test_a_method_not_allowed_is_translated(run_cli, rest_env, rest_server):
    rest_server.status_override = (405, {"message": "Method not allowed"})
    code, out = run_cli(["api", "DELETE", "/states", "--write"], rest_env)
    assert code == 1
    assert "DELETE is not allowed" in out
    assert "hass-axi api --help" in out


def test_an_unmapped_status_reports_the_code_and_the_server_message(run_cli, rest_env, rest_server):
    rest_server.status_override = (503, {"message": "Service unavailable"})
    code, out = run_cli(["state", "list"], rest_env)
    assert code == 1
    assert "HTTP 503" in out
    assert "Service unavailable" in out
    assert "Traceback" not in out


def test_an_unparseable_json_body_falls_back_to_the_raw_text(run_cli, rest_env, rest_server):
    """A malformed body must not raise; the caller still gets something usable."""
    rest_server.malformed_json = b"{not json at all"
    code, out = run_cli(["api", "/config"], rest_env)
    assert code == 0
    assert "not json at all" in out


def test_a_slow_instance_times_out_with_an_actionable_error(run_cli, rest_server):
    rest_server.delay = 1.0
    env = {"HA_URL": rest_server.url, "HA_TOKEN": FAKE_TOKEN}
    code, out = run_cli(["--timeout", "0.15", "state", "list"], env)
    assert code == 1
    assert "timed out after 0.15s" in out
    assert "--timeout 60" in out
    assert "Traceback" not in out


def test_a_tls_failure_is_named_as_one(rest_env):
    """Translated directly: standing up a broken TLS endpoint proves nothing extra."""
    import ssl
    import urllib.error

    from hass_axi.config import load
    from hass_axi.errors import ConnectionFailed
    from hass_axi.rest import RestClient

    client = RestClient(load(rest_env))
    error = client._url_error(urllib.error.URLError(ssl.SSLError("handshake failure")))
    assert isinstance(error, ConnectionFailed)
    assert error.code == "TLS_ERROR"
    assert "scheme your instance actually serves" in " ".join(error.help_lines)


# ------------------------------------------- what the landing view counts


def test_the_home_view_counts_unavailable_and_unknown_apart(run_cli, rest_env):
    # `unknown` means the entity is reachable and has not reported yet;
    # `unavailable` means it cannot be reached. Summing them under the name of
    # one of them made the first thing an agent reads contradict
    # `state list --state unavailable` on any installation that has either.
    code, out = run_cli([], rest_env)
    assert code == 0
    assert "unavailable: 2" in out
    assert "unknown: 1" in out

    _, unavailable = run_cli(["state", "list", "--state", "unavailable"], rest_env)
    assert "switch.example_outlet" in unavailable
    assert "calendar.example_old_agenda" in unavailable
    assert "count: 2 of 2 matched (11 total)" in unavailable

    _, unknown = run_cli(["state", "list", "--state", "unknown"], rest_env)
    assert "sensor.example_reading" in unknown
    assert "count: 1 of 1 matched (11 total)" in unknown


# ------------------------------------------------ a 404 that says something


def test_a_404_that_carries_a_message_reports_the_message(run_cli, rest_env):
    # The path was fine and the entity was not. Reporting it as a bad path sends
    # an agent looking for a spelling mistake that is not there.
    code, out = run_cli(["api", "/states/light.no_such"], rest_env)
    assert code == 1
    assert "Entity not found." in out
    assert "no such API path" not in out


def test_a_404_with_no_message_is_still_reported_as_a_bad_path(run_cli, rest_env):
    # An unrouted path gets aiohttp's own plain-text 404 and no message at all,
    # so there is nothing to quote and the path really is the thing to fix.
    code, out = run_cli(["api", "/no_such_path_at_all"], rest_env)
    assert code == 1
    assert "no such API path: /no_such_path_at_all" in out


def test_template_render_reports_what_the_template_did_wrong(run_cli, rest_env):
    # A template that does not compile is a 400 with a message, not a render.
    code, out = run_cli(["template", "render", "--template", "{{ undefined_helper() }}"], rest_env)
    assert code == 1
    assert "'undefined_helper' is undefined" in out

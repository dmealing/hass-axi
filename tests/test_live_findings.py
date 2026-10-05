"""Regressions for the defects a live run against a real installation found.

Each test states one finding the way it was reproduced: the command, what it
answered and what it should have. The offline suite was green through all of
them, so the bar for a test here is the one the doubles are held to -- the code
before the fix fails it.
"""

from __future__ import annotations

import json

import pytest

from conftest import FAKE_TOKEN

#: An address nothing listens on: a usage error has to be the same error here.
UNREACHABLE = {"HA_URL": "http://127.0.0.1:9", "HA_TOKEN": FAKE_TOKEN}


def as_json(run_cli, argv, env):
    code, out = run_cli(["--json", *argv], env)
    return code, json.loads(out)


# ------------------------------------------------- a flag is not a flag's value


@pytest.mark.parametrize(
    "argv",
    [
        ["state", "list", "--domain", "--json"],
        ["state", "list", "--limit", "--json"],
        ["state", "list", "--domain", "--limit", "3"],
        ["entity", "list", "--area", "--fields", "entity_id"],
        ["logbook", "get", "--entity", "--human"],
    ],
)
def test_a_value_taking_flag_does_not_swallow_the_flag_after_it(run_cli, argv):
    code, out = run_cli(argv, UNREACHABLE)
    assert code == 2
    assert "MISSING_VALUE" in out


def test_the_mode_flag_after_a_valueless_flag_still_sets_the_mode(run_cli):
    code, doc = as_json(run_cli, ["state", "list", "--domain"], UNREACHABLE)
    assert code == 2
    assert doc["code"] == "MISSING_VALUE"


def test_free_text_may_still_begin_with_a_flag(run_cli, rest_env, rest_server):
    code, _ = run_cli(["template", "render", "--template", "--json"], rest_env)
    assert code == 0
    assert rest_server.requests[-1]["body"] == {"template": "--json"}


def test_the_inline_form_passes_a_value_that_looks_like_a_flag(run_cli, rest_env):
    code, out = run_cli(["state", "list", "--search=--json"], rest_env)
    assert code == 0
    assert "MISSING_VALUE" not in out


# -------------------------------------- a usage error needs no installation


USAGE_BEFORE_TRANSPORT = [
    (["state", "list", "--limit", "abc"], "BAD_LIMIT"),
    (["state", "list", "--fields", "nope"], "UNKNOWN_FIELD"),
    (["state", "list", "--stale", "soon"], "BAD_TIME"),
    (["sensor", "list", "--limit", "0"], "BAD_LIMIT"),
    (["sensor", "list", "--fields", "nope"], "UNKNOWN_FIELD"),
    (["entity", "list", "--limit", "abc"], "BAD_LIMIT"),
    (["entity", "list", "--fields", "nope"], "UNKNOWN_FIELD"),
    (["device", "list", "--limit", "abc"], "BAD_LIMIT"),
    (["device", "list", "--fields", "nope"], "UNKNOWN_FIELD"),
    (["statistics", "list", "--limit", "abc"], "BAD_LIMIT"),
    (["statistics", "list", "--fields", "nope"], "UNKNOWN_FIELD"),
    (["logbook", "get", "--limit", "abc"], "BAD_LIMIT"),
    (["logbook", "get", "--fields", "nope"], "UNKNOWN_FIELD"),
    (["service", "get", "light.turn_on", "--fields", "nope"], "UNKNOWN_FIELD"),
]


@pytest.mark.parametrize(("argv", "code"), USAGE_BEFORE_TRANSPORT)
@pytest.mark.parametrize("env", [UNREACHABLE, {}], ids=["unreachable", "unconfigured"])
def test_a_usage_error_is_decided_without_the_installation(run_cli, argv, code, env):
    exit_code, doc = as_json(run_cli, argv, env)
    assert (exit_code, doc["code"], doc["class"]) == (2, code, "usage")


# ------------------------------------------ an empty result keeps the key's type


@pytest.mark.parametrize(
    ("argv", "key"),
    [
        (["state", "list", "--domain", "nosuch"], "states"),
        (["entity", "list", "--domain", "nosuch"], "entities"),
        (["sensor", "list", "--unit", "nosuch"], "sensors"),
        (["device", "list", "--search", "nosuchdevice"], "devices"),
        (["statistics", "list", "--search", "nosuchstatistic"], "statistics"),
        (["logbook", "get", "--search", "nosuchentry", "--start", "1h"], "entries"),
    ],
)
def test_an_empty_listing_is_still_a_list(run_cli, installation_env, argv, key):
    code, doc = as_json(run_cli, argv, installation_env)
    assert code == 0
    assert doc[key] == []
    # The sentence that used to replace the list is still said, in `count`.
    assert doc["count"].startswith("0 ")


def test_the_empty_listing_says_the_same_thing_in_toon(run_cli, rest_env):
    code, out = run_cli(["state", "list", "--domain", "nosuch"], rest_env)
    assert code == 0
    assert "count: 0 entity states found in domain nosuch" in out


# --------------------------------------- "see all" lists the rows it counted


@pytest.mark.parametrize(
    "argv",
    [
        ["state", "list", "--domain", "light", "--limit", "1"],
        ["state", "list", "--search", "example", "--limit", "1"],
        ["entity", "list", "--domain", "light", "--limit", "1"],
        ["entity", "list", "--area", "Example Room", "--limit", "1"],
        ["device", "list", "--search", "example", "--limit", "1"],
        ["sensor", "list", "--all", "--area", "none", "--limit", "1"],
    ],
)
def test_the_see_all_suggestion_keeps_the_filter(run_cli, installation_env, argv):
    import shlex

    code, doc = as_json(run_cli, argv, installation_env)
    assert code == 0
    line = next(line for line in doc["help"] if "to see all" in line)
    suggested = shlex.split(line.split("`")[1])[1:]
    matched = int(line.rsplit(" ", 1)[1])
    key = next(k for k, v in doc.items() if isinstance(v, list) and k != "help")

    code, everything = as_json(run_cli, suggested, installation_env)
    assert code == 0
    # Running the suggestion returns the rows the count promised -- not the
    # first N of the whole installation, which is what dropping the filter gave.
    assert len(everything[key]) == matched
    assert doc[key][0] == everything[key][0]
    assert everything["count"].startswith(f"{matched} of {matched} matched")


# ------------------------------------------------------ suggestions that run


def test_a_single_subcommand_is_named_once(run_cli):
    code, out = run_cli(["ping", "extra"], {})
    assert code == 2
    assert "`ping ping`" not in out
    assert "Run `hass-axi ping`" in out
    code, out = run_cli(["doctor", "--fix"], {})
    assert "`doctor doctor`" not in out


def test_a_positional_to_area_create_is_pointed_at_the_name_flag(run_cli):
    code, doc = as_json(run_cli, ["area", "create", "Example Annex"], {})
    assert code == 2
    assert doc["code"] == "UNEXPECTED_ARGUMENT"
    assert "Run `hass-axi area create --name 'Example Annex'`" in doc["help"]


@pytest.mark.parametrize("flag", ["--format", "--output"])
def test_asking_for_json_under_another_name_is_pointed_at_the_json_flag(run_cli, flag):
    code, out = run_cli(["sensor", "list", flag, "json"], {})
    assert code == 2
    assert "use --json instead" in out
    assert "--fields" not in out


def test_a_field_list_under_another_name_is_still_pointed_at_fields(run_cli):
    code, out = run_cli(["sensor", "list", "--format", "entity_id,name"], {})
    assert code == 2
    assert "use --fields instead" in out


def test_an_unknown_global_flag_is_not_called_a_command(run_cli):
    code, doc = as_json(run_cli, ["--nosuchglobal", "state", "list"], {})
    assert code == 2
    assert doc["code"] == "UNKNOWN_FLAG"
    assert "unknown command" not in doc["error"]


# ------------------------------------------------------- the modes agree


def test_help_in_json_mode_is_json(run_cli):
    code, doc = as_json(run_cli, ["state", "--help"], {})
    assert code == 0
    assert doc["subcommands"] == ["list", "get <entity_id>"]
    code, doc = as_json(run_cli, ["--help"], {})
    assert code == 0
    assert "state" in doc["commands"]


def test_statistics_list_counts_against_the_whole_installation(run_cli, ws_env):
    _, everything = as_json(run_cli, ["statistics", "list"], ws_env)
    code, sums = as_json(run_cli, ["statistics", "list", "--kind", "sum"], ws_env)
    assert code == 0
    total = len(everything["statistics"])
    assert 0 < len(sums["statistics"]) < total
    assert sums["count"].endswith(f"({total} total)")


# ------------------------------------- a 200 is not a healthy Home Assistant

IMPOSTORS = {
    "html": (b"<html><body>router login</body></html>", "text/html"),
    "empty": (b"", "application/json"),
    "truncated": (b'{"message": "API running."', "application/json"),
    "other-json": (b'{"status": "ok"}', "application/json"),
}


@pytest.mark.parametrize("kind", sorted(IMPOSTORS))
@pytest.mark.parametrize("argv", [["ping"], ["state", "list"], ["service", "list"]])
def test_a_server_that_is_not_home_assistant_is_not_reported_healthy(
    run_cli, rest_env, rest_server, kind, argv
):
    rest_server.impostor = IMPOSTORS[kind]
    code, doc = as_json(run_cli, argv, rest_env)
    assert code == 1
    assert (doc["code"], doc["class"]) == ("NOT_HOME_ASSISTANT", "config")
    assert "ok" not in doc and "states" not in doc


def test_doctor_fails_its_rest_check_against_an_impostor(run_cli, rest_env, rest_server):
    rest_server.impostor = IMPOSTORS["html"]
    code, doc = as_json(run_cli, ["doctor"], rest_env)
    assert code == 1
    rest = next(row for row in doc["checks"] if row["check"] == "rest")
    assert (rest["status"], rest["code"]) == ("fail", "NOT_HOME_ASSISTANT")


def test_the_raw_escape_hatch_still_shows_what_arrived(run_cli, rest_env, rest_server):
    """`api` reaches endpoints with no known shape, so it reports rather than judges."""
    rest_server.impostor = IMPOSTORS["html"]
    code, out = run_cli(["api", "/config"], rest_env)
    assert code == 0
    assert "router login" in out


# ------------------------------------------------ a URL that cannot be one


@pytest.mark.parametrize(
    "url", ["not a url", "http://", "http://exa mple.invalid:8123", "http://host:port"]
)
@pytest.mark.parametrize("argv", [["ping"], ["entity", "list"], ["state", "list"]])
def test_a_malformed_base_url_is_a_configuration_fault(run_cli, url, argv):
    code, doc = as_json(run_cli, argv, {"HA_URL": url, "HA_TOKEN": FAKE_TOKEN})
    assert code == 1
    assert (doc["code"], doc["class"]) == ("BAD_URL", "config")
    assert not any("Retry" in line for line in doc["help"])


def test_a_path_with_a_space_is_a_missing_path_and_not_a_dropped_connection(run_cli, rest_env):
    code, doc = as_json(run_cli, ["api", "a b"], rest_env)
    assert code == 1
    assert doc["code"] == "NOT_FOUND"


# -------------------------------------------------- a binary body is not text


def test_a_binary_response_is_described_and_not_printed(run_cli, rest_env):
    code, doc = as_json(run_cli, ["api", "/camera_proxy/camera.example_camera"], rest_env)
    assert code == 0
    assert "image/jpeg" in doc["result"]
    assert "bytes" in doc["result"]
    assert "�" not in json.dumps(doc, ensure_ascii=False)
    assert "JFIF" not in doc["result"]


# ------------------------------------------------------------ every error helps


def test_a_server_error_carries_a_next_step(run_cli, rest_env, rest_server):
    rest_server.status_override = (500, "500 Internal Server Error")
    code, doc = as_json(run_cli, ["api", "/config"], rest_env)
    assert (code, doc["code"]) == (1, "SERVER_ERROR")
    assert doc["help"]


def test_a_bad_request_carries_a_next_step(run_cli, rest_env, rest_server):
    rest_server.status_override = (400, {"message": "nope"})
    code, doc = as_json(run_cli, ["api", "/config"], rest_env)
    assert (code, doc["code"]) == (1, "BAD_REQUEST")
    assert doc["help"]


def test_a_template_error_has_one_code_on_both_transports(run_cli, rest_env):
    code, doc = as_json(
        run_cli, ["template", "render", "--template", "{{ undefined_helper() }}"], rest_env
    )
    assert (code, doc["code"], doc["class"]) == (1, "TEMPLATE_ERROR", "refused")
    assert "undefined_helper" in doc["error"]
    assert doc["help"]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        ({"code": "not_found", "message": "Entity not found"}, "NOT_FOUND"),
        ({"code": "invalid_info", "message": "The name is already in use"}, "API_ERROR"),
        ({"code": "home_assistant_error", "message": "nope"}, "HOME_ASSISTANT_ERROR"),
    ],
)
def test_a_refused_websocket_command_carries_a_next_step(run_cli, ws_env, ws_server, error, code):
    ws_server.fail_next = error
    exit_code, doc = as_json(run_cli, ["ws", "entity.list"], ws_env)
    assert (exit_code, doc["code"]) == (1, code)
    assert doc["help"]


# -------------------------------------------- an id that cannot be an entity id


@pytest.mark.parametrize(
    "argv",
    [
        ["history", "get", "Example Reading Lamp"],
        ["logbook", "get", "--entity", "Example Reading Lamp", "--start", "1h"],
        ["state", "get", "Example Reading Lamp"],
    ],
)
def test_a_name_where_an_entity_id_belongs_is_answered_before_it_is_sent(
    run_cli, rest_env, rest_server, argv
):
    import shlex

    before = len(rest_server.requests)
    code, doc = as_json(run_cli, argv, rest_env)
    assert (code, doc["code"]) == (1, "NO_SUCH_ENTITY")
    assert len(rest_server.requests) == before
    # The suggestion runs as written: a multi-word name is one quoted argument.
    suggested = shlex.split(doc["help"][0].split("`")[1])
    assert suggested[-1] == "Example Reading Lamp"
    assert run_cli(suggested[1:], rest_env)[0] == 0


def test_a_missing_entity_points_at_the_registry_for_a_disabled_one(run_cli, rest_env):
    code, doc = as_json(run_cli, ["state", "get", "sensor.example_disabled_probe"], rest_env)
    assert code == 1
    assert any("hass-axi entity get sensor.example_disabled_probe" in line for line in doc["help"])


# ---------------------------------------------------------------- redaction


def synthetic_token() -> str:
    """A JWT-shaped token built at run time, so no credential shape is committed."""
    import base64

    def segment(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return ".".join(
        [
            segment(b'{"alg":"HS256","typ":"JWT"}'),
            segment(b'{"iss":"synthetic-live-finding","iat":1,"exp":2}'),
            segment(b"synthetic-signature-0123456789abcdef"),
        ]
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["state", "get", "{token}"],
        ["state", "get", "light.{token}"],
        ["entity", "get", "{token}"],
        ["statistics", "get", "{token}"],
        ["service", "get", "{token}"],
        ["history", "get", "{token}"],
        ["device", "get", "{token}"],
        ["area", "get", "{token}"],
    ],
)
def test_no_segment_of_the_token_survives_when_it_is_passed_as_an_identifier(
    run_cli, capsys, rest_server, ws_server, installation, argv
):
    token = synthetic_token()
    rest_server.token = token
    env = {**installation.environ, "HA_TOKEN": token}
    code, out = run_cli([part.format(token=token) for part in argv], env)
    err = capsys.readouterr().err
    assert code in (1, 2)
    for piece in [token, *token.split(".")]:
        assert piece not in out
        assert piece not in err


def test_a_long_value_is_redacted_before_it_is_cut(run_cli, rest_env, rest_server):
    """A cut that lands inside the token leaves a fragment no rule recognises."""
    from hass_axi.commands._common import PREVIEW_CHARS

    token = synthetic_token()
    header, payload, _signature = token.split(".")
    # Placed so the preview boundary falls inside the payload segment.
    padding = "x" * (PREVIEW_CHARS - len(header) - 1 - len(payload) // 2)
    rest_server.token = token
    rest_server.state["template"] = padding + token + " trailing text"
    code, out = run_cli(
        ["template", "render", "--template", "{{ 1 }}"], {**rest_env, "HA_TOKEN": token}
    )
    assert code == 0
    assert header not in out
    assert payload[: len(payload) // 2] not in out


def test_home_assistants_own_tokens_are_masked(run_cli, rest_env, rest_server):
    """A camera's attributes carry an access token and a signed picture URL."""
    secret = "0123456789abcdef" * 4
    rest_server.state["states"].append(
        {
            "entity_id": "camera.example_camera",
            "state": "idle",
            "attributes": {
                "friendly_name": "Example Camera",
                "access_token": secret,
                "entity_picture": f"/api/camera_proxy/camera.example_camera?token={secret}",
            },
            "last_changed": "2026-01-01T00:00:00+00:00",
            "last_updated": "2026-01-01T00:00:00+00:00",
        }
    )
    for argv in (["state", "get", "camera.example_camera"], ["api", "/states", "--full"]):
        for mode in ([], ["--json"], ["--human"]):
            code, out = run_cli([*argv, *mode], rest_env)
            assert code == 0
            assert secret not in out
            assert "camera_proxy/camera.example_camera?token=" in out


# ------------------------------------------- truncation bounds nested objects


def test_a_response_of_nested_objects_is_bounded(run_cli, rest_env, rest_server):
    from hass_axi.commands._common import RAW_BUDGET_CHARS

    services = {
        f"service_{i}": {"fields": {f"f{j}": {"description": "x" * 40} for j in range(12)}}
        for i in range(20)
    }
    rest_server.status_override = (200, {f"domain_{i}": services for i in range(60)})
    code, doc = as_json(run_cli, ["api", "/services"], rest_env)
    assert code == 0
    assert len(json.dumps(doc["result"])) <= RAW_BUDGET_CHARS
    assert "truncated" in doc
    assert any("--full" in line for line in doc["help"])

    code, full = as_json(run_cli, ["api", "/services", "--full"], rest_env)
    assert len(full["result"]) == 60
    assert "truncated" not in full


# ---------------------------------------------- names as a keyboard types them

CURLY = "Example’s Phone"  # the apostrophe a phone writes into its own name
TYPED = "Example's Phone"


def _add_curly_names(rest_server, ws_server):
    rest_server.state["states"].append(
        {
            "entity_id": "sensor.example_phone_battery",
            "state": "81",
            "attributes": {"friendly_name": f"{CURLY} Battery", "unit_of_measurement": "%"},
            "last_changed": "2026-01-01T00:00:00+00:00",
            "last_updated": "2026-01-01T00:00:00+00:00",
        }
    )
    ws_server.areas.append(
        {
            "area_id": "example_s_den",
            "name": "Example’s Den",
            "icon": None,
            "floor_id": None,
            "aliases": [],
        }
    )
    ws_server.devices.append(
        {
            "id": "device_phone",
            "name": CURLY,
            "name_by_user": None,
            "disabled_by": None,
            "area_id": "example_s_den",
            "manufacturer": "Example Co",
            "model": "Phone",
        }
    )


def test_a_typed_apostrophe_finds_a_typographic_one(
    run_cli, installation_env, rest_server, ws_server
):
    _add_curly_names(rest_server, ws_server)
    code, doc = as_json(run_cli, ["state", "list", "--search", TYPED], installation_env)
    assert code == 0
    assert [row["entity_id"] for row in doc["states"]] == ["sensor.example_phone_battery"]

    code, doc = as_json(run_cli, ["device", "list", "--search", "example's"], installation_env)
    assert [row["device_id"] for row in doc["devices"]] == ["device_phone"]

    code, doc = as_json(run_cli, ["area", "get", "Example's Den"], installation_env)
    assert (code, doc["area"]["area_id"]) == (0, "example_s_den")

    code, doc = as_json(run_cli, ["device", "get", TYPED], installation_env)
    assert (code, doc["device"]["device_id"]) == (0, "device_phone")

    code, doc = as_json(run_cli, ["device", "list", "--area", "Example's Den"], installation_env)
    assert (code, len(doc["devices"])) == (0, 1)


def test_the_typographic_spelling_still_finds_itself(
    run_cli, installation_env, rest_server, ws_server
):
    _add_curly_names(rest_server, ws_server)
    code, doc = as_json(run_cli, ["state", "list", "--search", CURLY], installation_env)
    assert (code, len(doc["states"])) == (0, 1)


@pytest.mark.parametrize(
    "argv",
    [
        ["entity", "list", "--area", "Example Rom"],
        ["state", "list", "--area", "Example Rom"],
        ["sensor", "list", "--area", "Example Rom"],
        ["device", "list", "--area", "Example Rom"],
        ["entity", "update", "light.example_lamp", "--area", "Example Rom"],
        ["device", "update", "device_one", "--area", "Example Rom"],
    ],
)
def test_a_mistyped_area_is_answered_with_the_nearest_ones_and_not_with_create(
    run_cli, installation_env, argv
):
    code, doc = as_json(run_cli, argv, installation_env)
    assert (code, doc["code"]) == (1, "NO_SUCH_AREA")
    assert doc["help"][0].startswith("did you mean: ")
    assert "'Example Room' (id example_room)" in doc["help"][0]
    assert not any("area create" in line for line in doc["help"])


def test_area_get_offers_creation_last_and_as_what_it_is(run_cli, ws_env):
    code, doc = as_json(run_cli, ["area", "get", "Example Rom"], ws_env)
    assert (code, doc["code"]) == (1, "NO_SUCH_AREA")
    assert doc["help"][0].startswith("did you mean: ")
    assert "area create --name 'Example Rom'" in doc["help"][-1]
    assert "new area" in doc["help"][-1]


def test_a_truncated_device_id_is_answered_with_the_device_it_begins(run_cli, ws_env):
    code, doc = as_json(run_cli, ["device", "get", "device_tw"], ws_env)
    assert (code, doc["code"]) == (1, "NO_SUCH_DEVICE")
    assert "(id device_two)" in doc["help"][0]


def test_a_missing_service_domain_is_not_sent_to_list_its_services(run_cli, rest_env):
    code, doc = as_json(run_cli, ["service", "get", "nodomain.turn_on"], rest_env)
    assert (code, doc["code"]) == (1, "NO_SUCH_DOMAIN")
    assert not any("--domain nodomain" in line for line in doc["help"])


def test_a_method_that_is_not_one_is_called_that(run_cli):
    code, doc = as_json(run_cli, ["api", "BOGUS", "/config"], {})
    assert (code, doc["code"]) == (2, "UNEXPECTED_ARGUMENT")
    assert "unknown method" in doc["error"]
    assert "Run `hass-axi api GET /config`" in doc["help"]


# ----------------------------------------------------------- floors and icons


def test_a_floor_that_does_not_exist_is_refused_and_nothing_is_stored(run_cli, ws_env, ws_server):
    before = json.dumps(ws_server.areas)
    code, doc = as_json(
        run_cli,
        ["area", "update", "example_room", "--floor", "no_such_floor", "--write"],
        ws_env,
    )
    assert (code, doc["code"], doc["class"]) == (1, "NO_SUCH_FLOOR", "not_found")
    assert "ground" in " ".join(doc["help"])
    assert json.dumps(ws_server.areas) == before
    assert not [c for c in ws_server.received if c["type"] == "config/area_registry/update"]


def test_a_floor_is_accepted_by_id_or_by_name(run_cli, ws_env, ws_server):
    code, doc = as_json(
        run_cli,
        ["area", "update", "example_room", "--floor", "Example Upper Floor", "--write"],
        ws_env,
    )
    assert code == 0
    assert doc["area"]["floor_id"] == "example_upper_floor"
    sent = [c for c in ws_server.received if c["type"] == "config/area_registry/update"]
    assert sent[-1]["floor_id"] == "example_upper_floor"


def test_area_create_refuses_a_floor_that_does_not_exist(run_cli, ws_env, ws_server):
    code, doc = as_json(
        run_cli,
        ["area", "create", "--name", "Example Annex", "--floor", "nowhere", "--write"],
        ws_env,
    )
    assert (code, doc["code"]) == (1, "NO_SUCH_FLOOR")
    assert len(ws_server.areas) == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["area", "update", "example_room", "--icon", "not-an-icon", "--write"],
        ["area", "create", "--name", "Example Annex", "--icon", "sofa", "--write"],
        ["entity", "update", "light.example_lamp", "--icon", "sofa", "--write"],
    ],
)
def test_an_icon_that_is_not_one_is_refused_before_anything_is_read(run_cli, argv):
    code, doc = as_json(run_cli, argv, UNREACHABLE)
    assert (code, doc["code"], doc["class"]) == (2, "BAD_ICON", "usage")


# ------------------------------------------------------------- service targets


def test_a_preview_says_when_a_service_needs_a_target_it_was_not_given(run_cli, rest_env):
    code, doc = as_json(run_cli, ["service", "call", "light.turn_on"], rest_env)
    assert code == 0
    assert "refuses it without one" in doc["target"]
    assert "--target-entity" in doc["help"][0]


def test_a_call_refused_for_want_of_a_target_says_so(run_cli, rest_env, rest_server):
    code, doc = as_json(run_cli, ["service", "call", "light.turn_on", "--write"], rest_env)
    assert (code, doc["code"], doc["class"]) == (1, "MISSING_TARGET", "refused")
    assert "--target-entity" in doc["help"][0]
    # The refusal is Home Assistant's own, and it says nothing: an empty 400.
    assert rest_server.requests[0]["path"] == "/api/services/light/turn_on"


# ------------------------------------------------------------------ recorder


def _pin_now(monkeypatch, iso: str):
    from datetime import datetime

    from hass_axi.commands import _window

    moment = datetime.fromisoformat(iso)
    monkeypatch.setattr(_window, "now", lambda: moment)


@pytest.mark.parametrize("period", [None, "day", "week", "month", "hour"])
def test_a_statistics_total_covers_the_window_and_nothing_before_it(
    run_cli, ws_env, ws_server, monkeypatch, period
):
    """Three days of a meter at 1 kWh an hour, asked for the last 36 hours."""
    from conftest import meter_rows

    ws_server.statistics["sensor.example_legacy_meter"] = meter_rows([1.0] * 72)
    _pin_now(monkeypatch, "2026-01-04T00:00:00+00:00")
    argv = ["statistics", "get", "sensor.example_legacy_meter", "--start", "36h"]
    if period:
        argv += ["--period", period]
    code, doc = as_json(run_cli, argv, ws_env)
    assert code == 0
    # 36 hours at 1 kWh. Daily buckets sum to 48, a weekly or monthly one to 72.
    assert doc["statistics"][0]["total"] == 36
    assert "caveats" not in doc["statistics"][0]


def test_a_mean_is_read_from_inside_the_window_too(run_cli, ws_env, ws_server, monkeypatch):
    from conftest import hourly_rows

    values = [10.0] * 24 + [20.0] * 24
    ws_server.statistics["sensor.example_temperature"] = hourly_rows(
        [{"mean": v, "min": v - 1, "max": v + 1} for v in values]
    )
    _pin_now(monkeypatch, "2026-01-03T00:00:00+00:00")
    code, doc = as_json(
        run_cli,
        ["statistics", "get", "sensor.example_temperature", "--start", "36h", "--period", "day"],
        ws_env,
    )
    assert code == 0
    row = doc["statistics"][0]
    # Twelve hours at 10 and twenty-four at 20. The mean of the two daily
    # means is 15, which weights half a day as a whole one.
    assert (row["mean"], row["min"], row["max"]) == (16.667, 9, 21)


def test_one_bucket_that_dwarfs_the_rest_is_called_out(run_cli, ws_env, ws_server, monkeypatch):
    from conftest import meter_rows

    changes = [1.0] * 24
    changes[9] = 5000.0
    ws_server.statistics["sensor.example_legacy_meter"] = meter_rows(changes)
    _pin_now(monkeypatch, "2026-01-02T00:00:00+00:00")
    code, doc = as_json(
        run_cli, ["statistics", "get", "sensor.example_legacy_meter", "--start", "24h"], ws_env
    )
    assert code == 0
    row = doc["statistics"][0]
    # Stated, never adjusted: the total still includes the bucket.
    assert row["total"] == 5023
    caveat = next(c for c in row["caveats"] if "one bucket" in c)
    assert "2026-01-01T09:00:00+00:00" in caveat
    assert "5000 kWh" in caveat
    assert "the total includes it" in caveat
    assert doc["help"]


def test_an_even_meter_gets_no_outlier_caveat_and_no_empty_help(run_cli, ws_env, monkeypatch):
    _pin_now(monkeypatch, "2026-01-02T00:00:00+00:00")
    code, doc = as_json(
        run_cli, ["statistics", "get", "sensor.example_legacy_meter", "--start", "24h"], ws_env
    )
    assert code == 0
    assert "caveats" not in doc["statistics"][0]
    # TOON omits an empty help block; JSON has to omit it too.
    assert "help" not in doc


def test_a_whole_number_is_the_same_in_every_mode(run_cli, ws_env, monkeypatch):
    _pin_now(monkeypatch, "2026-01-02T00:00:00+00:00")
    argv = ["statistics", "get", "sensor.example_legacy_meter", "--start", "24h"]
    _, toon = run_cli(argv, ws_env)
    _, human = run_cli([*argv, "--human"], ws_env)
    _, doc = as_json(run_cli, argv, ws_env)
    assert doc["statistics"][0]["total"] == 12
    assert "12.0" not in toon and "12.0" not in human


def test_history_says_what_part_of_the_window_it_does_not_cover(
    run_cli, rest_env, rest_server, monkeypatch
):
    from conftest import _at

    # An entity the recorder first saw 30 hours into a 48-hour window.
    rest_server.state["history"]["light.example_lamp"] = [(_at(6), "off"), (_at(18), "on")]
    _pin_now(monkeypatch, "2026-01-02T00:00:00+00:00")
    code, doc = as_json(
        run_cli, ["history", "get", "light.example_lamp", "--start", "48h"], rest_env
    )
    assert code == 0
    note = doc["history"][0]["note"]
    assert "no recorded state before 2026-01-01T06:00:00+00:00" in note
    assert "1d6h of the window is not covered" in note


def test_history_covering_the_whole_window_says_nothing_extra(run_cli, rest_env, monkeypatch):
    _pin_now(monkeypatch, "2026-01-02T00:00:00+00:00")
    code, doc = as_json(
        run_cli, ["history", "get", "light.example_lamp", "--start", "24h"], rest_env
    )
    assert code == 0
    assert "note" not in doc["history"][0]


def test_a_clipped_history_says_how_many_rows_exist(run_cli, rest_env, monkeypatch):
    _pin_now(monkeypatch, "2026-01-02T00:00:00+00:00")
    code, doc = as_json(
        run_cli,
        ["history", "get", "sensor.example_temperature", "--start", "24h", "--limit", "2"],
        rest_env,
    )
    assert code == 0
    entity = doc["history"][0]
    total = int(entity["rows"].rsplit(" ", 1)[1])
    assert total > 2
    assert any(f"--limit {total}" in line for line in doc["help"])


# ------------------------------------------------------------------ home view


def test_the_home_view_does_not_promise_two_commands_list_its_count(
    run_cli, installation_env, rest_server
):
    for index in range(12):
        rest_server.state["states"].append(
            {
                "entity_id": f"button.example_button_{index}",
                "state": "unknown",
                "attributes": {"friendly_name": f"Example Button {index}"},
                "last_changed": "2026-01-01T00:00:00+00:00",
                "last_updated": "2026-01-01T00:00:00+00:00",
            }
        )
        rest_server.state["states"].append(
            {
                "entity_id": f"sensor.example_gone_{index}",
                "state": "unavailable",
                "attributes": {"friendly_name": f"Example Gone {index}"},
                "last_changed": "2026-01-01T00:00:00+00:00",
                "last_updated": "2026-01-01T00:00:00+00:00",
            }
        )
    code, doc = as_json(run_cli, [], installation_env)
    assert code == 0
    line = next(line for line in doc["help"] if "not reporting" in line)
    code, unavailable = as_json(
        run_cli, ["state", "list", "--state", "unavailable", "--limit", "500"], installation_env
    )
    code, unknown = as_json(
        run_cli, ["state", "list", "--state", "unknown", "--limit", "500"], installation_env
    )
    # Each number in the sentence is one a named command returns.
    assert f"for the {len(unavailable['states'])} unavailable" in line
    assert f"all {len(unknown['states'])} entities whose state is unknown" in line


# ------------------------------------------------------------ session records


def test_session_ends_arriving_together_all_keep_their_record(tmp_path):
    """Twenty-four hooks at once into one state file used to keep six."""
    import os
    import subprocess
    import sys

    env = {**os.environ, "XDG_STATE_HOME": str(tmp_path)}
    env.pop("HASS_AXI_READ_ONLY", None)
    env.pop("HA_AXI_READ_ONLY", None)
    workers = []
    for n in range(24):
        payload = {
            "session_id": f"session-{n}",
            "cwd": f"/example/session-{n}",
            "commands": ["hass-axi state list"],
        }
        worker = subprocess.Popen(
            [sys.executable, "-m", "hass_axi", "context", "end"],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        worker.stdin.write(json.dumps(payload))
        worker.stdin.close()
        workers.append(worker)
    assert [worker.wait() for worker in workers] == [0] * 24
    for worker in workers:
        assert "recorded: 1 hass-axi command(s)" in worker.stdout.read()
        worker.stdout.close()
    records = json.loads(next(tmp_path.rglob("sessions.json")).read_text(encoding="utf-8"))
    assert sorted(r["session"] for r in records) == sorted(f"session-{n}" for n in range(24))


# ------------------------------------------------- hook files it cannot fix


def _setup(run_cli, home, *argv):
    return as_json(run_cli, ["setup", "hooks", *argv, "--home", str(home)], {})


@pytest.mark.parametrize(
    "content",
    ['["somebody", "wrote", "a", "list"]', '{"hooks": "a string, not a table"}', '"just text"'],
)
def test_a_settings_file_in_another_shape_is_left_alone_and_reported(run_cli, tmp_path, content):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(content, encoding="utf-8")
    code, doc = _setup(run_cli, tmp_path)
    assert code == 1
    rows = {row["target"]: row["status"] for row in doc["targets"]}
    assert rows["claude-code"] == rows["claude-code-session-end"] == "failed"
    assert settings.read_text(encoding="utf-8") == content
    assert doc["errors"]


def test_a_codex_config_that_does_not_parse_is_not_appended_to(run_cli, tmp_path):
    pytest.importorskip("tomllib")
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    broken = 'model = "example\n[unterminated\n'
    config.write_text(broken, encoding="utf-8")
    code, doc = _setup(run_cli, tmp_path)
    assert code == 1
    rows = {row["target"]: row["status"] for row in doc["targets"]}
    assert rows["codex-features"] == "skipped"
    assert config.read_text(encoding="utf-8") == broken
    assert any("TOML" in line for line in doc["errors"])
    code, doc = _setup(run_cli, tmp_path, "status")
    rows = {row["target"]: row["status"] for row in doc["targets"]}
    assert rows["codex-features"] == "missing"


def test_a_home_that_does_not_exist_is_refused_and_not_created(run_cli, tmp_path):
    missing = tmp_path / "no-such-home"
    code, doc = _setup(run_cli, missing)
    assert (code, doc["code"]) == (2, "UNWRITABLE")
    assert not missing.exists()


def test_install_then_remove_leaves_a_fresh_home_without_empty_files(run_cli, tmp_path):
    assert _setup(run_cli, tmp_path)[0] == 0
    code, doc = _setup(run_cli, tmp_path, "remove")
    assert code == 0
    assert not (tmp_path / ".claude" / "settings.json").exists()
    assert not (tmp_path / ".codex" / "hooks.json").exists()
    # The same rows install and status report, so a missing row reads as a fault.
    install_rows = [row["target"] for row in _setup(run_cli, tmp_path, "status")[1]["targets"]]
    assert [row["target"] for row in doc["targets"]] == install_rows


def test_remove_keeps_a_file_that_holds_somebody_elses_settings(run_cli, tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"theme": "dark"}', encoding="utf-8")
    assert _setup(run_cli, tmp_path)[0] == 0
    assert _setup(run_cli, tmp_path, "remove")[0] == 0
    assert json.loads(settings.read_text(encoding="utf-8")) == {"theme": "dark"}


# ------------------------------------------------- a folded tie is not a guess


def test_two_areas_that_fold_to_one_name_are_reported_and_neither_is_picked(
    run_cli, ws_env, ws_server
):
    for area_id, name in (("cafe", "Caf\u00e9"), ("cafe_2", "Cafe")):
        ws_server.areas.append(
            {"area_id": area_id, "name": name, "icon": None, "floor_id": None, "aliases": []}
        )
    code, doc = as_json(run_cli, ["area", "get", "CAFE"], ws_env)
    assert (code, doc["code"], doc["class"]) == (1, "AMBIGUOUS_AREA", "not_found")
    assert "(id cafe)" in doc["help"][0] and "(id cafe_2)" in doc["help"][0]
    # The id still names exactly one of them.
    code, doc = as_json(run_cli, ["area", "get", "cafe_2"], ws_env)
    assert (code, doc["area"]["name"]) == (0, "Cafe")


def test_a_dash_and_an_accent_are_typed_plain(run_cli, ws_env, ws_server):
    ws_server.areas.append(
        {
            "area_id": "north_east",
            "name": "North\u2013East Caf\u00e9\u2026",
            "icon": None,
            "floor_id": None,
            "aliases": [],
        }
    )
    code, doc = as_json(run_cli, ["area", "get", "north-east cafe..."], ws_env)
    assert (code, doc["area"]["area_id"]) == (0, "north_east")


# ------------------------------------------------------ what the review found


def test_a_query_written_into_the_path_is_still_a_query(run_cli, rest_env, rest_server):
    code, _ = run_cli(["api", "/states?entity_id=light.example_lamp"], rest_env)
    assert code == 0
    sent = rest_server.requests[-1]
    assert (sent["path"], sent["query"]) == ("/api/states", "entity_id=light.example_lamp")
    code, _ = run_cli(["api", "/states?a=1", "--query", "b=2"], rest_env)
    assert rest_server.requests[-1]["query"] == "a=1&b=2"


def test_a_wide_flat_answer_of_long_strings_is_bounded_too(run_cli, rest_env, rest_server):
    """No list, no nesting, no string over the per-string limit -- and 24,000 characters."""
    from hass_axi.commands._common import RAW_BUDGET_CHARS

    rest_server.status_override = (200, {f"field_{i}": "x" * 1190 for i in range(20)})
    code, doc = as_json(run_cli, ["api", "/config"], rest_env)
    assert code == 0
    assert len(json.dumps(doc["result"])) <= RAW_BUDGET_CHARS
    assert "20 strings cut to 200 chars" in doc["truncated"]
    code, full = as_json(run_cli, ["api", "/config", "--full"], rest_env)
    assert len(full["result"]["field_0"]) == 1190


def test_an_area_name_that_cannot_be_looked_up_is_not_sent_as_an_id(
    run_cli, installation_env, rest_server, ws_server
):
    ws_server.fail_all = {"code": "unknown_error", "message": "Unknown error"}
    code, doc = as_json(
        run_cli,
        ["service", "call", "light.turn_on", "--target-area", "Example Room", "--write"],
        installation_env,
    )
    assert code == 1
    assert doc["code"] == "API_ERROR"
    assert not [r for r in rest_server.requests if r["method"] == "POST"]


def test_buckets_with_no_hourly_rows_behind_them_are_summed_and_said():
    from datetime import datetime, timedelta, timezone

    from hass_axi.toolkit import recorder

    start = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    day = {
        "start": int((start - timedelta(hours=12)).timestamp() * 1000),
        "end": int((start + timedelta(hours=12)).timestamp() * 1000),
        "change": 24.0,
        "state": 124.0,
    }
    meta = {"statistic_id": "sensor.example_meter", "has_sum": True}
    summary = recorder.summarize(
        meta, [day], start, "day", end=start + timedelta(hours=12), hourly=[]
    )
    assert summary["total"] == 24
    assert any("more than the window asked for" in c for c in summary["caveats"])

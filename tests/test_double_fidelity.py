"""What the doubles must keep modelling, stated as tests rather than as prose.

Every defect this file guards against reached a published release behind a green
suite, and every one of them was a *fixture* problem rather than a code problem:
the doubles described a Home Assistant tidier than any that exists -- an entity
registry where each entry names itself, a state that is never `unknown`, a
service that documents itself, an entity that is never disabled. All four are the
majority case upstream, and all four were reachable by hand.

So the assertions here are about the fixture set, not about `hass-axi`. A fixture
edit that quietly removes one of these shapes fails here, where the reason is
written down, rather than three releases later on somebody's installation.
"""

from __future__ import annotations

import json

import pytest

from conftest import (
    DEVICE_REGISTRY,
    ENTITY_REGISTRY,
    SERVICES,
    STATES,
    capability_masks,
    declared_fields,
    displayed_name,
    extended_entry,
    slugify,
)
from hass_axi.ws import REGISTRY


def _states_by_id() -> dict:
    return {state["entity_id"]: state for state in STATES}


# ------------------------------------------------------------- the name rule


def test_every_registry_entry_agrees_with_the_state_home_assistant_publishes():
    """The two halves of the double have to describe one installation.

    `friendly_name` on a state and the composed name in the registry come from
    one function upstream -- `entity_registry.async_get_full_entity_name`, which
    `Entity.__async_calculate_state` calls for every registered entity. A fixture
    set where the two disagreed would let a client be right about one view and
    wrong about the other with nothing to notice it.
    """
    states = _states_by_id()
    for entry in ENTITY_REGISTRY:
        state = states.get(entry["entity_id"])
        if state is None:
            continue
        published = (state.get("attributes") or {}).get("friendly_name", "")
        assert displayed_name(entry) == published, entry["entity_id"]


def test_the_registry_carries_entries_that_name_none_of_themselves():
    """The case that was missing, and the one that caused the headline defect.

    On a real installation most entries set `has_entity_name` and take all or
    part of their name from the device. A fixture set where every entry carried
    its own `original_name` meant the composition rule never had to exist.
    """
    from_device_alone = [
        entry
        for entry in ENTITY_REGISTRY
        if not entry["name"] and not entry["original_name"] and entry["device_id"]
    ]
    assert from_device_alone, "no entry takes its whole name from its device"
    for entry in from_device_alone:
        assert displayed_name(entry), entry["entity_id"]

    composed = [
        entry
        for entry in ENTITY_REGISTRY
        if not entry["name"] and entry["original_name"] and entry["device_id"]
    ]
    assert composed, "no entry takes a device prefix in front of its own name"


def test_the_registry_carries_both_settings_of_has_entity_name():
    """`has_entity_name` is not the gate, and only both cases can prove it.

    Home Assistant applies the flag on the way *out*, by publishing
    `original_name_unprefixed` under the `original_name` key, and then composes
    device and entity halves for either kind of entry. A fixture set holding only
    one setting would let a wrong reading of the flag pass.
    """
    settings = {entry["has_entity_name"] for entry in ENTITY_REGISTRY}
    assert settings == {True, False}
    prefixed = [
        entry
        for entry in ENTITY_REGISTRY
        if not entry["has_entity_name"] and entry["device_id"] and entry["original_name"]
    ]
    assert prefixed, "nothing exercises composition with has_entity_name unset"


def test_a_user_override_is_present_and_is_never_composed_over():
    overrides = [entry for entry in ENTITY_REGISTRY if entry["name"]]
    assert overrides
    for entry in overrides:
        assert displayed_name(entry) == entry["name"]


# ------------------------------------------- the value shapes that were absent


def test_the_states_hold_an_unknown_as_well_as_an_unavailable():
    values = {state["state"] for state in STATES}
    assert "unknown" in values, "nothing is `unknown`, so the two can be conflated freely"
    assert "unavailable" in values


def test_a_registry_entry_exists_that_has_no_state_at_all():
    """Disabled entries are ordinary, and they appear in one view only."""
    disabled = [entry for entry in ENTITY_REGISTRY if entry["disabled_by"]]
    assert disabled
    known = set(_states_by_id())
    for entry in disabled:
        assert entry["entity_id"] not in known, entry["entity_id"]


def test_a_state_exists_that_has_no_registry_entry():
    registered = {entry["entity_id"] for entry in ENTITY_REGISTRY}
    assert [state for state in STATES if state["entity_id"] not in registered]


def test_entities_exist_with_no_device_and_with_no_area():
    assert [entry for entry in ENTITY_REGISTRY if not entry["device_id"]]
    assert [entry for entry in ENTITY_REGISTRY if not entry["area_id"]]
    # And a device in no area, which is how an entity ends up in none either.
    assert [device for device in DEVICE_REGISTRY if not device["area_id"]]


def test_a_device_carries_a_user_rename_which_is_what_gets_displayed():
    renamed = [device for device in DEVICE_REGISTRY if device["name_by_user"]]
    assert renamed
    for device in renamed:
        assert device["name_by_user"] != device["name"]


def test_every_state_carries_the_keys_home_assistant_sends():
    for state in STATES:
        assert set(state) >= {
            "attributes",
            "context",
            "entity_id",
            "last_changed",
            "last_reported",
            "last_updated",
            "state",
        }, state["entity_id"]


def test_every_registry_entry_carries_the_keys_home_assistant_publishes():
    """`as_partial_dict` sends 21 keys, and the double sent 11.

    Nothing in `hass-axi` reads most of them. That is exactly why they belong here:
    a client that starts to should find out against the double.
    """
    expected = {
        "area_id",
        "categories",
        "config_entry_id",
        "config_subentry_id",
        "created_at",
        "device_id",
        "disabled_by",
        "entity_category",
        "entity_id",
        "has_entity_name",
        "hidden_by",
        "icon",
        "id",
        "labels",
        "modified_at",
        "name",
        "options",
        "original_name",
        "platform",
        "translation_key",
        "unique_id",
    }
    for entry in ENTITY_REGISTRY:
        assert set(entry) == expected, entry["entity_id"]


def test_the_extended_entry_is_a_different_shape_from_the_listed_one():
    """`get` and `update` answer with `extended_dict`, and `list` does not.

    An entity with no aliases comes back as `[None]` rather than `[]`, because
    the empty alias is serialised rather than dropped.
    """
    entry = ENTITY_REGISTRY[0]
    extended = extended_entry(entry)
    assert set(extended) - set(entry) == {
        "aliases",
        "capabilities",
        "device_class",
        "original_device_class",
        "original_icon",
    }
    assert extended["aliases"] == [None]


def test_most_services_and_fields_publish_no_prose():
    """Almost nothing upstream documents itself over `/api/services`.

    The descriptions moved into the translation files years ago and
    `/api/services` does not serve those, so a real installation publishes a
    `description` on a handful of services and on no fields at all. A model where
    everything described itself let a view be designed around a column that
    arrives empty.
    """
    undocumented = [
        (entry["domain"], name)
        for entry in SERVICES
        for name, description in entry["services"].items()
        if not description.get("description")
    ]
    assert undocumented, "every service documents itself, which no installation does"

    fields_without_prose = [
        name
        for entry in SERVICES
        for description in entry["services"].values()
        for name, field in declared_fields(description).items()
        if not field.get("description")
    ]
    assert fields_without_prose


def test_a_response_capable_service_also_publishes_a_capability_requirement():
    """The pairing that makes a filtered-out refusal reachable.

    Home Assistant narrows a target's candidates by availability and capability
    before deciding it matched nothing, and the `return_response` refusal that
    follows carries nothing to read on the wire. A model where no
    response-capable service publishes a mask leaves the capability half of
    that verdict unexercisable against the double, which is how the
    availability half went unexercised too.
    """
    paired = [
        (entry["domain"], name)
        for entry in SERVICES
        for name, description in entry["services"].items()
        if isinstance(description.get("response"), dict)
        and capability_masks(description, entry["domain"])
    ]
    assert paired, "no response-capable service publishes a capability requirement"


def test_a_response_capable_domain_holds_an_unavailable_entity():
    """The other half of the same pairing, on the entity side.

    The filtering rule drops `unavailable` candidates before the not-matched
    check, so a response service whose domain holds no such entity can never
    produce the refusal that rule exists to explain.
    """
    response_domains = {
        entry["domain"]
        for entry in SERVICES
        for description in entry["services"].values()
        if isinstance(description.get("response"), dict)
    }
    states = _states_by_id()
    unavailable = [
        state["entity_id"]
        for state in STATES
        if state["state"] == "unavailable"
        and state["entity_id"].split(".", 1)[0] in response_domains
    ]
    assert unavailable, "no unavailable entity sits in a response-capable domain"
    for entity_id in unavailable:
        assert entity_id in states


# ---------------------------------------------------- what the doubles answer


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_websocket_command_the_cli_ships_is_modelled(name, ws_server, ws_env):
    """Six of the fourteen fell through to `unknown_command` and were untested.

    A command with no branch in the double is a command with no coverage at all,
    which is the gap most likely to grow the next defect. Sending each one with
    its declared parameters is the cheapest possible proof that the double knows
    what the CLI can ask for.
    """
    from hass_axi.config import load
    from hass_axi.ws import WsClient

    sample = {
        "entity_id": "light.example_lamp",
        "area_id": "example_hall",
        "device_id": "device_one",
        "name": "Example Study",
        "start_time": "2026-01-01T00:00:00+00:00",
        "statistic_ids": ["sensor.example_legacy_meter"],
        "period": "hour",
    }
    command = REGISTRY[name]
    params = {key: sample[key] for key in command.required if key in sample}
    assert len(params) == len(command.required), f"{name} needs a sample value"

    with WsClient(load(ws_env)) as client:
        client.run(name, params)

    answered = ws_server.received[-1]
    assert answered["type"] == command.type


def test_slugify_matches_how_home_assistant_mints_an_area_id():
    assert slugify("Example Study") == "example_study"
    assert slugify("Example Nook & Study 2") == "example_nook_study_2"
    assert slugify("Example's Study") == "example_s_study"


def test_the_doubles_refusals_carry_no_body_where_home_assistant_carries_none(rest_server):
    """A `HomeAssistantError` reaches the wire as a bodyless plain-text 500.

    Not a JSON `400` naming the entity: `entity_service_call` raises, aiohttp
    apologises, and the client is left with a status number that means nothing.
    A double that explained itself here would license a client that could never
    explain a real refusal, so the assertion is that there is nothing to read.
    """
    import urllib.error
    import urllib.request

    from conftest import FAKE_TOKEN

    request = urllib.request.Request(
        f"{rest_server.url}/api/services/media_player/media_next_track",
        data=json.dumps({"entity_id": ["media_player.example_speaker"]}).encode(),
        headers={"Authorization": f"Bearer {FAKE_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(request, timeout=5)
    assert raised.value.code == 500
    body = raised.value.read().decode()
    assert "media_player" not in body
    assert "does not support" not in body


def _refused(rest_server, *, token=None, path="/api/states"):
    """One request against the REST double, returning the refusal it raised."""
    import urllib.error
    import urllib.request

    from conftest import FAKE_TOKEN

    request = urllib.request.Request(
        f"{rest_server.url}{path}",
        headers={"Authorization": f"Bearer {FAKE_TOKEN if token is None else token}"},
    )
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(request, timeout=5)
    return raised.value


def test_a_rejected_token_explains_nothing_because_a_real_one_does_not(rest_server):
    """401 is aiohttp's own status line in text/plain, and nothing else.

    `helpers/http.py` raises a bare `HTTPUnauthorized` when a view requires auth
    and the request has none, and an `HTTPException` with no text renders as
    `"<status>: <reason>"`. The double used to answer `{"message":
    "Unauthorized"}`, which is generous in exactly the way that lets a client
    pass by reading a field a real instance never sends.
    """
    refusal = _refused(rest_server, token="not-the-right-token")
    assert refusal.code == 401
    assert refusal.headers.get("Content-Type", "").startswith("text/plain")
    assert refusal.read().decode() == "401: Unauthorized"


def test_a_banned_address_is_refused_before_the_token_is_read(rest_server):
    """403 comes from a middleware, which is why a valid token does not help.

    `components/http/ban.py` raises a bare `HTTPForbidden` for an address in
    `ip_bans_lookup` before the handler runs at all. Modelling it as a refusal
    the token could have avoided would make the whole point of the class --
    that minting a new token is the wrong next move -- untestable.
    """
    rest_server.forbidden = True
    refusal = _refused(rest_server)
    assert refusal.code == 403
    assert refusal.read().decode() == "403: Forbidden"


def test_a_restarting_instance_answers_with_no_body_at_all(rest_server):
    """503 while `hass.is_stopping`, and it is not even an `HTTPException`.

    `helpers/http.py` returns `web.Response(status=SERVICE_UNAVAILABLE)` -- a
    plain response, so there is no `"503: ..."` line either. Nothing to read at
    all is the shape, and a client that needed a reason here would have none.
    """
    rest_server.stopping = True
    refusal = _refused(rest_server)
    assert refusal.code == 503
    assert refusal.read() == b""


def test_an_unknown_websocket_command_is_refused_without_naming_itself(ws_server, ws_env):
    """`connection.py` sends a fixed `"Unknown command."` and logs the type.

    The type never travels: it goes to `logger.info`, not into the message. A
    double that echoed it back would let a client read a command name out of a
    message that has never carried one.
    """
    from hass_axi.config import load
    from hass_axi.errors import NotFound
    from hass_axi.ws import WsClient

    with WsClient(load(ws_env)) as client, pytest.raises(NotFound) as raised:
        client.send_command("config/nothing_registry/list")
    assert raised.value.code == "NO_SUCH_WS_COMMAND"
    assert [c for c in ws_server.received if c["type"] == "config/nothing_registry/list"]


# ------------------------------------------------------------- the recorder


def _get(server, path):
    import urllib.error
    import urllib.request

    from conftest import FAKE_TOKEN

    request = urllib.request.Request(
        f"{server.url}{path}", headers={"Authorization": f"Bearer {FAKE_TOKEN}"}
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_a_history_window_with_no_end_ends_a_day_after_it_starts(rest_server):
    """`HistoryPeriodView` does not end an open window now, and a client that relies on it is wrong."""
    _, answer = _get(
        rest_server,
        "/api/history/period/2025-12-31T00:00:00%2B00:00?filter_entity_id=light.example_lamp",
    )
    # The changes at 06:00 and 18:00 on the fixture day are outside that one day.
    assert [row["state"] for row in answer[0]] == ["on"]


def test_history_refuses_a_request_without_its_entity_filter(rest_server):
    status, answer = _get(rest_server, "/api/history/period/2026-01-01T00:00:00%2B00:00")
    assert (status, answer) == (400, {"message": "filter_entity_id is missing"})


def test_a_minimal_history_carries_attributes_on_its_first_row_only(rest_server):
    _, answer = _get(
        rest_server,
        "/api/history/period/2026-01-01T00:00:00%2B00:00"
        "?filter_entity_id=light.example_lamp&end_time=2026-01-02T00:00:00%2B00:00"
        "&minimal_response",
    )
    first, *rest = answer[0]
    assert "attributes" in first and "entity_id" in first
    assert all(set(row) == {"state", "last_changed"} for row in rest)


def test_a_statistic_asked_for_a_type_it_does_not_keep_answers_empty(ws_server, ws_env):
    from hass_axi.config import load
    from hass_axi.ws import WsClient

    with WsClient(load(ws_env)) as client:
        result = client.run(
            "statistics.during_period",
            {
                "start_time": "2026-01-01T00:00:00+00:00",
                "statistic_ids": ["sensor.example_temperature", "example:grid_import"],
                "period": "hour",
                "types": ["change"],
            },
        )
    # A mean statistic has no change: the rows are there and the value is not.
    assert {row["change"] for row in result["sensor.example_temperature"]} == {None}
    # A statistic with no rows in the window is absent, not an empty list.
    assert "example:grid_import" not in result

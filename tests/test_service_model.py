"""`service call` and `service get` against Home Assistant's own service model.

Three of the cases here are regressions for defects an architecture review found
on the `service call` path, all with one root cause: it was the only command
that sent without ever consulting the model Home Assistant publishes at
`GET /api/services`.

1. A refused call was a dead end -- `error` and `code`, and no `help[]` block at
   all, on the most-used mutation path in the tool.
2. A response-only service leaked Home Assistant's own vocabulary: the agent was
   told to add a `return_response` query parameter it has no way to set, rather
   than the `--response` flag it does.
3. "0 states changed" could not distinguish "nothing to do" from "nothing
   targeted". A call that reached no entity at all read exactly like one that
   reached three and found them already as asked.

The REST double models the refusals rather than the successes, so every case
below fails the way a real instance would: an unknown service and a rejected
field both come back as an empty 400, because that is all Home Assistant sends.

**The model reader itself is no longer in this repository.** `service call` and
`service get` read `axi_toolkit.ha.services`, which is the old `servicemodel.py`
moved whole. Four cases here stated that module's own rules directly -- the two
capability rules and the empty readings of `target_domains` -- and they went
across with the code, unchanged apart from the module's name. They are not
restored here: two copies of one test is the divergence the shared package
exists to end, and the rule that matters most to this tool is exercised end to
end below anyway, by `test_a_service_with_an_upstream_fallback_is_not_gated`.
Everything in this file drives the command path, which is this repository's own.
"""

from __future__ import annotations

import json

from conftest import FEATURE_NEXT_TRACK, FEATURE_VOLUME_SET

# --------------------------------------------------- defect 1: the dead end


def test_a_refused_service_call_names_the_services_that_do_exist(run_cli, rest_env):
    """Regression: a refused call answered with `error` and `code` and nothing else.

    Home Assistant sends an empty 400 for an unknown service, so the only place
    the real names can come from is the model -- fetched here, on failure only.
    """
    code, out = run_cli(
        ["service", "call", "light.turn_onn", "--target-entity", "light.example_lamp"],
        rest_env,
    )
    assert code == 1
    assert "help[" in out, "a refused service call must never be a dead end"
    assert "light.turn_on" in out
    assert "hass-axi service list --domain light" in out


def test_a_refused_call_on_an_unknown_domain_lists_the_domains(run_cli, rest_env):
    code, out = run_cli(["service", "call", "lightt.turn_on"], rest_env)
    assert code == 1
    assert "no service domain named 'lightt'" in out
    assert "light" in out
    assert "hass-axi service list" in out


def test_a_field_the_service_does_not_declare_is_named_back(run_cli, rest_env):
    """The double answers an undeclared key with an empty 400, as PREVENT_EXTRA does.

    Which field was wrong is knowable only from the model, so this is the same
    enrichment seen from the other side.
    """
    code, out = run_cli(
        ["service", "call", "light.turn_on", "--data", "brightnes=180"],
        rest_env,
    )
    assert code == 1
    assert "brightnes" in out
    assert "brightness" in out
    assert "hass-axi service get light.turn_on" in out


def test_a_missing_required_field_is_named_rather_than_guessed_at(run_cli, rest_env):
    code, out = run_cli(["service", "call", "calendar.get_events", "--response"], rest_env)
    assert code == 1
    assert "start_date_time" in out
    assert "hass-axi service get calendar.get_events" in out


def test_the_enrichment_survives_a_model_that_cannot_be_read(run_cli, rest_env, rest_server):
    """A failed enrichment must not replace the original error, or drop the help block."""
    original = rest_server.state["services"]
    rest_server.state["services"] = "not a list at all"
    try:
        code, out = run_cli(["service", "call", "light.turn_onn"], rest_env)
    finally:
        rest_server.state["services"] = original
    assert code == 1
    assert "HTTP 400" in out
    assert "help[" in out
    assert "hass-axi service list" in out


# ------------------------------------ defect 2: the leaked upstream vocabulary


def test_a_response_only_service_asks_for_the_flag_not_the_query_parameter(run_cli, rest_env):
    """Regression: the agent was told to add `?return_response`, which it cannot.

    Home Assistant's message names a query parameter of its own REST API. The
    flag that actually does it is `--response`, and that is what must be said.
    """
    code, out = run_cli(
        [
            "service",
            "call",
            "calendar.list_events",
            "--data",
            "start_date_time=2026-01-01 00:00:00",
        ],
        rest_env,
    )
    assert code == 1
    assert "return_response" not in out, "Home Assistant's own vocabulary must not leak"
    assert "--response" in out
    assert "hass-axi service call calendar.list_events --response" in out


def test_asking_for_a_response_a_service_cannot_give_says_to_drop_the_flag(run_cli, rest_env):
    code, out = run_cli(
        ["service", "call", "light.turn_on", "--target-entity", "light.example_lamp", "--response"],
        rest_env,
    )
    assert code == 1
    assert "return_response" not in out
    assert "--response" in out
    assert "does not return a response" in out


# ------------------------------------------ defect 3: 0 states changed, but why


def test_an_empty_change_set_says_whether_anything_was_targeted(run_cli, installation_env):
    """Regression: a call that reached no entity read like one that had nothing to do.

    `example_room` holds a light, so `switch.turn_off` aimed at it resolves to
    no switch at all. That is not the same answer as "the switch was already
    off", and reporting exit 0 with the identical sentence is a soft failure.
    """
    code, out = run_cli(
        ["service", "call", "switch.toggle", "--target-area", "example_room"],
        installation_env,
    )
    assert code == 1
    assert "0 entities" in out
    assert "example_room" in out


def test_an_area_that_does_not_exist_is_reported_rather_than_accepted(run_cli, installation_env):
    code, out = run_cli(
        ["service", "call", "light.turn_on", "--target-area", "nowhere"],
        installation_env,
    )
    assert code == 1
    assert "no area with id or name 'nowhere'" in out
    assert "hass-axi area list" in out


def test_an_area_name_passed_where_an_id_belongs_is_diagnosed(run_cli, installation_env):
    """`--target-area` is an area_id; a name silently matches nothing upstream."""
    code, out = run_cli(
        ["service", "call", "light.turn_on", "--target-area", "Example Room"],
        installation_env,
    )
    assert code == 1
    assert "example_room" in out
    assert "area name" in out


def test_a_target_that_matched_but_changed_nothing_says_so(run_cli, installation_env):
    """The other world: entities were reached, and none of them changed."""
    code, out = run_cli(
        ["service", "call", "light.turn_off", "--target-entity", "light.example_ceiling"],
        installation_env,
    )
    assert code == 0
    assert "0 states changed" in out
    assert "matched 1 entity" in out


def test_an_unavailable_entity_is_named_rather_than_silently_skipped(run_cli, installation_env):
    """Home Assistant skips an unavailable entity without a word about it."""
    code, out = run_cli(
        ["service", "call", "switch.toggle", "--target-entity", "switch.example_outlet"],
        installation_env,
    )
    assert code == 0
    assert "unavailable" in out


def test_an_entity_that_does_not_exist_is_not_reported_as_success(run_cli, installation_env):
    code, out = run_cli(
        ["service", "call", "light.turn_on", "--target-entity", "light.absent"],
        installation_env,
    )
    assert code == 1
    assert "light.absent" in out


def test_a_call_that_changed_something_pays_for_no_extra_reads(
    run_cli, installation_env, rest_server
):
    """The happy path stays one request: no model, no registry, no state sweep."""
    code, out = run_cli(
        ["service", "call", "light.turn_off", "--target-entity", "light.example_lamp"],
        installation_env,
    )
    assert code == 0
    assert "changed[1]{entity_id,name,state}:" in out
    paths = [r["path"] for r in rest_server.requests]
    assert paths == ["/api/services/light/turn_off"]


# ------------------------------------------------ enrichment: `service get`


def test_service_get_renders_one_service_field_table_live(run_cli, rest_env):
    code, out = run_cli(["service", "get", "light.turn_on"], rest_env)
    assert code == 0
    assert "service: light.turn_on" in out
    assert "name: Turn on" in out
    assert "fields[3]{field,required,type,description}:" in out
    assert "brightness,false,number," in out
    assert "hass-axi service call light.turn_on" in out


def test_service_get_flattens_a_section_the_way_a_call_does(run_cli, rest_env):
    """A section is a display grouping; its fields go in the data flat."""
    code, out = run_cli(["service", "get", "light.turn_on", "--fields", "field,section"], rest_env)
    assert code == 0
    assert "profile,advanced_fields" in out
    assert "advanced_fields,advanced_fields" not in out


def test_service_get_reports_the_capability_a_target_must_have(run_cli, rest_env):
    code, out = run_cli(["service", "get", "media_player.media_next_track"], rest_env)
    assert code == 0
    assert f"{FEATURE_NEXT_TRACK}" in out
    assert "supported_features" in out


def test_service_get_reports_the_response_mode(run_cli, rest_env):
    _, out = run_cli(["service", "get", "calendar.list_events"], rest_env)
    assert "response: required" in out
    assert "--response" in out
    _, out = run_cli(["service", "get", "calendar.get_events"], rest_env)
    assert "response: optional" in out
    assert "--response" in out
    _, out = run_cli(["service", "get", "light.turn_on"], rest_env)
    assert "response: none" in out


def test_service_get_states_an_empty_field_list_definitively(run_cli, rest_env):
    code, out = run_cli(["service", "get", "light.turn_off"], rest_env)
    assert code == 0
    assert "fields: 0 fields declared on light.turn_off" in out


def test_service_get_rejects_an_unknown_service_with_the_near_misses(run_cli, rest_env):
    code, out = run_cli(["service", "get", "light.turn_onn"], rest_env)
    assert code == 1
    assert "light.turn_on" in out
    assert "hass-axi service list --domain light" in out


def test_service_get_rejects_a_malformed_name_as_a_usage_error(run_cli, rest_env):
    code, out = run_cli(["service", "get", "turn_on"], rest_env)
    assert code == 2
    assert "expected <domain>.<service>" in out


def test_service_get_is_json_renderable(run_cli, rest_env):
    code, out = run_cli(["--json", "service", "get", "light.turn_on"], rest_env)
    assert code == 0
    doc = json.loads(out)
    assert doc["service"] == "light.turn_on"
    assert [row["field"] for row in doc["fields"]] == ["brightness", "transition", "profile"]


# ------------------------------------------- enrichment: the capability gate


def test_a_capability_the_target_cannot_have_is_refused_before_dispatch(
    run_cli, installation_env, rest_server
):
    """The A11 case: skipping a track on a player that cannot skip.

    Home Assistant reaches area-resolved entities and silently drops the ones
    without the feature, so the call returns 200 with an empty list and the
    agent learns nothing. The requirement is published, so it can be read first.
    """
    code, out = run_cli(
        [
            "service",
            "call",
            "media_player.media_next_track",
            "--target-area",
            "example_hall",
        ],
        installation_env,
    )
    assert code == 1
    assert "media_player.example_speaker" in out
    assert "supported_features" in out
    assert "hass-axi service get media_player.media_next_track" in out
    assert "/api/services/media_player/media_next_track" not in [
        r["path"] for r in rest_server.requests
    ], "a call the installation cannot serve must not be sent"


def test_a_service_with_an_upstream_fallback_is_not_gated(run_cli, installation_env):
    """The caveat that keeps the gate honest.

    `volume_up` publishes two acceptable capabilities because Home Assistant
    backs a player that cannot step with one that can set. The speaker has the
    second, so the call must go through -- gating it would break behaviour that
    works today.
    """
    code, out = run_cli(
        ["service", "call", "media_player.volume_up", "--target-area", "example_hall"],
        installation_env,
    )
    assert code == 0
    assert "media_player.example_speaker" in out


def test_the_capability_gate_can_be_skipped(run_cli, installation_env, rest_server):
    """An integration whose published requirement is wrong must not be a wall."""
    code, _ = run_cli(
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
    assert "/api/services/media_player/media_next_track" in [
        r["path"] for r in rest_server.requests
    ]


def test_an_explicit_entity_target_is_not_pre_checked(run_cli, installation_env, rest_server):
    """Home Assistant refuses a named entity loudly, so the check would only cost.

    The call goes out, comes back refused, and the refusal is enriched from the
    same model -- on the failure path, where it is free.
    """
    code, out = run_cli(
        [
            "service",
            "call",
            "media_player.media_next_track",
            "--target-entity",
            "media_player.example_speaker",
        ],
        installation_env,
    )
    assert code == 1
    assert "/api/services/media_player/media_next_track" in [
        r["path"] for r in rest_server.requests
    ]
    assert "supported_features" in out
    assert f"{FEATURE_VOLUME_SET}" in out


def test_a_service_with_no_capability_requirement_reads_no_states(
    run_cli, installation_env, rest_server
):
    """The gate costs one model read and stops there when nothing is gated."""
    code, _ = run_cli(
        ["service", "call", "light.turn_off", "--target-area", "example_room"],
        installation_env,
    )
    assert code == 0
    paths = [r["path"] for r in rest_server.requests]
    assert paths.count("/api/states") == 0
    assert "/api/services" in paths


def test_a_target_that_cannot_be_resolved_does_not_fail_a_call_that_worked(
    run_cli, rest_env, rest_server
):
    """The registries answer the follow-up question, not the call itself.

    `rest_env` serves REST and nothing else, so resolving an area target is
    impossible. The call still went out and Home Assistant still accepted it, so
    the report says what it could not read rather than inventing a failure.
    """
    rest_server.state["service_result"] = []
    code, out = run_cli(
        ["service", "call", "light.turn_off", "--target-area", "example_room"],
        rest_env,
    )
    assert code == 0
    assert "0 states changed" in out
    assert "could not be resolved" in out


def test_a_name_too_far_off_gets_a_listing_rather_than_a_wrong_guess(run_cli, rest_env):
    """`did you mean` has to mean it; the rest of the domain is a separate sentence."""
    code, out = run_cli(["service", "call", "light.zzzzzz"], rest_env)
    assert code == 1
    assert "did you mean" not in out
    assert "hass-axi service list --domain light" in out


def test_a_response_call_that_reached_nothing_gets_the_same_answer_as_one_that_did_not(
    run_cli, installation_env
):
    """Regression: `--response` turned a good diagnosis into a bare HTTP 500.

    Reaching no entity is a `200 []` for an ordinary call, which
    `_report_target` reads and answers. For a `return_response` call Home
    Assistant raises `HomeAssistantError("Service call requested response data
    but did not match any entities")` instead, which aiohttp renders as a
    bodyless 500 -- so the identical situation arrived with nothing to read and
    fell through to help about *fields*, which were never the problem.

    `example_room` holds no calendar, so both spellings reach nothing, and both
    have to say so.
    """
    argv = [
        "service",
        "call",
        "calendar.get_events",
        "--target-area",
        "example_room",
        "--data",
        "start_date_time=2026-01-01 00:00:00",
    ]
    with_flag_code, with_flag = run_cli([*argv, "--response"], installation_env)
    assert with_flag_code == 1
    assert "NO_ENTITIES_TARGETED" in with_flag
    assert "matched 0 entities calendar.get_events can act on" in with_flag
    assert "HTTP_500" not in with_flag
    assert "to see the fields it takes" not in with_flag


def test_a_response_call_that_reached_something_is_untouched_by_that_branch(
    run_cli, installation_env
):
    """The other half, and the one that keeps the double honest.

    The refusal is about reach, so a `--response` call that does reach an entity
    has to come back 200 -- and the double has to be the thing proving it, or a
    double that answered 500 for *every* response call would satisfy the test
    above and be wrong in the opposite direction.
    """
    code, out = run_cli(
        [
            "service",
            "call",
            "calendar.get_events",
            "--target-entity",
            "calendar.example_agenda",
            "--response",
            "--data",
            "start_date_time=2026-01-01 00:00:00",
        ],
        installation_env,
    )
    assert code == 0
    assert "NO_ENTITIES_TARGETED" not in out
    assert "calendar.get_events" in out


def test_a_named_entity_outside_the_services_domain_is_reported_as_unreached(
    run_cli, installation_env
):
    # A light is not a calendar, so this reaches nothing -- and says which
    # entity it was, rather than blaming the fields.
    code, out = run_cli(
        [
            "service",
            "call",
            "calendar.get_events",
            "--target-entity",
            "light.example_lamp",
            "--response",
            "--data",
            "start_date_time=2026-01-01 00:00:00",
        ],
        installation_env,
    )
    assert code == 1
    assert "NO_ENTITIES_TARGETED" in out


def test_a_response_call_that_matched_only_unavailable_entities_names_them(
    run_cli, installation_env
):
    """Regression: the re-derived verdict stopped at the domain filter.

    Home Assistant drops `unavailable` candidates before it decides a target
    matched nothing, so an entity that is in the domain yet unreachable arrives
    as the same bodyless 500 -- and the verdict used to see the non-empty
    domain match, give up, and fall through to help about fields. The entity
    was matched and then skipped, and the verdict has to say that rather than
    report a match that never happened.
    """
    code, out = run_cli(
        [
            "service",
            "call",
            "calendar.get_events",
            "--target-entity",
            "calendar.example_old_agenda",
            "--response",
            "--data",
            "start_date_time=2026-01-01 00:00:00",
        ],
        installation_env,
    )
    assert code == 1
    assert "NO_ENTITIES_TARGETED" in out
    assert (
        "entity calendar.example_old_agenda matched 1 entity, but "
        "calendar.example_old_agenda is unavailable" in out
    )
    assert "which Home Assistant skips before matching" in out
    assert "matched 0 entities" not in out
    assert "to see the fields it takes" not in out


def test_the_same_call_without_response_keeps_reporting_the_silent_skip(run_cli, installation_env):
    """The 200 half of the pair is a report, not a refusal.

    Without `--response`, Home Assistant answers the skipped entity with an
    empty change set and says nothing at all, so the call is reported as having
    matched an entity it skipped -- a different fact from reaching nothing,
    which is why the two sides of this outcome are not the same verdict.
    """
    code, out = run_cli(
        [
            "service",
            "call",
            "calendar.get_events",
            "--target-entity",
            "calendar.example_old_agenda",
            "--data",
            "start_date_time=2026-01-01 00:00:00",
        ],
        installation_env,
    )
    assert code == 0
    assert "matched 1 entity" in out
    assert (
        "calendar.example_old_agenda unavailable, which Home Assistant skips without a word" in out
    )
    assert "NO_ENTITIES_TARGETED" not in out


def test_a_no_check_response_call_names_the_capability_it_lacked(run_cli, installation_env):
    """The same verdict for the other filter Home Assistant applies.

    Under `--no-check` an incapable entity stays in the target, and once every
    candidate has been filtered out a service that asks for a response refuses
    with the same bodyless 500. The verdict names what the entity reports and
    what the service accepts, rather than blaming the fields.
    """
    code, out = run_cli(
        [
            "service",
            "call",
            "media_player.media_next_track",
            "--target-area",
            "example_hall",
            "--no-check",
            "--response",
        ],
        installation_env,
    )
    assert code == 1
    assert "NO_ENTITIES_TARGETED" in out
    assert f"media_player.example_speaker reports 3, not any of {FEATURE_NEXT_TRACK}" in out
    assert "which Home Assistant skips before matching" in out
    assert "to see the fields it takes" not in out


def test_a_device_target_that_reached_nothing_suggests_a_command_that_works(
    run_cli, installation_env
):
    """Regression: the suggestion named a search that could never match.

    A device id was never in `entity list --search`'s haystack, so the line
    printed here answered `0 registry entries found` every time it was run --
    and nothing else in the tool went from a device to its entities.
    """
    code, out = run_cli(
        ["service", "call", "light.turn_on", "--target-device", "device_three"],
        installation_env,
    )
    assert code == 1
    assert "Run `hass-axi entity list --device device_three` to see a device's entities" in out

    # And that line is runnable, which is the whole claim being made.
    listed_code, listed = run_cli(["entity", "list", "--device", "device_three"], installation_env)
    assert listed_code == 0
    assert "sensor.example_temperature" in listed

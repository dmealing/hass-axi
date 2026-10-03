"""Fake Home Assistant servers, so every test runs against a real socket.

No live installation and no live token is ever needed: the REST double is an
``http.server`` and the WebSocket double is a real ``websockets`` server that
performs the same auth handshake Home Assistant does. Both bind to loopback on
an ephemeral port.

Every fixture in this suite is synthetic. Entity ids, names and areas are
invented (``light.example_lamp``, ``Example Room``) and the token is an obvious
placeholder, so nothing here describes any particular installation.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pytest

from hass_axi import output

#: An obviously-synthetic token. It is not a JWT and grants nothing.
FAKE_TOKEN = "example-test-token-not-a-real-credential"


def synthetic_jwt() -> str:
    """A structurally valid, entirely fake JWT, assembled at run time.

    Encoding the header and payload here rather than writing the `eyJ...`
    literal keeps the shape out of the test sources, so the leak scanner's
    condensed pass -- which exists to catch exactly such a literal split across
    lines -- does not fire on the tests that exercise it.
    """
    import base64
    import json

    def segment(payload):
        return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")

    return f"{segment({'alg': 'HS256'})}.{segment({'sub': 'example'})}.c2lnbmF0dXJlaGVyZQ"


# --------------------------------------------------------------- fixture data

#: Capability bits for this synthetic installation.
#:
#: Home Assistant publishes the same shape -- one integer mask per entity in its
#: `supported_features` attribute, and a list of acceptable masks per service in
#: `target.entity[].supported_features` -- but every value below is invented.
#: An entity qualifies when it satisfies *any* one mask in the list, which is
#: how a service with an upstream fallback declares it: `volume_up` accepts
#: either VOLUME_SET or VOLUME_STEP, so a speaker that steps by setting still
#: passes.
FEATURE_TURN_ON = 1
FEATURE_VOLUME_SET = 2
FEATURE_VOLUME_STEP = 4
FEATURE_NEXT_TRACK = 8
FEATURE_TARGET_TEMPERATURE = 16
FEATURE_TRANSITION = 32

#: Keys Home Assistant treats as targeting rather than as service data.
TARGET_KEYS = ("entity_id", "device_id", "area_id", "floor_id", "label_id")

STATES = [
    {
        "entity_id": "light.example_lamp",
        "state": "on",
        "attributes": {"friendly_name": "Example Lamp", "brightness": 180},
        "last_changed": "2026-01-01T00:00:00+00:00",
        "last_reported": "2026-01-01T00:00:00+00:00",
        "last_updated": "2026-01-01T00:00:00+00:00",
        "context": {"id": "01EXAMPLECONTEXT0000000001", "parent_id": None, "user_id": None},
    },
    {
        "entity_id": "light.example_ceiling",
        "state": "off",
        "attributes": {"friendly_name": "Example Ceiling"},
        "last_changed": "2026-01-01T00:00:00+00:00",
        "last_reported": "2026-01-01T00:00:00+00:00",
        "last_updated": "2026-01-01T00:00:00+00:00",
        "context": {"id": "01EXAMPLECONTEXT0000000002", "parent_id": None, "user_id": None},
    },
    {
        "entity_id": "sensor.example_temperature",
        "state": "21.5",
        "attributes": {"friendly_name": "Example Hub Temperature", "unit_of_measurement": "C"},
        "last_changed": "2026-01-01T00:00:00+00:00",
        "last_reported": "2026-01-01T00:00:00+00:00",
        "last_updated": "2026-01-01T00:00:00+00:00",
        "context": {"id": "01EXAMPLECONTEXT0000000003", "parent_id": None, "user_id": None},
    },
    {
        "entity_id": "switch.example_outlet",
        "state": "unavailable",
        "attributes": {"friendly_name": "Example Outlet"},
        "last_changed": "2026-01-01T00:00:00+00:00",
        "last_reported": "2026-01-01T00:00:00+00:00",
        "last_updated": "2026-01-01T00:00:00+00:00",
        "context": {"id": "01EXAMPLECONTEXT0000000004", "parent_id": None, "user_id": None},
    },
    {
        "entity_id": "media_player.example_speaker",
        "state": "playing",
        "attributes": {
            "friendly_name": "Example Speaker",
            # Volume can be set but not stepped, and the track cannot be
            # skipped. That is the shape of the capability case worth testing:
            # `volume_up` still works, through the VOLUME_SET alternative the
            # service declares, while `media_next_track` genuinely cannot.
            "supported_features": FEATURE_TURN_ON | FEATURE_VOLUME_SET,
        },
        "last_changed": "2026-01-01T00:00:00+00:00",
        "last_reported": "2026-01-01T00:00:00+00:00",
        "last_updated": "2026-01-01T00:00:00+00:00",
        "context": {"id": "01EXAMPLECONTEXT0000000005", "parent_id": None, "user_id": None},
    },
    {
        "entity_id": "climate.example_thermostat",
        "state": "heat",
        "attributes": {
            "friendly_name": "Example Thermostat",
            "supported_features": FEATURE_TARGET_TEMPERATURE,
            "temperature": 21,
        },
        "last_changed": "2026-01-01T00:00:00+00:00",
        "last_reported": "2026-01-01T00:00:00+00:00",
        "last_updated": "2026-01-01T00:00:00+00:00",
        "context": {"id": "01EXAMPLECONTEXT0000000006", "parent_id": None, "user_id": None},
    },
    {
        # The entity whose whole name is its device's. Its registry entry names
        # nothing at all, and this is the state that says what Home Assistant
        # displays for it -- the two have to agree, and did not.
        "entity_id": "binary_sensor.example_doorway",
        "state": "off",
        "attributes": {"friendly_name": "Example Doorway", "device_class": "door"},
        "last_changed": "2026-01-01T00:00:00+00:00",
        "last_reported": "2026-01-01T00:00:00+00:00",
        "last_updated": "2026-01-01T00:00:00+00:00",
        "context": {"id": "01EXAMPLECONTEXT0000000007", "parent_id": None, "user_id": None},
    },
    {
        "entity_id": "sensor.example_legacy_meter",
        "state": "7",
        "attributes": {
            "friendly_name": "Example Doorway Legacy Meter",
            "unit_of_measurement": "kWh",
        },
        "last_changed": "2026-01-01T00:00:00+00:00",
        "last_reported": "2026-01-01T00:00:00+00:00",
        "last_updated": "2026-01-01T00:00:00+00:00",
        "context": {"id": "01EXAMPLECONTEXT0000000008", "parent_id": None, "user_id": None},
    },
    {
        # A calendar, so `calendar.get_events` has something to reach: a
        # `return_response` call that reaches nothing cannot answer with an empty
        # change set, and a double with no reachable entity for the only
        # response service it publishes could only ever exercise the refusal.
        "entity_id": "calendar.example_agenda",
        "state": "on",
        "attributes": {"friendly_name": "Example Agenda"},
        "last_changed": "2026-01-01T00:00:00+00:00",
        "last_reported": "2026-01-01T00:00:00+00:00",
        "last_updated": "2026-01-01T00:00:00+00:00",
        "context": {"id": "01EXAMPLECONTEXT0000000010", "parent_id": None, "user_id": None},
    },
    {
        # A second calendar the response service *matches* but cannot act on.
        # Home Assistant drops `unavailable` candidates before it decides a
        # target matched nothing, so under `return_response` this entity turns
        # the call into the bodyless 500 -- the one refusal shape whose reason
        # exists only in the filtering rule -- while an ordinary call skips it
        # in silence and answers an empty change set.
        "entity_id": "calendar.example_old_agenda",
        "state": "unavailable",
        "attributes": {"friendly_name": "Example Old Agenda"},
        "last_changed": "2026-01-01T00:00:00+00:00",
        "last_reported": "2026-01-01T00:00:00+00:00",
        "last_updated": "2026-01-01T00:00:00+00:00",
        "context": {"id": "01EXAMPLECONTEXT0000000011", "parent_id": None, "user_id": None},
    },
    {
        # `unknown` is not `unavailable`: the entity is reachable and has simply
        # not reported a value yet. A double that never produced one let the
        # home view count the two together under the name of one of them.
        "entity_id": "sensor.example_reading",
        "state": "unknown",
        "attributes": {"friendly_name": "Example Hub Reading", "unit_of_measurement": "A"},
        "last_changed": "2026-01-01T00:00:00+00:00",
        "last_reported": "2026-01-01T00:00:00+00:00",
        "last_updated": "2026-01-01T00:00:00+00:00",
        "context": {"id": "01EXAMPLECONTEXT0000000009", "parent_id": None, "user_id": None},
    },
]

#: The service model, in the shape `GET /api/services` actually returns.
#:
#: Home Assistant builds each description from the integration's
#: `services.yaml`: `fields` (with `required`, a `selector`, and an optional
#: `filter.supported_features`), an optional `target` whose entity filter
#: carries the capability a target must have, and a `response` key that is
#: present only when the service can answer with a payload -- `optional: false`
#: meaning it answers with one or not at all.
#:
#: Written by hand rather than copied from any installation: every domain and
#: service name here is part of Home Assistant's public vocabulary, and every
#: entity, area and capability value is invented.
SERVICES = [
    {
        "domain": "light",
        "services": {
            "turn_on": {
                "name": "Turn on",
                "description": "Turn on one or more lights.",
                "fields": {
                    "brightness": {
                        "description": "Brightness, from 0 to 255.",
                        "selector": {"number": {"min": 0, "max": 255}},
                    },
                    "transition": {
                        "description": "Seconds to fade over.",
                        "filter": {"supported_features": [FEATURE_TRANSITION]},
                        "selector": {"number": {"min": 0, "max": 300}},
                    },
                    "advanced_fields": {
                        "collapsed": True,
                        "fields": {
                            "profile": {
                                "description": "A named light profile.",
                                "selector": {"text": None},
                            }
                        },
                    },
                },
                "target": {"entity": [{"domain": ["light"]}]},
            },
            "turn_off": {
                "name": "Turn off",
                "description": "Turn off one or more lights.",
                "fields": {},
                "target": {"entity": [{"domain": ["light"]}]},
            },
        },
    },
    {
        # The ordinary case on a real installation, and the one the rest of this
        # table gets wrong: almost no service publishes a `name` or a
        # `description`, and almost no field publishes one either. They moved to
        # the translation files, which `/api/services` does not serve. A model
        # where everything documents itself let `service get` be designed around
        # a column that is empty on every row of a real instance.
        "domain": "switch",
        "services": {
            "toggle": {
                "fields": {"delay": {"selector": {"number": {"min": 0, "max": 60}}}},
                "target": {"entity": [{"domain": ["switch"]}]},
            }
        },
    },
    {
        "domain": "media_player",
        "services": {
            "media_next_track": {
                "name": "Next track",
                "description": "Skip to the next track.",
                "fields": {},
                # One acceptable mask, so there is no alternative to fall back
                # on: an entity without it cannot be reached by this service.
                "target": {
                    "entity": [
                        {"domain": ["media_player"], "supported_features": [FEATURE_NEXT_TRACK]}
                    ]
                },
                # A response mode alongside that mask, so the one refusal whose
                # reason lives only in the filtering rule is reachable in both
                # of its forms: an `unavailable` candidate and an incapable one
                # are dropped the same way, and only a `return_response` call
                # turns the empty result into the bodyless 500. Without one
                # service publishing both keys, the capability half of that
                # verdict could never be exercised against the double.
                "response": {"optional": True},
            },
            "volume_up": {
                "name": "Turn up volume",
                "description": "Turn the volume up.",
                "fields": {},
                # Two acceptable masks: Home Assistant backs a player that
                # cannot step with one that can set, so declaring both is how
                # the fallback is published.
                "target": {
                    "entity": [
                        {
                            "domain": ["media_player"],
                            "supported_features": [FEATURE_VOLUME_SET, FEATURE_VOLUME_STEP],
                        }
                    ]
                },
            },
        },
    },
    {
        "domain": "climate",
        "services": {
            "set_temperature": {
                "name": "Set target temperature",
                "description": "Set the target temperature.",
                "fields": {
                    "temperature": {
                        "description": "The target temperature.",
                        "filter": {"supported_features": [FEATURE_TARGET_TEMPERATURE]},
                        "selector": {"number": {"min": 0, "max": 250}},
                    },
                    "hvac_mode": {
                        "description": "The mode to switch to first.",
                        "selector": {"select": {"options": ["off", "heat", "cool"]}},
                    },
                },
                "target": {
                    "entity": [
                        {
                            "domain": ["climate"],
                            "supported_features": [FEATURE_TARGET_TEMPERATURE],
                        }
                    ]
                },
            }
        },
    },
    {
        "domain": "calendar",
        "services": {
            "get_events": {
                "name": "Get events",
                "description": "List events in a window.",
                "fields": {
                    "start_date_time": {
                        "required": True,
                        "description": "The start of the window.",
                        "selector": {"datetime": None},
                    }
                },
                "target": {"entity": [{"domain": ["calendar"]}]},
                # Changed to optional:true so the unavailable-entity test can
                # exercise both sides of the verdict: with --response (bodyless
                # 500) and without (empty change set with diagnostic).
                "response": {"optional": True},
            },
            "list_events": {
                "name": "List events",
                "description": "List events (response-only service for testing).",
                "fields": {
                    "start_date_time": {
                        "required": True,
                        "description": "The start of the window.",
                        "selector": {"datetime": None},
                    }
                },
                "target": {"entity": [{"domain": ["calendar"]}]},
                # Response-required service for testing the error message when
                # --response is omitted.
                "response": {"optional": False},
            },
        },
    },
]


#: The entity registry, in the shape `config/entity_registry/list` returns.
#:
#: The distribution matters as much as the shape. On a real installation most
#: entries carry `has_entity_name` and name *part* of themselves at most: the
#: rest of the name comes from the device, and a majority name none of
#: themselves at all. A fixture set where every entry carried its own
#: `original_name` and none carried `has_entity_name` described an installation
#: that does not exist, and let a display name that disagreed with Home
#: Assistant's for four entities in five ship behind a green suite.
#:
#: Every key Home Assistant publishes is present, including the ones nothing
#: reads: a client that started depending on a missing key would find out here
#: rather than on somebody's installation.
def _registry_entry(**overrides) -> dict:
    """One registry entry with every key `as_partial_dict` publishes."""
    entry = {
        "area_id": None,
        "categories": {},
        "config_entry_id": "example-config-entry",
        "config_subentry_id": None,
        "created_at": 1767225600.0,
        "device_id": None,
        "disabled_by": None,
        "entity_category": None,
        "entity_id": "",
        "has_entity_name": False,
        "hidden_by": None,
        "icon": None,
        "id": "",
        "labels": [],
        "modified_at": 1767225600.0,
        "name": None,
        "options": {},
        "original_name": None,
        "platform": "demo",
        "translation_key": None,
        "unique_id": "",
    }
    entry.update(overrides)
    return entry


ENTITY_REGISTRY = [
    # A user override, which wins outright -- device prefix and all. Home
    # Assistant does not compose over a name somebody typed.
    _registry_entry(
        entity_id="light.example_lamp",
        id="registry-one",
        name="Example Lamp",
        original_name="Lamp",
        has_entity_name=True,
        area_id="example_room",
        device_id="device_one",
        unique_id="unique-one",
    ),
    # The majority case: names nothing itself, so its whole name is its
    # device's. Reading the entity row alone renders this one blank.
    _registry_entry(
        entity_id="light.example_ceiling",
        id="registry-two",
        has_entity_name=True,
        device_id="device_two",
        unique_id="unique-two",
    ),
    # Names its own half only, so the device supplies the prefix. Reading the
    # entity row alone renders `Temperature` where Home Assistant shows
    # `Example Hub Temperature`.
    _registry_entry(
        entity_id="sensor.example_temperature",
        id="registry-three",
        original_name="Temperature",
        has_entity_name=True,
        device_id="device_three",
        entity_category="diagnostic",
        unique_id="unique-three",
    ),
    # No device at all, so there is nothing to compose with and the entity's own
    # name stands. This is the minority that the old fixture set made universal.
    _registry_entry(
        entity_id="media_player.example_speaker",
        id="registry-four",
        original_name="Example Speaker",
        area_id="example_hall",
        platform="example",
        unique_id="unique-four",
    ),
    _registry_entry(
        entity_id="climate.example_thermostat",
        id="registry-five",
        original_name="Example Thermostat",
        platform="example",
        unique_id="unique-five",
    ),
    # Neither a name nor an area of its own: the name comes from the device and
    # so does the area. Searching for what Home Assistant displays has to find
    # it, which is the whole point of the registry view.
    _registry_entry(
        entity_id="binary_sensor.example_doorway",
        id="registry-six",
        has_entity_name=True,
        device_id="device_four",
        unique_id="unique-six",
    ),
    # `has_entity_name` is false and the entry still takes the device prefix.
    # It is not a gate on composition: Home Assistant strips the device name
    # from `original_name` for these integrations *before publishing it*
    # (`RegistryEntry.as_partial_dict` sends `original_name_unprefixed`), then
    # composes the two halves back together in
    # `_async_get_full_entity_name`, which is the single rule behind both this
    # view and the `friendly_name` on the state above.
    _registry_entry(
        entity_id="sensor.example_legacy_meter",
        id="registry-seven",
        original_name="Legacy Meter",
        device_id="device_four",
        platform="example",
        unique_id="unique-seven",
    ),
    _registry_entry(
        entity_id="sensor.example_reading",
        id="registry-eight",
        original_name="Reading",
        has_entity_name=True,
        device_id="device_three",
        unique_id="unique-eight",
    ),
    _registry_entry(
        entity_id="calendar.example_agenda",
        id="registry-ten",
        original_name="Example Agenda",
        platform="example",
        unique_id="unique-ten",
    ),
    _registry_entry(
        entity_id="calendar.example_old_agenda",
        id="registry-eleven",
        original_name="Example Old Agenda",
        platform="example",
        unique_id="unique-eleven",
    ),
    # Disabled by its integration, and therefore has no state at all. A registry
    # entry without a state is ordinary -- every installation has some -- and a
    # fixture set where every entry had one never exercised the case.
    _registry_entry(
        entity_id="sensor.example_disabled_probe",
        id="registry-nine",
        original_name="Probe",
        has_entity_name=True,
        device_id="device_three",
        disabled_by="integration",
        entity_category="diagnostic",
        unique_id="unique-nine",
    ),
]

AREA_REGISTRY = [
    {
        "area_id": "example_room",
        "name": "Example Room",
        "icon": None,
        "floor_id": None,
        "aliases": [],
    },
    {
        "area_id": "example_hall",
        "name": "Example Hall",
        "icon": "mdi:door",
        "floor_id": "ground",
        "aliases": [],
    },
]

DEVICE_REGISTRY = [
    {
        "id": "device_one",
        "name": "Example Lamp Fitting",
        "name_by_user": None,
        "disabled_by": None,
        "area_id": "example_room",
        "manufacturer": "Example Co",
        "model": "Model X",
    },
    {
        # A user rename, which is what the display name is composed from -- the
        # integration's own name is never what gets shown once one exists.
        "id": "device_two",
        "name": "Ceiling Fitting",
        "name_by_user": "Example Ceiling",
        "disabled_by": None,
        "area_id": "example_hall",
        "manufacturer": "Example Co",
        "model": "Model Y",
    },
    {
        # A device in no area, which supplies entities in no area.
        "id": "device_three",
        "name": "Example Hub",
        "name_by_user": None,
        "disabled_by": None,
        "area_id": None,
        "manufacturer": "Example Co",
        "model": "Hub 1",
    },
    {
        "id": "device_four",
        "name": "Example Doorway",
        "name_by_user": None,
        "disabled_by": None,
        "area_id": "example_room",
        "manufacturer": "Example Co",
        "model": "Model Z",
    },
]

# ------------------------------------------------- the service model, read back
#
# These helpers are what the REST double consults to decide whether to refuse a
# service call. They read `SERVICES` and the registries directly and share no
# code with `axi_toolkit.ha.services`, the reader the client uses: a double that
# took its reading of the model from the client could only ever prove the client
# agrees with itself. That the reader now ships in a shared package rather than
# in `hass_axi` changes nothing here -- it is still the client's reading, and the
# point of the second opinion is that it is arrived at independently.


def service_description(domain: str, service: str):
    """The published description of one service, or None if it is not registered."""
    for entry in SERVICES:
        if entry["domain"] == domain:
            return (entry.get("services") or {}).get(service)
    return None


def _walk_fields(description):
    """Every declared field, with sections flattened as Home Assistant flattens them.

    A section is a display grouping only: its fields arrive in the service data
    at the top level, exactly like an ungrouped one.
    """
    for name, field in (description.get("fields") or {}).items():
        field = field or {}
        if "fields" in field:
            for inner_name, inner in (field["fields"] or {}).items():
                yield inner_name, (inner or {})
        else:
            yield name, field


def declared_fields(description) -> dict:
    return dict(_walk_fields(description))


def target_domains(description) -> list:
    """The entity domains a service publishes that it can be aimed at."""
    target = description.get("target")
    if not isinstance(target, dict):
        return []
    entries = target.get("entity")
    if not isinstance(entries, list):
        return []
    out = []
    for entry in entries:
        declared = (entry or {}).get("domain")
        if declared is None:
            return []
        out.extend([declared] if isinstance(declared, str) else list(declared))
    return sorted(set(out))


def capability_masks(description, domain: str) -> list:
    """The masks a target entity must satisfy, when the service declares any.

    Only read when the service's entity filter names exactly the service's own
    domain: a service that targets another domain's entities publishes that
    domain's capability names, which say nothing about the entity it reaches.
    """
    target = description.get("target")
    if not isinstance(target, dict):
        return []
    entries = target.get("entity")
    if not isinstance(entries, list) or len(entries) != 1:
        return []
    entry = entries[0] or {}
    domains = entry.get("domain")
    domains = [domains] if isinstance(domains, str) else list(domains or [])
    if domains != [domain]:
        return []
    masks = entry.get("supported_features") or []
    return [m for m in masks if isinstance(m, int) and not isinstance(m, bool)]


def displayed_name(entry) -> str:
    """The name Home Assistant displays for a registry entry.

    A second opinion, written from `helpers/entity_registry` rather than taken
    from `hass_axi.commands._common`: a double that read the rule off the client
    could only ever prove the client agrees with itself, and this is the rule the
    client got wrong. `_async_get_full_entity_name` is called with
    `parts=(DEVICE, ENTITY)` and `use_legacy_naming=True`, so a `name` somebody
    set wins outright and everything else is the device's name joined to
    `original_name` -- which arrives already stripped of any device prefix,
    because `as_partial_dict` publishes `original_name_unprefixed` under that key.
    """
    if entry.get("name"):
        return entry["name"]
    device_name = ""
    for device in DEVICE_REGISTRY:
        if device["id"] == entry.get("device_id"):
            device_name = device.get("name_by_user") or device.get("name") or ""
    return " ".join(part for part in (device_name, entry.get("original_name") or "") if part)


def area_of_registry_entry(entry) -> str:
    """The area an entity sits in, its device's area standing in when it has none."""
    if entry.get("area_id"):
        return entry["area_id"]
    for device in DEVICE_REGISTRY:
        if device["id"] == entry.get("device_id"):
            return device.get("area_id") or ""
    return ""


def entities_targeted(body) -> list:
    """Expand a service call's flat target keys into entity ids, as HA does."""
    body = body if isinstance(body, dict) else {}

    def listed(key):
        value = body.get(key)
        if value is None:
            return []
        return list(value) if isinstance(value, list) else [value]

    found = list(listed("entity_id"))
    areas, devices = listed("area_id"), listed("device_id")
    if areas or devices:
        for entry in ENTITY_REGISTRY:
            if area_of_registry_entry(entry) in areas or entry.get("device_id") in devices:
                found.append(entry["entity_id"])
    seen, ordered = set(), []
    for entity_id in found:
        if entity_id not in seen:
            seen.add(entity_id)
            ordered.append(entity_id)
    return ordered


#: The state a service leaves an entity in, for the few where it is knowable.
#:
#: Home Assistant returns the states that actually *changed*, so an entity
#: already as asked is absent from the answer -- which is precisely why an empty
#: change set had to stop being the same answer as "nothing was targeted".
#: Anything not listed here is reported as changed, because the double cannot
#: know better and guessing the other way would hide a real change.
RESULTING_STATE = {"turn_on": "on", "turn_off": "off"}


#: Every key each modelled WebSocket command accepts, beyond `id` and `type`.
#:
#: Declared here rather than imported from ``hass_axi.ws.REGISTRY`` on purpose: a
#: double that takes its schema from the client can only ever prove the client
#: agrees with itself. Home Assistant validates each command against a
#: voluptuous schema that defaults to ``PREVENT_EXTRA``, so an undeclared key
#: comes back as ``invalid_format`` rather than being quietly ignored -- which
#: is what makes a wrong wire shape fail a test instead of passing one.
#:
#: A parameter added to the client and not to this table will be rejected here.
#: That is the point: the table is a second opinion, and updating it is how a
#: new parameter gets confirmed against what the API actually accepts.
WS_COMMAND_KEYS = {
    "config/entity_registry/list": (),
    "config/entity_registry/get": ("entity_id",),
    "config/entity_registry/update": (
        "entity_id",
        "name",
        "icon",
        "area_id",
        "new_entity_id",
        "disabled_by",
        "hidden_by",
        "labels",
        "aliases",
    ),
    "config/area_registry/list": (),
    "config/area_registry/create": ("name", "icon", "floor_id", "aliases", "labels", "picture"),
    "config/area_registry/update": (
        "area_id",
        "name",
        "icon",
        "floor_id",
        "aliases",
        "labels",
        "picture",
    ),
    "config/area_registry/delete": ("area_id",),
    "config/device_registry/list": (),
    "config/device_registry/update": (
        "device_id",
        "name_by_user",
        "area_id",
        "disabled_by",
        "labels",
    ),
    "config/floor_registry/list": (),
    "config/label_registry/list": (),
    "get_config": (),
    "get_services": (),
    "get_states": (),
}

#: The fields `config/entity_registry/update` writes onto the stored entry.
ENTITY_UPDATE_FIELDS = ("name", "icon", "area_id", "disabled_by", "hidden_by", "labels", "aliases")


def extended_entry(entry: dict) -> dict:
    """The larger entry `get` and `update` answer with, which `list` does not.

    `RegistryEntry.extended_dict` is `as_partial_dict` plus five keys, so the two
    reads of the same entity are not the same shape -- and an entity with no
    aliases comes back as `[None]`, not `[]`, because the empty alias is
    serialised rather than dropped. Nothing in `hass-axi` reads any of this today;
    it is here so that a client which starts to has something honest to read.
    """
    return {
        **entry,
        "aliases": list(entry.get("aliases") or [None]),
        "capabilities": None,
        "device_class": None,
        "original_device_class": None,
        "original_icon": None,
    }


def slugify(name: str) -> str:
    """Home Assistant's own rule for turning an area name into an area_id.

    Non-alphanumerics collapse to a single underscore and the ends are trimmed,
    so `&` and an apostrophe disappear rather than surviving into the id. A
    double that only lowercased and replaced spaces handed back an id no real
    instance would ever mint.
    """
    out = []
    for char in name.lower():
        out.append(char if char.isalnum() else "_")
    return "_".join(part for part in "".join(out).split("_") if part)


# ------------------------------------------------------------------ REST double


class FakeRestServer:
    """An HTTP server that answers the Home Assistant REST endpoints under test."""

    def __init__(self):
        self.requests = []
        self.state = {
            "states": [json.loads(json.dumps(s)) for s in STATES],
            "services": SERVICES,
            "template": "rendered",
            "service_result": None,
        }
        self.status_override = None
        #: The address is banned. `components/http/ban.py` raises a bare
        #: `HTTPForbidden` from a middleware, so this answers before the token
        #: is looked at -- which is the whole point of the fault: a valid token
        #: does not get past it either.
        self.forbidden = False
        #: `hass.is_stopping`. `helpers/http.py` answers every request with
        #: `web.Response(status=SERVICE_UNAVAILABLE)` while an instance shuts
        #: down: no body at all, not even aiohttp's `"503: ..."` line, because
        #: a bare `web.Response` is not an `HTTPException`.
        self.stopping = False
        #: Nothing is routed under `/api`. aiohttp's router misses before any
        #: view runs, so this answers ahead of the token check, exactly as an
        #: unrouted path does on a real instance -- which is why the 404 it
        #: sends carries no body worth reading.
        self.unrouted = False
        #: Seconds to stall before answering, for exercising client timeouts.
        self.delay = 0.0
        #: Answer with this raw body and Content-Type: application/json.
        self.malformed_json = None
        #: When set to a URL, the NEXT request answers 302 pointing at it and
        #: the setting clears, so a followed redirect can reach a real handler.
        self.redirect_to = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _authorized(self):
                header = self.headers.get("Authorization", "")
                return header == f"Bearer {FAKE_TOKEN}"

            def _send(self, code, payload, content_type="application/json"):
                if isinstance(payload, bytes):
                    body = payload
                elif content_type == "application/json":
                    body = json.dumps(payload).encode()
                else:
                    body = str(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle(self, method):
                path = urlparse(self.path).path
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                outer.requests.append(
                    {
                        "method": method,
                        "path": path,
                        "body": body,
                        "query": urlparse(self.path).query,
                    }
                )

                if outer.delay:
                    time.sleep(outer.delay)
                if outer.malformed_json is not None:
                    return self._send(200, outer.malformed_json, content_type="application/json")
                if outer.redirect_to is not None:
                    location, outer.redirect_to = outer.redirect_to, None
                    self.send_response(302)
                    self.send_header("Location", location)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if outer.status_override is not None:
                    code, payload = outer.status_override
                    return self._send(code, payload)
                # The order is Home Assistant's: the ban middleware runs before
                # any view, `hass.is_stopping` is the first thing the view
                # handler checks, and only then is the token read.
                if outer.forbidden:
                    return self._send(403, "403: Forbidden", content_type="text/plain")
                if outer.stopping:
                    return self._send(503, b"", content_type="application/octet-stream")
                if outer.unrouted:
                    return self._not_found()
                if not self._authorized():
                    # aiohttp renders a bare `HTTPUnauthorized` as its own
                    # status line in text/plain. It carries no JSON and no
                    # `message` key: answering with one let a client believe a
                    # rejected token explains itself, and it never does.
                    return self._send(401, "401: Unauthorized", content_type="text/plain")

                if path == "/api/":
                    return self._send(200, {"message": "API running."})
                if path == "/api/config":
                    return self._send(200, {"version": "2026.1.0", "location_name": "Example Home"})
                if path == "/api/states":
                    return self._send(200, outer.state["states"])
                if path.startswith("/api/states/"):
                    entity_id = path[len("/api/states/") :]
                    for state in outer.state["states"]:
                        if state["entity_id"] == entity_id:
                            return self._send(200, state)
                    # A routed path whose *subject* is missing says so, in JSON.
                    # An unrouted one does not -- see `_not_found` below. The two
                    # are different answers and a client that flattens them tells
                    # an agent to go looking for a typo in a path that was fine.
                    return self._send(404, {"message": "Entity not found."})
                if path == "/api/services" and method == "GET":
                    return self._send(200, outer.state["services"])
                if path.startswith("/api/services/") and method == "POST":
                    name = path[len("/api/services/") :].split("/")
                    if len(name) != 2:
                        return self._not_found()
                    return self._call_service(name[0], name[1], body, urlparse(self.path).query)
                if path == "/api/template" and method == "POST":
                    return self._render_template(body)
                return self._not_found()

            def _not_found(self):
                """The bodyless 404 an unrouted path actually gets.

                aiohttp renders its own `404: Not Found` in text/plain; there is
                no JSON and no message. Answering with `{"message": ...}` here
                let a client believe every 404 carries something to read.
                """
                return self._send(404, "404: Not Found", content_type="text/plain")

            def _render_template(self, body):
                """Render, or refuse the way Home Assistant refuses.

                A template that does not compile is a `400` naming what went
                wrong, in JSON. A double that always rendered made the error path
                unreachable, so nothing ever checked that the message survives.
                """
                template = (body or {}).get("template") if isinstance(body, dict) else None
                if isinstance(template, str) and "undefined_helper" in template:
                    return self._send(
                        400,
                        {
                            "message": "Error rendering template: UndefinedError: "
                            "'undefined_helper' is undefined"
                        },
                    )
                return self._send(200, outer.state["template"], content_type="text/plain")

            # -- the service call, refusals first -------------------------

            def _server_error(self):
                """The bodyless 500 a `HomeAssistantError` renders as.

                Home Assistant lets a `HomeAssistantError` out of a service call
                unhandled, and aiohttp turns it into a plain-text 500 whose body
                is a fixed apology -- no message, no entity, no service name.
                Both cases the double models this way were once answered with a
                helpful JSON `400`, which licensed a client to read a status
                number and a message that never arrive.
                """
                return self._send(
                    500,
                    "500 Internal Server Error\n\nServer got itself in trouble",
                    content_type="text/plain",
                )

            def _bad_request(self):
                """The empty 400 Home Assistant actually answers with.

                `APIDomainServicesView.post` raises `HTTPBadRequest` from the
                underlying `ServiceNotFound` or `vol.Invalid`, and aiohttp
                renders that as a plain-text status line with no JSON body. So
                the wire carries the status and nothing else: a client that
                wants to tell the agent *which* service or *which* field was
                wrong has to read the service model to find out. Answering with
                a helpful message here would hide exactly that.
                """
                return self._send(400, "400: Bad Request", content_type="text/plain")

            def _call_service(self, domain, service, body, query):
                description = service_description(domain, service)
                if description is None:
                    return self._bad_request()

                data = dict(body) if isinstance(body, dict) else {}
                response = description.get("response")
                wants_response = "return_response" in query
                if wants_response and response is None:
                    return self._send(
                        400,
                        {
                            "message": "Service does not support responses. "
                            "Remove return_response from request."
                        },
                    )
                if not wants_response and response is not None and not response.get("optional"):
                    return self._send(
                        400,
                        {
                            "message": "Service call requires responses but caller did not "
                            "ask for responses. Add ?return_response to query parameters."
                        },
                    )

                # Entity service schemas are PREVENT_EXTRA, so a key that is
                # neither a target nor a declared field is a vol.Invalid -- and
                # therefore, again, an empty 400. A nested `target` key lands
                # here: the REST endpoint hands the body straight to the
                # service and never unwraps one.
                fields = declared_fields(description)
                for key in data:
                    if key not in TARGET_KEYS and key not in fields:
                        return self._bad_request()
                for name, field in fields.items():
                    if field.get("required") and name not in data:
                        return self._bad_request()

                override = outer.state["service_result"]
                if override is not None:
                    return self._send(200, override)

                states = {s["entity_id"]: s for s in outer.state["states"]}
                masks = capability_masks(description, domain)
                reaches = target_domains(description) or [domain]
                fields_sent = {k: v for k, v in data.items() if k not in TARGET_KEYS}
                changed = []
                #: The entities the call actually selected, whether or not each
                #: went on to change. `helpers/service.py` filters candidates by
                #: availability, then by device class and feature, and asks
                #: `if not entities` of exactly what is left -- so this list, and
                #: not the change set, is what decides the `--response` refusal.
                selected = []
                for entity_id in entities_targeted(data):
                    state = states.get(entity_id)
                    if state is None or entity_id.split(".", 1)[0] not in reaches:
                        continue
                    # Home Assistant skips an unavailable entity in silence,
                    # and skips one that lacks the capability unless it was
                    # named outright, in which case it refuses. Both halves
                    # matter: the silence is what makes an area-targeted call
                    # indistinguishable from one that had nothing to do.
                    if state["state"] == "unavailable":
                        continue
                    features = (state.get("attributes") or {}).get("supported_features") or 0
                    if masks and not any(features & mask == mask for mask in masks):
                        if entity_id in (data.get("entity_id") or []):
                            # `entity_service_call` raises `ServiceNotSupported`,
                            # a `HomeAssistantError`, which reaches the wire as a
                            # bodyless 500. The status is deliberately not the
                            # point and neither is the body: hass-axi re-derives
                            # every refusal from the model, and this is what makes
                            # that the only thing it *can* do.
                            return self._server_error()
                        continue
                    selected.append(entity_id)
                    # Only a state that actually changes comes back, so a
                    # service asked for what already holds answers with an
                    # empty list -- the "nothing to do" half of the pair.
                    resulting = RESULTING_STATE.get(service)
                    if resulting is not None and not fields_sent:
                        if state["state"] == resulting:
                            continue
                        state["state"] = resulting
                    changed.append(state)

                if wants_response:
                    # A response call that reached no entity cannot answer with
                    # an empty change set, because there is no response to give:
                    # `helpers/service.py` raises
                    # `HomeAssistantError("Service call requested response data
                    # but did not match any entities")` and the wire carries a
                    # bodyless 500. Answering 200 here made "nothing targeted"
                    # unreachable for the one call shape that can only fail that
                    # way, and the client fell through to help about fields.
                    if not selected:
                        return self._server_error()
                    return self._send(200, {"changed_states": changed, "service_response": None})
                return self._send(200, changed)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_DELETE(self):
                self._handle("DELETE")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # A short poll interval keeps shutdown() from costing half a second per test.
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()

    @property
    def port(self):
        return self._server.server_address[1]

    @property
    def url(self):
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"


# ------------------------------------------------------------- WebSocket double


class FakeWsServer:
    """A WebSocket server performing the Home Assistant auth handshake and registry commands."""

    def __init__(self, *, reject_auth=False):
        self.reject_auth = reject_auth
        self.received = []
        self.entities = [json.loads(json.dumps(e)) for e in ENTITY_REGISTRY]
        self.areas = [json.loads(json.dumps(a)) for a in AREA_REGISTRY]
        self.devices = [json.loads(json.dumps(d)) for d in DEVICE_REGISTRY]
        self.fail_next = None
        #: A refusal that applies to *every* command rather than the next one.
        #: That is the shape the interesting faults actually have: a non-admin
        #: token is refused `unauthorized` by every `@require_admin` command it
        #: ever sends, and an instance that predates a command answers
        #: `unknown_command` to it forever.
        self.fail_all = None
        self.close_after = None
        #: Frames to emit before the next command result, e.g. an event or a
        #: pong, which a real instance interleaves freely.
        self.interleave = []
        #: Replace the auth_required greeting with something else.
        self.greeting = None
        #: Send an extra message between `auth` and `auth_ok`.
        self.mid_auth = None
        #: Emit a non-JSON frame instead of the next result.
        self.send_garbage = False
        #: Close the connection instead of answering the next command.
        self.close_on_command = False
        self._server = None
        self._thread = None

    # -- protocol ----------------------------------------------------------

    def _handler(self, websocket):
        if self.greeting is not None:
            websocket.send(json.dumps(self.greeting))
            return
        websocket.send(json.dumps({"type": "auth_required", "ha_version": "2026.1.0"}))
        message = json.loads(websocket.recv())
        if message.get("type") != "auth" or message.get("access_token") != FAKE_TOKEN:
            websocket.send(json.dumps({"type": "auth_invalid", "message": "Invalid access token"}))
            return
        if self.reject_auth:
            websocket.send(json.dumps({"type": "auth_invalid", "message": "Invalid access token"}))
            return
        if self.mid_auth is not None:
            websocket.send(json.dumps(self.mid_auth))
            return
        websocket.send(json.dumps({"type": "auth_ok", "ha_version": "2026.1.0"}))

        while True:
            try:
                raw = websocket.recv()
            except Exception:
                return
            command = json.loads(raw)
            self.received.append(command)
            if self.close_on_command:
                websocket.close()
                return
            if self.send_garbage:
                self.send_garbage = False
                websocket.send("this is not json")
                continue
            # A real instance interleaves events and pongs with results; the
            # client must skip them rather than mis-correlate.
            for frame in self.interleave:
                websocket.send(json.dumps(frame))
            self.interleave = []
            websocket.send(json.dumps(self._respond(command)))
            if self.close_after is not None and len(self.received) >= self.close_after:
                # Close inside the handler so the close frame follows the last
                # reply immediately, the way a restart drops a session mid-flight.
                websocket.close()
                return

    def _respond(self, command):
        message_id, type_ = command.get("id"), command.get("type")
        if self.fail_next is not None:
            error = self.fail_next
            self.fail_next = None
            return {"id": message_id, "type": "result", "success": False, "error": error}
        if self.fail_all is not None:
            return {
                "id": message_id,
                "type": "result",
                "success": False,
                "error": self.fail_all,
            }

        def fail(code, message):
            return {
                "id": message_id,
                "type": "result",
                "success": False,
                "error": {"code": code, "message": message},
            }

        def ok(result):
            # Serialized on the way out, as a real instance necessarily is: a
            # client can never end up holding a reference into server state,
            # so a test cannot pass because both sides share one object.
            return {
                "id": message_id,
                "type": "result",
                "success": True,
                "result": json.loads(json.dumps(result)),
            }

        allowed = WS_COMMAND_KEYS.get(type_)
        if allowed is not None:
            extra = sorted(set(command) - {"id", "type"} - set(allowed))
            if extra:
                return fail("invalid_format", f"extra keys not allowed @ data[{extra[0]!r}]")

        if type_ == "config/entity_registry/list":
            return ok(self.entities)
        if type_ == "config/area_registry/list":
            return ok(self.areas)
        if type_ == "config/device_registry/list":
            return ok(self.devices)
        if type_ == "config/floor_registry/list":
            return ok([])
        if type_ == "config/entity_registry/get":
            for entry in self.entities:
                if entry["entity_id"] == command.get("entity_id"):
                    return ok(extended_entry(entry))
            return fail("not_found", "Entity not found")
        if type_ == "config/entity_registry/update":
            for entry in self.entities:
                if entry["entity_id"] != command.get("entity_id"):
                    continue
                for key in ENTITY_UPDATE_FIELDS:
                    if key in command:
                        entry[key] = command[key]
                if "new_entity_id" in command:
                    entry["entity_id"] = command["new_entity_id"]
                # Home Assistant answers with the registry entry that now
                # exists, not with the request that produced it: every stored
                # field, including the ones the request never mentioned, and an
                # `area_id` that stays null when the entity's area comes from
                # its device. A double that echoed the request instead would
                # let a client report an entity's area from its own payload and
                # never be contradicted.
                return ok({"entity_entry": extended_entry(entry)})
            return fail("not_found", "Entity not found")
        if type_ == "config/area_registry/create":
            area = {
                "area_id": slugify(command["name"]),
                "name": command["name"],
                "icon": command.get("icon"),
                "floor_id": command.get("floor_id"),
                "aliases": [],
            }
            self.areas.append(area)
            return ok(area)
        if type_ == "config/area_registry/update":
            for area in self.areas:
                if area["area_id"] != command.get("area_id"):
                    continue
                for key in ("name", "icon", "floor_id"):
                    if key in command:
                        area[key] = command[key]
                return ok(area)
            return fail("not_found", "Area not found")
        if type_ == "config/area_registry/delete":
            for index, area in enumerate(self.areas):
                if area["area_id"] != command.get("area_id"):
                    continue
                del self.areas[index]
                # Home Assistant clears the deleted area from everything that
                # pointed at it rather than leaving dangling ids behind, which
                # is why a *typo* and not a delete is what strands an entity.
                for entry in self.entities:
                    if entry.get("area_id") == command["area_id"]:
                        entry["area_id"] = None
                for device in self.devices:
                    if device.get("area_id") == command["area_id"]:
                        device["area_id"] = None
                return ok(None)
            return fail("not_found", "Area not found")
        if type_ == "config/device_registry/update":
            for device in self.devices:
                if device["id"] != command.get("device_id"):
                    continue
                for key in ("name_by_user", "area_id", "disabled_by", "labels"):
                    if key in command:
                        device[key] = command[key]
                return ok(device)
            return fail("not_found", "Device not found")
        if type_ == "config/label_registry/list":
            return ok([])
        if type_ == "get_config":
            return ok({"version": "2026.1.0", "location_name": "Example Home"})
        if type_ == "get_services":
            return ok({entry["domain"]: entry["services"] for entry in SERVICES})
        if type_ == "get_states":
            return ok(STATES)
        # Home Assistant's own wording, and it names nothing: `connection.py`
        # sends a fixed `"Unknown command."` and logs the type rather than
        # returning it. A double that echoed the type back would let a client
        # pass that reads the command name out of a message that never carries
        # one.
        return fail("unknown_command", "Unknown command.")

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        from websockets.sync.server import serve

        self._server = serve(self._handler, "127.0.0.1", 0)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()

    @property
    def port(self):
        return self._server.socket.getsockname()[1]


class FakeInstallation:
    """Both transports on one base URL, which is what the CLI expects.

    Home Assistant serves REST and WebSocket from a single origin, and the CLI
    derives the WebSocket URL from ``HA_URL`` accordingly. The two doubles are
    separate servers on separate ports, so a command that needs both --
    ``state list --area``, ``doctor`` -- had no single ``HA_URL`` to run
    against, and could only be exercised by hand-wiring a Context.

    This restores the real topology with a front door: it reads the request
    line of each incoming connection and splices the connection to the
    WebSocket double for ``/api/websocket`` and to the REST double for
    everything else. Routing on the request line alone means neither double
    changes and no request body is ever parsed here.
    """

    WS_PATH = "/api/websocket"

    def __init__(self, rest, ws):
        self.rest = rest
        self.ws = ws
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(32)
        self._closing = threading.Event()
        self._thread = threading.Thread(target=self._accept_forever, daemon=True)

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._closing.set()
        with suppress(OSError):
            # Closing a socket another thread is blocked in accept() on does
            # not reliably wake it, so knock once and let the loop notice.
            socket.create_connection(self._listener.getsockname(), timeout=1).close()
        self._thread.join(timeout=5)
        with suppress(OSError):
            self._listener.close()

    @property
    def url(self):
        host, port = self._listener.getsockname()[:2]
        return f"http://{host}:{port}"

    @property
    def environ(self):
        return {"HA_URL": self.url, "HA_TOKEN": FAKE_TOKEN}

    # -- routing -----------------------------------------------------------

    def _accept_forever(self):
        while not self._closing.is_set():
            try:
                client, _ = self._listener.accept()
            except OSError:
                # The listener was closed by stop(); nothing further arrives.
                return
            if self._closing.is_set():
                client.close()
                return
            threading.Thread(target=self._route, args=(client,), daemon=True).start()

    def _route(self, client):
        upstream = None
        try:
            head = self._read_request_line(client)
            if not head:
                return
            parts = head.split(" ")
            path = parts[1].split("?")[0] if len(parts) > 1 else ""
            port = self.ws.port if path == self.WS_PATH else self.rest.port
            upstream = socket.create_connection(("127.0.0.1", port))
            upstream.sendall(head.encode("latin-1"))
            # One direction per thread, and this one blocks until the upstream
            # is done, so both sockets close exactly once the exchange ends.
            back = threading.Thread(target=self._splice, args=(upstream, client), daemon=True)
            back.start()
            self._splice(client, upstream)
            back.join(timeout=5)
        except OSError:
            pass
        finally:
            for sock in (client, upstream):
                if sock is not None:
                    with suppress(OSError):
                        sock.close()

    @staticmethod
    def _read_request_line(client) -> str:
        """Read only the request line, which is all the routing decision needs."""
        line = b""
        while not line.endswith(b"\n"):
            byte = client.recv(1)
            if not byte or len(line) > 8192:
                return ""
            line += byte
        return line.decode("latin-1")

    @staticmethod
    def _splice(src, dst) -> None:
        try:
            while True:
                chunk = src.recv(65536)
                if not chunk:
                    break
                dst.sendall(chunk)
        except OSError:
            pass
        finally:
            with suppress(OSError):
                dst.shutdown(socket.SHUT_WR)


@pytest.fixture(autouse=True)
def _clean_secrets():
    output.reset_secrets()
    output.reset_notices()
    yield
    output.reset_secrets()
    output.reset_notices()


@pytest.fixture
def rest_server():
    server = FakeRestServer().start()
    yield server
    server.stop()


@pytest.fixture
def ws_server():
    server = FakeWsServer().start()
    yield server
    server.stop()


@pytest.fixture
def rest_env(rest_server):
    return {"HA_URL": rest_server.url, "HA_TOKEN": FAKE_TOKEN}


@pytest.fixture
def ws_env(ws_server):
    return {"HA_URL": f"http://127.0.0.1:{ws_server.port}", "HA_TOKEN": FAKE_TOKEN}


@pytest.fixture
def installation(rest_server, ws_server):
    server = FakeInstallation(rest_server, ws_server).start()
    yield server
    server.stop()


@pytest.fixture
def installation_env(installation):
    """One HA_URL serving REST and WebSocket, as a real instance does.

    Use this for anything that crosses transports; `rest_env` and `ws_env`
    stay the cheaper choice when only one is in play.
    """
    return installation.environ


@pytest.fixture
def run_cli(capsys):
    """Invoke the CLI exactly as a shell would and return (exit code, stdout)."""
    from hass_axi.cli import main

    def invoke(argv, environ=None):
        capsys.readouterr()
        code = main(list(argv), environ=dict(environ or {}))
        return code, capsys.readouterr().out

    return invoke

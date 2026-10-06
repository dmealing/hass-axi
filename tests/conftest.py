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
import os
import re
import socket
import threading
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

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
        "attributes": {
            "friendly_name": "Example Hub Temperature",
            "unit_of_measurement": "C",
            "device_class": "temperature",
            "state_class": "measurement",
        },
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
            "device_class": "energy",
            "state_class": "total_increasing",
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
        "attributes": {
            "friendly_name": "Example Hub Reading",
            "unit_of_measurement": "A",
            "device_class": "current",
            "state_class": "measurement",
        },
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

#: The floor registry, as `config/floor_registry/list` publishes an entry. One
#: floor holds an area and one holds none, so "a floor that exists" and "a
#: floor in use" are two cases rather than one.
FLOOR_REGISTRY = [
    {
        "aliases": [],
        "created_at": 1767225600.0,
        "floor_id": "ground",
        "icon": None,
        "level": 0,
        "name": "Example Ground Floor",
        "modified_at": 1767225600.0,
    },
    {
        "aliases": [],
        "created_at": 1767225600.0,
        "floor_id": "example_upper_floor",
        "icon": "mdi:home-floor-1",
        "level": 1,
        "name": "Example Upper Floor",
        "modified_at": 1767225600.0,
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

# ------------------------------------------------------------- the recorder
#
# Everything below is what Home Assistant's recorder answers with, in the shapes
# read out of `components/history`, `components/logbook` and
# `components/recorder/websocket_api.py` at 2026.8.3. The window every test of
# these reads uses is the fixture day: `RECORDER_NOW` is the instant the tests
# pin as "now", so the default `--start 24h` opens at `RECORDER_DAY`.

RECORDER_DAY = "2026-01-01T00:00:00+00:00"
RECORDER_NOW = "2026-01-02T00:00:00+00:00"
_DAY_EPOCH = 1767225600  # RECORDER_DAY as epoch seconds
HOUR_MS = 3_600_000


def _at(hour: float) -> str:
    """An ISO instant ``hour`` hours into the fixture day (negative is the day before)."""
    return datetime.fromtimestamp(_DAY_EPOCH + hour * 3600, tz=timezone.utc).isoformat()


#: State changes as the recorder stored them: entity -> [(when, state)], oldest
#: first. The first change of each entity predates the fixture day, so the
#: state the window opens in has to be carried in from before it -- which is
#: what `include_start_time_state` does upstream and what a client that read
#: the first row as a change would get wrong.
HISTORY = {
    "light.example_lamp": [(_at(-4), "on"), (_at(6), "off"), (_at(18), "on")],
    "sensor.example_temperature": [
        (_at(-1), "20.5"),
        (_at(3), "19.0"),
        (_at(9), "unavailable"),
        (_at(10), "22.5"),
        (_at(20), "21.5"),
    ],
    "binary_sensor.example_doorway": [(_at(-30), "off"), (_at(12), "on"), (_at(12.5), "off")],
}

#: Logbook rows as `EventProcessor` renders them with `timestamp=False` and
#: `include_entity_name=True`: `when` is ISO text, a state change carries
#: `state` and no `message`, an event carries `message`, and the cause arrives
#: spread across `context_*` keys -- or not at all.
LOGBOOK = [
    {
        "when": _at(6),
        "name": "Example Morning",
        "message": "triggered by time",
        "domain": "automation",
        "entity_id": "automation.example_morning",
        "source": "time",
        "context_id": "01EXAMPLELOGBOOK0000000001",
    },
    {
        "when": _at(6),
        "state": "off",
        "entity_id": "light.example_lamp",
        "name": "Example Lamp",
        "context_id": "01EXAMPLELOGBOOK0000000001",
        "context_event_type": "automation_triggered",
        "context_domain": "automation",
        "context_name": "Example Morning",
        "context_entity_id": "automation.example_morning",
        "context_entity_id_name": "Example Morning",
        "context_source": "time",
        "context_message": "triggered by time",
    },
    {
        "when": _at(12),
        "state": "on",
        "entity_id": "binary_sensor.example_doorway",
        "name": "Example Doorway",
    },
    {
        "when": _at(18),
        "state": "on",
        "entity_id": "light.example_lamp",
        "name": "Example Lamp",
        "context_id": "01EXAMPLELOGBOOK0000000002",
        "context_user_id": "example-user",
        "context_event_type": "call_service",
        "context_domain": "light",
        "context_service": "turn_on",
    },
]

#: `recorder/list_statistic_ids` rows, flattened the way
#: `_flatten_list_statistic_ids_metadata_result` flattens them. `has_mean` is
#: still published beside `mean_type`; a meter has a sum and no mean, a reading
#: a mean and no sum, and an external statistic has a `source` that is not the
#: recorder and an id that is not an entity's.
STATISTICS_METADATA = [
    {
        "statistic_id": "sensor.example_legacy_meter",
        "display_unit_of_measurement": "kWh",
        "has_mean": False,
        "mean_type": 0,
        "has_sum": True,
        "name": None,
        "source": "recorder",
        "statistics_unit_of_measurement": "kWh",
        "unit_class": "energy",
    },
    {
        "statistic_id": "sensor.example_temperature",
        "display_unit_of_measurement": "C",
        "has_mean": True,
        "mean_type": 1,
        "has_sum": False,
        "name": None,
        "source": "recorder",
        "statistics_unit_of_measurement": "C",
        "unit_class": "temperature",
    },
    {
        "statistic_id": "example:grid_import",
        "display_unit_of_measurement": "kWh",
        "has_mean": False,
        "mean_type": 0,
        "has_sum": True,
        "name": "Example Grid Import",
        "source": "example",
        "statistics_unit_of_measurement": "kWh",
        "unit_class": "energy",
    },
]


def hourly_rows(values) -> list:
    """Hourly statistics rows across the fixture day, one per value given.

    Each value is a dict of the types that hour holds; ``start`` and ``end``
    are epoch milliseconds, which is what `ws_handle_get_statistics_during_period`
    converts them to before sending.
    """
    rows = []
    for hour, value in enumerate(values):
        if value is None:
            continue
        start = _DAY_EPOCH * 1000 + hour * HOUR_MS
        rows.append({"start": start, "end": start + HOUR_MS, **value})
    return rows


def meter_rows(changes) -> list:
    """A meter's hourly rows from its per-hour change, its reading and its running sum."""
    values, reading, running = [], 100.0, 0.0
    for change in changes:
        if change is None:
            values.append(None)
            continue
        reading += change
        running += change
        values.append(
            {
                "change": change,
                "state": reading,
                "sum": running,
                "mean": None,
                "min": None,
                "max": None,
            }
        )
    return hourly_rows(values)


def statistics_rows() -> dict:
    """Every statistic's stored hourly rows, freshly built for each double."""
    return {
        "sensor.example_legacy_meter": meter_rows([0.5] * 24),
        "sensor.example_temperature": hourly_rows(
            [
                {
                    "mean": 20 + (hour % 6) * 0.5,
                    "min": 19.5 + (hour % 6) * 0.5,
                    "max": 20.5 + (hour % 6) * 0.5,
                    "change": None,
                    "state": None,
                    "sum": None,
                }
                for hour in range(24)
            ]
        ),
    }


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
    "config/floor_registry/create": ("name", "aliases", "icon", "level"),
    "config/floor_registry/update": ("floor_id", "aliases", "icon", "level", "name"),
    "config/floor_registry/delete": ("floor_id",),
    "config/entity_registry/remove": ("entity_id",),
    "config/label_registry/list": (),
    "get_config": (),
    "get_services": (),
    "get_states": (),
    "recorder/list_statistic_ids": ("statistic_type",),
    "recorder/get_statistics_metadata": ("statistic_ids",),
    "recorder/statistics_during_period": (
        "start_time",
        "end_time",
        "statistic_ids",
        "period",
        "units",
        "types",
    ),
}

#: What each write command's schema requires and what type it takes, as
#: `vol.Required` / `vol.Optional` declare them upstream: key -> (required,
#: types, nullable). A missing required key and a value of the wrong type are
#: both `invalid_format`, raised by the schema before the handler runs -- so a
#: write the real server never accepts is not accepted here either. Transcribed
#: from `components/config/{area,floor,entity,device}_registry.py`, and
#: deliberately not derived from `hass_axi.ws.REGISTRY`.
WS_WRITE_SCHEMAS = {
    "config/area_registry/create": {
        "name": (True, (str,), False),
        "icon": (False, (str,), False),
        "floor_id": (False, (str,), False),
        "aliases": (False, (list,), False),
        "labels": (False, (list,), False),
        "picture": (False, (str,), True),
    },
    "config/area_registry/update": {
        "area_id": (True, (str,), False),
        "name": (False, (str,), False),
        "icon": (False, (str,), True),
        "floor_id": (False, (str,), True),
        "aliases": (False, (list,), False),
        "labels": (False, (list,), False),
        "picture": (False, (str,), True),
    },
    "config/area_registry/delete": {"area_id": (True, (str,), False)},
    "config/floor_registry/create": {
        "name": (True, (str,), False),
        "aliases": (False, (list,), False),
        "icon": (False, (str,), True),
        "level": (False, (int,), True),
    },
    "config/floor_registry/update": {
        "floor_id": (True, (str,), False),
        "name": (False, (str,), False),
        "aliases": (False, (list,), False),
        "icon": (False, (str,), True),
        "level": (False, (int,), True),
    },
    "config/floor_registry/delete": {"floor_id": (True, (str,), False)},
    "config/entity_registry/update": {
        "entity_id": (True, (str,), False),
        "name": (False, (str,), True),
        "icon": (False, (str,), True),
        "area_id": (False, (str,), True),
        "new_entity_id": (False, (str,), False),
        "disabled_by": (False, (str,), True),
        "hidden_by": (False, (str,), True),
        "labels": (False, (list,), False),
        "aliases": (False, (list,), False),
    },
    "config/entity_registry/remove": {"entity_id": (True, (str,), False)},
    "config/device_registry/update": {
        "device_id": (True, (str,), False),
        "name_by_user": (False, (str,), True),
        "area_id": (False, (str,), True),
        "disabled_by": (False, (str,), True),
        "labels": (False, (list,), False),
    },
}

#: `cv.entity_id`, which the entity registry commands validate their subject with.
_VALID_ENTITY_ID = re.compile(r"^(?!.+__)(?!_)[\da-z_]+(?<!_)\.(?!_)[\da-z_]+(?<!_)$")


def schema_fault(type_: str, command: dict) -> str | None:
    """The `invalid_format` message a write's schema raises, or ``None``."""
    schema = WS_WRITE_SCHEMAS.get(type_)
    if schema is None:
        return None
    for key, (required, types, nullable) in schema.items():
        if key not in command:
            if required:
                return f"required key not provided @ data['{key}']"
            continue
        value = command[key]
        if value is None:
            if not nullable:
                return f"expected {types[0].__name__} for dictionary value @ data['{key}']"
            continue
        # `bool` is an `int` to Python and not to voluptuous's `int`.
        if not isinstance(value, types) or (isinstance(value, bool) and bool not in types):
            return f"expected {types[0].__name__} for dictionary value @ data['{key}']"
    if type_.startswith("config/entity_registry/") and not _VALID_ENTITY_ID.match(
        command["entity_id"]
    ):
        return "Entity ID is an invalid entity ID for dictionary value @ data['entity_id']"
    if command.get("disabled_by") not in (None, "user"):
        return "value must be one of ['user'] for dictionary value @ data['disabled_by']"
    if command.get("hidden_by") not in (None, "user"):
        return "value must be one of ['user'] for dictionary value @ data['hidden_by']"
    return None


def normalized_name(name: str) -> str:
    """`helpers/normalized_name_base_registry.normalize_name`: casefolded, no spaces.

    The area and floor registries refuse a name whose *normalized* form is
    taken, so `example room` collides with `Example Room`.
    """
    return name.casefold().replace(" ", "")


def unique_id_from_name(name: str, taken) -> str:
    """`BaseRegistryItems.generate_id_from_name`: the slug, suffixed until it is free."""
    base = slugify(name)
    candidate, tries = base, 1
    while candidate in taken:
        tries += 1
        candidate = f"{base}_{tries}"
    return candidate


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

#: What a camera proxy answers with: the opening of a JPEG, which no text
#: encoding reads. Synthetic -- four marker bytes and filler, not a picture.
CAMERA_IMAGE = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + bytes(range(128, 256)) * 4 + b"\xff\xd9"


class FakeRestServer:
    """An HTTP server that answers the Home Assistant REST endpoints under test."""

    def __init__(self):
        self.requests = []
        self.state = {
            "states": [json.loads(json.dumps(s)) for s in STATES],
            "services": SERVICES,
            "template": "rendered",
            "service_result": None,
            "history": {k: list(v) for k, v in HISTORY.items()},
            "logbook": [dict(entry) for entry in LOGBOOK],
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
        #: The credential this server accepts. A test that needs a token of a
        #: particular shape -- a JWT, to prove none of its segments is printed
        #: -- sets it here and in the environment it runs the command with.
        self.token = FAKE_TOKEN
        #: `(body, content_type)` answered with a 200 to *every* request, by
        #: something that is not Home Assistant at all: a router's login page,
        #: a proxy's own error document, another application on the port. It
        #: reads no token and routes nothing, which is exactly what makes it a
        #: 200 -- and a client that takes a 200 for health passes it.
        self.impostor = None
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
                return header == f"Bearer {outer.token}"

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
                if outer.impostor is not None:
                    body, content_type = outer.impostor
                    return self._send(200, body, content_type=content_type)
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
                if path.startswith("/api/camera_proxy/") and method == "GET":
                    # `CameraImageView` answers with the image itself: bytes
                    # that are not text in any encoding, typed as what they are.
                    return self._send(200, CAMERA_IMAGE, content_type="image/jpeg")
                query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
                if path.startswith("/api/history/period") and method == "GET":
                    return self._history(path[len("/api/history/period") :], query)
                if path.startswith("/api/logbook") and method == "GET":
                    return self._logbook(path[len("/api/logbook") :], query)
                return self._not_found()

            # -- the recorder, read over REST -------------------------------

            @staticmethod
            def _instant(text):
                """`dt_util.parse_datetime`, near enough: ISO 8601 or nothing."""
                try:
                    moment = datetime.fromisoformat(unquote(text))
                except ValueError:
                    return None
                return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)

            def _history(self, suffix, query):
                """`HistoryPeriodView.get`, transcribed.

                A window with no `end_time` ends one day after it starts, not
                now; the entity filter is required; and the answer is one list
                per requested entity, in the order requested, empty where the
                recorder holds nothing. With `minimal_response` only the first
                row is a whole state.
                """
                start = datetime.now(timezone.utc) - timedelta(days=1)
                if suffix.startswith("/"):
                    start = self._instant(suffix[1:])
                    if start is None:
                        return self._send(400, {"message": "Invalid datetime"})
                wanted = (query.get("filter_entity_id") or [""])[0].strip().lower()
                if not wanted:
                    return self._send(400, {"message": "filter_entity_id is missing"})
                ids = wanted.split(",")
                if any("." not in entity_id for entity_id in ids):
                    return self._send(400, {"message": "Invalid filter_entity_id"})
                end = start + timedelta(days=1)
                if "end_time" in query:
                    end = self._instant(query["end_time"][0])
                    if end is None:
                        return self._send(400, {"message": "Invalid end_time"})
                minimal = "minimal_response" in query
                states = {s["entity_id"]: s for s in outer.state["states"]}
                answer = []
                for entity_id in ids:
                    changes = outer.state["history"].get(entity_id, [])
                    before = [c for c in changes if self._instant(c[0]) < start]
                    within = [c for c in changes if start <= self._instant(c[0]) <= end]
                    if before:
                        # The state already held when the window opened, timed
                        # at the window's start, as `LazyState` does with a
                        # start time.
                        within = [(start.isoformat(), before[-1][1]), *within]
                    rows = []
                    for index, (when, value) in enumerate(within):
                        if index and minimal:
                            rows.append({"state": value, "last_changed": when})
                            continue
                        attributes = (states.get(entity_id) or {}).get("attributes") or {}
                        rows.append(
                            {
                                "entity_id": entity_id,
                                "state": value,
                                "attributes": attributes,
                                "last_changed": when,
                                "last_updated": when,
                            }
                        )
                    answer.append(rows)
                return self._send(200, answer)

            def _logbook(self, suffix, query):
                """`LogbookView.get`, transcribed: the same one-day default end."""
                start = datetime.now(timezone.utc)
                if suffix.startswith("/"):
                    start = self._instant(suffix[1:])
                    if start is None:
                        return self._send(400, {"message": "Invalid datetime"})
                end = start + timedelta(days=1)
                if "end_time" in query:
                    end = self._instant(query["end_time"][0])
                    if end is None:
                        return self._send(400, {"message": "Invalid end_time"})
                ids = [i for i in (query.get("entity") or [""])[0].split(",") if i]
                rows = [
                    entry
                    for entry in outer.state["logbook"]
                    if start <= self._instant(entry["when"]) < end
                    and (not ids or entry.get("entity_id") in ids)
                ]
                return self._send(200, rows)

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
                # An entity service is registered with
                # `cv.has_at_least_one_key(*ENTITY_SERVICE_FIELDS)`, so one
                # called with no target at all is one more `vol.Invalid` and one
                # more empty 400. Accepting it here answered 200 with an empty
                # list to a call a real instance refuses.
                if description.get("target") is not None and not any(
                    key in data for key in TARGET_KEYS
                ):
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
                    # The response is always a mapping: `ServiceRegistry.async_call`
                    # raises on anything else, and an entity service answers with
                    # one keyed by each entity that ran. It was `None` here, which
                    # no real instance sends.
                    answered = {entity_id: {} for entity_id in selected}
                    return self._send(
                        200, {"changed_states": changed, "service_response": answered}
                    )
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


def _period_bounds(moment: datetime, period: str) -> tuple:
    """The start and end of the day, week or month ``moment`` falls in.

    `_statistics_during_period_with_session` aligns with the installation's
    local time; this double's time zone is UTC. A week starts on Monday.
    """
    day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        return day, day + timedelta(days=1)
    if period == "week":
        monday = day - timedelta(days=day.weekday())
        return monday, monday + timedelta(days=7)
    first = day.replace(day=1)
    following = (first + timedelta(days=32)).replace(day=1)
    return first, following


def _roll_up(rows: list, period: str) -> list:
    """Hourly rows compiled into days, weeks or months, as the recorder compiles them."""
    groups: dict = {}
    for row in rows:
        moment = datetime.fromtimestamp(row["start"] / 1000, tz=timezone.utc)
        start, end = _period_bounds(moment, period)
        groups.setdefault((start.timestamp() * 1000, end.timestamp() * 1000), []).append(row)
    rolled = []
    for (start_ms, end_ms), hours in sorted(groups.items()):
        means = [h["mean"] for h in hours if h.get("mean") is not None]
        changes = [h["change"] for h in hours if h.get("change") is not None]
        rolled.append(
            {
                "start": int(start_ms),
                "end": int(end_ms),
                "mean": sum(means) / len(means) if means else None,
                "min": min((h["min"] for h in hours if h.get("min") is not None), default=None),
                "max": max((h["max"] for h in hours if h.get("max") is not None), default=None),
                "change": sum(changes) if changes else None,
                "state": hours[-1].get("state"),
                "sum": hours[-1].get("sum"),
            }
        )
    return rolled


class FakeWsServer:
    """A WebSocket server performing the Home Assistant auth handshake and registry commands."""

    def __init__(self, *, reject_auth=False):
        self.reject_auth = reject_auth
        self.received = []
        self.entities = [json.loads(json.dumps(e)) for e in ENTITY_REGISTRY]
        self.areas = [json.loads(json.dumps(a)) for a in AREA_REGISTRY]
        self.devices = [json.loads(json.dumps(d)) for d in DEVICE_REGISTRY]
        self.floors = [json.loads(json.dumps(f)) for f in FLOOR_REGISTRY]
        #: The credential this server accepts; see `FakeRestServer.token`.
        self.token = FAKE_TOKEN
        self.statistics_meta = [json.loads(json.dumps(m)) for m in STATISTICS_METADATA]
        #: Stored hourly rows per statistic. A test reshapes one to model a
        #: meter that resets, goes backwards or stops reporting for a while.
        self.statistics = statistics_rows()
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
        if message.get("type") != "auth" or message.get("access_token") != self.token:
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

        # The schema runs before the handler, so a write with a missing key or
        # a value of the wrong type is refused without touching anything.
        fault = schema_fault(type_, command)
        if fault is not None:
            return fail("invalid_format", fault)

        def unhandled():
            # A handler that raises anything but the error it catches reaches
            # `connection.async_handle_exception`, which answers with a fixed
            # `unknown_error` and logs the cause. Updating an area, a floor or
            # a device that does not exist is a `KeyError` nobody catches.
            return fail("unknown_error", "Unknown error")

        if type_ == "config/entity_registry/list":
            return ok(self.entities)
        if type_ == "config/area_registry/list":
            return ok(self.areas)
        if type_ == "config/device_registry/list":
            return ok(self.devices)
        if type_ == "config/floor_registry/list":
            return ok(self.floors)
        if type_ == "config/floor_registry/create":
            taken = next(
                (
                    f
                    for f in self.floors
                    if normalized_name(f["name"]) == normalized_name(command["name"])
                ),
                None,
            )
            if taken is not None:
                return fail(
                    "invalid_info",
                    f"The name {command['name']} ({normalized_name(taken['name'])}) "
                    "is already in use",
                )
            floor = {
                "aliases": [a.strip() for a in command.get("aliases") or [] if a.strip()],
                "created_at": 1767312000.0,
                "floor_id": unique_id_from_name(
                    command["name"], {f["floor_id"] for f in self.floors}
                ),
                "icon": command.get("icon"),
                "level": command.get("level"),
                "name": command["name"],
                "modified_at": 1767312000.0,
            }
            self.floors.append(floor)
            return ok(floor)
        if type_ == "config/floor_registry/update":
            for floor in self.floors:
                if floor["floor_id"] != command["floor_id"]:
                    continue
                if "name" in command and any(
                    other is not floor
                    and normalized_name(other["name"]) == normalized_name(command["name"])
                    for other in self.floors
                ):
                    return fail(
                        "invalid_info",
                        f"The name {command['name']} ({normalized_name(command['name'])}) "
                        "is already in use",
                    )
                for key in ("name", "icon", "level", "aliases"):
                    if key in command:
                        floor[key] = command[key]
                floor["modified_at"] += 1
                return ok(floor)
            return unhandled()
        if type_ == "config/floor_registry/delete":
            for index, floor in enumerate(self.floors):
                if floor["floor_id"] != command["floor_id"]:
                    continue
                del self.floors[index]
                # The area registry listens for the removal and clears the
                # floor from every area on it, as it does for a deleted area.
                for area in self.areas:
                    if area.get("floor_id") == command["floor_id"]:
                        area["floor_id"] = None
                return ok(None)
            return fail("invalid_info", "Floor ID doesn't exist")
        if type_ == "config/entity_registry/remove":
            for index, entry in enumerate(self.entities):
                if entry["entity_id"] == command["entity_id"]:
                    del self.entities[index]
                    return ok(None)
            return fail("not_found", "Entity not found")
        if type_ == "config/entity_registry/get":
            for entry in self.entities:
                if entry["entity_id"] == command.get("entity_id"):
                    return ok(extended_entry(entry))
            return fail("not_found", "Entity not found")
        if type_ == "config/entity_registry/update":
            for entry in self.entities:
                if entry["entity_id"] != command.get("entity_id"):
                    continue
                if command.get("disabled_by", "") is None and entry.get("device_id"):
                    # "Don't allow enabling an entity of a disabled device."
                    device = next((d for d in self.devices if d["id"] == entry["device_id"]), None)
                    if device is not None and device.get("disabled_by"):
                        return fail("invalid_info", "Device is disabled")
                new_id = command.get("new_entity_id")
                if new_id is not None and new_id != entry["entity_id"]:
                    # `EntityRegistry._async_update_entity` raises `ValueError`
                    # for each of these, and the handler turns one into
                    # `invalid_info` carrying its text.
                    if not _VALID_ENTITY_ID.match(new_id):
                        return fail("invalid_info", "Invalid entity ID")
                    if new_id.split(".", 1)[0] != entry["entity_id"].split(".", 1)[0]:
                        return fail("invalid_info", "New entity ID should be same domain")
                    if any(other["entity_id"] == new_id for other in self.entities):
                        return fail("invalid_info", "Entity with this ID is already registered")
                enabling = "disabled_by" in command and command["disabled_by"] is None
                for key in ENTITY_UPDATE_FIELDS:
                    if key in command:
                        entry[key] = command[key]
                if new_id is not None:
                    entry["entity_id"] = new_id
                if enabling:
                    # Enabling needs a reload of the entry that supplies the
                    # entity, and the answer says so beside the entry.
                    return ok({"entity_entry": extended_entry(entry), "reload_delay": 30})
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
            taken = next(
                (
                    a
                    for a in self.areas
                    if normalized_name(a["name"]) == normalized_name(command["name"])
                ),
                None,
            )
            if taken is not None:
                # `AreaRegistry.async_create` raises `ValueError`, sent on as
                # `invalid_info`. Appending a second area of the same name --
                # which this double did -- is a state no real registry reaches.
                return fail(
                    "invalid_info",
                    f"The name {command['name']} ({normalized_name(taken['name'])}) "
                    "is already in use",
                )
            area = {
                "area_id": unique_id_from_name(command["name"], {a["area_id"] for a in self.areas}),
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
                if "name" in command and any(
                    other is not area
                    and normalized_name(other["name"]) == normalized_name(command["name"])
                    for other in self.areas
                ):
                    return fail(
                        "invalid_info",
                        f"The name {command['name']} ({normalized_name(command['name'])}) "
                        "is already in use",
                    )
                # `floor_id` is stored as given: the area registry does not ask
                # the floor registry whether it exists, which is why a client
                # has to.
                for key in ("name", "icon", "floor_id"):
                    if key in command:
                        area[key] = command[key]
                return ok(area)
            return unhandled()
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
            return fail("invalid_info", "Area ID doesn't exist")
        if type_ == "config/device_registry/update":
            for device in self.devices:
                if device["id"] != command.get("device_id"):
                    continue
                for key in ("name_by_user", "area_id", "disabled_by", "labels"):
                    if key in command:
                        device[key] = command[key]
                return ok(device)
            return unhandled()
        if type_ == "config/label_registry/list":
            return ok([])
        if type_ == "get_config":
            return ok({"version": "2026.1.0", "location_name": "Example Home"})
        if type_ == "get_services":
            return ok({entry["domain"]: entry["services"] for entry in SERVICES})
        if type_ == "get_states":
            return ok(STATES)
        if type_ == "recorder/list_statistic_ids":
            kind = command.get("statistic_type")
            if kind not in (None, "sum", "mean"):
                return fail("invalid_format", "value must be one of ['mean', 'sum']")
            return ok(
                [
                    meta
                    for meta in self.statistics_meta
                    if kind is None
                    or (kind == "sum" and meta["has_sum"])
                    or (kind == "mean" and meta["mean_type"])
                ]
            )
        if type_ == "recorder/get_statistics_metadata":
            wanted = command.get("statistic_ids")
            return ok(
                [m for m in self.statistics_meta if not wanted or m["statistic_id"] in wanted]
            )
        if type_ == "recorder/statistics_during_period":
            return self._statistics_during_period(command, ok, fail)
        # Home Assistant's own wording, and it names nothing: `connection.py`
        # sends a fixed `"Unknown command."` and logs the type rather than
        # returning it. A double that echoed the type back would let a client
        # pass that reads the command name out of a message that never carries
        # one.
        return fail("unknown_command", "Unknown command.")

    def _statistics_during_period(self, command, ok, fail):
        """`ws_handle_get_statistics_during_period`, transcribed.

        The schema refuses a missing `start_time`, `statistic_ids` or `period`
        and an unknown period; an unparseable start is `invalid_start_time`.
        Rows carry only the requested types, a type the statistic does not keep
        comes back `None` rather than as an error -- which is why a client has
        to choose the types by kind -- and a statistic with no rows in the
        window is absent from the answer altogether. Hourly rows are stored;
        daily, weekly and monthly ones are rolled up from them the way the
        recorder compiles them.

        **The window is widened to the period before anything is read.**
        `_statistics_during_period_with_session` moves `start_time` back to the
        start of its day, week or month and `end_time` forward to the end of
        its own, so a coarse bucket covers time before the start that was
        asked for. A double that filtered on the window as given -- which this
        one did -- cannot show a client summing a bucket that begins two days
        early, and a total 4% too high on a real installation passed here.
        """
        for key in ("start_time", "statistic_ids", "period"):
            if key not in command:
                return fail("invalid_format", f"required key not provided @ data['{key}']")
        if command["period"] not in ("5minute", "hour", "day", "week", "month", "year"):
            return fail("invalid_format", "value must be one of the periods")
        try:
            start = datetime.fromisoformat(command["start_time"])
        except ValueError:
            return fail("invalid_start_time", "Invalid start_time")
        end = datetime.fromisoformat(command["end_time"]) if command.get("end_time") else None
        types = command.get("types") or [
            "change",
            "last_reset",
            "max",
            "mean",
            "min",
            "state",
            "sum",
        ]
        start_ms = start.timestamp() * 1000
        end_ms = end.timestamp() * 1000 if end else float("inf")
        answer = {}
        for statistic_id in command["statistic_ids"]:
            low, high = start_ms, end_ms
            period = command["period"]
            if period in ("day", "week", "month"):
                low = _period_bounds(start, period)[0].timestamp() * 1000
                if end is not None:
                    high = _period_bounds(end, period)[1].timestamp() * 1000
            rows = [r for r in self.statistics.get(statistic_id, []) if low <= r["start"] < high]
            if period in ("day", "week", "month"):
                rows = _roll_up(rows, period)
            elif period != "hour":
                rows = []
            if rows:
                answer[statistic_id] = [
                    {"start": r["start"], "end": r["end"], **{t: r.get(t) for t in types}}
                    for r in rows
                ]
        return ok(answer)

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
            if self._thread is not None:
                self._thread.join(timeout=5)
            # The sync Server owns an os.pipe() pair used to interrupt
            # accept() on shutdown; shutdown() does not close it, and
            # leaking two descriptors per start/stop cycle exhausts the
            # suite's file-descriptor budget over ~2,200 tests.
            for attr in ("shutdown_watcher", "shutdown_notifier"):
                fd = getattr(self._server, attr, None)
                if fd is not None:
                    try:
                        if hasattr(fd, "close"):
                            fd.close()
                        else:
                            os.close(fd)
                    except OSError:
                        pass

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
def _isolated_session_record(tmp_path, monkeypatch):
    """Point the session record at a directory this test owns.

    `hass-axi context` reads it and `hass-axi context end` writes it, and the
    default is under the real home directory: a suite that left it there would
    read whatever the developer's own sessions recorded, and write to it.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))


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

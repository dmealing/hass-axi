#!/usr/bin/env python3
"""Capture the *shape* of a real Home Assistant's answers: names and JSON types, never values.

The doubles in ``tests/conftest.py`` are transcribed from upstream source, and
the lab suite reads a real server without writing down what it saw, so until
this file nothing committed here said what a real Home Assistant sends. This
script reads one and records, per object, the keys it carries and the JSON type
of each: a state, the registry entries, a service definition, the recorder's
statistics, a history row, a logbook row, and the WebSocket frames.
``tests/test_shape_contract.py`` then holds the tool's readers and the doubles
to the result, offline.

Usage::

    .venv/bin/python scripts/shapecapture.py              # capture the lab, print it
    .venv/bin/python scripts/shapecapture.py --write      # refresh the committed capture
    .venv/bin/python scripts/shapecapture.py --check      # exit 1 if the lab has drifted
    .venv/bin/python scripts/shapecapture.py --check --house [--env-file PATH]
                                                          # report how an installation differs

**The committed capture comes from the lab and from nowhere else.** The lab is
the pinned, disposable container the live suite already uses
(``tests/live/lab/container.py``): it runs on loopback, holds the demo
integration and nothing of anybody's, and is removed when the run ends.
``--write`` starts one itself and refuses any other target, so the file cannot
come to hold a house. It needs docker, as the lab suite does.

**What keeps a value out of the file.** Three things, none of them a promise:

- A key is recorded only at a position where the key is a *field name*. Where
  Home Assistant keys a mapping by an identifier -- services by domain and by
  name, fields by name, statistics by id -- the script walks the values and
  never looks at the keys. Nested objects are descended only where this file
  names them.
- Every recorded key passes :func:`is_name`. One that does not is counted and
  never printed, and ``--write`` fails rather than drop it quietly.
- The only things written beside a key are words from :data:`JSON_TYPES`.

A household word shaped like a key cannot be told from a key by its shape,
which is why the first rule is structural and why ``--write`` has one source.

**The lab is seeded before it is read**, because a fresh one is the convenient
case: no floor, no entity anybody renamed, no statistics compiled, and a
logbook nothing caused. :func:`seed` writes those states through the API, with
the repository's synthetic vocabulary, and only into a disposable target.

**``--house`` is read-only and reports names sparingly.** It sends ``GET``
requests and the WebSocket types in :data:`READ_TYPES`; :class:`Target` refuses
anything else unless the target is disposable. It never prints the address, the
token or a value, and it prints the *names* of what differs only for objects
whose keys Home Assistant itself defines. :data:`OPEN_OBJECTS` are keyed by
whatever an integration chose, so only their counts are shown unless
``--open-names`` is passed. It reads ``HA_URL`` and ``HA_TOKEN`` from the
environment or from ``--env-file``, and always exits 0 when the comparison ran:
an installation has integrations the lab does not, so a difference is a report
and not a failure.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "tests" / "fixtures" / "ha-shape" / "capture.json"

NOTE = "names and JSON types only: no value from an installation is recorded here"

JSON_TYPES = ("array", "boolean", "integer", "null", "number", "object", "string")

#: Objects whose keys an integration chooses rather than Home Assistant core.
OPEN_OBJECTS = frozenset({"state.attributes"})

#: Every object a lab capture must produce. ``--write`` fails when one is
#: missing or empty: a capture that quietly lost an object would make the
#: contract test pass by having nothing to compare.
EXPECTED = (
    "history.first_row",
    "history.later_row",
    "logbook.row",
    "recorder.statistic_meta",
    "recorder.statistics_row",
    "registry.area",
    "registry.device",
    "registry.entity",
    "registry.entity.extended",
    "registry.floor",
    "rest.api_root",
    "rest.config",
    "rest.error",
    "service",
    "service.call",
    "service.domain",
    "service.field",
    "service.field.filter",
    "service.field.selector",
    "service.field.selector.select",
    "service.response",
    "service.target",
    "service.target.entity",
    "state",
    "state.attributes",
    "state.context",
    "websocket.auth_invalid",
    "websocket.auth_ok",
    "websocket.auth_required",
    "websocket.entity_update",
    "websocket.error",
    "websocket.error.error",
    "websocket.event",
    "websocket.event.event",
    "websocket.event.state_changed",
    "websocket.pong",
    "websocket.result",
)

#: The WebSocket types a target that is not disposable may be sent. The last
#: one is no command at all: it is how an error frame is asked for.
UNKNOWN_COMMAND = "shape_capture/no_such_command"
READ_TYPES = frozenset(
    {
        "config/area_registry/list",
        "config/device_registry/list",
        "config/entity_registry/get",
        "config/entity_registry/list",
        "config/floor_registry/list",
        "ping",
        "recorder/get_statistics_metadata",
        "recorder/list_statistic_ids",
        "recorder/statistics_during_period",
        "subscribe_events",
        "unsubscribe_events",
        UNKNOWN_COMMAND,
    }
)

#: An entity id no installation has, for the one read that is meant to miss.
ABSENT_ENTITY = "shape_capture.absent"

#: Services that answer with data, tried in order with the first entity of the
#: domain. One is enough: the object recorded is the envelope around the answer.
RESPONSE_PROBES = (
    ("weather", "get_forecasts", {"type": "daily"}),
    ("calendar", "get_events", {"duration": {"hours": 24}}),
)

SAMPLE_ENTITIES = 60
WINDOW = datetime.timedelta(hours=6)
STATISTICS_WINDOW = datetime.timedelta(days=3)
SETTLE_TIMEOUT = 90

_NAME = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
_HEX = re.compile(r"[0-9a-f]{8,}\Z")
_VERSION = re.compile(r"\d{4}\.\d{1,2}\.\d+[0-9a-z.]*\Z")


def is_name(key) -> bool:
    """Whether ``key`` reads as a field name rather than as a value.

    Lower snake case, short, and with no segment that is an identifier: a run
    of eight or more hexadecimal digits holding a digit is how Home Assistant
    writes a registry id, and four or more digits alone is a number.
    """
    if not isinstance(key, str) or not _NAME.match(key):
        return False
    for segment in key.split("_"):
        if _HEX.match(segment) and any(c.isdigit() for c in segment):
            return False
        if segment.isdigit() and len(segment) > 3:
            return False
    return True


def json_type(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


class Refused(Exception):
    """A write was asked of a target that may only be read."""


class Unreadable(Exception):
    """A step that the capture cannot do without did not answer as expected."""


class Shapes:
    """The objects seen so far: per object, each key and the types it held."""

    def __init__(self) -> None:
        self.objects: dict = {}
        self.rejected: dict = {}

    def add(self, name: str, rows) -> None:
        """Fold the keys of every mapping in ``rows`` into the object ``name``."""
        for row in rows or ():
            if not isinstance(row, dict):
                continue
            found = self.objects.setdefault(name, {})
            for key, value in row.items():
                if not is_name(key):
                    self.rejected[name] = self.rejected.get(name, 0) + 1
                    continue
                found.setdefault(key, set()).add(json_type(value))
                if isinstance(value, list):
                    # The element types, under the key with `[]` appended.
                    for item in value:
                        found.setdefault(f"{key}[]", set()).add(json_type(item))

    def document(self) -> dict:
        return {
            name: {key: sorted(types) for key, types in sorted(keys.items())}
            for name, keys in sorted(self.objects.items())
            if keys
        }


class Socket:
    """One authenticated WebSocket connection, sending numbered commands."""

    def __init__(self, target: Target, connection) -> None:
        self.target = target
        self.connection = connection
        self.sent = 0
        self.events: list = []
        self.greeting: dict = {}
        self.welcome: dict = {}

    def receive(self, timeout=30):
        return json.loads(self.connection.recv(timeout=timeout))

    def frame(self, type_: str, **payload) -> dict:
        """Send one command and return its own answer, whatever it says."""
        if not self.target.disposable and type_ not in READ_TYPES:
            raise Refused(f"{type_} is not a read, and this target may only be read")
        self.sent += 1
        self.connection.send(json.dumps({"id": self.sent, "type": type_, **payload}))
        while True:
            frame = self.receive()
            if frame.get("type") == "event":
                self.events.append(frame)
                continue
            if frame.get("id") == self.sent:
                return frame

    def result(self, type_: str, **payload):
        """Send one command and return its result, or ``None`` if it was refused."""
        frame = self.frame(type_, **payload)
        return frame.get("result") if frame.get("success") else None

    def must(self, type_: str, **payload):
        frame = self.frame(type_, **payload)
        if not frame.get("success"):
            code = (frame.get("error") or {}).get("code")
            raise Unreadable(f"{type_} was refused with {code}")
        return frame.get("result")

    def event(self, timeout: float):
        """The next event frame, or ``None`` when none arrives in ``timeout`` seconds."""
        deadline = time.monotonic() + timeout
        while not self.events:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                frame = self.receive(timeout=remaining)
            except TimeoutError:
                return None
            if frame.get("type") == "event":
                self.events.append(frame)
        return self.events.pop(0)


class Target:
    """One Home Assistant, and whether it is one that may be written to."""

    def __init__(self, url: str, token: str, *, disposable: bool) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.disposable = disposable

    def _request(self, method: str, path: str, query=None, body=None):
        """``(status, parsed body)``; the body is ``None`` when it is not JSON."""
        if method != "GET" and not self.disposable:
            raise Refused(f"{method} is not a read, and this target may only be read")
        text = urllib.parse.urlencode({k: v for k, v in (query or {}).items() if v is not None})
        headers = {"Authorization": f"Bearer {self.token}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.url + path + (f"?{text}" if text else ""),
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, None

    def get(self, path: str, **query):
        status, answer = self._request("GET", path, query)
        return answer if status == 200 else None

    def get_error(self, path: str):
        status, answer = self._request("GET", path)
        return answer if status >= 400 else None

    def post(self, path: str, body: dict, **query):
        status, answer = self._request("POST", path, query, body)
        return answer if status == 200 else None

    def connect(self):
        """A connection to the WebSocket API, to be entered; nothing has been sent on it."""
        from websockets.sync.client import connect

        return connect(
            self.url.replace("http", "ws", 1) + "/api/websocket", open_timeout=20, max_size=None
        )

    def handshake(self, connection, token: str) -> tuple:
        greeting = json.loads(connection.recv(timeout=30))
        connection.send(json.dumps({"type": "auth", "access_token": token}))
        return greeting, json.loads(connection.recv(timeout=30))


def _open(target: Target, connection) -> Socket:
    socket = Socket(target, connection)
    socket.greeting, socket.welcome = target.handshake(connection, target.token)
    if socket.welcome.get("type") != "auth_ok":
        raise Unreadable("the WebSocket API did not accept the token")
    return socket


def _instant(moment: datetime.datetime) -> str:
    return moment.isoformat()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _entity_ids(states, domain: str) -> list:
    prefix = f"{domain}."
    return [
        s["entity_id"]
        for s in states or ()
        if isinstance(s, dict) and str(s.get("entity_id", "")).startswith(prefix)
    ]


def _settle(describe: str, probe, timeout: float = SETTLE_TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        answer = probe()
        if answer:
            return answer
        time.sleep(2)
    raise Unreadable(f"the lab did not reach '{describe}' in {timeout:g}s")


# ------------------------------------------------------------------------- seeding


def seed(target: Target) -> None:
    """Put a disposable target into the states a fresh one does not have.

    Every name written here is the repository's synthetic vocabulary, and none
    of it is recorded: what the capture keeps is that an area *can* carry a
    floor, not which one.
    """
    if not target.disposable:
        raise Refused("only a disposable target is seeded")
    states = target.get("/api/states")
    lights = _entity_ids(states, "light")
    if not lights:
        raise Unreadable("the lab has no light to act on")

    with target.connect() as connection:
        socket = _open(target, connection)
        floor = socket.must(
            "config/floor_registry/create",
            name="Example Floor",
            level=1,
            icon="mdi:home-floor-1",
            aliases=["Example Level"],
        )
        # A second floor and an area with nothing set beyond a name: what
        # either carries when nobody chose an icon or a level for it.
        socket.must("config/floor_registry/create", name="Example Annex")
        socket.must("config/area_registry/create", name="Example Room")
        areas = socket.must("config/area_registry/list")
        area_id = areas[0]["area_id"]
        socket.must(
            "config/area_registry/update",
            area_id=area_id,
            floor_id=floor["floor_id"],
            aliases=["Example Alias"],
        )
        entries = socket.must("config/entity_registry/list")
        known = {entry["entity_id"]: entry for entry in entries}
        placed = [entity_id for entity_id in lights if entity_id in known]
        if len(placed) < 2:
            raise Unreadable("the lab has fewer than two lights in its entity registry")
        socket.must(
            "config/entity_registry/update",
            entity_id=placed[0],
            name="Example Lamp",
            icon="mdi:lamp",
            area_id=area_id,
            aliases=["Example Light"],
        )
        socket.must("config/entity_registry/update", entity_id=placed[1], hidden_by="user")
        device_id = next((e["device_id"] for e in entries if e.get("device_id")), None)
        if device_id:
            socket.must(
                "config/device_registry/update",
                device_id=device_id,
                name_by_user="Example Device",
                area_id=area_id,
            )

        hour = _now().replace(minute=0, second=0, microsecond=0)
        hours = [hour - datetime.timedelta(hours=n) for n in range(72, 0, -1)]
        socket.must(
            "recorder/import_statistics",
            metadata={
                "has_mean": False,
                "mean_type": 0,
                "has_sum": True,
                "name": "Example Meter",
                "source": "example",
                "statistic_id": "example:meter",
                "unit_class": "energy",
                "unit_of_measurement": "kWh",
            },
            stats=[
                {"start": _instant(at), "state": float(n), "sum": float(n), "last_reset": None}
                for n, at in enumerate(hours)
            ],
        )
        socket.must(
            "recorder/import_statistics",
            metadata={
                "has_mean": True,
                "mean_type": 1,
                "has_sum": False,
                "name": "Example Reading",
                "source": "example",
                "statistic_id": "example:reading",
                "unit_class": "temperature",
                "unit_of_measurement": "°C",
            },
            stats=[{"start": _instant(at), "mean": 20.5, "min": 19.0, "max": 22.0} for at in hours],
        )

        # The lab's own script toggles a helper, and its automation answers
        # that with a second: a change a service call caused, and one an
        # automation did. The light is switched by this caller directly.
        if not _entity_ids(states, "script"):
            raise Unreadable("the lab has no script to run")
        target.post("/api/services/script/example_script", {"example_field": "example"})
        target.post("/api/services/light/toggle", {"entity_id": placed[1]})

        window = {"start_time": _instant(hours[0]), "end_time": _instant(_now()), "period": "hour"}
        _settle(
            "imported statistics",
            lambda: all(
                (socket.must("recorder/statistics_during_period", statistic_ids=[sid], **window))
                for sid in ("example:meter", "example:reading")
            ),
        )

    def caused():
        rows = _logbook(target) or []
        seen = {key for row in rows if isinstance(row, dict) for key in row}
        return {"context_user_id", "context_service", "context_entity_id", "context_name"} <= seen

    _settle("a logbook entry with a cause", caused)


# ------------------------------------------------------------------------- reading


def _logbook(target: Target):
    start = _now() - WINDOW
    return target.get(
        f"/api/logbook/{urllib.parse.quote(_instant(start), safe='')}", end_time=_instant(_now())
    )


def _read_rest(target: Target, shapes: Shapes) -> list:
    root = target.get("/api/")
    if not isinstance(root, dict) or "message" not in root:
        raise Unreadable("the target did not answer as a Home Assistant API")
    shapes.add("rest.api_root", [root])
    shapes.add("rest.config", [target.get("/api/config")])
    shapes.add("rest.error", [target.get_error(f"/api/states/{ABSENT_ENTITY}")])

    states = target.get("/api/states")
    if not isinstance(states, list):
        raise Unreadable("the states answer was not a list")
    shapes.add("state", states)
    shapes.add("state.context", [s.get("context") for s in states if isinstance(s, dict)])
    shapes.add("state.attributes", [s.get("attributes") for s in states if isinstance(s, dict)])

    _read_services(target.get("/api/services"), shapes)

    # History and the logbook, asked for the way the tool asks: an explicit
    # end, and `minimal_response`, under which only the first row of each
    # entity's list is a whole state.
    sample = [s["entity_id"] for s in states if isinstance(s, dict) and "entity_id" in s]
    start = _now() - WINDOW
    history = target.get(
        f"/api/history/period/{urllib.parse.quote(_instant(start), safe='')}",
        filter_entity_id=",".join(sample[:SAMPLE_ENTITIES]),
        end_time=_instant(_now()),
        minimal_response="",
    )
    timelines = [t for t in history or () if isinstance(t, list) and t]
    shapes.add("history.first_row", [t[0] for t in timelines])
    shapes.add("history.later_row", [row for t in timelines for row in t[1:]])
    shapes.add("logbook.row", _logbook(target))
    return states


def _read_services(listing, shapes: Shapes) -> None:
    """A service definition and what hangs off it, never the names it is filed under."""
    domains = [d for d in listing or () if isinstance(d, dict)]
    shapes.add("service.domain", domains)
    specs = [
        spec
        for domain in domains
        for spec in (domain.get("services") or {}).values()
        if isinstance(spec, dict)
    ]
    shapes.add("service", specs)
    fields: list = []
    pending = [spec.get("fields") for spec in specs]
    while pending:
        declared = pending.pop()
        if not isinstance(declared, dict):
            continue
        for field in declared.values():
            if isinstance(field, dict):
                fields.append(field)
                # A section is a field that holds fields.
                pending.append(field.get("fields"))
    shapes.add("service.field", fields)
    shapes.add("service.field.filter", [f.get("filter") for f in fields])
    selectors = [f.get("selector") for f in fields if isinstance(f.get("selector"), dict)]
    shapes.add("service.field.selector", selectors)
    selects = [s.get("select") for s in selectors if isinstance(s.get("select"), dict)]
    shapes.add("service.field.selector.select", selects)
    # An option is a plain string or a mapping; only the mappings have keys.
    shapes.add(
        "service.field.selector.select.option",
        [option for select in selects for option in select.get("options") or ()],
    )
    targets = [s.get("target") for s in specs if isinstance(s.get("target"), dict)]
    shapes.add("service.target", targets)
    shapes.add(
        "service.target.entity",
        [entry for t in targets for entry in t.get("entity") or ()],
    )
    shapes.add("service.response", [s.get("response") for s in specs])


def _of_type(frames, type_: str) -> list:
    """The frames that are what they were asked for, so an answer is never filed as another."""
    return [f for f in frames if isinstance(f, dict) and f.get("type") == type_]


def _read_websocket(target: Target, shapes: Shapes, states: list) -> str:
    with target.connect() as connection:
        socket = _open(target, connection)
        shapes.add("websocket.auth_required", [socket.greeting])
        shapes.add("websocket.auth_ok", [socket.welcome])
        version = str(socket.welcome.get("ha_version", ""))

        entities = socket.frame("config/entity_registry/list")
        shapes.add("websocket.result", _of_type([entities], "result"))
        entries = entities.get("result") or []
        shapes.add("registry.entity", entries)
        ids = [e["entity_id"] for e in entries if isinstance(e, dict) and "entity_id" in e]
        shapes.add(
            "registry.entity.extended",
            [
                socket.result("config/entity_registry/get", entity_id=entity_id)
                for entity_id in ids[:SAMPLE_ENTITIES]
            ],
        )
        shapes.add("registry.device", socket.result("config/device_registry/list"))
        shapes.add("registry.area", socket.result("config/area_registry/list"))
        shapes.add("registry.floor", socket.result("config/floor_registry/list"))

        metadata = socket.result("recorder/list_statistic_ids") or []
        shapes.add("recorder.statistic_meta", metadata)
        statistic_ids = [m["statistic_id"] for m in metadata if isinstance(m, dict)]
        shapes.add(
            "recorder.statistic_meta",
            socket.result("recorder/get_statistics_metadata", statistic_ids=statistic_ids),
        )
        window = {
            "start_time": _instant(_now() - STATISTICS_WINDOW),
            "end_time": _instant(_now()),
        }
        for period in ("5minute", "hour", "day"):
            buckets = socket.result(
                "recorder/statistics_during_period",
                statistic_ids=statistic_ids,
                period=period,
                **window,
            )
            if isinstance(buckets, dict):
                shapes.add(
                    "recorder.statistics_row", [row for rows in buckets.values() for row in rows]
                )

        refusal = socket.frame(UNKNOWN_COMMAND)
        if not refusal.get("success"):
            shapes.add("websocket.error", _of_type([refusal], "result"))
            shapes.add("websocket.error.error", [refusal.get("error")])
        shapes.add("websocket.pong", _of_type([socket.frame("ping")], "pong"))

        _read_event(target, socket, shapes, states)
        if target.disposable and ids:
            current = socket.result("config/entity_registry/get", entity_id=ids[0]) or {}
            updated = socket.result(
                "config/entity_registry/update", entity_id=ids[0], name=current.get("name")
            )
            shapes.add("websocket.entity_update", [updated])
            shapes.add("registry.entity.extended", [(updated or {}).get("entity_entry")])

    if target.disposable:
        # A credential that is refused, which only a server that may be
        # damaged can be sent: a real one raises a notification and can ban
        # the address that sent it.
        with target.connect() as connection:
            _, verdict = target.handshake(connection, "shape-capture-not-a-token")
        shapes.add("websocket.auth_invalid", _of_type([verdict], "auth_invalid"))
    return version


def _read_event(target: Target, socket: Socket, shapes: Shapes, states: list) -> None:
    """One `state_changed` event: caused on a disposable target, awaited on any other."""
    subscription = socket.frame("subscribe_events", event_type="state_changed")
    if not subscription.get("success"):
        return
    lights = _entity_ids(states, "light")
    if target.disposable and lights:
        target.post("/api/services/light/toggle", {"entity_id": lights[0]})
    frame = socket.event(timeout=15)
    socket.frame("unsubscribe_events", subscription=subscription["id"])
    if frame is None:
        return
    event = frame.get("event") if isinstance(frame.get("event"), dict) else {}
    shapes.add("websocket.event", [frame])
    shapes.add("websocket.event.event", [event])
    shapes.add("websocket.event.state_changed", [event.get("data")])


def _read_service_call(target: Target, shapes: Shapes, states: list) -> None:
    """The envelope a service answers in when it is asked for its response."""
    for domain, service, data in RESPONSE_PROBES:
        entities = _entity_ids(states, domain)
        if not entities:
            continue
        answer = target.post(
            f"/api/services/{domain}/{service}",
            {"entity_id": entities[0], **data},
            return_response="",
        )
        if isinstance(answer, dict):
            shapes.add("service.call", [answer])
            return


def read(target: Target) -> tuple:
    """Everything this script records about ``target``: ``(shapes, version)``.

    A target that is not disposable is only read. A disposable one is also
    sent a refused credential, one write of a value back to itself, and two
    service calls, because four of the objects exist only as answers to those.
    """
    shapes = Shapes()
    states = _read_rest(target, shapes)
    version = _read_websocket(target, shapes, states)
    if target.disposable:
        _read_service_call(target, shapes, states)
    return shapes, version


def document(shapes: Shapes, version: str, image: str = "") -> dict:
    captured = {
        "date": datetime.date.today().isoformat(),
        "home_assistant": version if _VERSION.match(version) else "",
        "note": NOTE,
    }
    if image:
        captured["image"] = image
    return {"captured": captured, "objects": shapes.document()}


def render(doc: dict) -> str:
    """The capture as JSON, with each key's types on its own line."""
    text = json.dumps(doc, indent=2, sort_keys=True)
    text = re.sub(
        r"\[\s+((?:\"[a-z]+\",?\s+)+)\]",
        lambda found: "[" + " ".join(found.group(1).split()) + "]",
        text,
    )
    return text + "\n"


# ----------------------------------------------------------------------- comparing


def differences(committed: dict, fresh: dict) -> list:
    """One entry per object that differs: ``(object, added, removed, retyped)``.

    ``added`` and ``removed`` are key names; ``retyped`` maps a key both hold
    to the types only the fresh capture saw and the types only the committed
    one did.
    """
    found = []
    for name in sorted(set(committed) | set(fresh)):
        old, new = committed.get(name, {}), fresh.get(name, {})
        added = sorted(set(new) - set(old))
        removed = sorted(set(old) - set(new))
        retyped = {
            key: (sorted(set(new[key]) - set(old[key])), sorted(set(old[key]) - set(new[key])))
            for key in sorted(set(old) & set(new))
            if set(old[key]) != set(new[key])
        }
        if added or removed or retyped:
            found.append((name, added, removed, retyped))
    return found


def _retyped(retyped: dict) -> str:
    parts = []
    for key, (gained, lost) in retyped.items():
        change = [f"+{t}" for t in gained] + [f"-{t}" for t in lost]
        parts.append(f"{key} ({' '.join(change)})")
    return ", ".join(parts)


def report(found: list, *, open_names: bool, out) -> None:
    """Print ``found`` as counts and key names; an open object's names only on request."""
    for name, added, removed, retyped in found:
        withheld = name in OPEN_OBJECTS and not open_names
        print(f"  {name}", file=out)
        if added:
            shown = "names withheld, see --open-names" if withheld else ", ".join(added)
            print(f"    {len(added)} not in the committed capture: {shown}", file=out)
        if removed:
            print(
                f"    {len(removed)} only in the committed capture: {', '.join(removed)}", file=out
            )
        if retyped:
            shown = "names withheld, see --open-names" if withheld else _retyped(retyped)
            print(f"    {len(retyped)} with other types: {shown}", file=out)


# ---------------------------------------------------------------------------- main


def _env_file(path: str) -> dict:
    """``KEY=VALUE`` lines from ``path``; nothing is expanded and nothing is run."""
    found = {}
    for line in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        found[key.strip()] = value
    return found


def _house(env_file) -> Target:
    env = dict(os.environ)
    if env_file:
        env.update(_env_file(env_file))
    raw = env.get("HA_URL") or env.get("HASS_SERVER") or ""
    token = (env.get("HA_TOKEN") or env.get("HASS_TOKEN") or "").strip()
    if not raw or not token:
        raise Unreadable("--house needs HA_URL and HA_TOKEN, in the environment or --env-file")
    for candidate in (part.strip() for part in raw.split(",")):
        if not candidate:
            continue
        if "://" not in candidate:
            candidate = f"https://{candidate}"
        target = Target(candidate, token, disposable=False)
        try:
            if isinstance(target.get("/api/"), dict):
                return target
        except (urllib.error.URLError, OSError):
            continue
    raise Unreadable("no candidate in HA_URL answered as a Home Assistant API")


def _lab():
    sys.path.insert(0, str(ROOT))
    from tests.live.lab import container

    image = os.environ.get("HASS_AXI_LAB_IMAGE") or container.DEFAULT_IMAGE
    return container, container.start(image), image


def capture_lab() -> dict:
    """Start a lab, seed it, read it, and remove it whatever happened."""
    container, running, image = _lab()
    try:
        target = Target(running.url, running.token, disposable=True)
        seed(target)
        shapes, version = read(target)
    finally:
        container.remove(running)
    if shapes.rejected:
        counts = ", ".join(f"{name}: {count}" for name, count in sorted(shapes.rejected.items()))
        raise Unreadable(f"the lab sent keys that do not read as names ({counts})")
    doc = document(shapes, version, image)
    missing = [name for name in EXPECTED if not doc["objects"].get(name)]
    if missing:
        raise Unreadable(f"the lab capture is missing {', '.join(missing)}")
    return doc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help="refresh the committed capture")
    group.add_argument("--check", action="store_true", help="compare with the committed capture")
    parser.add_argument(
        "--house",
        action="store_true",
        help="with --check: read the installation in HA_URL instead of the lab, read-only",
    )
    parser.add_argument("--env-file", help="with --house: read HA_URL and HA_TOKEN from this file")
    parser.add_argument(
        "--open-names",
        action="store_true",
        help="with --house: print the names in objects an integration keys, not only the count",
    )
    args = parser.parse_args(argv)
    if args.house and not args.check:
        parser.error("--house is a comparison: it goes with --check, and is never written")
    if (args.env_file or args.open_names) and not args.house:
        parser.error("--env-file and --open-names go with --house")

    try:
        if args.house:
            return _check_house(args)
        doc = capture_lab()
    except (Unreadable, Refused) as exc:
        print(f"shape-capture: {exc}", file=sys.stderr)
        return 2
    except ImportError as exc:
        print(
            f"shape-capture: {exc.name} is missing; run this with .venv/bin/python", file=sys.stderr
        )
        return 2

    if args.write:
        CAPTURE.parent.mkdir(parents=True, exist_ok=True)
        CAPTURE.write_text(render(doc), encoding="utf-8")
        print(f"shape-capture: wrote {CAPTURE.relative_to(ROOT)}")
        return 0
    if args.check:
        committed = json.loads(CAPTURE.read_text(encoding="utf-8"))
        found = differences(committed["objects"], doc["objects"])
        if not found:
            print("shape-capture: the lab still matches the committed capture")
            return 0
        print("shape-capture: the lab has drifted from the committed capture", file=sys.stderr)
        report(found, open_names=True, out=sys.stderr)
        return 1
    sys.stdout.write(render(doc))
    return 0


def _check_house(args) -> int:
    """Compare an installation with the committed capture. It is read, and never named."""
    committed = json.loads(CAPTURE.read_text(encoding="utf-8"))
    target = _house(args.env_file)
    try:
        shapes, version = read(target)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # The message may carry the address, so only the kind of failure is shown.
        raise Unreadable(f"the installation could not be read ({type(exc).__name__})") from None
    fresh = shapes.document()
    lab_version = committed["captured"].get("home_assistant", "")
    shown = version if _VERSION.match(version) else "an unreadable version"
    print(f"shape-capture: an installation on {shown}, against the lab capture of {lab_version}")
    absent = sorted(set(committed["objects"]) - set(fresh))
    if absent:
        print(f"  {len(absent)} objects not read from it: {', '.join(absent)}")
    found = [entry for entry in differences(committed["objects"], fresh) if entry[0] in fresh]
    report(found, open_names=args.open_names, out=sys.stdout)
    for name, count in sorted(shapes.rejected.items()):
        print(f"  {name}: {count} keys that do not read as names, not shown")
    if not found:
        print("  no object it sends differs from the lab capture")
    return 0


if __name__ == "__main__":
    sys.exit(main())

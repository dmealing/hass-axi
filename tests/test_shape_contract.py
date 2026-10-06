"""The readers and the doubles are held to a capture of a real server: names and types.

``tests/fixtures/ha-shape/capture.json`` is what a real Home Assistant sent,
reduced by ``scripts/shapecapture.py`` to the keys each object carries and the
JSON type of each. It was read from the pinned lab container, so it holds no
value and nobody's house. This file asks three questions of it, offline:

- **Is every key the tool reads one a real server sends?** The readers are found
  by scanning the source, not by a list somebody keeps: every expression a
  string key is read off is classified in :data:`READERS` as an upstream object
  or in :data:`OWN` as the tool's own structure, and one that is in neither
  fails. A key read off an upstream object has to be in the capture.
- **Does every key the doubles send exist on a real server?** The script that
  read the lab reads the doubles, and the two answers are compared.
- **Is the capture still only names?** A string that looks like a value fails.

**What a failure means.** A name is read or emitted that the capture does not
have. Either nobody sends it -- remove it, and fix whatever depended on it --
or a server sends it only in a state the lab was not in, and then the model
declares it *with the reason it could not be observed*, in an ``unobserved`` bag
beside the key in ``metaobjects/``. :data:`UNOBSERVED` is those reasons, read back.
A reason is required, and an entry the capture has since caught up with fails too.

Whether the capture still matches a server is a different question, asked by
``scripts/shapecapture.py --check``.

**The model.** What Home Assistant answers is declared in ``metaobjects/``, and three
things are generated from it: the column vocabularies in :mod:`hass_axi.model.rows`,
the builders in ``tests/hamodel/`` that the doubles make their answers through, and
the check beside them that every declared key is in the capture. The last section
here holds the first two to what the commands print and to what a builder refuses.
The scan of the readers stays until the readers are generated too: it is what holds
a key read with ``.get`` to the capture.
"""

from __future__ import annotations

import ast
import copy
import datetime
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

import conftest
from conftest import FAKE_TOKEN, RECORDER_DAY, RECORDER_NOW, synthetic_jwt
from hamodel import capture_contract as contract
from hamodel import elements
from hass_axi import commands, ws
from hass_axi.commands import _window
from hass_axi.model import rows as vocabulary

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "hass_axi"
sys.path.insert(0, str(ROOT / "scripts"))

import shapecapture  # noqa: E402

CAPTURE_PATH = ROOT / "tests" / "fixtures" / "ha-shape" / "capture.json"
CAPTURE = json.loads(CAPTURE_PATH.read_text(encoding="utf-8"))
OBJECTS = CAPTURE["objects"]

#: The one object the readers read that the model does not declare, because no capture
#: has one to hold a declaration to: object -> name -> why.
NOT_DECLARED = {
    "service.field.selector.select.option": {
        "value": (
            "an option written as a label and a value; every select in the lab lists "
            "its options as plain strings"
        ),
    },
}


def _unobserved() -> dict:
    """Names the tool reads, or the doubles send, that the lab could not show.

    ``captured object -> name -> why``. The reasons are the model's own, kept beside the
    key each one excuses in ``metaobjects/``: the lab runs the demo integration and what
    ``tests/live/lab/container.py`` declares, so anything only another integration
    publishes was not there to read.
    """
    found = {name: dict(entries) for name, entries in NOT_DECLARED.items()}
    for entry in contract.DECLARED.values():
        for name in entry["capture"]:
            if entry["unobserved"]:
                found.setdefault(name, {}).update(entry["unobserved"])
    return found


UNOBSERVED = _unobserved()

#: Types the doubles send for a key the capture has, where the lab showed the
#: key with other types only: (object, key, type) -> why.
UNOBSERVED_TYPES = {
    ("service.call", "changed_states[]", "object"): (
        "the list holds the states a call changed, and the lab's one service that "
        "answers with data changes none, so the list it sent was empty"
    ),
    ("service.field.selector", "datetime", "null"): (
        "a selector declared with no configuration is published as null, as the "
        "capture shows for `boolean` and `text`; the lab's datetime selectors all carry one"
    ),
}

#: Objects in the capture that the doubles have no answer for, and why.
NOT_MODELLED = {
    "websocket.event": "the tool subscribes to nothing, so the double never pushes a frame",
    "websocket.event.event": "the tool subscribes to nothing, so the double never pushes a frame",
    "websocket.event.state_changed": (
        "the tool subscribes to nothing, so the double never pushes a frame"
    ),
    "websocket.pong": "the tool sends no ping, so the double has no answer to one",
}

#: Modules that never hold an answer from Home Assistant. A new module has to
#: be put here or have its readers classified below.
LOCAL = {
    "argspec.py",
    "cli.py",
    "config.py",
    "entry.py",
    "errors.py",
    "hooks.py",
    "model/rows.py",
    "output.py",
    "readonly.py",
    "sessionlog.py",
    "skill.py",
    "commands/_window.py",
    "commands/api.py",
    "commands/context.py",
    "commands/setup.py",
    "commands/template.py",
    "commands/wscmd.py",
    "toolkit/names.py",
}

#: The parsed command line, which every command module reads by this name.
ARGUMENTS = "parsed"

AREA = "registry.area"
DEVICE = "registry.device"
ENTITY = ("registry.entity", "registry.entity.extended")
FLOOR = "registry.floor"
META = "recorder.statistic_meta"
ROW = "recorder.statistics_row"
ATTRIBUTES = "state.attributes"
FRAMES = (
    "websocket.auth_invalid",
    "websocket.auth_ok",
    "websocket.auth_required",
    "websocket.error",
    "websocket.result",
)

#: Every expression an upstream answer is read off: module -> expression ->
#: the object it holds, or the objects where one name holds several in turn.
READERS = {
    "commands/_common.py": {
        "a": AREA,
        "area": AREA,
        "attributes": ATTRIBUTES,
        "d": DEVICE,
        "device": DEVICE,
        "entry": ENTITY,
        "f": FLOOR,
        "floor": FLOOR,
        "state": "state",
    },
    "commands/area.py": {
        "a": AREA,
        "area": AREA,
        "d": DEVICE,
        "device": DEVICE,
        "existing": AREA,
        "resolve_floor(floors, raw)": FLOOR,
        "result": AREA,
    },
    "commands/device.py": {
        "current": DEVICE,
        "device": DEVICE,
        "entry": (*ENTITY, DEVICE),
        "resolve_area(areas, area_arg)": AREA,
        "result": DEVICE,
    },
    "commands/doctor.py": {"health": "rest.api_root", "info": "rest.config"},
    "commands/entity.py": {
        "current": ENTITY,
        "device": DEVICE,
        "entry": ENTITY,
        "resolve_area(areas, area_arg)": AREA,
        "result": "websocket.entity_update",
    },
    "commands/history.py": {
        "first": "history.first_row",
        "first.get('attributes') or {}": ATTRIBUTES,
        "row": ("history.first_row", "history.later_row"),
    },
    "commands/home.py": {"attributes": ATTRIBUTES, "s": "state", "state": "state"},
    "commands/logbook.py": {"entry": "logbook.row"},
    "commands/ping.py": {"info": "rest.config"},
    "commands/sensor.py": {
        "attributes": ATTRIBUTES,
        "entry": ENTITY,
        "entry or {}": ENTITY,
        "s": "state",
        "state": "state",
    },
    "commands/service.py": {
        "area": AREA,
        "device": DEVICE,
        "entry": (*ENTITY, "service.domain", "service.target.entity"),
        "field": "service.field",
        "match": "service.domain",
        "resolve_area_target(areas, value)": AREA,
        "result": "service.call",
        "s": "state",
        "spec": "service",
        "spec or {}": "service",
        "state": "state",
        "target": "service.target",
    },
    "commands/state.py": {"entry": ENTITY, "state": "state"},
    "commands/statistics.py": {
        "known[sid]": META,
        "m": META,
        "meta": META,
        "s": "state",
        "s.get('attributes') or {}": ATTRIBUTES,
    },
    "rest.py": {"json.loads(raw)": "rest.error"},
    "toolkit/recorder.py": {
        "meta": META,
        "r": ROW,
        "row": ROW,
        "rows[-1]": ROW,
        "rows[0]": ROW,
    },
    "toolkit/shapes.py": {"value": "rest.api_root"},
    "ws.py": {"error": "websocket.error.error", "message": FRAMES},
}

#: Expressions in those modules that hold the tool's own structures: a row it
#: built, a document it is about to print, a message it composed.
OWN = {
    "commands/_common.py": {"item", "row"},
    "commands/area.py": {"row"},
    "commands/device.py": {"row"},
    "commands/doctor.py": {"env"},
    "commands/entity.py": {"row"},
    "commands/history.py": {"e", "summary"},
    "commands/home.py": {"row"},
    "commands/logbook.py": {"r"},
    "commands/sensor.py": {"r", "row"},
    "commands/service.py": {"detail", "doc", "row", "rows[0]"},
    "commands/state.py": {"row"},
    "commands/statistics.py": {"r", "row"},
}

#: The service reader the tool imports from the shared package, classified the
#: same way. It reads nothing of its own, so there is no second table.
SERVICE_READERS = {
    "config": "service.field.selector.select",
    "entry": ("service.domain", "service.target.entity"),
    "entry or {}": "service.domain",
    "field": "service.field",
    "o": "service.field.selector.select.option",
    "response": "service.response",
    "selector": "service.field.selector",
    "spec": "service",
    "state": "state",
    "state.get('attributes') or {}": ATTRIBUTES,
    "target": "service.target",
}

_KEY = re.compile(r"[a-z_][a-z0-9_]*\Z")


def _objects(named) -> tuple:
    return (named,) if isinstance(named, str) else tuple(named)


def _known(named) -> set:
    """Every name the capture, or a stated reason, allows on the objects ``named``."""
    found: set = set()
    for name in _objects(named):
        found |= set(OBJECTS.get(name, {})) | set(UNOBSERVED.get(name, {}))
    return found


def _is_elements(key: str) -> bool:
    """Whether ``key`` records what a list held, which is a type and not a name."""
    return key.endswith("[]")


def key_reads(path: Path) -> dict:
    """Every string key read in ``path``: the expression it is read off -> the keys.

    A subscript, a ``.get`` or a ``.pop`` with a literal key, and a literal on
    the left of ``in``. A key that is not shaped like one -- the ``"."`` in
    ``"." in entity_id`` -- is a substring test and is left out.
    """
    found: dict = {}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        holder = key = None
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("get", "pop")
            and node.args
        ):
            holder, key = node.func.value, node.args[0]
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            holder, key = node.value, node.slice
        elif (
            isinstance(node, ast.Compare)
            and len(node.ops) == 1
            and isinstance(node.ops[0], (ast.In, ast.NotIn))
        ):
            holder, key = node.comparators[0], node.left
        if (
            holder is not None
            and isinstance(key, ast.Constant)
            and isinstance(key.value, str)
            and _KEY.match(key.value)
        ):
            found.setdefault(ast.unparse(holder), set()).add(key.value)
    return found


def _modules() -> dict:
    return {
        str(path.relative_to(SOURCE)): path
        for path in sorted(SOURCE.rglob("*.py"))
        if path.name not in ("__init__.py", "__main__.py")
    }


def _scanned() -> dict:
    """``module -> expression -> keys`` for every module that may hold an answer."""
    found = {}
    for name, path in _modules().items():
        if name in LOCAL:
            continue
        reads = key_reads(path)
        if name.startswith("commands/"):
            for own in (ARGUMENTS, "vocabulary.FIELDS", "vocabulary.DEFAULT"):
                reads.pop(own, None)
        found[name] = reads
    return found


# --------------------------------------------------------------- the capture itself

_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_RELEASE = re.compile(r"\d{4}\.\d{1,2}\.\d+\Z")
_IMAGE = re.compile(r"ghcr\.io/home-assistant/home-assistant:\d{4}\.\d{1,2}\.\d+\Z")
#: Four digits together, or eight hexadecimal characters holding one: an id.
_IDENTIFIER = re.compile(r"[0-9]{4}|(?<![a-z0-9])(?=[a-f]*[0-9])[0-9a-f]{8,}(?![a-z0-9])")
_TYPES = {"array", "boolean", "integer", "null", "number", "object", "string"}


def _reads_as_a_name(text: str) -> bool:
    return bool(_NAME.match(text)) and len(text) <= 48 and not _IDENTIFIER.search(text)


def faults(doc) -> list:
    """Everything in ``doc`` that is not a name, a JSON type or the stated provenance.

    Written here rather than borrowed from the script: a check that asked the
    script whether the script's output was acceptable would agree with it.
    """
    found = []
    if set(doc) != {"captured", "objects"}:
        found.append(f"top-level keys {sorted(doc)}")
    captured = doc.get("captured", {})
    rules = {"date": _DATE, "home_assistant": _RELEASE, "image": _IMAGE}
    if set(captured) != {*rules, "note"}:
        found.append(f"provenance keys {sorted(captured)}")
    for key, rule in rules.items():
        if not isinstance(captured.get(key), str) or not rule.match(captured[key]):
            found.append(f"captured.{key} is not what it should be")
    if captured.get("note") != shapecapture.NOTE:
        found.append("captured.note is not the note")
    for name, keys in doc.get("objects", {}).items():
        if name not in shapecapture.EXPECTED:
            found.append("an object the script does not capture")
        for key, types in keys.items():
            base = key[:-2] if key.endswith("[]") else key
            if not _reads_as_a_name(base):
                found.append(f"{name}: a key that is not a name")
            if not isinstance(types, list) or not types or set(types) - _TYPES:
                found.append(f"{name}.{key}: something that is not a JSON type")
            elif types != sorted(set(types)):
                found.append(f"{name}.{key}: types out of order")
    return found


def _lab_image() -> str:
    """`DEFAULT_IMAGE` from the lab module, loaded by path: it is not on the import path."""
    spec = importlib.util.spec_from_file_location(
        "lab_container", ROOT / "tests" / "live" / "lab" / "container.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module.DEFAULT_IMAGE


def _value_looking() -> dict:
    """Strings a house would send as values. Built here, so none is a literal in this file."""
    return {
        "an entity id": "light.example_lamp",
        "a friendly name": "Example Lamp",
        "an area name": "Example Room",
        "an address": ".".join(["192", "168", "1", "20"]),
        "a base URL": "https://homeassistant.example.com",
        "a registry id": "a1b2c3d4e5f6" * 2 + "a1b2c3d4",
        "a context id": "01EXAMPLECONTEXT0000000001",
        "a token": synthetic_jwt(),
        "a statistic id": "example:meter",
        "a number": "20251231",
    }


def test_the_capture_holds_names_and_types_and_nothing_else():
    assert faults(CAPTURE) == []
    assert set(OBJECTS) == set(shapecapture.EXPECTED)


@pytest.mark.parametrize("kind", sorted(_value_looking()))
def test_a_value_looking_string_fails_wherever_it_is_put(kind):
    """As a key, as a type, as an object name and as provenance: each one is a fault."""
    value = _value_looking()[kind]
    as_key = copy.deepcopy(CAPTURE)
    as_key["objects"]["state"][value] = ["string"]
    as_type = copy.deepcopy(CAPTURE)
    as_type["objects"]["state"]["state"] = [value]
    as_object = copy.deepcopy(CAPTURE)
    as_object["objects"][value] = {"name": ["string"]}
    as_provenance = copy.deepcopy(CAPTURE)
    as_provenance["captured"]["source"] = value
    for tampered in (as_key, as_type, as_object, as_provenance):
        assert faults(tampered) != [], kind
    assert not shapecapture.is_name(value), kind


def test_the_script_counts_a_key_that_is_not_a_name_and_never_keeps_it():
    shapes = shapecapture.Shapes()
    row = dict.fromkeys(_value_looking().values(), 1)
    shapes.add("state", [{**row, "state": "on", "labels": ["example"]}])
    assert shapes.document() == {
        "state": {"labels": ["array"], "labels[]": ["string"], "state": ["string"]}
    }
    assert shapes.rejected == {"state": len(row)}


def test_every_name_in_the_capture_is_one_the_script_would_keep():
    for name, keys in OBJECTS.items():
        for key in keys:
            assert shapecapture.is_name(key[:-2] if key.endswith("[]") else key), (name, key)


def test_the_capture_is_of_the_image_the_lab_pins():
    """A lab image moved without a fresh capture is a capture of another release."""
    captured = CAPTURE["captured"]
    assert captured["image"] == _lab_image()
    assert captured["image"].endswith(":" + captured["home_assistant"])


def test_the_committed_file_is_what_the_script_writes():
    assert CAPTURE_PATH.read_text(encoding="utf-8") == shapecapture.render(CAPTURE)


# ------------------------------------------------------- what the script may be sent


def test_a_target_that_is_not_disposable_is_only_read():
    """Refused before anything is sent: the address here is one nothing listens on."""
    house = shapecapture.Target("http://127.0.0.1:9", FAKE_TOKEN, disposable=False)
    with pytest.raises(shapecapture.Refused):
        house.post("/api/services/light/turn_on", {})
    with pytest.raises(shapecapture.Refused):
        shapecapture.seed(house)
    socket = shapecapture.Socket(house, connection=None)
    for write in ("config/entity_registry/update", "call_service", "recorder/import_statistics"):
        with pytest.raises(shapecapture.Refused):
            socket.frame(write)
    assert socket.sent == 0


def test_every_type_a_house_is_sent_is_one_the_tool_calls_a_read():
    """The tool's own read-only classification, asked about the script's list."""
    outside = {t for t in shapecapture.READ_TYPES if ws.access_for_type(t) != ws.READ}
    assert outside == {
        "ping",
        "subscribe_events",
        "unsubscribe_events",
        shapecapture.UNKNOWN_COMMAND,
    }


@pytest.mark.parametrize("argv", [["--write", "--house"], ["--house"], ["--open-names"]])
def test_a_house_is_compared_and_never_written(argv, capsys):
    with pytest.raises(SystemExit) as refused:
        shapecapture.main(argv)
    assert refused.value.code == 2
    capsys.readouterr()


def test_an_env_file_is_read_and_nothing_in_it_is_run(tmp_path):
    path = tmp_path / "env"
    path.write_text(
        "# a comment\nexport HA_URL='https://homeassistant.example.com'\n"
        'HA_TOKEN="$(echo example)"\n\nnot a setting\n',
        encoding="utf-8",
    )
    assert shapecapture._env_file(str(path)) == {
        "HA_URL": "https://homeassistant.example.com",
        "HA_TOKEN": "$(echo example)",
    }


def test_a_difference_names_what_was_added_removed_and_retyped():
    old = {"state": {"state": ["string"], "context": ["object"]}, "gone": {"name": ["string"]}}
    new = {"state": {"state": ["null", "string"], "extra": ["integer"]}}
    assert shapecapture.differences(old, new) == [
        ("gone", [], ["name"], {}),
        ("state", ["extra"], ["context"], {"state": (["null"], [])}),
    ]
    assert shapecapture.differences(old, old) == []


def test_an_open_objects_names_are_withheld_unless_asked_for(capsys):
    found = shapecapture.differences(
        {"state.attributes": {"friendly_name": ["string"]}, "state": {"state": ["string"]}},
        {
            "state.attributes": {"friendly_name": ["null"], "example_household_word": ["string"]},
            "state": {"state": ["string"], "example_core_key": ["string"]},
        },
    )
    shapecapture.report(found, open_names=False, out=sys.stdout)
    shown = capsys.readouterr().out
    assert "example_core_key" in shown
    assert "example_household_word" not in shown and "friendly_name" not in shown
    assert "1 not in the committed capture" in shown
    shapecapture.report(found, open_names=True, out=sys.stdout)
    assert "example_household_word" in capsys.readouterr().out


# ------------------------------------------------------------------------ the doubles


@pytest.fixture(scope="module")
def doubles():
    """The script that read the lab, reading the doubles: ``(shapes, the servers)``."""
    rest = conftest.FakeRestServer().start()
    socket = conftest.FakeWsServer().start()
    installation = conftest.FakeInstallation(rest, socket).start()
    patch = pytest.MonkeyPatch()
    # The doubles' recorder holds one fixed day, and their one service that
    # answers with data declares the field named here.
    patch.setattr(shapecapture, "_now", lambda: datetime.datetime.fromisoformat(RECORDER_NOW))
    patch.setattr(shapecapture, "WINDOW", datetime.timedelta(days=2))
    patch.setattr(
        shapecapture,
        "RESPONSE_PROBES",
        (("calendar", "get_events", {"start_date_time": RECORDER_DAY}),),
    )
    try:
        target = shapecapture.Target(installation.url, FAKE_TOKEN, disposable=True)
        shapes, _version = shapecapture.read(target)
    finally:
        patch.undo()
        installation.stop()
        rest.stop()
        socket.stop()
    return shapes, rest, socket


def test_the_doubles_answer_for_every_object_they_model(doubles):
    shapes, _rest, _socket = doubles
    assert shapes.rejected == {}
    assert set(shapes.document()) == set(OBJECTS) - set(NOT_MODELLED)


@pytest.mark.parametrize("name", sorted(set(shapecapture.EXPECTED) - set(NOT_MODELLED)))
def test_a_double_sends_only_names_a_real_server_does(doubles, name):
    sent = {key for key in doubles[0].document()[name] if not _is_elements(key)}
    assert sent - _known(name) == set(), f"the double invents these on {name}"


@pytest.mark.parametrize("name", sorted(set(shapecapture.EXPECTED) - set(NOT_MODELLED)))
def test_a_double_sends_each_name_with_a_type_a_real_server_does(doubles, name):
    sent, real = doubles[0].document()[name], OBJECTS[name]
    odd = {
        (key, kind)
        for key, types in sent.items()
        if key in real or _is_elements(key)
        for kind in set(types) - set(real.get(key, ()))
        if (name, key, kind) not in UNOBSERVED_TYPES
    }
    assert odd == set(), f"types the lab never showed on {name}"


def test_reading_the_doubles_as_a_house_writes_nothing(monkeypatch, installation, capsys):
    """`--check --house`, end to end: read-only, and silent about where and with what."""
    monkeypatch.setenv("HA_URL", installation.url)
    monkeypatch.setenv("HA_TOKEN", FAKE_TOKEN)
    assert shapecapture.main(["--check", "--house"]) == 0
    printed = capsys.readouterr()
    for secret in (FAKE_TOKEN, installation.url, "127.0.0.1"):
        assert secret not in printed.out + printed.err
    assert "against the lab capture" in printed.out
    assert {request["method"] for request in installation.rest.requests} == {"GET"}
    sent = {command["type"] for command in installation.ws.received}
    assert sent <= shapecapture.READ_TYPES and "config/entity_registry/list" in sent


# ------------------------------------------------------------------------ the readers


def test_every_module_is_local_or_has_its_readers_classified():
    modules = set(_modules())
    assert modules >= LOCAL, "a module named as local is gone"
    assert set(READERS) | set(OWN) <= modules - LOCAL
    unclassified = {name for name, reads in _scanned().items() if reads and name not in READERS}
    assert unclassified - set(OWN) == set()


@pytest.mark.parametrize("module", sorted(_scanned()))
def test_every_expression_a_key_is_read_off_is_classified(module):
    """Upstream or the tool's own: a new one has to be put in one table."""
    reads = _scanned()[module]
    upstream, own = set(READERS.get(module, {})), OWN.get(module, set())
    assert upstream & own == set()
    assert set(reads) - upstream - own == set(), f"unclassified in {module}"
    assert (upstream | own) - set(reads) == set(), f"classified in {module} and no longer read"


def _reader_cases() -> list:
    return [(module, holder) for module in sorted(READERS) for holder in sorted(READERS[module])]


@pytest.mark.parametrize(("module", "holder"), _reader_cases())
def test_every_key_a_reader_reads_is_one_a_real_server_sends(module, holder):
    read = key_reads(SOURCE / module)[holder]
    missing = read - _known(READERS[module][holder])
    assert missing == set(), f"{module} reads {sorted(missing)} off {holder}, which nothing sends"


def test_the_shared_service_reader_reads_only_what_a_real_server_sends():
    from axi_toolkit.ha import services

    reads = key_reads(Path(services.__file__))
    assert set(reads) == set(SERVICE_READERS)
    for holder, read in reads.items():
        assert read - _known(SERVICE_READERS[holder]) == set(), holder


@pytest.mark.parametrize(
    ("command", "stored", "renamed"),
    [
        ("entity.update", "registry.entity.extended", {"new_entity_id": "entity_id"}),
        ("area.update", AREA, {}),
        ("device.update", DEVICE, {}),
    ],
)
def test_every_parameter_a_write_previews_is_a_key_the_stored_entry_has(command, stored, renamed):
    """A preview compares each parameter with the stored key of the same name."""
    sent = {renamed.get(name, name) for name in ws.REGISTRY[command].optional}
    assert sent - set(OBJECTS[stored]) == set()


# ------------------------------------------------------------------------- the tables


def _built_by_the_doubles() -> dict:
    """``captured object -> names`` the doubles pass to a builder by name.

    Read off the source, because a double sends some keys only in a state the script
    that reads it never puts it in: an update that enables an entity, for one.
    """
    captures = {entry["key"]: entry["capture"] for entry in contract.DECLARED.values()}
    found: dict = {}
    for node in ast.walk(ast.parse(Path(conftest.__file__).read_text(encoding="utf-8"))):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "elements"
        ):
            continue
        keys = {keyword.arg for keyword in node.keywords if keyword.arg}
        for name in captures.get(node.func.attr, ()):
            found.setdefault(name, set()).update(keys)
    return found


def _read_or_sent(doubles_document: dict) -> dict:
    """``object -> names`` somebody depends on: read by a reader or sent by a double."""
    found: dict = {}
    for module, holders in READERS.items():
        reads = key_reads(SOURCE / module)
        for holder, named in holders.items():
            for name in _objects(named):
                found.setdefault(name, set()).update(reads.get(holder, ()))
    from axi_toolkit.ha import services

    for holder, read in key_reads(Path(services.__file__)).items():
        for name in _objects(SERVICE_READERS[holder]):
            found.setdefault(name, set()).update(read)
    for name, keys in doubles_document.items():
        found.setdefault(name, set()).update(keys)
    for name, keys in _built_by_the_doubles().items():
        found.setdefault(name, set()).update(keys)
    return found


def test_every_exception_states_why_and_is_still_needed(doubles):
    """An entry the capture has caught up with, or nothing depends on, has to go."""
    depended_on = _read_or_sent(doubles[0].document())
    # The generated check only reports a reason the capture has caught up with. Here it
    # fails, because the generated files are not edited by hand to make them fail it.
    assert contract.outlived(contract.answers()) == {}
    for name, entries in UNOBSERVED.items():
        for key, reason in entries.items():
            assert len(reason) > 40, (name, key)
            assert key not in OBJECTS.get(name, {}), f"{name}.{key} is in the capture now"
            assert key in depended_on.get(name, ()), f"nothing reads or sends {name}.{key}"
    sent = doubles[0].document()
    for (name, key, kind), reason in UNOBSERVED_TYPES.items():
        assert len(reason) > 40, (name, key)
        assert kind not in OBJECTS[name].get(key, ()), f"{name}.{key} is {kind} in the capture now"
        assert kind in sent[name][key], f"no double sends {name}.{key} as {kind}"
    for name, reason in NOT_MODELLED.items():
        assert len(reason) > 40 and name in OBJECTS, name


# -------------------------------------------------------------------------- the model

#: Each generated vocabulary: the command module that prints the row, and a command
#: that prints at least one against the doubles.
PRINTED = {
    "state": ("state", ["state", "list"]),
    "entity": ("entity", ["entity", "list"]),
    "device": ("device", ["device", "list"]),
    "area": ("area", ["area", "list"]),
    "logbook": ("logbook", ["logbook", "get"]),
    "statistic": ("statistics", ["statistics", "list"]),
    "service_domain": ("service", ["service", "list"]),
    "service": ("service", ["service", "list", "--domain", "light"]),
    "service_field": ("service", ["service", "get", "light.turn_on"]),
    "sensor": ("sensor", ["sensor", "list", "--all"]),
}


def test_every_generated_vocabulary_is_printed_by_a_command():
    assert set(vocabulary.FIELDS) == set(PRINTED) == set(vocabulary.DEFAULT)


@pytest.mark.parametrize("key", sorted(PRINTED))
def test_a_row_is_built_with_the_columns_the_model_declares(
    key, monkeypatch, run_cli, installation_env
):
    """The declared columns and the row a command builds are two statements of one set.

    `project` reads a column with `.get`, so a column the model declares and the
    builder forgot would print as null and fail nothing.
    """
    name, argv = PRINTED[key]
    module = importlib.import_module(f"{commands.__name__}.{name}")
    built = []

    def spy(rows, fields):
        built.extend(list(row) for row in rows)
        return project(rows, fields)

    project = module.project
    monkeypatch.setattr(module, "project", spy)
    monkeypatch.setattr(_window, "now", lambda: datetime.datetime.fromisoformat(RECORDER_NOW))
    code, _out = run_cli(argv, installation_env)
    assert code == 0 and built
    assert {tuple(row) for row in built} == {vocabulary.FIELDS[key]}
    assert set(vocabulary.DEFAULT[key]) <= set(vocabulary.FIELDS[key])
    assert set(vocabulary.READS[key]) == set(vocabulary.FIELDS[key])


def test_a_builder_refuses_a_name_the_model_does_not_declare():
    with pytest.raises(KeyError, match="example_invented_key"):
        elements.state_attributes(friendly_name="Example Lamp", example_invented_key=1)
    with pytest.raises(KeyError, match="entity_id"):
        elements.state(state="on", attributes={})


@pytest.mark.parametrize("key", sorted(PRINTED))
def test_the_capture_check_holds_a_row_to_what_its_vocabulary_says_it_reads(key):
    """Two generated files state what a row reads, and the check runs on only one of them."""
    row, held = contract.ROWS[key], set()
    for names in vocabulary.READS[key].values():
        held.update(names)
    filed_under = set(vocabulary.KEY_OF.get(key, {}).values())
    own = {name for name in held if "::" not in name}
    own |= {ref.rpartition(".")[2] for ref in filed_under if ref.startswith(row["of"] + ".")}
    assert own == set(row["reads"])
    others = {f"{fqn}.{name}" for fqn, names in row.get("also", {}).items() for name in names}
    assert others == {name for name in held if "::" in name} | {
        ref for ref in filed_under if not ref.startswith(row["of"] + ".")
    }


def test_a_column_filed_under_a_key_prints_a_key_of_the_map_the_model_names(
    run_cli, installation_env
):
    """`KEY_OF` says which map a column is a key of; these are the keys of those maps."""
    assert {
        "service": dict.fromkeys(
            ("service", "name"), "homeassistant::services::ServiceDomain.services"
        ),
        "service_field": dict.fromkeys(
            ("field", "section"), "homeassistant::services::Service.fields"
        ),
    } == vocabulary.KEY_OF
    light = next(entry for entry in conftest.SERVICES if entry["domain"] == "light")
    code, out = run_cli(["--json", "service", "list", "--domain", "light"], installation_env)
    assert code == 0
    printed = {row["service"] for row in json.loads(out)["services"]}
    assert printed == {f"light.{name}" for name in light["services"]}

    declared = light["services"]["turn_on"]["fields"]
    code, out = run_cli(
        ["--json", "service", "get", "light.turn_on", "--fields", "field,section"],
        installation_env,
    )
    assert code == 0
    rows = json.loads(out)["fields"]
    sections = {name for name, field in declared.items() if "fields" in field}
    assert {row["section"] for row in rows} == {"", *sections}
    for row in rows:
        holder = declared[row["section"]]["fields"] if row["section"] else declared
        assert row["field"] in holder and "fields" not in holder[row["field"]]



"""The generated readers, as the commands read an answer through them.

:mod:`hass_axi.model.readers` is generated from ``metaobjects/`` and is not edited;
what it reads is held to a real server by the capture check beside the builders.
This file holds the three things the commands rely on that the generator does not
decide for them: that a key sent as null is still told from one not sent, that a
name the model does not declare is an error rather than a blank, and that the
module the wheel ships imports nothing a Python 3.9 install does not have.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from hass_axi.commands import _common, state
from hass_axi.commands import _window as window
from hass_axi.model import readers

READERS = Path(readers.__file__)

#: One state as a real server sends one, with nobody's names in it.
STATE = {
    "entity_id": "light.example_lamp",
    "state": "on",
    "attributes": {"friendly_name": "Example Lamp", "example_only": [1, 2]},
    "last_changed": "2030-01-01T00:00:00+00:00",
    "last_reported": "2030-01-01T00:00:00+00:00",
    "last_updated": "2030-01-01T00:00:00+00:00",
}


def _without(answer: dict, name: str) -> dict:
    return {key: value for key, value in answer.items() if key != name}


@pytest.mark.parametrize("name", ["entity_id", "state", "last_changed", "last_updated"])
def test_a_key_sent_as_null_is_told_from_one_not_sent(name):
    """What a row prints for each is what reading the answer by key printed."""
    for answer in (STATE, {**STATE, name: None}, _without(STATE, name)):
        assert _common.sent_or(readers.State.read(answer), name) == answer.get(name, "")
    absent = readers.State.read(_without(STATE, name))
    assert _common.sent_or(absent, name, "elsewhere") == "elsewhere"


def test_a_row_prints_a_null_as_a_null_and_a_missing_key_as_a_blank():
    current = window.now()
    sent_null = state._row(readers.State.read({**STATE, "last_changed": None}), current)
    not_sent = state._row(readers.State.read(_without(STATE, "last_changed")), current)
    assert sent_null["last_changed"] is None
    assert not_sent["last_changed"] == ""


def test_a_name_the_model_does_not_declare_is_an_error_and_never_the_default():
    answer = readers.State.read({**STATE, "last_change": "a misspelling"})
    with pytest.raises(AttributeError):
        _common.sent_or(answer, "last_change")
    with pytest.raises(AttributeError):
        _common.sent_or(readers.State.read(_without(STATE, "state")), "stat")


def test_an_answer_is_kept_whole_for_what_is_printed_untouched():
    answer = readers.State.read(STATE)
    assert answer.raw is STATE
    assert _common.attributes_of(answer).raw == STATE["attributes"]


@pytest.mark.parametrize("attributes", [None, {}])
def test_a_state_with_no_attributes_reads_as_one_with_none(attributes):
    answer = readers.State.read({**STATE, "attributes": attributes})
    assert _common.attributes_of(answer).friendly_name is None
    assert _common.friendly_name(answer) == "light.example_lamp"
    assert dict(_common.attributes_of(answer).raw) == {}


def test_every_answer_of_a_list_is_read_and_no_list_is_none():
    assert _common.read_each(readers.State, None) == []
    assert [one.entity_id for one in _common.read_each(readers.State, [STATE])] == [
        "light.example_lamp"
    ]


def test_the_readers_import_the_standard_library_alone():
    """The wheel ships this module, and its floor is a Python with nothing installed."""
    imported = set()
    for node in ast.walk(ast.parse(READERS.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a relative import reaches into the package"
            imported.add(node.module.split(".")[0])
    standard = getattr(sys, "stdlib_module_names", None)
    if standard is None:  # Python 3.9 keeps no list; the names are few enough to state.
        standard = {"__future__", "collections", "dataclasses"}
    assert imported <= set(standard) | {"__future__"}

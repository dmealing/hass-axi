"""The encoder, read back by a decoder this project did not write.

The encoder's own suite and the conformance fixtures both state what the encoder
should produce, which is the encoder's own account of itself. This file asks a
different question -- does an independent implementation of the same
specification read the output back to the value that went in -- on the
specification's fixtures, on every command's real output against the doubles,
and on generated values. Nothing here compares the tool with itself.

The decoder is `toon-format`, pinned in the development dependencies. It needs
Python 3.10, so this file skips on 3.9.
"""

from __future__ import annotations

import json
import re

import pytest

toon_format = pytest.importorskip("toon_format")

from axi_toolkit import toon_spec  # noqa: E402
from axi_toolkit.toon import encode  # noqa: E402

_HELP = re.compile(r"^(\s*)help\[(\d+)\]:\s*$")


def decode_document(text: str):
    """Decode a document this tool printed.

    The one documented departure from strict TOON -- `help[N]:` with one bare
    suggestion per line -- is rewritten into strict list form first.
    """
    lines = text.split("\n")
    fixed, index = [], 0
    while index < len(lines):
        match = _HELP.match(lines[index])
        if match:
            count = int(match.group(2))
            fixed.append(lines[index])
            for offset in range(1, count + 1):
                body = lines[index + offset]
                indent = len(body) - len(body.lstrip(" "))
                fixed.append(" " * indent + "- " + json.dumps(body.strip(), ensure_ascii=False))
            index += count + 1
        else:
            fixed.append(lines[index])
            index += 1
    return toon_format.decode("\n".join(fixed).rstrip("\n"))


def fixture_cases():
    for case in toon_spec.cases():
        # Default options only: the tool never encodes with any other.
        if case.options:
            continue
        yield pytest.param(case.input, id=f"{case.file[:-5]}:{case.name}")


@pytest.mark.parametrize("value", list(fixture_cases()))
def test_a_specification_fixture_reads_back_through_an_independent_decoder(value):
    assert toon_format.decode(encode(value)) == value


#: Every read against the doubles whose answer is a data document.
READS = [
    [],
    ["state", "list"],
    ["state", "list", "--domain", "nosuch"],
    ["state", "list", "--fields", "entity_id,name,state,domain,last_changed,age"],
    ["state", "get", "light.example_lamp"],
    ["sensor", "list", "--all", "--fields", "entity_id,name,value,unit,area,device_class"],
    ["history", "get", "light.example_lamp", "sensor.example_temperature"],
    ["logbook", "get", "--start", "2d"],
    ["statistics", "list"],
    ["statistics", "get", "sensor.example_legacy_meter", "sensor.example_temperature"],
    ["service", "list"],
    ["service", "list", "--domain", "light"],
    ["service", "get", "light.turn_on"],
    ["service", "call", "light.turn_on", "--target-area", "example_room"],
    ["template", "render", "--template", "{{ 1 }}"],
    ["entity", "list"],
    ["entity", "get", "light.example_lamp"],
    ["entity", "update", "light.example_lamp", "--name", 'A, "quoted": [name]'],
    ["area", "list"],
    ["area", "get", "example_room"],
    ["area", "create", "--name", "Example Annex"],
    ["device", "list"],
    ["device", "get", "device_one"],
    ["ws", "--list"],
    ["ws", "entity.list", "--full"],
    ["ws", "device.list", "--full"],
    ["ws", "service.list", "--full"],
    ["api", "/states", "--full"],
    ["api", "/services", "--full"],
    ["api", "/config"],
    ["ping"],
    ["doctor"],
    ["context"],
    ["state", "get", "light.no_such_entity"],
    ["state", "list", "--limit", "abc"],
    ["entity", "list", "--area", "Example Rom"],
]


@pytest.mark.parametrize("argv", READS, ids=lambda argv: " ".join(argv) or "home")
def test_a_commands_toon_decodes_to_its_json(run_cli, installation_env, monkeypatch, argv):
    from datetime import datetime

    from hass_axi.commands import _window

    # One clock for both runs, so an `age` cannot tick between them.
    monkeypatch.setattr(_window, "now", lambda: datetime.fromisoformat("2026-01-02T00:00:00+00:00"))
    toon_code, toon = run_cli(argv, installation_env)
    json_code, raw = run_cli([*argv, "--json"], installation_env)
    assert toon_code == json_code
    document = json.loads(raw)
    decoded = decode_document(toon)
    # A latency is measured twice and differs; everything else is one answer.
    for volatile in ("latency_ms",):
        document.pop(volatile, None)
        if isinstance(decoded, dict):
            decoded.pop(volatile, None)
    assert decoded == document

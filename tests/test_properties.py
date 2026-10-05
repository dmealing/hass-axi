"""Properties that hold for every input, checked with generated ones.

Two surfaces take arbitrary input and both are worth more than the examples
anybody thought to write: the encoder, which is handed whatever JSON Home
Assistant answers with, and the argument parser, which is handed whatever an
agent types. A flag that swallowed the flag after it was found by a person
typing `--domain --json`; it is exactly the input a generator produces in its
first hundred tries.

Needs `hypothesis` and the independent decoder, both Python 3.10 and later, so
this file skips on 3.9.
"""

from __future__ import annotations

import json
import math

import pytest

hypothesis = pytest.importorskip("hypothesis")
toon_format = pytest.importorskip("toon_format")

from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from conftest import FAKE_TOKEN, FakeInstallation, FakeRestServer, FakeWsServer  # noqa: E402
from hass_axi import cli, errors  # noqa: E402
from hass_axi.toon import encode  # noqa: E402

# --------------------------------------------------------------- the encoder

#: Text a Home Assistant answer can hold: delimiters, quotes, structural
#: characters, line breaks, typographic marks and anything else printable.
TEXT = st.text(
    alphabet=st.one_of(
        st.sampled_from(list(' ,:"[]{}-#|\\\t\n’é中')),
        st.characters(blacklist_categories=("Cs",), max_codepoint=0x2FFF),
    ),
    max_size=24,
)
FINITE = st.floats(allow_nan=False, allow_infinity=False, width=64)
SCALARS = st.one_of(st.none(), st.booleans(), st.integers(-(2**53), 2**53), FINITE, TEXT)
VALUES = st.recursive(
    SCALARS,
    lambda inner: st.one_of(
        st.lists(inner, max_size=5),
        st.dictionaries(TEXT, inner, max_size=5),
        # Uniform objects, the shape tabular form is for.
        st.lists(st.fixed_dictionaries({"a": SCALARS, "b": SCALARS}), max_size=4),
    ),
    max_leaves=25,
)


def normalised(value):
    """A value as JSON holds it: `-0.0` is `0`, and a whole float is an integer."""
    if isinstance(value, float):
        if value == 0:
            return 0
        return int(value) if value.is_integer() and abs(value) < 1e21 else value
    if isinstance(value, list):
        return [normalised(item) for item in value]
    if isinstance(value, dict):
        return {key: normalised(item) for key, item in value.items()}
    return value


def close(left, right) -> bool:
    if isinstance(left, float) or isinstance(right, float):
        return (
            isinstance(left, (int, float))
            and isinstance(right, (int, float))
            and not isinstance(left, bool)
            and not isinstance(right, bool)
            and math.isclose(left, right, rel_tol=1e-12, abs_tol=0.0)
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(close(a, b) for a, b in zip(left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(close(left[k], right[k]) for k in left)
    return type(left) is type(right) and left == right


@settings(max_examples=400, deadline=None)
@given(st.dictionaries(TEXT, VALUES, max_size=6))
def test_any_json_document_reads_back_through_an_independent_decoder(document):
    decoded = toon_format.decode(encode(document))
    assert close(normalised(decoded), normalised(document)), encode(document)


# ------------------------------------------------------- the argument parser

COMMANDS = [name for name in cli.COMMAND_ORDER if name != "setup"]
SUBS = sorted({sub.name for name in COMMANDS for sub in cli.command_specs()[name].subs})
FLAGS = sorted(
    {flag.name for name in COMMANDS for sub in cli.command_specs()[name].subs for flag in sub.flags}
    | {"--json", "--human", "--help", "--timeout", "--debug", "--nosuch", "--", "-"}
)
HOSTILE = [
    "",
    " ",
    "0",
    "-1",
    "1e9",
    "NaN",
    "null",
    "true",
    "none",
    "*",
    "%",
    "%20",
    "..",
    "../../config",
    "/",
    "//",
    "\\",
    "light.",
    ".lamp",
    "a..b",
    "LIGHT.EXAMPLE_LAMP",
    "light.example_lamp",
    "sensor.example_temperature",
    "example_room",
    "Example Room",
    "device_one",
    "light.turn_on",
    "a?b",
    "a#b",
    "a&b",
    "$(id)",
    "'",
    '"',
    "\t",
    "a\nb",
    "café",
    "Example’s",
    "中文",
    "‮abc",
    "x" * 300,
    "=",
    "a=b",
    "a==b",
    "=b",
    "a=",
    "{}",
    "[]",
    "{",
    "24h",
    "7d",
    "soon",
    "2026-01-01T00:00:00+00:00",
    "/config",
    "/states",
    "GET",
    "POST",
    "entity.list",
]
TOKENS = st.one_of(st.sampled_from(FLAGS), st.sampled_from(HOSTILE), st.sampled_from(SUBS))
ARGV = st.builds(
    lambda command, rest: [command, *rest],
    st.sampled_from(COMMANDS),
    st.lists(TOKENS, max_size=6),
)


@pytest.fixture(scope="module")
def doubles():
    rest, ws = FakeRestServer().start(), FakeWsServer().start()
    installation = FakeInstallation(rest, ws).start()
    yield installation
    installation.stop()
    rest.stop()
    ws.stop()


@settings(
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(ARGV)
def test_any_invocation_ends_in_a_contract_answer(doubles, capsys, monkeypatch, argv):
    """Exit 0, 1 or 2; one JSON document; exit 2 exactly when the fault is `usage`."""
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(""))
    capsys.readouterr()
    code = cli.main([*argv, "--json"], environ={**doubles.environ, "HA_TOKEN": FAKE_TOKEN})
    captured = capsys.readouterr()
    assert code in (0, 1, 2), argv
    assert "Traceback" not in captured.out + captured.err, argv
    if "--help" in argv or "-h" in argv:
        return
    try:
        document = json.loads(captured.out)
    except ValueError:
        # `--json` consumed as a free-text value, or placed after `--` where
        # everything is a positional, leaves the default mode.
        assert "--template" in argv or "--name" in argv or "--" in argv, (argv, captured.out)
        return
    if not isinstance(document, dict) or "error" not in document:
        return
    assert document.get("code") != "INTERNAL_ERROR", (argv, document)
    assert document["code"] in errors.CODES, (argv, document)
    assert document["class"] == errors.CODES[document["code"]], (argv, document)
    assert (code == 2) == (document["class"] == "usage"), (argv, document)
    assert code != 0, (argv, document)

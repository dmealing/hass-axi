"""The official TOON conformance fixtures, run against this encoder.

`tests/test_toon.py` states the encoder's behaviour in this project's own words,
which is worth having and is not the same thing as conformance: a rule nobody
thought to write a test for reads as passing. These fixtures are the
specification's own opinion, vendored byte-for-byte from `toon-format/spec`
(MIT; see `fixtures/toon-spec/PROVENANCE.md`), so the strict-encoder claim in the
README is a property this suite checks rather than one the docs assert.

Every case in `fixtures/toon-spec/encode/` runs, and all of them must pass. The
count is asserted too: a fixture file deleted, emptied or left unparsed would
otherwise shrink the suite in silence, which is exactly how a partial score
ships.

Cases are keyed on file name and array index, never on the fixture's prose
`name` -- upstream rewrites those whenever the specification's terminology
changes, and a runner keyed on them breaks on a refresh that changed nothing.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hass_axi.toon import encode

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "toon-spec"
ENCODE_ROOT = FIXTURE_ROOT / "encode"
CHECKSUMS = FIXTURE_ROOT / "checksums.txt"

#: Total encode cases published by the vendored spec version. Enforcing the
#: number is what makes "179/179" a test result instead of a claim in a report.
CASE_COUNT = 179

#: Fixture option names this runner knows how to apply. An unrecognised one is
#: a failure, not a skip: silently ignoring an option would run the case with
#: the wrong settings and report a pass.
KNOWN_OPTIONS = {"delimiter", "indentSize"}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_files() -> list[Path]:
    return sorted(ENCODE_ROOT.glob("*.json"))


def _cases() -> list:
    params = []
    for path in _fixture_files():
        for index, case in enumerate(_load(path)["tests"]):
            params.append(pytest.param(case, id=f"{path.stem}-{index}"))
    return params


def _kwargs(case: dict) -> dict:
    """Map the fixture's options onto this encoder's keyword arguments.

    The specification spells the indentation option ``indentSize`` and this
    encoder's keyword is ``indent`` (spec section 13 governs the option name,
    not the bytes emitted). The mapping lives here, in one place, so no vendored
    file has to be edited to run.
    """
    options = case.get("options") or {}
    unknown = set(options) - KNOWN_OPTIONS
    assert not unknown, f"fixture uses an option this runner does not apply: {sorted(unknown)}"
    kwargs = {}
    if "delimiter" in options:
        kwargs["delimiter"] = options["delimiter"]
    if "indentSize" in options:
        kwargs["indent"] = options["indentSize"]
    return kwargs


@pytest.mark.parametrize("case", _cases())
def test_encode_matches_the_specification_fixture(case):
    detail = f"{case['name']} (spec section {case.get('specSection', '?')})"
    assert encode(case["input"], **_kwargs(case)) == case["expected"], detail


def test_the_whole_published_suite_runs():
    """A fixture that stops being collected must fail, not quietly shrink the score."""
    assert len(_cases()) == CASE_COUNT


def test_every_vendored_fixture_matches_its_recorded_checksum():
    """A fixture edited to suit the encoder is no longer the specification's opinion."""
    recorded = {}
    for line in CHECKSUMS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, name = line.split(maxsplit=1)
            recorded[name.strip()] = digest

    present = {path.name for path in _fixture_files()}
    assert present == set(recorded), "vendored fixture set differs from checksums.txt"

    for name, digest in sorted(recorded.items()):
        actual = hashlib.sha256((ENCODE_ROOT / name).read_bytes()).hexdigest()
        assert actual == digest, f"{name} no longer matches the vendored upstream copy"


def test_every_fixture_file_is_an_encode_fixture():
    """Decode fixtures are not vendored; one arriving here would silently not run."""
    for path in _fixture_files():
        assert _load(path)["category"] == "encode", path.name


def test_no_case_expects_an_error():
    """`shouldError` has no encode cases today; if one appears it needs handling here."""
    for path in _fixture_files():
        for index, case in enumerate(_load(path)["tests"]):
            assert not case.get("shouldError"), f"{path.name}[{index}]"

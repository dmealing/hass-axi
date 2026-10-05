"""The official TOON conformance fixtures, run against the encoder this tool prints with.

The encoder is `axi_toolkit.toon` and the fixtures ship inside the same package, with
the rig that runs them. This project used to carry its own copy of both; three
byte-identical encoders across two tools and the library is how one regular-expression
defect came to live in all three. So nothing is vendored here any more, and what this
file adds is the claim that matters to *this* tool: the encoder `output.py` imports,
at whatever version the dependency floor resolved to, scores every published case.

The count is asserted too: a fixture file deleted, emptied or left unparsed upstream
would otherwise shrink the suite in silence, which is exactly how a partial score
ships. Cases are keyed on file name and array index, never on the fixture's prose
`name` -- upstream rewrites those whenever the specification's terminology changes.
"""

from __future__ import annotations

import pytest
from axi_toolkit import toon_spec

from hass_axi import output

#: Total encode cases published by the specification version the dependency ships.
#: Enforcing the number is what makes "179/179" a test result instead of a claim in
#: a report.
CASE_COUNT = 179


def test_the_encoder_under_test_is_the_one_the_output_boundary_uses():
    """Scoring an encoder nothing prints with would prove nothing about this tool."""
    from axi_toolkit.toon import encode

    assert output.encode is encode


def test_the_score_is_every_published_case():
    report = toon_spec.run(output.encode)
    explained = "\n".join(
        f"{toon_spec.case_id(failure.case)}: expected {failure.case.expected!r}, "
        f"got {failure.got!r}"
        for failure in report.failures[:5]
    )
    assert report.failures == [], explained
    assert report.score == f"{CASE_COUNT}/{CASE_COUNT}"


@pytest.mark.parametrize("case", toon_spec.cases(), ids=toon_spec.case_id)
def test_encode_matches_the_specification_fixture(case):
    detail = f"{case.name} (spec section {case.spec_section or '?'})"
    assert output.encode(case.input, **toon_spec.encoder_kwargs(case)) == case.expected, detail


def test_the_whole_published_suite_runs():
    """A fixture that stops being collected must fail, not quietly shrink the score."""
    assert len(toon_spec.cases()) == CASE_COUNT


def test_every_fixture_matches_its_recorded_checksum():
    """A fixture edited to suit the encoder is no longer the specification's opinion."""
    assert toon_spec.digest_mismatches() == []


def test_every_fixture_file_is_an_encode_fixture():
    """Decode fixtures are not shipped; one arriving there would silently not run."""
    assert set(toon_spec.categories()) <= toon_spec.RUNNABLE_CATEGORIES

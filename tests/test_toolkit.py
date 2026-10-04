"""`hass_axi.toolkit`: the library surface, tested as functions.

The toolkit holds rules a program other than the CLI needs unchanged -- names,
response shapes, recorder statistics -- so it is written to be imported: plain
values in, plain values out, a failed lookup as data. The first test here is
what keeps it a library; the rest state each rule against plain values, with no
server and no command line.
"""

from __future__ import annotations

import ast
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hass_axi import toolkit
from hass_axi.toolkit import names, recorder, shapes

TOOLKIT = Path(toolkit.__file__).parent
MODULES = sorted(TOOLKIT.glob("*.py"))


def test_the_toolkit_imports_nothing_from_the_cli_layers():
    """Only the standard library and its own modules: no command, parser, output or transport.

    A library that imported the output boundary would print; one that imported
    a command module would drag the argument parser and both transports in
    behind it. Either way it would stop being something another program can
    import for its rules alone.
    """
    assert {path.name for path in MODULES} >= {
        "__init__.py",
        "names.py",
        "shapes.py",
        "recorder.py",
    }
    own = {path.stem for path in MODULES}
    stdlib = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
    for path in MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                if node.level == 1:
                    # A sibling inside the toolkit, and nothing above it.
                    named = {node.module} if node.module else {a.name for a in node.names}
                    assert named <= own, (
                        f"{path.name} imports {sorted(named - own)} from the package"
                    )
                    continue
                assert node.level == 0, f"{path.name} reaches above the toolkit package"
                roots = {(node.module or "").split(".")[0]}
            else:
                continue
            assert "hass_axi" not in roots, f"{path.name} imports the CLI package"
            assert not {"axi_toolkit", "websockets"} & roots, f"{path.name} imports a dependency"
            if len(stdlib) > 1:
                assert roots <= stdlib, f"{path.name} imports {sorted(roots - stdlib)}"


def test_the_toolkit_says_nothing_about_the_command_line():
    """Its text is about Home Assistant: no message it produces names a CLI command."""
    for path in MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings:
                    continue
                assert "hass-axi" not in node.value and "Run `" not in node.value, (
                    f"{path.name} produces text that names the command line: {node.value!r}"
                )


# --------------------------------------------------------------------- names


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("Example's Phone", "Example’s Phone"),
        ("example's phone", "Example‘s Phone"),
        ("Example's Phone", "Exampleʼs Phone"),
        ('the "den"', "The “Den”"),
        ("  Example Room ", "example room"),
        ("STRASSE", "straße"),
    ],
)
def test_two_spellings_of_one_name_fold_to_the_same_thing(typed, stored):
    assert names.fold(typed) == names.fold(stored)


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("cafe", "Caf\u00e9"),
        ("uber", "\u00dcber"),
        ("nino", "Ni\u00f1o"),
        ("north-east", "North\u2013East"),
        ("north-east", "North\u2014East"),
        ("north-east", "North\u2011East"),
        ("5-10", "5\u221210"),
        ("and so on...", "And so on\u2026"),
        ("example room", "Example\u00a0Room"),
        ("example room", "Example   Room"),
        ("it's", "it\u2032s"),
    ],
)
def test_dashes_the_ellipsis_accents_and_spacing_fold_too(typed, stored):
    assert names.fold(typed) == names.fold(stored)


def test_folding_keeps_names_that_differ_apart():
    assert names.fold("Example Room") != names.fold("Example Rooms")
    assert names.fold("Example Room") != names.fold("ExampleRoom")
    assert names.fold("north-east") != names.fold("north east")
    assert names.fold(None) == "" and names.fold(7) == "7" and names.fold(0) == "0"


def test_matching_is_a_folded_substring():
    assert names.matches("example's", "The Example\u2019s Phone")
    assert names.matches("cafe", None, "Corner Caf\u00e9")
    assert not names.matches("garage", "Corner Caf\u00e9")


AREAS = [
    {"id": "kitchen", "name": "Kitchen"},
    {"id": "cafe", "name": "Caf\u00e9"},
    {"id": "cafe_2", "name": "Cafe"},
    {"id": "garage", "name": "Garage"},
    {"id": "den", "name": "Example\u2019s Den"},
]


def resolve(needle, **options):
    return names.resolve(
        needle, AREAS, ident=lambda a: a["id"], name=lambda a: a["name"], **options
    )


def test_an_identifier_resolves_before_any_name():
    found = resolve("cafe")
    assert (found.match["id"], found.by, found.ties, found.near) == ("cafe", "id", (), ())


def test_a_name_resolves_however_it_was_typed():
    found = resolve("example's den")
    assert (found.match["id"], found.by) == ("den", "name")
    assert resolve("  KITCHEN ").match["id"] == "kitchen"


def test_a_folded_tie_returns_every_candidate_and_picks_none():
    """`Caf\u00e9` and `Cafe` are one name once folded, so typing either is a tie."""
    for typed in ("Caf\u00e9", "Cafe", "CAFE"):
        found = resolve(typed)
        assert found.match is None and found.found is False
        assert found.ambiguous
        assert [a["id"] for a in found.ties] == ["cafe", "cafe_2"]
        assert found.near == ()


def test_a_miss_returns_the_nearest_entries_as_data():
    found = resolve("Kitchn")
    assert not found.found and not found.ambiguous
    assert found.near[0]["id"] == "kitchen"
    assert resolve("zzzzzz").near == ()
    assert resolve("").near == ()


def test_resolution_by_identifier_alone_ignores_names():
    assert resolve("Kitchen", by_name=False).match is None
    assert resolve("kitchen", by_name=False).match["id"] == "kitchen"
    assert resolve("kitche", by_name=False).near[0]["id"] == "kitchen"


def test_near_entries_are_returned_once_and_bounded():
    entries = [{"id": f"room_{n}", "name": f"Example Room {n}"} for n in range(40)]
    near = names.nearest("Example Room", entries, lambda e: [e["name"], e["id"]])
    assert len(near) == names.MAX_CANDIDATES
    assert len({e["id"] for e in near}) == len(near)


def test_two_entries_sharing_a_folded_label_are_both_near():
    entries = [{"name": "Caf\u00e9"}, {"name": "Cafe"}]
    assert len(names.nearest("caff", entries, lambda e: [e["name"]])) == 2


def test_a_near_miss_is_offered_and_a_far_one_is_not():
    labelled = [("Kitchen", "K"), ("Kitsch Corner", "KC"), ("Garage", "G"), ("", "empty")]
    assert names.close_matches("Kitchn", labelled)[0] == "K"
    assert names.close_matches("kitch", labelled)[0] == "K"
    assert names.close_matches("zzzzzz", labelled) == []
    assert names.close_matches("", labelled) == []


def test_containment_ranks_ahead_of_similarity():
    labelled = [("Example Rood", "similar"), ("Example Room Annex", "contains")]
    assert names.close_matches("Example Room", labelled)[0] == "contains"


def test_candidates_are_bounded():
    labelled = [(f"Example Room {n}", str(n)) for n in range(40)]
    assert len(names.close_matches("Example Room", labelled)) == names.MAX_CANDIDATES


# -------------------------------------------------------------------- shapes


@pytest.mark.parametrize(
    ("value", "healthy"),
    [
        ({"message": "API running."}, True),
        ({"message": "something else"}, True),
        ({"status": "ok"}, False),
        ({}, False),
        ([], False),
        ("<html>router login</html>", False),
        ("", False),
        (None, False),
        ({"message": 7}, False),
    ],
)
def test_the_api_root_has_one_shape(value, healthy):
    assert (shapes.health_fault(value) is None) is healthy


def test_a_shape_fault_says_what_arrived_instead():
    assert shapes.shape_fault([], list) is None
    assert shapes.shape_fault({}, list) == "a JSON object"
    assert shapes.shape_fault("<html>", list) == "text that is not JSON"
    assert shapes.shape_fault(None, dict) == "an empty response"
    assert shapes.shape_fault(3, list) == "a JSON int"


@pytest.mark.parametrize(
    ("content_type", "raw", "text"),
    [
        ("application/json", b"{}", True),
        ("application/json; charset=utf-8", b"{}", True),
        ("text/plain", b"hello", True),
        ("text/html", b"<html>", True),
        ("application/vnd.example+json", b"{}", True),
        ("image/jpeg", b"\xff\xd8\xff", False),
        ("application/octet-stream", b"plain ascii", False),
        ("", b"plain ascii", True),
        ("", b"\xff\xd8\xff", False),
    ],
)
def test_a_body_is_text_by_its_type_or_failing_that_by_its_bytes(content_type, raw, text):
    assert shapes.is_text(content_type, raw) is text


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("light.example_lamp", True),
        ("LIGHT.Example_Lamp", True),
        ("sensor.a1", True),
        ("Example Reading Lamp", False),
        ("nodot", False),
        ("light.", False),
        (".lamp", False),
        ("a.b.c", False),
        ("light.example lamp", False),
        ("", False),
    ],
)
def test_an_entity_id_is_a_domain_a_dot_and_an_object_id(value, valid):
    assert shapes.is_entity_id(value) is valid


# ----------------------------------------------------------- recorder rules

START = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
HOUR = 3_600_000
METER = {
    "statistic_id": "sensor.example_meter",
    "has_sum": True,
    "display_unit_of_measurement": "kWh",
}
READING = {
    "statistic_id": "sensor.example_reading",
    "mean_type": 1,
    "display_unit_of_measurement": "C",
}


def at(hours: float) -> int:
    return int((START + timedelta(hours=hours)).timestamp() * 1000)


def hours(changes, first=0) -> list:
    rows, reading = [], 100.0
    for index, change in enumerate(changes):
        reading += change
        rows.append(
            {
                "start": at(first + index),
                "end": at(first + index + 1),
                "change": change,
                "state": reading,
            }
        )
    return rows


def bucket(start, length, change) -> dict:
    return {
        "start": at(start),
        "end": at(start + length),
        "change": change,
        "state": 100.0 + change,
    }


def test_kind_comes_from_the_metadata():
    assert recorder.kind_of({"has_sum": True, "mean_type": 1}) == "sum"
    assert recorder.kind_of({"mean_type": 1}) == "mean"
    assert recorder.kind_of({"mean_type": 2}) == "circular mean"
    assert recorder.kind_of({"has_mean": True}) == "mean"
    assert recorder.kind_of({}) == ""


def test_the_default_period_follows_the_window():
    assert recorder.default_period(86400) == "hour"
    assert recorder.default_period(7 * 86400) == "day"
    assert recorder.default_period(90 * 86400) == "month"
    assert recorder.wants_hourly("day", 7 * 86400)
    assert not recorder.wants_hourly("hour", 7 * 86400)
    assert not recorder.wants_hourly("month", 800 * 86400)


def test_a_total_is_read_from_the_hourly_rows_inside_the_window():
    """A daily bucket that began twelve hours before the window holds twice the answer."""
    end = START + timedelta(hours=12)
    daily = [bucket(-12, 24, 24.0)]
    hourly = hours([1.0] * 24, first=-12)
    summary = recorder.summarize(METER, daily, START, "day", end=end, hourly=hourly)
    assert summary["total"] == 12
    assert summary["buckets"] == 1
    assert "caveats" not in summary


def test_without_hourly_rows_the_summary_says_what_its_buckets_cover():
    end = START + timedelta(hours=12)
    summary = recorder.summarize(METER, [bucket(-12, 24, 24.0)], START, "day", end=end)
    assert summary["total"] == 24
    assert any("more than the window asked for" in c for c in summary["caveats"])


def test_buckets_inside_the_window_carry_no_overhang_caveat():
    end = START + timedelta(hours=48)
    rows = [bucket(0, 24, 5.0), bucket(24, 24, 6.0)]
    summary = recorder.summarize(METER, rows, START, "day", end=end)
    assert summary["total"] == 11 and "caveats" not in summary


def test_one_bucket_that_dwarfs_the_rest_is_stated_and_not_removed():
    changes = [1.0] * 10
    changes[4] = 500.0
    summary = recorder.summarize(
        METER, hours(changes), START, "hour", end=START + timedelta(hours=10)
    )
    assert summary["total"] == 509
    caveat = next(c for c in summary["caveats"] if c.startswith("one bucket"))
    assert "500 kWh" in caveat and "the total includes it" in caveat
    assert recorder.iso(START + timedelta(hours=4)) in caveat


@pytest.mark.parametrize(
    "changes",
    [
        [1.0] * 10,
        [1.0, 1.0, 15.0, 1.0, 1.0],  # large, but not twenty times the median
        [0.0, 0.0, 9.0],  # one positive bucket is not a pattern
        [1.0, 1.0, 30.0, 30.0, 30.0],  # most of the buckets are large
    ],
)
def test_ordinary_meters_get_no_outlier_caveat(changes):
    summary = recorder.summarize(
        METER, hours(changes), START, "hour", end=START + timedelta(hours=len(changes))
    )
    assert not any("one bucket" in c for c in summary.get("caveats", []))


def test_a_meter_that_went_backwards_is_reported_and_counted():
    summary = recorder.summarize(
        METER, hours([1.0, -2.0, 1.0]), START, "hour", end=START + timedelta(hours=3)
    )
    assert summary["total"] == 0
    assert any("went backwards by 2 kWh" in c for c in summary["caveats"])


def test_a_reset_is_a_drop_in_state_the_change_does_not_show():
    rows = hours([1.0, 1.0, 1.0])
    rows[1]["state"] = 0.5  # the reading fell; the recorder carried the sum across
    summary = recorder.summarize(METER, rows, START, "hour", end=START + timedelta(hours=3))
    assert summary["total"] == 3
    assert any("reset 1 time" in c for c in summary["caveats"])


def test_missing_buckets_are_counted_before_the_first_and_between_but_not_after():
    rows = hours([1.0, 1.0], first=2) + hours([1.0], first=6)
    summary = recorder.summarize(METER, rows, START, "hour", end=START + timedelta(hours=12))
    gap = next(c for c in summary["caveats"] if "have no data" in c)
    assert gap.startswith("4 of 7 hourly buckets")


def test_no_rows_is_said():
    summary = recorder.summarize(METER, [], START, "hour", end=START + timedelta(hours=3))
    assert summary["caveats"] == ["no statistics were recorded in this window"]


def test_a_mean_reports_mean_min_and_max():
    rows = [
        {"start": at(n), "end": at(n + 1), "mean": 20.0 + n, "min": 19.0 + n, "max": 21.0 + n}
        for n in range(3)
    ]
    summary = recorder.summarize(READING, rows, START, "hour", end=START + timedelta(hours=3))
    assert (summary["mean"], summary["min"], summary["max"]) == (21, 19, 23)


def test_a_bearing_gets_a_circular_mean_and_no_extremes():
    meta = {"statistic_id": "sensor.example_bearing", "mean_type": 2}
    rows = [
        {"start": at(n), "end": at(n + 1), "mean": value} for n, value in enumerate([350.0, 10.0])
    ]
    summary = recorder.summarize(meta, rows, START, "hour", end=START + timedelta(hours=2))
    assert summary["mean"] in (0, 360)
    assert "min" not in summary and "max" not in summary


def test_a_timestamp_is_read_in_every_form_the_recorder_sends():
    moment = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for value in (
        1767225600,
        1767225600000,
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:00:00",
    ):
        assert recorder.parse_timestamp(value) == moment
    assert recorder.parse_timestamp("soon") is None
    assert recorder.parse_timestamp(None) is None

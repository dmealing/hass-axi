"""Conformance tests for the TOON encoder, against the rules in the spec."""

from __future__ import annotations

import pytest

from hass_axi.toon import encode


def test_uniform_object_array_uses_tabular_form():
    doc = {"users": [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Bob"}]}
    assert encode(doc) == "users[2]{id,name}:\n  1,Ada\n  2,Bob"


def test_nested_uniform_column_becomes_a_nested_field_group():
    doc = {
        "rows": [
            {"id": 1, "who": {"name": "Ada", "role": "admin"}},
            {"id": 2, "who": {"name": "Bob", "role": "user"}},
        ]
    }
    assert encode(doc) == "rows[2]{id,who{name,role}}:\n  1,Ada,admin\n  2,Bob,user"


def test_object_of_uniform_objects_uses_keyed_tabular_form():
    doc = {"totals": {"a": {"x": 1, "y": 2}, "b": {"x": 3, "y": 4}}}
    assert encode(doc) == "totals[2:]{x,y}:\n  a: 1,2\n  b: 3,4"


def test_single_entry_object_stays_nested():
    # Keyed tabular form needs at least two entries (spec section 9.5).
    assert encode({"totals": {"a": {"x": 1}}}) == "totals:\n  a:\n    x: 1"


def test_primitive_array_is_inline():
    assert encode({"ids": [1, 2, 3]}) == "ids[3]: 1,2,3"


def test_empty_array_uses_the_bracket_literal_not_a_header():
    assert encode({"ids": []}) == "ids: []"
    assert encode([]) == "[]"


def test_empty_object_field_is_a_bare_key():
    assert encode({"meta": {}}) == "meta:"


def test_mixed_array_falls_back_to_list_form():
    assert encode({"items": [1, "two", {"a": 1}]}) == "items[3]:\n  - 1\n  - two\n  - a: 1"


def test_empty_object_list_item_is_a_bare_hyphen():
    assert encode({"items": [{"a": 1}, {}]}) == "items[2]:\n  - a: 1\n  -"


def test_inner_empty_array_item_uses_a_zero_header():
    # Spec section 9.2 forbids emitting the bare `- []` item form.
    assert encode({"items": [[], [1]]}) == "items[2]:\n  - [0]:\n  - [1]: 1"


def test_tabular_form_is_unavailable_in_list_item_position():
    """Section 9.4: uniform inner arrays still take list form.

    A tabular header on a hyphen line would be a keyless fields-bearing header,
    which section 6 allows only at the document root, so however tabular-eligible
    the items are the encoder MUST fall back to list form here.
    """
    assert encode({"a": [[{"x": 1}, {"x": 2}]]}) == "a[1]:\n  - [2]:\n    - x: 1\n    - x: 2"


def test_the_list_form_fallback_survives_further_nesting():
    doc = {"a": [[[{"x": 1}]]]}
    assert encode(doc) == "a[1]:\n  - [1]:\n    - [1]:\n      - x: 1"


def test_a_key_still_reaches_tabular_form_from_inside_a_list_item():
    """The restriction is the position, not the depth: a key can carry a header."""
    doc = {"a": [{"rows": [{"x": 1}, {"x": 2}]}]}
    assert encode(doc) == "a[1]:\n  - rows[2]{x}:\n      1\n      2"


def test_list_item_object_with_tabular_first_field_indents_rows_two_levels():
    doc = {"outer": [{"rows": [{"a": 1}, {"a": 2}], "note": "hi"}]}
    assert encode(doc) == "outer[1]:\n  - rows[2]{a}:\n      1\n      2\n    note: hi"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", '""'),
        (" pad ", '" pad "'),
        ("true", '"true"'),
        ("null", '"null"'),
        ("42", '"42"'),
        ("-3.14", '"-3.14"'),
        ("1e-6", '"1e-6"'),
        ("a,b", '"a,b"'),
        ("a:b", '"a:b"'),
        ("a[b]", '"a[b]"'),
        ("a{b}", '"a{b}"'),
        ("-lead", '"-lead"'),
        ("#lead", '"#lead"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("back\\slash", '"back\\\\slash"'),
        ("line\nbreak", '"line\\nbreak"'),
        ("tab\there", '"tab\\there"'),
        ("plain value", "plain value"),
        ("emoji ok", "emoji ok"),
    ],
)
def test_string_quoting_follows_the_spec(value, expected):
    assert encode({"k": value}) == f"k: {expected}"


def test_control_characters_use_lowercase_unicode_escapes():
    assert encode({"k": "a\x01b"}) == 'k: "a\\u0001b"'


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "null"), (True, "true"), (False, "false"), (7, "7"), (1.5, "1.5"), (2.0, "2")],
)
def test_primitive_encoding(value, expected):
    assert encode({"k": value}) == f"k: {expected}"


def test_non_finite_numbers_normalize_to_null():
    assert encode({"k": float("nan")}) == "k: null"
    assert encode({"k": float("inf")}) == "k: null"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, "0"),
        (-0.0, "0"),
        (1e-6, "0.000001"),
        (1e-5, "0.00001"),
        (1.5e-5, "0.000015"),
        (-2.5e-5, "-0.000025"),
        (0.0001, "0.0001"),
        (0.1 + 0.2, "0.30000000000000004"),
        (1e15, "1000000000000000"),
        (1e16, "10000000000000000"),
        (1e20, "100000000000000000000"),
        (1.2345678901234568e17, "123456789012345680"),
        (-1e18, "-1000000000000000000"),
    ],
)
def test_canonical_decimal_form_is_used_across_the_required_range(value, expected):
    """Section 2: 0 and 1e-6 <= |n| < 1e21 MUST carry no exponent.

    Python's float repr leaves that range at both ends, so the two bands either
    side of it -- a current reading in amps, a byte counter -- are exactly where
    a repr-based encoder stops conforming.
    """
    assert encode({"k": value}) == f"k: {expected}"
    assert float(expected) == value, "the emitted form must still round-trip"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1e21, "1e+21"), (5e-7, "5e-07"), (1e-300, "1e-300"), (-1.5e22, "-1.5e+22")],
)
def test_exponent_notation_is_kept_for_magnitudes_outside_that_range(value, expected):
    """Section 2 permits an exponent there, and recommends this exact spelling."""
    assert encode({"k": value}) == f"k: {expected}"


def test_a_small_sensor_reading_survives_the_document_boundary():
    """The reachable case: an attribute in either band, as `state get --full` prints it."""
    doc = {
        "entity_id": "sensor.example_current",
        "attributes": {"current": 1.5e-05, "lifetime_bytes": 1.8e16},
    }
    assert encode(doc) == (
        "entity_id: sensor.example_current\n"
        "attributes:\n"
        "  current: 0.000015\n"
        "  lifetime_bytes: 18000000000000000"
    )


def test_keys_are_quoted_only_when_required():
    assert encode({"ok_key.dotted": 1}) == "ok_key.dotted: 1"
    assert encode({"needs-quote": 1}) == '"needs-quote": 1'
    assert encode({"9lead": 1}) == '"9lead": 1'


def test_array_header_keys_are_quoted_too():
    assert encode({"my-key": [1, 2]}) == '"my-key"[2]: 1,2'


def test_root_forms():
    assert encode([{"a": 1}, {"a": 2}]) == "[2]{a}:\n  1\n  2"
    assert encode({}) == ""
    assert encode("hello") == "hello"


def test_column_mixing_null_and_object_disqualifies_tabular_form():
    doc = {"rows": [{"a": None}, {"a": {"b": 1}}]}
    assert encode(doc) == "rows[2]:\n  - a: null\n  - a:\n      b: 1"


def test_delimiter_declaration_changes_quoting():
    doc = {"ids": ["a,b", "c|d"]}
    assert encode(doc, delimiter="|") == 'ids[2|]: a,b|"c|d"'


def test_unsupported_delimiter_is_rejected():
    with pytest.raises(ValueError, match="delimiter"):
        encode({"a": 1}, delimiter=";")

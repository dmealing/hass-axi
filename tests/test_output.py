"""The output boundary: redaction, render modes and truncation."""

from __future__ import annotations

from conftest import synthetic_jwt
from hass_axi import output
from hass_axi.output import MODE_HUMAN, MODE_JSON, HelpBlock, redact, register_secret, render

# Built at run time; see conftest.synthetic_jwt for why no literal appears here.
FAKE_JWT = synthetic_jwt()


def test_a_registered_token_never_survives_rendering():
    register_secret("a-very-secret-value")
    assert "a-very-secret-value" not in redact("token=a-very-secret-value trailing")
    assert output.REDACTED in redact("token=a-very-secret-value")


def test_a_jwt_shape_is_redacted_even_when_it_was_never_registered():
    assert FAKE_JWT not in redact(f"leaked {FAKE_JWT} here")
    assert output.REDACTED in redact(f"leaked {FAKE_JWT} here")


# Assembled at run time so no literal credential shape sits in the source.
FAKE_BEARER = "abcdef" + "123456" + "ghijkl"


def test_a_bearer_header_is_redacted():
    assert FAKE_BEARER not in redact(f"Authorization: Bearer {FAKE_BEARER}")
    assert output.REDACTED in redact(f"Authorization: Bearer {FAKE_BEARER}")


def test_short_values_are_not_registered_as_secrets():
    register_secret("abc")
    assert redact("abc") == "abc"


def test_write_applies_redaction(capsys):
    register_secret("a-very-secret-value")
    output.write({"note": "token a-very-secret-value"})
    assert "a-very-secret-value" not in capsys.readouterr().out


def test_write_text_applies_redaction(capsys):
    output.write_text(f"help {FAKE_JWT}")
    assert FAKE_JWT not in capsys.readouterr().out


def test_help_blocks_render_one_suggestion_per_line():
    doc = {"count": "1", "help": HelpBlock(["Run `x list, now`", "Run `y`"])}
    assert render(doc) == 'count: "1"\nhelp[2]:\n  Run `x list, now`\n  Run `y`'


def test_empty_help_blocks_are_omitted():
    assert render({"a": 1, "help": HelpBlock([])}) == "a: 1"


def test_help_blocks_become_plain_lists_in_json_mode():
    import json

    doc = {"help": HelpBlock(["one", "two"])}
    assert json.loads(render(doc, MODE_JSON)) == {"help": ["one", "two"]}


def test_human_mode_renders_an_aligned_table():
    doc = {"rows": [{"id": "1", "name": "Example Lamp"}, {"id": "22", "name": "Hall"}]}
    lines = render(doc, MODE_HUMAN).splitlines()
    assert lines[0] == "rows:"
    assert lines[1].split() == ["id", "name"]
    assert lines[3].startswith("  1 ")


def test_truncate_reports_the_withheld_size():
    text, hint = output.truncate("x" * 100, 10, "Run with --full")
    assert text.startswith("x" * 10)
    assert "100 chars total" in text
    assert hint == "Run with --full"


def test_truncate_leaves_short_values_alone():
    text, hint = output.truncate("short", 10, "Run with --full")
    assert (text, hint) == ("short", "")

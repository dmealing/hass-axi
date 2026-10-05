"""A pattern that judges a whole string must not end in ``$``.

In Python ``$`` matches at the end of the string *and* just before a trailing newline,
so ``^[0-9]+$`` accepts ``"123\\n"`` and stops one character short of it. The shared
encoder shipped that -- a key ending in a newline passed as a bare key, was written
unquoted, and the document read back as a different one -- and seven patterns in this
package carried the same anchor when this file was written: an entity id, an icon, an
age, the label and date a session record is read back through, and two that read a
line of a configuration file. ``\\Z`` matches at
the end and nowhere else, and ``re.fullmatch`` needs no anchor at all.

This is the same instrument the shared toolkit holds its own patterns to, pointed at
this package. Three things are stated, and they are different instruments on purpose:

- **No pattern in the package uses ``$`` outside multiline mode.** Each pattern is
  handed to the regular-expression parser and the parsed form is searched for the
  end-of-string-or-before-newline anchor, so ``\\$`` and ``[$]`` are not mistaken for
  one and a ``$`` in the middle of an alternation is not missed.
- **Every pattern anchored at the start has an example here, and neither the example
  nor the example with a newline appended matches short of the end.** That is the
  behaviour the first rule exists to protect, stated as behaviour, with fixed inputs:
  a randomised test finds a trailing newline only when it happens to draw one.
- **The functions built on those patterns refuse the newline, or keep it on purpose.**

The first rule reads source, which is a weak instrument on its own: a behaviour-
preserving rewrite can move the text. It is here because the failure it prevents is a
single character that reads as correct, in a file nobody has a reason to open, and the
other two rules only cover the patterns somebody remembered to give an example.
"""

from __future__ import annotations

import ast
import importlib
import json
import pkgutil
import re
from pathlib import Path

import pytest

import hass_axi
from hass_axi import sessionlog
from hass_axi.commands import _common, _window
from hass_axi.errors import UsageError
from hass_axi.toolkit import shapes

try:  # Python 3.11 moved the parser under ``re``; the old names warn and then go.
    from re import _constants as sre_constants
    from re import _parser as sre_parse
except ImportError:  # pragma: no cover - the 3.9 and 3.10 spelling
    import sre_constants
    import sre_parse

PACKAGE_ROOT = Path(hass_axi.__file__).resolve().parent

#: Every ``re`` function that takes a pattern first, and where its flags sit when they
#: are passed by position.
_FLAGS_POSITION = {
    "compile": 1,
    "match": 2,
    "fullmatch": 2,
    "search": 2,
    "findall": 2,
    "finditer": 2,
    "split": 3,
    "sub": 4,
    "subn": 4,
}


# ------------------------------------------------------------- reading a pattern


def _nodes(node):
    """Every ``(op, argument)`` pair in a parsed pattern, however deeply nested."""
    if isinstance(node, sre_parse.SubPattern):
        for op, argument in node.data:
            yield op, argument
            yield from _nodes(argument)
    elif isinstance(node, (tuple, list)):
        for child in node:
            yield from _nodes(child)


def dollar_anchors(source: str, flags: int = 0) -> int:
    """How many ``$`` anchors a pattern holds that can match before a trailing newline.

    Zero in multiline mode, where ``$`` means end of line and is the right spelling.
    """
    parsed = sre_parse.parse(source, flags)
    if parsed.state.flags & re.MULTILINE:
        return 0
    return sum(
        1
        for op, argument in _nodes(parsed)
        if op is sre_constants.AT and argument is sre_constants.AT_END
    )


def anchored_at_the_start(source: str, flags: int = 0) -> bool:
    data = sre_parse.parse(source, flags).data
    return (
        bool(data)
        and data[0][0] is sre_constants.AT
        and data[0][1]
        in (
            sre_constants.AT_BEGINNING,
            sre_constants.AT_BEGINNING_STRING,
        )
    )


# ---------------------------------------------------------- finding the patterns


def _flags(call: ast.Call) -> int:
    position = _FLAGS_POSITION[call.func.attr]
    node = next((keyword.value for keyword in call.keywords if keyword.arg == "flags"), None)
    if node is None and len(call.args) > position:
        node = call.args[position]
    if node is None:
        return 0
    return int(eval(compile(ast.Expression(node), "<flags>", "eval"), {"re": re}))


def written_patterns() -> list[tuple[str, str, int]]:
    """Every pattern written as a literal in the package: where, its source, its flags."""
    found = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "re"
                and node.func.attr in _FLAGS_POSITION
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                continue
            where = f"{path.relative_to(PACKAGE_ROOT).as_posix()}:{node.lineno}"
            found.append((where, node.args[0].value, _flags(node)))
    return found


def compiled_patterns() -> list[tuple[str, re.Pattern]]:
    """Every compiled pattern a module of the package holds once it is imported."""
    found = []
    modules = pkgutil.walk_packages(hass_axi.__path__, hass_axi.__name__ + ".")
    for name in ["hass_axi", *(info.name for info in modules)]:
        module = importlib.import_module(name)
        for attribute, value in vars(module).items():
            if isinstance(value, re.Pattern) and getattr(module, "__name__", "") == name:
                found.append((f"{name}.{attribute}", value))
    return found


# ---------------------------------------------------------- the rule on the text


@pytest.mark.parametrize(
    ("source", "flags", "expected"),
    [
        (r"^[0-9]+$", 0, 1),
        (r"^(mon|[smhdwy])$", re.IGNORECASE, 1),
        (r"^a$|^b\Z", 0, 1),
        (r"^(?:a$|b$)", 0, 2),
        (r"^[0-9]+\Z", 0, 0),
        (r"^price: \$[0-9]+\Z", 0, 0),
        (r"^[$][0-9]+\Z", 0, 0),
        (r"^line$", re.MULTILINE, 0),
        (r"(?m)^line$", 0, 0),
        (r"[0-9]+", 0, 0),
    ],
)
def test_the_reader_tells_a_dollar_anchor_from_everything_that_looks_like_one(
    source, flags, expected
):
    """A check that has never failed is not yet a check: these are the ones it must."""
    assert dollar_anchors(source, flags) == expected


def test_no_pattern_in_the_package_can_stop_before_a_trailing_newline():
    offenders = [
        f"{where}: {source!r}"
        for where, source, flags in written_patterns()
        if dollar_anchors(source, flags)
    ]
    assert not offenders, (
        "`$` also matches just before a trailing newline; end the pattern in `\\Z`, "
        "or drop the anchors and call `fullmatch`:\n  " + "\n  ".join(offenders)
    )


def test_every_compiled_pattern_is_one_the_rule_above_read():
    """The rule reads ``re.<function>("literal")`` calls, so that is what must exist.

    A pattern built some other way -- an aliased import, a concatenation, a helper --
    would be compiled and never read. This is the other half: every pattern a module
    actually holds has to be one the reader found.
    """
    read = {source for _, source, _ in written_patterns()}
    unread = [name for name, pattern in compiled_patterns() if pattern.pattern not in read]
    assert not unread, f"compiled, but not written where the rule can read it: {unread}"


# ------------------------------------------------------- the rule as behaviour

#: Every compiled pattern in the package that is anchored at the start, with strings
#: it accepts. Fixed inputs, not generated ones.
VALIDATORS = [
    (shapes._ENTITY_ID, "light.example_lamp"),
    (_common._ICON, "mdi:lamp"),
    (_window._AGE, "24h"),
    (_window._AGE, " 1.5 D "),
    (sessionlog._LABEL, "state"),
    (sessionlog._LABEL, "entity update"),
    (sessionlog._DATE, "2024-01-31"),
]

#: Patterns written inline at their one call site, so there is no compiled object to
#: put in the table above. Each judges one line of a file already split on its
#: newlines; the example is such a line.
INLINE = [
    (r"^\s*(\[{1,2})([^\]]+)(\]{1,2})\s*(?:#.*)?\Z", "[features]  # a comment"),
    (r"^\s*hooks\s*=\s*true\s*(?:#.*)?\Z", "hooks = true"),
]

#: Anchored at the start and deliberately not at the end: these find a prefix, so
#: there is no whole-string verdict for a trailing newline to slip past.
PREFIXES = {r"^\s*hooks\s*="}


@pytest.mark.parametrize(
    ("pattern", "accepted"),
    VALIDATORS + [(re.compile(source), accepted) for source, accepted in INLINE],
)
def test_a_match_never_stops_short_of_the_end_of_the_string(pattern, accepted):
    """Accept the whole string or refuse it; never accept all of it but the newline."""
    whole = pattern.match(accepted)
    assert whole is not None and whole.end() == len(accepted)
    with_newline = pattern.match(accepted + "\n")
    assert with_newline is None or with_newline.end() == len(accepted) + 1


def test_every_pattern_anchored_at_the_start_has_an_example_above():
    """A new validator arrives with an example or this fails, so the table stays whole."""
    exemplified = {pattern.pattern for pattern, _ in VALIDATORS}
    exemplified |= {source for source, _ in INLINE} | PREFIXES
    missing = [
        f"{where}: {source!r}"
        for where, source, flags in written_patterns()
        if anchored_at_the_start(source, flags) and source not in exemplified
    ]
    assert not missing, "anchored at the start, with no example above:\n  " + "\n  ".join(missing)


# ------------------------------------------------- the functions built on them
#
# One explicit trailing-newline example for each pattern that carried the anchor.


@pytest.mark.parametrize(
    ("pattern", "accepted"),
    [
        (shapes._ENTITY_ID, "light.example_lamp"),
        (_common._ICON, "mdi:lamp"),
        (sessionlog._LABEL, "state"),
        (sessionlog._LABEL, "entity update"),
        (sessionlog._DATE, "2024-01-31"),
    ],
)
def test_a_validator_refuses_the_same_string_with_a_newline_after_it(pattern, accepted):
    assert pattern.match(accepted)
    assert pattern.match(accepted + "\n") is None


def test_an_entity_id_ending_in_a_newline_is_not_an_entity_id():
    assert shapes.is_entity_id("light.example_lamp")
    assert not shapes.is_entity_id("light.example_lamp\n")


def test_an_icon_ending_in_a_newline_is_refused_before_it_is_sent():
    _common.check_icon("mdi:lamp")
    with pytest.raises(UsageError) as refused:
        _common.check_icon("mdi:lamp\n")
    assert refused.value.code == "BAD_ICON"


def test_an_age_still_tolerates_the_whitespace_around_it_and_nothing_after():
    """The one pattern that ends in optional whitespace, so a newline is its to consume.

    What the anchor changes here is only that the match now reaches the end of the
    string in every case, instead of being allowed to stop one character short of it.
    """
    for raw in ("24h", "24h\n", " 24 h \n"):
        match = _window._AGE.match(raw)
        assert match is not None and match.end() == len(raw), raw
    assert _window._AGE.match("24h\nx") is None


@pytest.mark.parametrize(
    "entry",
    [
        {"commands": {"state\n": 2}, "ended": "2024-01-31"},
        {"commands": {"entity update\n": 2}, "ended": "2024-01-31"},
        {"commands": {"state": 2}, "ended": "2024-01-31\n"},
    ],
    ids=["label", "two-word label", "date"],
)
def test_a_session_record_with_a_newline_in_its_label_or_date_is_not_read_back(entry, tmp_path):
    """The record is a file anything on the machine could have edited, read strictly.

    Its label and date are printed into the document a session hook hands an agent, so
    one that carries a newline must not pass as the well-formed thing it starts with.
    """
    environ = {"XDG_STATE_HOME": str(tmp_path)}
    path = sessionlog.state_path(environ)
    path.parent.mkdir(parents=True)

    clean = {"cwd": "/work", "commands": {"state": 2}, "ended": "2024-01-31"}
    path.write_text(json.dumps([clean]), encoding="utf-8")
    assert sessionlog.last_session("/work", environ) == "2024-01-31 ran state x2 with nothing sent"

    path.write_text(json.dumps([{"cwd": "/work", **entry}]), encoding="utf-8")
    assert sessionlog.last_session("/work", environ) == ""

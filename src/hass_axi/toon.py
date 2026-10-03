"""A strict TOON (Token-Oriented Object Notation) encoder.

Implements the encoder half of the TOON specification v4.1
(https://github.com/toon-format/spec) for the JSON data model: objects,
primitive arrays in inline form, arrays of uniform objects in tabular form,
objects of uniform objects in keyed tabular form, and list form for everything
else.

TOON is the wire format every AXI CLI prints on stdout; encoding happens at the
output boundary only -- internal command logic stays on plain JSON-shaped dicts.
"""

from __future__ import annotations

import json
import math
import re
from decimal import Decimal
from typing import Any

__all__ = ["TOON_DELIMITERS", "encode", "encode_scalar"]

# Delimiter symbol as it appears inside a bracket segment. Comma is the default
# and is spelled by omitting the symbol entirely (spec section 6).
TOON_DELIMITERS = {",": "", "\t": "\t", "|": "|"}

# Section 7.2: a string that would otherwise be read back as a number.
_NUMERIC_LIKE = re.compile(r"^[+-]?[0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?$", re.IGNORECASE)

# Section 7.3: keys and header field names safe to emit unquoted.
_BARE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")

_CONTROL = re.compile(r"[\x00-\x1f]")

_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}

_STRUCTURAL = set(':"\\[]{}')

# Section 2: canonical decimal form is REQUIRED for 0 and for this magnitude
# range. Outside it, exponent notation is permitted.
_CANONICAL_MIN = 1e-6
_CANONICAL_MAX = 1e21


def encode(value: Any, *, indent: int = 2, delimiter: str = ",") -> str:
    """Encode a JSON-shaped value as a TOON document.

    ``delimiter`` becomes the document delimiter and is declared on every header
    this encoder emits (spec section 11.1).
    """
    if delimiter not in TOON_DELIMITERS:
        raise ValueError(f"unsupported TOON delimiter: {delimiter!r}")
    enc = _Encoder(indent=indent, delimiter=delimiter)
    return "\n".join(enc.root(value))


def encode_scalar(value: Any, *, delimiter: str = ",") -> str:
    """Encode a single primitive the way it would appear as a field value."""
    return _Encoder(indent=2, delimiter=delimiter).primitive(value)


def _is_primitive(value: Any) -> bool:
    return value is None or isinstance(value, (str, bool, int, float))


class _Encoder:
    def __init__(self, *, indent: int, delimiter: str) -> None:
        self.indent = indent
        self.delim = delimiter
        self.delim_sym = TOON_DELIMITERS[delimiter]

    # ---------------------------------------------------------------- scalars

    def primitive(self, value: Any) -> str:
        """Encode a primitive, quoting per spec section 7.2 when required."""
        if value is None:
            return "null"
        if value is True:
            return "true"
        if value is False:
            return "false"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return self._number(value)
        if isinstance(value, str):
            return self._string(value)
        # Section 3: unrecognized host values normalize to null.
        return "null"

    def _number(self, value: float) -> str:
        """Encode a float in the form section 2 requires for its magnitude.

        Python's float repr switches to exponent notation outside roughly
        [1e-4, 1e16), which is narrower than the range the spec makes canonical
        at both ends, so ``json.dumps`` alone emits ``1e-06`` and ``1e+16``
        where the spec demands ``0.000001`` and ``10000000000000000``.
        """
        # Section 3: non-finite numbers normalize to null.
        if math.isnan(value) or math.isinf(value):
            return "null"
        if value == 0 or _CANONICAL_MIN <= abs(value) < _CANONICAL_MAX:
            # Canonical decimal form: no exponent, no trailing fractional
            # zeros, an integral value as an integer, and -0 as 0.
            if value == int(value):
                return str(int(value))
            # Decimal(repr(value)) reads the shortest round-tripping digits
            # Python already computed; Decimal(value) would expand the exact
            # binary value into a fifty-digit fraction instead.
            text = format(Decimal(repr(value)), "f")
            if "." in text:
                # A guard, not a fix-up: the shortest round-tripping digits
                # never end in a fractional zero. Section 2 states the rule, so
                # this states it too rather than resting on that.
                text = text.rstrip("0").rstrip(".")
            return text
        # Outside the canonical range section 2 permits exponent notation, and
        # json.dumps emits the lowercase `e` and explicit sign it recommends.
        return json.dumps(value)

    def _string(self, value: str) -> str:
        if self._needs_quote(value):
            return self.quote(value)
        return value

    def _needs_quote(self, value: str) -> bool:
        if value == "":
            return True
        if value != value.strip(" \t"):
            return True
        if value in ("true", "false", "null"):
            return True
        if _NUMERIC_LIKE.match(value):
            return True
        if any(ch in value for ch in _STRUCTURAL):
            return True
        if _CONTROL.search(value):
            return True
        if self.delim in value:
            return True
        return bool(value.startswith("-") or value.startswith("#"))

    @staticmethod
    def quote(value: str) -> str:
        out = ['"']
        for ch in value:
            esc = _ESCAPES.get(ch)
            if esc is not None:
                out.append(esc)
            elif ch < "\x20":
                out.append(f"\\u{ord(ch):04x}")
            else:
                out.append(ch)
        out.append('"')
        return "".join(out)

    def key(self, name: str) -> str:
        """Encode an object key or header field name (spec section 7.3)."""
        if _BARE_KEY.match(name):
            return name
        return self.quote(name)

    # ---------------------------------------------------------------- helpers

    def pad(self, depth: int) -> str:
        return " " * (self.indent * depth)

    def bracket(self, length: int, *, keyed: bool = False) -> str:
        marker = f"{length}:" if keyed else str(length)
        return f"[{marker}{self.delim_sym}]"

    def field_list(self, fields: list[tuple[str, list | None]]) -> str:
        parts = []
        for name, nested in fields:
            if nested is None:
                parts.append(self.key(name))
            else:
                parts.append(f"{self.key(name)}{{{self.field_list(nested)}}}")
        return self.delim.join(parts)

    # ------------------------------------------------------- shape detection

    def tabular_fields(self, items: list[Any]) -> list[tuple[str, list | None]] | None:
        """Return the header field entries if ``items`` is a uniform object list.

        Doubles as the nested-uniform column test of spec section 9.3: both ask
        whether a sequence of values is a list of non-empty objects sharing one
        key set whose every column is uniform-primitive or nested-uniform.
        """
        if not items:
            return None
        if not all(isinstance(item, dict) and item for item in items):
            return None
        first = items[0]
        keyset = set(first)
        if any(set(item) != keyset for item in items):
            return None
        fields: list[tuple[str, list | None]] = []
        for name in first:
            column = [item[name] for item in items]
            if all(_is_primitive(v) for v in column):
                fields.append((name, None))
                continue
            nested = self.tabular_fields(column)
            if nested is None:
                return None
            fields.append((name, nested))
        return fields

    def keyed_fields(self, obj: dict) -> list[tuple[str, list | None]] | None:
        """Return header field entries if ``obj`` qualifies for keyed tabular form."""
        if len(obj) < 2:
            return None
        return self.tabular_fields(list(obj.values()))

    def cells(self, obj: dict, fields: list[tuple[str, list | None]]) -> list[str]:
        """Flatten one object into row cells, depth-first over the field list."""
        out: list[str] = []
        for name, nested in fields:
            if nested is None:
                out.append(self.primitive(obj[name]))
            else:
                out.extend(self.cells(obj[name], nested))
        return out

    def row(self, obj: dict, fields: list[tuple[str, list | None]]) -> str:
        return self.delim.join(self.cells(obj, fields))

    # ------------------------------------------------------------- root form

    def root(self, value: Any) -> list[str]:
        if isinstance(value, dict):
            if not value:
                return []
            keyed = self.keyed_fields(value)
            if keyed is not None:
                head = f"{self.bracket(len(value), keyed=True)}{{{self.field_list(keyed)}}}:"
                lines = [head]
                for entry_key, entry in value.items():
                    lines.append(f"{self.pad(1)}{self.key(entry_key)}: {self.row(entry, keyed)}")
                return lines
            return self.object_body(value, 0)
        if isinstance(value, list):
            return self.array(None, value, 0)
        return [self.primitive(value)]

    # --------------------------------------------------------------- objects

    def object_body(self, obj: dict, depth: int) -> list[str]:
        lines: list[str] = []
        for name, value in obj.items():
            lines.extend(self.field(name, value, depth))
        return lines

    def field(self, name: str, value: Any, depth: int) -> list[str]:
        """Encode one ``key: value`` object field and everything it opens."""
        pad = self.pad(depth)
        key = self.key(name)
        if isinstance(value, list):
            return self.array(key, value, depth)
        if isinstance(value, dict):
            if not value:
                return [f"{pad}{key}:"]
            keyed = self.keyed_fields(value)
            if keyed is not None:
                head = (
                    f"{pad}{key}{self.bracket(len(value), keyed=True)}{{{self.field_list(keyed)}}}:"
                )
                lines = [head]
                for entry_key, entry in value.items():
                    lines.append(
                        f"{self.pad(depth + 1)}{self.key(entry_key)}: {self.row(entry, keyed)}"
                    )
                return lines
            return [f"{pad}{key}:", *self.object_body(value, depth + 1)]
        return [f"{pad}{key}: {self.primitive(value)}"]

    # ---------------------------------------------------------------- arrays

    def array(
        self, key: str | None, items: list, depth: int, *, allow_tabular: bool = True
    ) -> list[str]:
        """Encode an array in whichever of the three array forms its shape requires.

        ``allow_tabular`` is false in list-item position: a tabular header is a
        keyless fields-bearing header there, which section 6 allows only at the
        document root, so section 9.4 requires list form however uniform the
        items are.
        """
        pad = self.pad(depth)
        prefix = key or ""
        if not items:
            # Section 9.1: empty arrays never use a header.
            return [f"{pad}{prefix}: []"] if key else [f"{pad}[]"]

        if all(_is_primitive(item) for item in items):
            values = self.delim.join(self.primitive(item) for item in items)
            return [f"{pad}{prefix}{self.bracket(len(items))}: {values}"]

        fields = self.tabular_fields(items) if allow_tabular else None
        if fields is not None:
            head = f"{pad}{prefix}{self.bracket(len(items))}{{{self.field_list(fields)}}}:"
            return [head, *(f"{self.pad(depth + 1)}{self.row(item, fields)}" for item in items)]

        lines = [f"{pad}{prefix}{self.bracket(len(items))}:"]
        for item in items:
            lines.extend(self.list_item(item, depth + 1))
        return lines

    def list_item(self, value: Any, depth: int) -> list[str]:
        """Encode one ``- `` list item (spec sections 9.4 and 10)."""
        pad = self.pad(depth)
        if _is_primitive(value):
            return [f"{pad}- {self.primitive(value)}"]

        if isinstance(value, list):
            if not value:
                # Section 9.2: the bare `- []` item form is decode-only.
                return [f"{pad}- {self.bracket(0)}:"]
            # A keyless header on a hyphen line is the item itself, so it stands
            # at this depth and its own content sits one deeper (section 10).
            return self._hyphenate(
                self.array(None, value, depth, allow_tabular=False), depth, depth
            )

        if isinstance(value, dict):
            if not value:
                return [f"{pad}-"]
            names = list(value)
            first, rest = names[0], names[1:]
            # A keyed first field carried on the hyphen line stands at depth + 1,
            # so anything it opens lands at depth + 2 (spec section 10).
            lines = self._hyphenate(self.field(first, value[first], depth + 1), depth, depth + 1)
            for name in rest:
                lines.extend(self.field(name, value[name], depth + 1))
            return lines

        return [f"{pad}- {self.primitive(value)}"]

    def _hyphenate(self, lines: list[str], marker_depth: int, head_depth: int) -> list[str]:
        """Move the first of ``lines`` onto a ``- `` marker at ``marker_depth``.

        ``head_depth`` is the depth the head line was rendered at, which differs
        between the two cases section 10 distinguishes: a keyed first field of a
        list-item object is rendered one level deeper than its marker, while a
        keyless array header on a hyphen line stands at the marker's own depth.
        """
        head, *tail = lines
        return [f"{self.pad(marker_depth)}- {head[len(self.pad(head_depth)) :]}", *tail]

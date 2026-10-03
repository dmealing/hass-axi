"""The output boundary: redaction, render modes, and the AXI document shapes.

Everything the CLI prints passes through :func:`write`, which is the single
place a credential could escape and therefore the single place redaction has to
hold. Command modules build ordinary dicts; conversion to TOON happens here.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

from .toon import encode

#: Placeholder substituted for anything that looks like a credential.
REDACTED = "<redacted>"

MODE_TOON = "toon"
MODE_HUMAN = "human"
MODE_JSON = "json"

# A Home Assistant long-lived access token is a JWT: three base64url segments,
# the first of which starts `eyJ` because it encodes a JSON object.
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}")
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")

_secrets: set = set()


#: Below this length, redacting a literal does more damage than good: a short
#: string collides with ordinary words and would corrupt unrelated output.
MIN_SECRET_LENGTH = 8


def register_secret(value: str | None, *, min_length: int = MIN_SECRET_LENGTH) -> None:
    """Register a literal string that must never reach stdout or stderr.

    ``min_length`` is lowered only for values already known to be credentials
    by where they came from -- URL userinfo, say -- rather than by their shape.
    """
    if value and len(value) >= min_length:
        _secrets.add(value)


def reset_secrets() -> None:
    """Drop registered secrets. Used by tests to isolate cases."""
    _secrets.clear()


def redact(text: str) -> str:
    """Remove credentials from ``text`` by literal match and by shape."""
    # Longest first, so an overlapping pair (`user:password`) is replaced whole
    # rather than leaving a half-redacted fragment behind.
    for secret in sorted(_secrets, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    text = _BEARER.sub(lambda m: m.group(1) + REDACTED, text)
    return _JWT.sub(REDACTED, text)


class HelpBlock:
    """A ``help[N]:`` block of contextual next steps.

    The AXI standard renders these one suggestion per line under the header
    rather than as a delimiter-joined TOON primitive array, because the
    suggestions are command lines that routinely contain commas. Data payloads
    are strict TOON; this block follows the AXI standard's own shape so the
    output matches the sibling AXI CLIs. See README, "Output format".
    """

    __slots__ = ("lines",)

    def __init__(self, lines) -> None:
        self.lines = [line for line in lines if line]

    def __bool__(self) -> bool:
        return bool(self.lines)

    def __iter__(self):
        return iter(self.lines)


def truncate(text: str, limit: int, hint: str) -> tuple:
    """Shorten ``text`` to ``limit`` characters, reporting what was withheld.

    Returns the possibly-shortened text and a help line, empty when the whole
    value fits. Large fields are previewed rather than dropped so the agent can
    tell whether fetching the rest is worth a second call.
    """
    if len(text) <= limit:
        return text, ""
    return f"{text[:limit]}... (truncated, {len(text)} chars total)", hint


def _flatten(doc) -> Any:
    """Replace HelpBlock values with plain lists for JSON rendering."""
    if isinstance(doc, HelpBlock):
        return list(doc.lines)
    if isinstance(doc, dict):
        return {k: _flatten(v) for k, v in doc.items()}
    if isinstance(doc, list):
        return [_flatten(v) for v in doc]
    return doc


def render(doc, mode: str = MODE_TOON) -> str:
    if mode == MODE_JSON:
        return json.dumps(_flatten(doc), indent=2, ensure_ascii=False)
    if mode == MODE_HUMAN:
        return _render_human(doc)
    return _render_toon(doc)


def _render_toon(doc) -> str:
    if not isinstance(doc, dict):
        return encode(doc)
    chunks = []
    plain: dict = {}

    def flush():
        if plain:
            chunks.append(encode(plain))
            plain.clear()

    for key, value in doc.items():
        if isinstance(value, HelpBlock):
            flush()
            if value:
                chunks.append(
                    "\n".join([f"{key}[{len(value.lines)}]:", *(f"  {ln}" for ln in value.lines)])
                )
        else:
            plain[key] = value
    flush()
    return "\n".join(c for c in chunks if c)


def _render_human(doc, depth: int = 0) -> str:
    pad = "  " * depth
    if isinstance(doc, HelpBlock):
        return "\n".join(f"{pad}* {line}" for line in doc.lines)
    if isinstance(doc, dict):
        out = []
        for key, value in doc.items():
            if isinstance(value, HelpBlock):
                if value:
                    out.append(f"{pad}{key}:")
                    out.append(_render_human(value, depth + 1))
            elif isinstance(value, (dict, list)) and value:
                out.append(f"{pad}{key}:")
                out.append(_render_human(value, depth + 1))
            elif isinstance(value, (dict, list)):
                out.append(f"{pad}{key}: (none)")
            else:
                out.append(f"{pad}{key}: {_scalar(value)}")
        return "\n".join(out)
    if isinstance(doc, list):
        if doc and all(isinstance(item, dict) for item in doc):
            return _render_table(doc, pad)
        return "\n".join(f"{pad}- {_render_human(item, 0).strip()}" for item in doc)
    return f"{pad}{_scalar(doc)}"


def _render_table(rows, pad: str) -> str:
    columns: list = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    cells = [[_scalar(row.get(col, "")) for col in columns] for row in rows]
    widths = [
        max(len(col), *(len(row[i]) for row in cells)) if cells else len(col)
        for i, col in enumerate(columns)
    ]
    out = [pad + "  ".join(col.ljust(widths[i]) for i, col in enumerate(columns)).rstrip()]
    out.append(pad + "  ".join("-" * w for w in widths).rstrip())
    for row in cells:
        out.append(pad + "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return "\n".join(out)


def _scalar(value) -> str:
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def write(doc, mode: str = MODE_TOON, stream=None) -> None:
    """Render, redact and print a document on stdout."""
    stream = sys.stdout if stream is None else stream
    text = redact(render(doc, mode))
    if text:
        stream.write(text + "\n")
    stream.flush()


def write_text(text: str, stream=None) -> None:
    """Print already-rendered text (help and usage) through the same redactor."""
    stream = sys.stdout if stream is None else stream
    stream.write(redact(text) + "\n")
    stream.flush()


_debug_enabled = False


def set_debug(enabled: bool) -> None:
    """Turn stderr diagnostics on for this process."""
    global _debug_enabled
    _debug_enabled = bool(enabled)


def debug_enabled() -> bool:
    if _debug_enabled or os.environ.get("HASS_AXI_DEBUG"):
        return True
    if os.environ.get("HA_AXI_DEBUG"):
        deprecated_variable("HA_AXI_DEBUG", "HASS_AXI_DEBUG")
        return True
    return False


#: Deprecation notices already printed by this process, so a variable read on
#: every request is announced once rather than once per request.
_announced: set = set()


def deprecated_variable(old: str, new: str) -> None:
    """Announce, once per process and on stderr, that ``old`` is a retired name.

    The variables this tool reads were renamed with it, from ``HA_AXI_*`` to
    ``HASS_AXI_*``. The old names keep working -- an installation must not stop
    behaving the way its operator configured it because a package was renamed
    -- and stderr is where the notice goes because stdout is the document an
    agent parses, which a deprecation is not part of.
    """
    if old in _announced:
        return
    _announced.add(old)
    sys.stderr.write(redact(f"hass-axi: {old} is deprecated; rename it to {new}") + "\n")
    sys.stderr.flush()


def reset_notices() -> None:
    """Forget which notices were printed. Used by tests to isolate cases."""
    _announced.clear()


def debug(message: str) -> None:
    """Emit a diagnostic on stderr, which agents do not read.

    Redacted like stdout: stderr is not a safe channel for a credential just
    because agents ignore it -- it still reaches terminals, logs and CI output.
    """
    if debug_enabled():
        sys.stderr.write(redact(f"hass-axi: {message}") + "\n")
        sys.stderr.flush()


def debug_exception(exc: BaseException) -> None:
    """Write a redacted traceback for an unexpected error."""
    if debug_enabled():
        import traceback

        trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        sys.stderr.write(redact(trace))
        sys.stderr.flush()

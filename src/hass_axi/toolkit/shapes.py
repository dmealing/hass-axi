"""What a Home Assistant answer has to look like, as predicates over plain values.

Pure rules: no request is made here, nothing is printed and no error type is
raised, so a client that reads Home Assistant some other way can ask the same
questions. The transport asks them and decides what to raise.

They exist because "the server answered 200" is not "this is Home Assistant".
A captive portal, a proxy's own error page and another application on the same
port all answer 200, and a client that stops at the status reports the first as
a healthy installation and the second as one with no entities.
"""

from __future__ import annotations

import re

_JSON = "application/json"

#: Content types that are text whatever they are called. Anything else that is
#: not JSON is treated as binary and never decoded onto a terminal.
_TEXT_TYPES = ("text/", _JSON, "application/xml", "application/yaml", "application/x-yaml")


def is_text(content_type: str, raw: bytes) -> bool:
    """Whether a body can be shown as text.

    The declared type decides when there is one; a body with none is text only
    if it decodes, so a server that mislabels nothing and labels nothing is
    still read correctly.
    """
    kind = content_type.split(";", 1)[0].strip().lower()
    if kind:
        return kind.startswith(_TEXT_TYPES) or kind.endswith(("+json", "+xml"))
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


#: `domain.object_id`, the only shape an entity id has. Case is not checked:
#: Home Assistant folds it.
_ENTITY_ID = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+\Z")


def is_entity_id(value: str) -> bool:
    return bool(_ENTITY_ID.match(value or ""))


def describe(value) -> str:
    """What a decoded response turned out to be, in words."""
    if value is None or value == "":
        return "an empty response"
    if isinstance(value, str):
        return "text that is not JSON"
    if isinstance(value, dict):
        return "a JSON object"
    if isinstance(value, list):
        return "a JSON list"
    return f"a JSON {type(value).__name__}"


def health_fault(value) -> str | None:
    """Why ``value`` is not what `GET /api/` answers, or ``None`` when it is.

    Home Assistant answers its API root with `{"message": "API running."}`. The
    message text is not compared -- it is prose, and prose changes -- but an
    object carrying a string under `message` is the shape, and anything else
    is something other than Home Assistant.
    """
    if not isinstance(value, dict):
        return describe(value)
    if not isinstance(value.get("message"), str):
        return "a JSON object with no `message`"
    return None


def shape_fault(value, kind: type) -> str | None:
    """Why ``value`` is not the ``kind`` an endpoint always answers with, or ``None``."""
    return None if isinstance(value, kind) else describe(value)

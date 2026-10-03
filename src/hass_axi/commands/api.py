"""`hass-axi api` -- an authenticated escape hatch to any REST path."""

from __future__ import annotations

import json

from ..argspec import Command, Flag, Sub
from ..errors import UsageError
from ..readonly import DYNAMIC, READ, WRITE
from ..rest import SAFE_METHODS, api_path
from ._common import parse_json_flag, parse_pairs

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")

COMMAND = Command(
    name="api",
    summary="Make an authenticated request to any Home Assistant REST path",
    usage="usage: hass-axi api [<method>] <path> [flags]",
    default_sub="api",
    subs=(
        Sub(
            name="api",
            # The path is opaque, so the verdict cannot come from the
            # declaration; `access` below reads the method, which is the only
            # thing the caller has told us.
            access=DYNAMIC,
            args=("<method-or-path>", "[path]"),
            summary="Request a REST path",
            flags=(
                Flag("--field", "<key=value>", repeat=True, note="request body field"),
                Flag("--body", "<object>", note="raw JSON body, merged over --field"),
                Flag("--query", "<key=value>", repeat=True, note="query string parameter"),
            ),
        ),
    ),
    notes=(
        f"methods: {', '.join(METHODS)}; GET is used when no method is given",
        "the registries are not reachable over REST -- use `hass-axi ws` for those",
    ),
    examples=(
        "hass-axi api /config",
        "hass-axi api /states/light.example_lamp",
        "hass-axi api POST /services/light/turn_on --field entity_id=light.example_lamp",
        'hass-axi api POST /template --body \'{"template": "{{ now() }}"}\'',
    ),
)


def access(sub: str, parsed) -> str:
    """The read-only verdict for one raw REST request.

    An arbitrary path carries no classification, so the method is all there is,
    and HTTP defines which methods are safe. Everything else is a write --
    including the POST that renders a template, which is reachable here and is
    genuinely a read: the typed `template render` is the route to it, and
    guessing on this surface is what a fail-closed guard must not do. A
    malformed invocation raises the same usage error it would have raised
    anyway, rather than being reported as a refusal it never got to.
    """
    method, _ = _method_and_path(parsed.positionals)
    return READ if method in SAFE_METHODS else WRITE


def run(ctx, sub: str, parsed):
    method, path = _method_and_path(parsed.positionals)
    body = parse_pairs(parsed.get("field", []), flag="--field")
    body.update(parse_json_flag(parsed.get("body"), flag="--body"))
    query = parse_pairs(parsed.get("query", []), flag="--query")

    result = ctx.rest().request(
        method,
        path,
        body=body if body or method in ("POST", "PUT", "PATCH") else None,
        query={k: _query_value(v) for k, v in query.items()} or None,
    )

    doc = {"request": {"method": method, "path": api_path(path)}}
    if result is None or result == "":
        doc["result"] = f"{method} succeeded with an empty response"
    else:
        doc["result"] = result
    return doc


def _query_value(value) -> str:
    """Render a parsed ``--query`` value as it should appear on the wire.

    ``parse_pairs`` reads values as JSON when they parse, so a boolean or a
    null has to go back to its JSON spelling rather than Python's.
    """
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"))


def _method_and_path(positionals: list):
    values = [value for value in positionals if value is not None]
    if not values:
        raise UsageError(
            "a path is required",
            help_lines=["Run `hass-axi api /config`", "Run `hass-axi api GET /states`"],
            code="MISSING_PATH",
        )
    if values[0].upper() in METHODS:
        if len(values) < 2:
            raise UsageError(
                f"a path is required after {values[0].upper()}",
                help_lines=[
                    "Run `hass-axi api POST /services/light/turn_on --field entity_id=<id>`"
                ],
                code="MISSING_PATH",
            )
        return values[0].upper(), values[1]
    if len(values) > 1:
        raise UsageError(
            f"unexpected argument {values[1]!r}",
            help_lines=[f"methods must come first: `hass-axi api GET {values[0]}`"],
            code="UNEXPECTED_ARGUMENT",
        )
    return "GET", values[0]

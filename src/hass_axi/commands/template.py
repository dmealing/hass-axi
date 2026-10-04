"""`hass-axi template` -- render a Jinja template against live state."""

from __future__ import annotations

import sys
from pathlib import Path

from ..argspec import Command, Flag, Sub
from ..errors import ApiError, UsageError
from ..output import HelpBlock, truncate
from ..readonly import READ
from ._common import PREVIEW_CHARS

COMMAND = Command(
    name="template",
    summary="Render a Home Assistant Jinja template server-side",
    usage="usage: hass-axi template render [flags]",
    default_sub="render",
    subs=(
        Sub(
            name="render",
            # A POST, and still a read: the template sandbox cannot call a
            # service or set a state, which is why `rest.READ_ONLY_POSTS` names
            # the path rather than the transport guessing from the verb.
            access=READ,
            summary="Render a template and print the result",
            flags=(
                Flag("--template", "<text>", free_text=True),
                Flag("--template-file", "<path>", note="use - for stdin"),
                Flag("--full", boolean=True, note="do not truncate the result"),
            ),
        ),
    ),
    notes=(
        "templates run on the Home Assistant instance, so they see every entity it knows about",
    ),
    examples=(
        "hass-axi template render --template '{{ states(\"light.example_lamp\") }}'",
        "hass-axi template render --template '{{ states.light | count }}'",
        "hass-axi template render --template-file report.j2",
        "echo '{{ now() }}' | hass-axi template render --template-file -",
    ),
)


def run(ctx, sub: str, parsed):
    template = _source(parsed)
    try:
        result = ctx.rest().render_template(template)
    except ApiError as exc:
        if exc.code != "BAD_REQUEST":
            raise
        # REST answers a template that does not compile or render with a 400
        # carrying the reason; over the WebSocket the same fault is already
        # `TEMPLATE_ERROR`. One fault, one code, and a next step.
        raise ApiError(
            exc.message.replace("Home Assistant refused the request (HTTP 400)", "template error"),
            help_lines=[
                "Fix the template; the message above is Home Assistant's own",
                "Run `hass-axi template render --template '{{ states(\"light.example_lamp\") }}'` "
                "for a form that works",
            ],
            code="TEMPLATE_ERROR",
        ) from None
    text, hint = ("", "")
    if parsed.get("full", False):
        text = result
    else:
        text, hint = truncate(
            result,
            PREVIEW_CHARS,
            "Run the same command with `--full` to see the complete result",
        )
    doc = {"template": {"result": text, "chars": len(result)}}
    if hint:
        doc["help"] = HelpBlock([hint])
    return doc


def _source(parsed) -> str:
    inline = parsed.get("template")
    path = parsed.get("template_file")
    if inline and path:
        raise UsageError(
            "--template and --template-file are mutually exclusive",
            help_lines=["Run `hass-axi template render --template '{{ now() }}'`"],
            code="CONFLICTING_FLAGS",
        )
    if inline:
        return inline
    if path:
        if path == "-":
            return sys.stdin.read()
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise UsageError(
                f"could not read --template-file {path}: {exc.strerror or exc}",
                help_lines=["Pass a readable path, or use `--template '<text>'`"],
                code="UNREADABLE_FILE",
            ) from None
    raise UsageError(
        "--template or --template-file is required",
        help_lines=[
            "Run `hass-axi template render --template '{{ states(\"light.example_lamp\") }}'`",
            "Run `hass-axi template render --template-file <path>` to read one from disk",
        ],
        code="MISSING_TEMPLATE",
    )

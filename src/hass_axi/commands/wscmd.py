"""`hass-axi ws` -- an escape hatch to any WebSocket command.

Every registry operation the typed commands perform is declared in
:data:`hass_axi.ws.REGISTRY`; this command exposes that table directly, so a
capability Home Assistant adds is reachable before a typed wrapper exists.
"""

from __future__ import annotations

from ..argspec import Command, Flag, Sub
from ..errors import UsageError
from ..output import HelpBlock
from ..readonly import DYNAMIC, READ
from ..ws import REGISTRY, access_for_type
from ._common import parse_json_flag, parse_pairs, plural

COMMAND = Command(
    name="ws",
    summary="Send a command over the Home Assistant WebSocket API",
    usage="usage: hass-axi ws <command> [flags]",
    default_sub="ws",
    subs=(
        Sub(
            name="ws",
            # Which command is being sent is an argument, so `access` below
            # resolves it -- from the declaration for a declared name, and from
            # the type for `--raw`.
            access=DYNAMIC,
            args=("[command]",),
            summary="Send one WebSocket command by name or by raw type",
            flags=(
                Flag(
                    "--param",
                    "<key=value>",
                    repeat=True,
                    note="value parsed as JSON when it parses",
                ),
                Flag("--params-json", "<object>", note="merged over --param"),
                Flag("--list", boolean=True, note="show the declared commands and exit"),
                Flag("--raw", boolean=True, note="treat <command> as a literal API type"),
            ),
        ),
    ),
    notes=(
        "declared names are stable; --raw passes any type straight through to the API",
        "--params-json takes a whole JSON object; --param takes repeated key=value pairs",
    ),
    examples=(
        "hass-axi ws --list",
        "hass-axi ws entity.list",
        "hass-axi ws area.update --param area_id=example_room --param name='Example Study'",
        "hass-axi ws --raw config/floor_registry/list",
    ),
)


def _listing_only(parsed) -> bool:
    """Whether this invocation only prints the command table."""
    return bool(parsed.get("list")) or (not parsed.positionals and not parsed.get("raw"))


def _resolve(parsed) -> str:
    """The API type this invocation names, or the usage error it has earned.

    Shared by :func:`access` and :func:`run` so the read-only gate and the
    dispatch agree about what is being sent. Resolving it twice from two
    readings of the same arguments is how a gate comes to guard a different
    command from the one that runs.
    """
    if not parsed.positionals:
        raise UsageError(
            "--raw needs an API command type",
            help_lines=[
                "Run `hass-axi ws --raw config/floor_registry/list`",
                "Run `hass-axi ws --list` to see the declared commands",
            ],
            code="MISSING_COMMAND",
        )
    name = parsed.positionals[0]
    if parsed.get("raw"):
        return name
    command = REGISTRY.get(name)
    if command is None:
        if "/" in name:
            raise UsageError(
                f"{name!r} looks like a raw API type, which needs --raw",
                help_lines=[f"Run `hass-axi ws --raw {name}`"],
                code="UNKNOWN_COMMAND",
            )
        raise UsageError(
            f"unknown websocket command: {name}",
            help_lines=[
                f"declared commands: {', '.join(sorted(REGISTRY))}",
                "Run `hass-axi ws --list` to see each command's parameters",
            ],
            code="UNKNOWN_COMMAND",
        )
    return command.type


def access(sub: str, parsed) -> str:
    """The read-only verdict for one WebSocket escape-hatch invocation.

    Printing the table changes nothing, so `--list` and a bare `hass-axi ws` are
    reads. Anything else is judged by the *type* it resolves to, which is what
    makes `--raw config/entity_registry/update` refuse exactly as
    `entity.update` does: the type is what reaches the installation, and a
    second spelling of it must not buy a second verdict. A type no declaration
    names is a write -- see :func:`hass_axi.ws.access_for_type`.
    """
    if _listing_only(parsed):
        return READ
    try:
        return access_for_type(_resolve(parsed))
    except UsageError:
        # A name that resolves to nothing sends nothing; `run` raises the same
        # error, which is a better answer than a refusal for a command that
        # does not exist.
        return READ


def run(ctx, sub: str, parsed):
    if _listing_only(parsed):
        return _list(parsed)

    name = parsed.positionals[0] if parsed.positionals else ""
    params = parse_pairs(parsed.get("param", []), flag="--param")
    params.update(parse_json_flag(parsed.get("params_json"), flag="--params-json"))
    type_ = _resolve(parsed)

    command = None if parsed.get("raw") else REGISTRY.get(name)
    if command is not None:
        missing = [key for key in command.required if key not in params]
        if missing:
            raise UsageError(
                f"{name} needs {', '.join(missing)}",
                help_lines=[
                    f"Run `hass-axi ws {name} "
                    + " ".join(f"--param {k}=<value>" for k in command.required)
                    + "`"
                ],
                code="MISSING_PARAM",
            )

    with ctx.ws() as client:
        result = client.send_command(type_, params)

    doc = {"command": {"name": name, "type": type_}}
    if result is None:
        doc["result"] = f"{type_} succeeded with an empty result"
    else:
        doc["result"] = result
    return doc


def _list(parsed):
    rows = [
        {
            "command": command.name,
            "type": command.type,
            "params": ",".join(command.params) or "",
        }
        for command in sorted(REGISTRY.values(), key=lambda c: c.name)
    ]
    return {
        "count": f"{plural(len(rows), 'declared command')}",
        "commands": rows,
        "help": HelpBlock(
            [
                "Run `hass-axi ws <command> --param key=value` to send one",
                "Run `hass-axi ws --raw <api/type>` for a command that is not declared here",
            ]
        ),
    }

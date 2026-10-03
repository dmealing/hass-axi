"""`hass-axi state` -- entity states over the REST API.

State is the runtime view: what an entity is doing right now. The registry view
-- names, areas, platforms -- lives under `hass-axi entity`, which speaks the
WebSocket API instead.
"""

from __future__ import annotations

from ..argspec import Command, Flag, Sub
from ..output import HelpBlock, truncate
from ..readonly import READ
from ._common import (
    PREVIEW_CHARS,
    count_line,
    device_area_map,
    domain_of,
    effective_area_id,
    filter_by_area,
    friendly_name,
    matches_search,
    parse_limit,
    project,
    select_fields,
)

DEFAULT_LIMIT = 100
LIST_FIELDS = ["entity_id", "name", "state", "domain", "last_changed", "last_updated"]
DEFAULT_LIST_FIELDS = ["entity_id", "name", "state"]

COMMAND = Command(
    name="state",
    summary="Read entity states from the Home Assistant REST API",
    usage="usage: hass-axi state <subcommand> [flags]",
    subs=(
        Sub(
            name="list",
            access=READ,
            summary="List entity states",
            flags=(
                Flag("--area", "<id|name>", note="'none' selects entities with no area"),
                Flag("--domain", "<name>", repeat=True, note="repeat to widen"),
                Flag("--state", "<value>", note="exact match"),
                Flag("--search", "<text>", note="matches entity_id and name"),
                Flag("--limit", "<n>", default=DEFAULT_LIMIT),
                Flag("--fields", "<a,b,c>", note=f"from {'|'.join(LIST_FIELDS)}"),
            ),
        ),
        Sub(
            name="get",
            access=READ,
            args=("<entity_id>",),
            summary="Show one entity state with its attributes",
            flags=(Flag("--full", boolean=True, note="do not truncate long attributes"),),
        ),
    ),
    notes=(
        "state is the runtime view; run `hass-axi entity list` for registry names and areas",
        "--area reads the WebSocket registry, where areas live; it costs one extra round-trip",
    ),
    examples=(
        "hass-axi state list --domain light",
        "hass-axi state list --area 'Example Room' --domain light",
        "hass-axi state list --search lamp --limit 20",
        "hass-axi state list --domain sensor --state unavailable",
        "hass-axi state get light.example_lamp",
        "hass-axi state get media_player.example_speaker --full",
    ),
)


def run(ctx, sub: str, parsed):
    if sub == "list":
        return _list(ctx, parsed)
    return _get(ctx, parsed)


def _row(state: dict) -> dict:
    """The one place a state row is built -- `list` and `get` both come here.

    The missing-`entity_id` default is `""` rather than the id `get` was asked
    for, because `list` has none to fall back to and because echoing the
    caller's own argument back would make a malformed answer read as a
    well-formed one. Neither default is reachable: `/api/states/<id>` answers
    with `State.as_dict()`, which always carries `entity_id`, and a missing
    subject is a 404 raised as `NO_SUCH_ENTITY` before any row is built.
    """
    return {
        "entity_id": state.get("entity_id", ""),
        "name": friendly_name(state),
        "state": state.get("state", ""),
        "domain": domain_of(state.get("entity_id", "")),
        "last_changed": state.get("last_changed", ""),
        "last_updated": state.get("last_updated", ""),
    }


def _list(ctx, parsed):
    states = ctx.rest().states()
    rows = [_row(state) for state in states]
    total = len(rows)

    scope: list = []
    rows = _narrow_to_area(ctx, rows, parsed.get("area"), scope)

    domains = [d.strip().lower() for d in parsed.get("domain", []) if d.strip()]
    if domains:
        rows = [row for row in rows if row["domain"].lower() in domains]
        scope.append(f"in domain {'|'.join(domains)}")
    wanted_state = parsed.get("state")
    if wanted_state:
        rows = [row for row in rows if row["state"] == wanted_state]
        scope.append(f"with state {wanted_state}")
    search = parsed.get("search")
    if search:
        rows = [row for row in rows if matches_search(search, row["entity_id"], row["name"])]
        scope.append(f"matching {search!r}")

    matched = len(rows)
    limit = parse_limit(parsed.get("limit"), default=DEFAULT_LIMIT)
    fields = select_fields(parsed.get("fields"), LIST_FIELDS, DEFAULT_LIST_FIELDS)
    shown = rows[:limit]

    if not shown:
        where = " ".join(scope) or "in this installation"
        return {
            "states": f"0 entity states found {where}",
            "total": f"{total} entities in this installation",
            "help": HelpBlock(
                [
                    "Run `hass-axi state list` with no filters to see every entity",
                    "Run `hass-axi entity list` to read the registry, which includes disabled entities",
                ]
            ),
        }

    count = count_line(len(shown), matched, total, filtered=bool(scope))

    help_lines = ["Run `hass-axi state get <entity_id>` for one entity's full attributes"]
    if len(shown) < matched:
        help_lines.append(f"Run `hass-axi state list --limit {matched}` to see all {matched}")
    if not domains:
        help_lines.append("Run `hass-axi state list --domain light` to narrow by domain")

    return {
        "count": count,
        "states": project(shown, fields),
        "help": HelpBlock(help_lines),
    }


def _narrow_to_area(ctx, rows: list, area_filter, scope: list) -> list:
    """Narrow runtime states to one area by cross-referencing the registry.

    States come from REST and carry no area at all; areas live in the
    WebSocket registry. Refusing `--area` here would mean an agent that learnt
    the flag on `entity list` or `device list` hits a wall on the command it
    reaches for most, so the registry is read instead -- one round-trip, paid
    only when the flag is passed. An entity with no registry entry has no area,
    so `--area none` finds it.
    """
    if not area_filter:
        return rows
    with ctx.ws() as client:
        entities = client.run("entity.list") or []
        areas = client.run("area.list") or []
        devices = client.run("device.list") or []
    device_areas = device_area_map(devices)
    area_of = {entry.get("entity_id"): effective_area_id(entry, device_areas) for entry in entities}
    for row in rows:
        row["area_id"] = area_of.get(row["entity_id"], "")
    return filter_by_area(rows, areas, area_filter, scope)


def _get(ctx, parsed):
    entity_id = parsed.positionals[0]
    state = ctx.rest().state(entity_id)
    attributes = dict(state.get("attributes") or {})
    full = parsed.get("full", False)

    hint = ""
    if not full:
        for key, value in list(attributes.items()):
            if isinstance(value, str) and len(value) > PREVIEW_CHARS:
                attributes[key], hint = truncate(
                    value,
                    PREVIEW_CHARS,
                    f"Run `hass-axi state get {entity_id} --full` to see complete attributes",
                )

    doc = {
        "state": _row(state),
        "attributes": attributes if attributes else {},
    }
    if not attributes:
        doc["attributes"] = "0 attributes on this entity"
    if hint:
        doc["help"] = HelpBlock([hint])
    return doc

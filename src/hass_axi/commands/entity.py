"""`hass-axi entity` -- the entity registry, which is WebSocket-only.

The registry is where an entity's stable identity lives: its user-set name, the
area it belongs to, the integration that supplied it. None of that is reachable
over REST, which is why this command exists.
"""

from __future__ import annotations

from ..argspec import Command, Flag, Sub
from ..errors import NotFound, UsageError
from ..model import rows as vocabulary
from ..model.readers import AreaEntry, DeviceEntry, EntityEntry, EntityUpdateResult
from ..output import HelpBlock
from ..readonly import DYNAMIC, READ
from ._common import (
    WRITE_FLAG,
    area_is_placed,
    area_name_map,
    change_rows,
    check_icon,
    count_line,
    device_area_map,
    device_name_map,
    domain_of,
    effective_area_id,
    empty_listing,
    filter_by_area,
    listing_args,
    matches_search,
    preview_help,
    preview_note,
    project,
    read_each,
    registry_name,
    reject_conflicting_flags,
    resolve_area,
    resolve_device,
    search_term,
    see_all_line,
    sent_or,
    write_access,
)

DEFAULT_LIMIT = 100
LIST_FIELDS = vocabulary.FIELDS["entity"]
DEFAULT_LIST_FIELDS = vocabulary.DEFAULT["entity"]

COMMAND = Command(
    name="entity",
    summary="Read and update the entity registry over the WebSocket API",
    usage="usage: hass-axi entity <subcommand> [flags]",
    subs=(
        Sub(
            name="list",
            access=READ,
            summary="List entity registry entries",
            flags=(
                Flag("--area", "<id|name>", note="'none' selects unassigned entities"),
                Flag("--domain", "<name>", repeat=True),
                Flag("--platform", "<name>"),
                Flag("--device", "<device_id>", note="the entities one device supplies"),
                Flag(
                    "--search",
                    "<text>",
                    note="matches entity_id, the displayed name and original_name",
                ),
                Flag("--limit", "<n>", default=DEFAULT_LIMIT),
                Flag("--fields", "<a,b,c>", note=f"from {'|'.join(LIST_FIELDS)}"),
            ),
        ),
        Sub(name="get", args=("<entity_id>",), summary="Show one registry entry", access=READ),
        Sub(
            name="update",
            access=DYNAMIC,
            args=("<entity_id>",),
            summary="Set an entity's name, area or icon (a preview unless --write is given)",
            flags=(
                Flag("--name", "<text>", free_text=True),
                Flag("--area", "<id|name>"),
                Flag("--icon", "<mdi:name>"),
                Flag("--new-id", "<entity_id>", note="rename the entity_id itself"),
                Flag("--clear-name", boolean=True, note="fall back to the integration's name"),
                Flag("--clear-area", boolean=True),
                Flag("--clear-icon", boolean=True),
                WRITE_FLAG,
            ),
        ),
    ),
    notes=(
        "an entity's area is inherited from its device until it is set here explicitly",
        "name is the name Home Assistant displays: its device's, plus original_name, "
        "unless one is set here",
        "entity_ids are not stable identity: filter by --area or --search, not by guessing ids",
        "update shows what it would change and sends nothing until --write is given",
    ),
    examples=(
        "hass-axi entity list --area 'Example Room'",
        "hass-axi entity list --domain light --fields entity_id,name,area,platform",
        "hass-axi entity list --area none --limit 500",
        "hass-axi entity list --device <device_id>",
        "hass-axi entity get light.example_lamp",
        "hass-axi entity update light.example_lamp --name 'Reading Lamp' --area example_room",
        "hass-axi entity update light.example_lamp --name 'Reading Lamp' --write",
    ),
)


def access(sub: str, parsed) -> str:
    """`entity update` writes only when it is told to; the preview only reads."""
    return write_access(parsed) if sub == "update" else READ


def run(ctx, sub: str, parsed):
    if sub == "list":
        return _list(ctx, parsed)
    if sub == "get":
        return _get(ctx, parsed)
    return _update(ctx, parsed)


def _snapshot(client):
    entities = read_each(EntityEntry, client.run("entity.list"))
    areas = read_each(AreaEntry, client.run("area.list"))
    devices = read_each(DeviceEntry, client.run("device.list"))
    return entities, areas, devices


def _row(entry: EntityEntry, area_names: dict, device_areas: dict, device_names: dict) -> dict:
    entity_id = sent_or(entry, "entity_id")
    area_id = effective_area_id(entry, device_areas)
    return {
        "entity_id": entity_id,
        "name": registry_name(entry, device_names),
        "area": area_names.get(area_id, "") if area_id else "",
        "area_id": area_id,
        "platform": sent_or(entry, "platform"),
        "domain": domain_of(entity_id),
        "device_id": entry.device_id or "",
        "original_name": entry.original_name or "",
        "disabled": bool(entry.disabled_by),
        "hidden": bool(entry.hidden_by),
        "entity_category": entry.entity_category or "",
    }


#: The filters a "see all" suggestion has to carry to list the same rows.
_FILTERS = ("--area", "--domain", "--platform", "--device", "--search")


def _list(ctx, parsed):
    limit, fields = listing_args(
        parsed, LIST_FIELDS, DEFAULT_LIST_FIELDS, default_limit=DEFAULT_LIMIT
    )
    with ctx.ws() as client:
        entities, areas, devices = _snapshot(client)

    area_names = area_name_map(areas)
    device_areas = device_area_map(devices)
    device_names = device_name_map(devices)
    rows = [_row(entry, area_names, device_areas, device_names) for entry in entities]
    total = len(rows)

    scope: list = []
    rows = filter_by_area(rows, areas, parsed.get("area"), scope)

    domains = [d.strip().lower() for d in parsed.get("domain", []) if d.strip()]
    if domains:
        rows = [row for row in rows if row["domain"].lower() in domains]
        scope.append(f"in domain {'|'.join(domains)}")

    platform = parsed.get("platform")
    if platform:
        rows = [row for row in rows if row["platform"].lower() == platform.strip().lower()]
        scope.append(f"from platform {platform}")

    # A device id is opaque, so there is nothing to search it by and no other
    # route from a device to the entities it supplies: `device list` reports a
    # count, not ids. Without this flag the suggestion `service call` prints
    # when a device target reaches nothing named a command that always answered 0.
    # The id is resolved against the registry the snapshot already holds, so a
    # truncated one is a failed lookup rather than a filter that matches nothing.
    device_id = parsed.get("device")
    if device_id:
        device = resolve_device(devices, device_id)
        rows = [row for row in rows if row["device_id"] == sent_or(device, "id")]
        scope.append(f"supplied by device {sent_or(device, 'id')}")

    search = parsed.get("search")
    if search:
        rows = [
            row
            for row in rows
            if matches_search(search, row["entity_id"], row["name"], row["original_name"])
        ]
        scope.append(f"matching {search!r}")

    matched = len(rows)
    if not rows:
        where = " ".join(scope) or "in this installation"
        return {
            **empty_listing("entities", f"0 registry entries found {where}"),
            "total": f"{total} entries in the entity registry",
            "help": HelpBlock(
                [
                    "Run `hass-axi entity list` with no filters to see every entry",
                    "Run `hass-axi area list` to see the areas that exist",
                ]
            ),
        }

    shown = rows[:limit]

    count = count_line(len(shown), matched, total, filtered=bool(scope))
    help_lines = ["Run `hass-axi entity get <entity_id>` for one entry in full"]
    if len(shown) < matched:
        help_lines.append(see_all_line("entity list", parsed, _FILTERS, matched))
    help_lines.append(
        'Run `hass-axi entity update <entity_id> --name "<name>" --area <id|name>` to preview '
        "a change, and add --write to send it"
    )

    return {"count": count, "entities": project(shown, fields), "help": HelpBlock(help_lines)}


def _find(entities: list, entity_id: str) -> EntityEntry:
    for entry in entities:
        if entry.entity_id == entity_id:
            return entry
    raise NotFound(
        f"no registry entry for {entity_id}",
        help_lines=[
            f"Run `hass-axi entity list --search {search_term(entity_id)}` to find it",
            "Run `hass-axi state get <entity_id>` if the entity exists but is not registered",
        ],
        code="NO_SUCH_ENTITY",
    )


def _get(ctx, parsed):
    entity_id = parsed.positionals[0]
    with ctx.ws() as client:
        entities, areas, devices = _snapshot(client)
    entry = _find(entities, entity_id)
    row = _row(entry, area_name_map(areas), device_area_map(devices), device_name_map(devices))
    row["unique_id"] = entry.unique_id or ""
    row["icon"] = entry.icon or ""
    row["area_source"] = _area_source(entry, row["area_id"], areas)
    return {"entity": row}


def _area_source(entry: EntityEntry, area_id: str, areas: list) -> str:
    """Where an entity's effective area came from: itself, its device, or nowhere.

    An `area_id` that names no area is called out rather than reported as a
    placement, because `area` is empty either way and the two are not the same
    thing: one entity is unassigned, the other is holding an id nothing answers
    to and is therefore missing from every per-area count.
    """
    if area_id and not area_is_placed(area_id, areas):
        return "no area has this id"
    if entry.area_id:
        return "entity"
    return "device" if area_id else ""


def _resulting_entry(result, current: EntityEntry, pending: dict) -> EntityEntry:
    """The registry entry as it stands after an update.

    Home Assistant answers `config/entity_registry/update` with the resulting
    entry under `entity_entry`. Prefer that, merged over the entry read before
    the call so a field the response happens to omit is not read as absent; if
    the response carries no entry at all, apply the pending changes locally.
    Either way what gets reported is the entity's state, never the request.
    """
    returned = EntityUpdateResult.read(result).entity_entry if isinstance(result, dict) else None
    if returned is not None:
        return EntityEntry.read({**current.raw, **returned.raw})
    entry = dict(current.raw)
    for key, value in pending.items():
        entry["entity_id" if key == "new_entity_id" else key] = value
    return EntityEntry.read(entry)


def _update(ctx, parsed):
    entity_id = parsed.positionals[0]
    reject_conflicting_flags(
        parsed,
        ("--name", "--clear-name"),
        ("--icon", "--clear-icon"),
        ("--area", "--clear-area"),
        invocation=f"hass-axi entity update {entity_id}",
    )

    changes: dict = {}
    if parsed.get("clear_name"):
        changes["name"] = None
    if parsed.get("name") is not None:
        changes["name"] = parsed.get("name")
    if parsed.get("clear_icon"):
        changes["icon"] = None
    if parsed.get("icon") is not None:
        changes["icon"] = parsed.get("icon")
    if parsed.get("new_id") is not None:
        changes["new_entity_id"] = parsed.get("new_id")

    check_icon(parsed.get("icon"))
    clear_area = parsed.get("clear_area")
    area_arg = parsed.get("area")

    if not changes and not clear_area and area_arg is None:
        raise UsageError(
            "nothing to update",
            help_lines=[
                f'Run `hass-axi entity update {entity_id} --name "<name>"` to set the name',
                f"Run `hass-axi entity update {entity_id} --area <id|name>` to move it",
            ],
            code="NO_CHANGES",
        )

    with ctx.ws() as client:
        # The device registry is read here because an entity with no area of
        # its own inherits its device's; without it the response would report
        # an area of "" for an entity that plainly has one, and an agent
        # reading that would conclude the entity needs reassigning.
        entities, areas, devices = _snapshot(client)
        current = _find(entities, entity_id)

        if clear_area:
            changes["area_id"] = None
        elif area_arg is not None:
            changes["area_id"] = resolve_area(areas, area_arg).area_id

        # Idempotent: a request that asks for the state already stored is a no-op.
        pending = {k: v for k, v in changes.items() if _differs(current, k, v)}
        if pending and not parsed.get("write"):
            return {
                "entity": entity_id,
                "preview": preview_note(ctx.environ),
                "would_change": change_rows(
                    current, pending, rename={"new_entity_id": "entity_id"}
                ),
                "help": HelpBlock(preview_help(ctx.environ)),
            }
        if pending:
            result = client.run("entity.update", {"entity_id": entity_id, **pending})
            entry = _resulting_entry(result, current, pending)
            updated: object = sorted(pending)
        else:
            entry = current
            updated = "already matches the requested values, no change made"

    # Reported through the same row builder `entity get` uses, from the
    # resulting registry entry rather than from the request, so the two views
    # cannot disagree about what the entity now is.
    row = _row(entry, area_name_map(areas), device_area_map(devices), device_name_map(devices))
    return {
        "entity": row["entity_id"],
        "updated": updated,
        "name": row["name"],
        "area": row["area"],
        "area_id": row["area_id"],
        "area_source": _area_source(entry, row["area_id"], areas),
    }


def _differs(current: EntityEntry, key: str, value) -> bool:
    if key == "new_entity_id":
        return current.entity_id != value
    return (getattr(current, key) or None) != (value or None)

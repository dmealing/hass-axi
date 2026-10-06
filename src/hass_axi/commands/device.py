"""`hass-axi device` -- the device registry, which is WebSocket-only.

The device is the level a name or an area is usually wrong *at*. An entity with
no area of its own inherits its device's, and an entity with no name of its own
takes all or part of its name from its device, so one correction here cascades
to every entity that device supplies -- and fixing the entities one at a time
leaves the device itself still wrong.

**`--name` writes `name_by_user`, and the asymmetry with `entity update` is Home
Assistant's rather than this tool's.** A device carries two names: `name`, which
the integration supplies and nothing can change, and `name_by_user`, the
override. `config/device_registry/update` accepts only the second, which is why
`--clear-name` falls back to the integration's name rather than to nothing.
"""

from __future__ import annotations

from ..argspec import Command, Flag, Sub
from ..errors import UsageError
from ..model import rows as vocabulary
from ..model.readers import AreaEntry, DeviceEntry, EntityEntry
from ..output import HelpBlock
from ..readonly import DYNAMIC, READ
from ._common import (
    WRITE_FLAG,
    area_is_placed,
    area_name_map,
    change_rows,
    count_line,
    displayed_device_name,
    empty_listing,
    filter_by_area,
    listing_args,
    matches_search,
    preview_help,
    preview_note,
    project,
    read_each,
    reject_conflicting_flags,
    resolve_area,
    resolve_device_ref,
    see_all_line,
    sent_or,
    write_access,
)

DEFAULT_LIMIT = 100
LIST_FIELDS = vocabulary.FIELDS["device"]
DEFAULT_LIST_FIELDS = vocabulary.DEFAULT["device"]

COMMAND = Command(
    name="device",
    summary="Read and update the device registry over the WebSocket API",
    usage="usage: hass-axi device <subcommand> [flags]",
    default_sub="list",
    subs=(
        Sub(
            name="list",
            access=READ,
            summary="List devices with their areas and entity counts",
            flags=(
                Flag("--area", "<id|name>", note="'none' selects unassigned devices"),
                Flag("--search", "<text>", note="matches name, manufacturer and model"),
                Flag("--limit", "<n>", default=DEFAULT_LIMIT),
                Flag("--fields", "<a,b,c>", note=f"from {'|'.join(LIST_FIELDS)}"),
            ),
        ),
        Sub(
            name="get",
            args=("<id|name>",),
            summary="Show one device registry entry",
            access=READ,
        ),
        Sub(
            name="update",
            access=DYNAMIC,
            args=("<id|name>",),
            summary="Set a device's name or area (a preview unless --write is given)",
            flags=(
                Flag(
                    "--name",
                    "<text>",
                    note="writes name_by_user; see the note below",
                    free_text=True,
                ),
                Flag("--area", "<id|name>"),
                Flag("--clear-name", boolean=True, note="fall back to the integration's name"),
                Flag("--clear-area", boolean=True),
                WRITE_FLAG,
            ),
        ),
    ),
    notes=(
        "an entity with no area of its own inherits the area of its device",
        "an entity with no name of its own is named after its device",
        "--name writes name_by_user: `name` is the integration's own and Home Assistant "
        "does not let anything change it",
        "devices accept a device_id or the displayed name anywhere <id|name> appears",
        "update shows what it would change and sends nothing until --write is given",
        "disabling or deleting a device is deliberately not exposed here; "
        "use `hass-axi ws device.update` if you mean it",
    ),
    examples=(
        "hass-axi device list",
        "hass-axi device list --area 'Example Room'",
        "hass-axi device list --search example --fields device_id,name,model",
        "hass-axi device get <device_id>",
        "hass-axi device update 'Example Ceiling' --name 'Hall Ceiling'",
        "hass-axi device update <device_id> --area 'Example Room' --clear-name --write",
    ),
)


def access(sub: str, parsed) -> str:
    """`device update` writes only when it is told to; the preview only reads."""
    return write_access(parsed) if sub == "update" else READ


def run(ctx, sub: str, parsed):
    if sub == "list":
        return _list(ctx, parsed)
    if sub == "get":
        return _get(ctx, parsed)
    return _update(ctx, parsed)


def _snapshot(client) -> tuple:
    devices = read_each(DeviceEntry, client.run("device.list"))
    areas = read_each(AreaEntry, client.run("area.list"))
    entities = read_each(EntityEntry, client.run("entity.list"))
    return devices, areas, entities


def _entity_counts(entities: list) -> dict:
    """How many registry entries each device supplies."""
    counts: dict = {}
    for entry in entities:
        device_id = entry.device_id
        if device_id:
            counts[device_id] = counts.get(device_id, 0) + 1
    return counts


def _row(device: DeviceEntry, area_names: dict, entity_counts: dict) -> dict:
    """The one device row shape, built in one place.

    `list`, `get` and `update` all report a device through this, so a field
    added later cannot reach some of them and miss others -- and an update
    cannot answer from its own request while `get` answers from the registry.
    """
    device_id = sent_or(device, "id")
    area_id = device.area_id or ""
    return {
        "device_id": device_id,
        "name": displayed_device_name(device),
        "area": area_names.get(area_id, ""),
        "area_id": area_id,
        "manufacturer": device.manufacturer or "",
        "model": device.model or "",
        "entities": entity_counts.get(device_id, 0),
    }


def _area_source(device: DeviceEntry, areas: list) -> str:
    """Whether a device's `area_id` is a placement, an absence, or a dangling id.

    A device has no area to inherit, so the interesting case is the third one:
    Home Assistant takes `ws device.update --param area_id=<typo>` without
    complaint, and the device is then in no area anybody can name while still
    carrying an id. `area` is empty either way, and the two are not the same
    fact.
    """
    area_id = device.area_id or ""
    if area_id and not area_is_placed(area_id, areas):
        return "no area has this id"
    return "device" if area_id else ""


def _list(ctx, parsed):
    limit, fields = listing_args(
        parsed, LIST_FIELDS, DEFAULT_LIST_FIELDS, default_limit=DEFAULT_LIMIT
    )
    with ctx.ws() as client:
        devices, areas, entities = _snapshot(client)

    area_names = area_name_map(areas)
    entity_counts = _entity_counts(entities)
    rows = [_row(device, area_names, entity_counts) for device in devices]
    total = len(rows)

    scope: list = []
    rows = filter_by_area(rows, areas, parsed.get("area"), scope)

    search = parsed.get("search")
    if search:
        rows = [
            row
            for row in rows
            if matches_search(search, row["name"], row["manufacturer"], row["model"])
        ]
        scope.append(f"matching {search!r}")

    matched = len(rows)
    if not rows:
        where = " ".join(scope) or "in this installation"
        return {
            **empty_listing("devices", f"0 devices found {where}"),
            "total": f"{total} devices in the device registry",
            "help": HelpBlock(["Run `hass-axi device list` with no filters to see every device"]),
        }

    shown = rows[:limit]
    count = count_line(len(shown), matched, total, filtered=bool(scope))
    help_lines = ["Run `hass-axi entity list --area <id|name>` to see the entities in an area"]
    if len(shown) < matched:
        help_lines.append(see_all_line("device list", parsed, ("--area", "--search"), matched))

    return {"count": count, "devices": project(shown, fields), "help": HelpBlock(help_lines)}


def _get(ctx, parsed):
    needle = parsed.positionals[0]
    with ctx.ws() as client:
        devices, areas, entities = _snapshot(client)

    device = resolve_device_ref(devices, needle)
    row = _row(device, area_name_map(areas), _entity_counts(entities))
    # Both names, because they are different fields with different owners and
    # only one of them is writable: `name` is what Home Assistant displays,
    # `name_by_user` is the override `--name` sets and `--clear-name` removes.
    row["name_by_user"] = device.name_by_user or ""
    row["name_source"] = "user" if device.name_by_user else "integration"
    row["area_source"] = _area_source(device, areas)
    row["disabled"] = bool(device.disabled_by)
    return {
        "device": row,
        "help": HelpBlock(
            [
                f"Run `hass-axi entity list --device {row['device_id']}` "
                "to see the entities it supplies",
                f'Run `hass-axi device update {row["device_id"]} --name "<name>"` to preview a '
                "rename, and add --write to send it",
            ]
        ),
    }


def _resulting_device(result, current: DeviceEntry, pending: dict) -> DeviceEntry:
    """The registry entry as it stands after an update.

    `websocket_update_device` answers with `entry.dict_repr` -- the stored
    device, not the request that produced it. Prefer that, merged over the entry
    read before the call so a field the response happens to omit is not read as
    absent; if the response carries no device at all, apply the pending changes
    locally. Either way what is reported is the device's state, never the ask.
    """
    if isinstance(result, dict) and DeviceEntry.read(result).id:
        return DeviceEntry.read({**current.raw, **result})
    return DeviceEntry.read({**current.raw, **pending})


def _update(ctx, parsed):
    needle = parsed.positionals[0]
    reject_conflicting_flags(
        parsed,
        ("--name", "--clear-name"),
        ("--area", "--clear-area"),
        invocation=f"hass-axi device update {needle}",
    )

    changes: dict = {}
    if parsed.get("clear_name"):
        changes["name_by_user"] = None
    if parsed.get("name") is not None:
        changes["name_by_user"] = parsed.get("name")

    clear_area = parsed.get("clear_area")
    area_arg = parsed.get("area")

    if not changes and not clear_area and area_arg is None:
        raise UsageError(
            "nothing to update",
            help_lines=[
                f'Run `hass-axi device update {needle} --name "<name>"` to rename it',
                f"Run `hass-axi device update {needle} --area <id|name>` to move it",
            ],
            code="NO_CHANGES",
        )

    with ctx.ws() as client:
        devices, areas, entities = _snapshot(client)
        current = resolve_device_ref(devices, needle)

        if clear_area:
            changes["area_id"] = None
        elif area_arg is not None:
            changes["area_id"] = resolve_area(areas, area_arg).area_id

        # Idempotent: a request that asks for the state already stored is a no-op.
        pending = {k: v for k, v in changes.items() if (getattr(current, k) or None) != (v or None)}
        if pending and not parsed.get("write"):
            return {
                "device": sent_or(current, "id"),
                "name": displayed_device_name(current),
                "preview": preview_note(ctx.environ),
                "would_change": change_rows(current, pending),
                "help": HelpBlock(preview_help(ctx.environ)),
            }
        if pending:
            result = client.run("device.update", {"device_id": sent_or(current, "id"), **pending})
            entry = _resulting_device(result, current, pending)
            updated: object = sorted(pending)
        else:
            entry = current
            updated = "already matches the requested values, no change made"

    # Reported through the row builder `device get` uses, from the resulting
    # registry entry rather than from the request, so the two views cannot
    # disagree about what the device now is.
    row = _row(entry, area_name_map(areas), _entity_counts(entities))
    return {
        "device": row["device_id"],
        "updated": updated,
        "name": row["name"],
        "name_by_user": entry.name_by_user or "",
        "area": row["area"],
        "area_id": row["area_id"],
        "area_source": _area_source(entry, areas),
    }

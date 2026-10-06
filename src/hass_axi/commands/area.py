"""`hass-axi area` -- the area registry, which is WebSocket-only."""

from __future__ import annotations

from ..argspec import Command, Flag, Sub
from ..errors import UsageError
from ..model import rows as vocabulary
from ..output import HelpBlock
from ..readonly import DYNAMIC, READ
from ._common import (
    WRITE_FLAG,
    area_is_placed,
    change_rows,
    check_icon,
    device_area_map,
    effective_area_id,
    empty_listing,
    fold,
    plural,
    preview_help,
    preview_note,
    project,
    reject_conflicting_flags,
    resolve_area,
    resolve_floor,
    select_fields,
    write_access,
)

#: Declared in ``metaobjects/meta.rows.yaml``, generated into :mod:`hass_axi.model.rows`.
LIST_FIELDS = vocabulary.FIELDS["area"]
#: What an area is and how much it holds. The floor is one `--fields` away, and
#: `area get` reports it with the icon and aliases.
DEFAULT_LIST_FIELDS = vocabulary.DEFAULT["area"]

COMMAND = Command(
    name="area",
    summary="Read and update the area registry over the WebSocket API",
    usage="usage: hass-axi area <subcommand> [flags]",
    subs=(
        Sub(
            name="list",
            access=READ,
            summary="List areas with their entity and device counts",
            flags=(Flag("--fields", "<a,b,c>", note=f"from {'|'.join(LIST_FIELDS)}"),),
        ),
        Sub(
            name="get", args=("<id|name>",), summary="Show one area and what it holds", access=READ
        ),
        Sub(
            name="create",
            access=DYNAMIC,
            summary="Create an area (a preview unless --write is given)",
            flags=(
                Flag("--name", "<text>", note="required", free_text=True),
                Flag("--icon", "<mdi:name>"),
                Flag("--floor", "<id|name>"),
                WRITE_FLAG,
            ),
        ),
        Sub(
            name="update",
            access=DYNAMIC,
            args=("<id|name>",),
            summary="Rename an area or change its icon or floor (a preview unless --write is given)",
            flags=(
                Flag("--name", "<text>", free_text=True),
                Flag("--icon", "<mdi:name>"),
                Flag("--floor", "<id|name>"),
                Flag("--clear-icon", boolean=True),
                Flag("--clear-floor", boolean=True),
                WRITE_FLAG,
            ),
        ),
    ),
    notes=(
        "areas accept an area_id or a name anywhere <id|name> appears",
        "create and update show what they would do and send nothing until --write is given",
        "--floor has to name a floor that exists; run `hass-axi ws floor.list` to see them",
        "deleting an area is deliberately not exposed here; use `hass-axi ws area.delete` if you mean it",
    ),
    examples=(
        "hass-axi area list",
        "hass-axi area list --fields area_id,name,floor_id",
        "hass-axi area get example_room",
        "hass-axi area create --name 'Example Room'",
        "hass-axi area create --name 'Example Room' --write",
        "hass-axi area update example_room --name 'Example Study'",
        "hass-axi area update 'Example Room' --icon mdi:sofa --write",
    ),
)


def access(sub: str, parsed) -> str:
    """`area create` and `area update` write only when told to; a preview only reads."""
    return write_access(parsed) if sub in ("create", "update") else READ


def run(ctx, sub: str, parsed):
    if sub == "list":
        return _list(ctx, parsed)
    if sub == "get":
        return _get(ctx, parsed)
    if sub == "create":
        return _create(ctx, parsed)
    return _update(ctx, parsed)


def _entity_counts(entities: list, devices: list, areas: list) -> tuple:
    """Count entities per area, and how many belong to no area at all.

    Both follow the device fallback, so the totals agree with what Home
    Assistant shows for each area. An `area_id` no area answers to counts as
    unassigned rather than as its own bucket nothing prints: counting it
    anywhere else is what let the per-area counts plus `unassigned_entities`
    quietly stop summing to the size of the registry.
    """
    device_areas = device_area_map(devices)
    counts: dict = {}
    unassigned = 0
    for entry in entities:
        area_id = effective_area_id(entry, device_areas)
        if area_is_placed(area_id, areas):
            counts[area_id] = counts.get(area_id, 0) + 1
        else:
            unassigned += 1
    return counts, unassigned


def _list(ctx, parsed):
    # Before the first request: a mistyped field is a usage error whether or
    # not the installation answers.
    fields = select_fields(parsed.get("fields"), LIST_FIELDS, DEFAULT_LIST_FIELDS)
    with ctx.ws() as client:
        areas = client.run("area.list") or []
        entities = client.run("entity.list") or []
        devices = client.run("device.list") or []

    if not areas:
        return {
            **empty_listing("areas", "0 areas defined in this installation"),
            "help": HelpBlock(["Run `hass-axi area create --name '<name>' --write` to add one"]),
        }

    counts, unassigned = _entity_counts(entities, devices, areas)
    device_counts: dict = {}
    for device in devices:
        if area_is_placed(device.get("area_id") or "", areas):
            device_counts[device["area_id"]] = device_counts.get(device["area_id"], 0) + 1

    rows = [
        {
            "area_id": area.get("area_id", ""),
            "name": area.get("name") or "",
            "entities": counts.get(area.get("area_id"), 0),
            "devices": device_counts.get(area.get("area_id"), 0),
            "floor_id": area.get("floor_id") or "",
        }
        for area in areas
    ]
    rows.sort(key=lambda row: row["name"].lower())

    return {
        "count": plural(len(rows), "area"),
        "unassigned_entities": unassigned,
        "areas": project(rows, fields),
        "help": HelpBlock(
            [
                "Run `hass-axi entity list --area <id|name>` to see what one area holds",
                "Run `hass-axi area update <id|name> --name '<name>'` to preview a rename, "
                "and add --write to send it",
                "Run `hass-axi entity list --area none` to find entities with no area",
            ]
        ),
    }


def _get(ctx, parsed):
    needle = parsed.positionals[0]
    with ctx.ws() as client:
        areas = client.run("area.list") or []
        entities = client.run("entity.list") or []
        devices = client.run("device.list") or []

    area = resolve_area(areas, needle, offer_create=True)
    area_id = area.get("area_id", "")
    counts, _ = _entity_counts(entities, devices, areas)
    return {
        "area": {
            "area_id": area_id,
            "name": area.get("name") or "",
            "icon": area.get("icon") or "",
            "floor_id": area.get("floor_id") or "",
            "entities": counts.get(area_id, 0),
            "devices": sum(1 for d in devices if d.get("area_id") == area_id),
            "aliases": list(area.get("aliases") or []),
        },
        "help": HelpBlock([f"Run `hass-axi entity list --area {area_id}` to list its entities"]),
    }


def _floor_id(client, raw) -> str:
    """The id of the floor ``raw`` names, read from the floor registry."""
    floors = client.run("floor.list") or []
    return resolve_floor(floors, raw).get("floor_id", "")


def _create(ctx, parsed):
    name = parsed.get("name")
    if not name:
        raise UsageError(
            "--name is required",
            help_lines=["Run `hass-axi area create --name 'Example Room'`"],
            code="MISSING_NAME",
        )
    check_icon(parsed.get("icon"))
    params = {"name": name}
    if parsed.get("icon") is not None:
        params["icon"] = parsed.get("icon")

    with ctx.ws() as client:
        areas = client.run("area.list") or []
        existing = next((a for a in areas if fold(a.get("name")) == fold(name)), None)
        # Idempotent: creating an area that already exists reports the existing one.
        if existing is not None:
            return {
                "area": {
                    "area_id": existing.get("area_id", ""),
                    "name": existing.get("name") or "",
                },
                "created": "an area with this name already exists, no change made",
            }
        if parsed.get("floor") is not None:
            params["floor_id"] = _floor_id(client, parsed.get("floor"))
        if not parsed.get("write"):
            return {
                "area": {"name": name},
                "preview": preview_note(ctx.environ),
                "would_create": params,
                "help": HelpBlock(preview_help(ctx.environ)),
            }
        result = client.run("area.create", params) or {}

    return {
        "area": {"area_id": result.get("area_id", ""), "name": result.get("name") or name},
        "created": True,
        "help": HelpBlock(
            [
                f"Run `hass-axi entity update <entity_id> --area {result.get('area_id', '')} "
                "--write` to fill it"
            ]
        ),
    }


def _update(ctx, parsed):
    needle = parsed.positionals[0]
    reject_conflicting_flags(
        parsed,
        ("--icon", "--clear-icon"),
        ("--floor", "--clear-floor"),
        invocation=f"hass-axi area update {needle}",
    )
    check_icon(parsed.get("icon"))

    changes: dict = {}
    if parsed.get("name") is not None:
        changes["name"] = parsed.get("name")
    if parsed.get("clear_icon"):
        changes["icon"] = None
    if parsed.get("icon") is not None:
        changes["icon"] = parsed.get("icon")
    if parsed.get("clear_floor"):
        changes["floor_id"] = None
    floor_arg = parsed.get("floor")

    if not changes and floor_arg is None:
        raise UsageError(
            "nothing to update",
            help_lines=[
                f"Run `hass-axi area update {needle} --name '<name>'` to rename it",
                f"Run `hass-axi area update {needle} --icon mdi:sofa` to set its icon",
            ],
            code="NO_CHANGES",
        )

    with ctx.ws() as client:
        areas = client.run("area.list") or []
        area = resolve_area(areas, needle)
        area_id = area.get("area_id", "")
        if floor_arg is not None:
            # Resolved against the floor registry: Home Assistant stores any
            # `floor_id` it is handed, so a typo is otherwise a floor nothing
            # answers to, stored at exit 0.
            changes["floor_id"] = _floor_id(client, floor_arg)
        pending = {k: v for k, v in changes.items() if (area.get(k) or None) != (v or None)}
        if not pending:
            return {
                "area": {"area_id": area_id, "name": area.get("name") or ""},
                "updated": "already matches the requested values, no change made",
            }
        if not parsed.get("write"):
            return {
                "area": {"area_id": area_id, "name": area.get("name") or ""},
                "preview": preview_note(ctx.environ),
                "would_change": change_rows(area, pending),
                "help": HelpBlock(preview_help(ctx.environ)),
            }
        result = client.run("area.update", {"area_id": area_id, **pending}) or {}

    return {
        "area": {
            "area_id": result.get("area_id", area_id),
            "name": result.get("name") or area.get("name") or "",
            "icon": result.get("icon") or "",
            "floor_id": result.get("floor_id") or "",
        },
        "updated": sorted(pending),
    }

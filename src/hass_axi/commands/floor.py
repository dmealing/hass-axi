"""`hass-axi floor` -- the floor registry, which is WebSocket-only."""

from __future__ import annotations

from ..argspec import Command, Sub
from ..model.readers import AreaEntry, FloorEntry
from ..output import HelpBlock
from ..readonly import READ
from ._common import empty_listing, plural, read_each, sent_or

COMMAND = Command(
    name="floor",
    summary="Read the floor registry over the WebSocket API",
    usage="usage: hass-axi floor <subcommand>",
    subs=(
        Sub(
            name="list",
            access=READ,
            summary="List floors with the number of areas on each",
        ),
    ),
    notes=(
        "a floor holds areas and an area holds entities, so `--target-floor` on `service call` "
        "reaches every entity in every area on the floor",
        "areas are placed on a floor with `hass-axi area update <id|name> --floor <id|name>`",
    ),
    examples=("hass-axi floor list",),
)


def access(sub: str, parsed) -> str:
    return READ


def run(ctx, sub: str, parsed):
    with ctx.ws() as client:
        floors = read_each(FloorEntry, client.run("floor.list"))
        areas = read_each(AreaEntry, client.run("area.list"))

    if not floors:
        return empty_listing("floors", "0 floors defined in this installation")

    counts: dict = {}
    for area in areas:
        counts[area.floor_id] = counts.get(area.floor_id, 0) + 1
    rows = [
        {
            "floor_id": sent_or(floor, "floor_id"),
            "name": floor.name or "",
            "level": floor.level if floor.level is not None else "",
            "areas": counts.get(floor.floor_id, 0),
        }
        for floor in floors
    ]
    rows.sort(key=lambda row: (row["level"] == "", row["level"] or 0, row["name"].lower()))

    on_a_floor = sum(row["areas"] for row in rows)
    return {
        "count": plural(len(rows), "floor"),
        "areas_without_floor": len(areas) - on_a_floor,
        "floors": rows,
        "help": HelpBlock(
            [
                "Run `hass-axi service call <domain>.<service> --target-floor <floor_id>` "
                "to preview a call on every entity on a floor",
                "Run `hass-axi area list --fields area_id,name,floor_id` to see which area is where",
            ]
        ),
    }

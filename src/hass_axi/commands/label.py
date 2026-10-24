"""`hass-axi label` -- the label registry, which is WebSocket-only."""

from __future__ import annotations

from ..argspec import Command, Sub
from ..model.readers import AreaEntry, DeviceEntry, EntityEntry, LabelEntry
from ..output import HelpBlock
from ..readonly import READ
from ._common import empty_listing, plural, read_each, sent_or

COMMAND = Command(
    name="label",
    summary="Read the label registry over the WebSocket API",
    usage="usage: hass-axi label <subcommand>",
    subs=(
        Sub(
            name="list",
            access=READ,
            summary="List labels with the entities, devices and areas that carry each",
        ),
    ),
    notes=(
        "a label can sit on an entity, a device or an area, and `--target-label` on "
        "`service call` reaches everything that carries it",
    ),
    examples=("hass-axi label list",),
)


def access(sub: str, parsed) -> str:
    return READ


def _carrying(label_id, entries) -> int:
    return sum(1 for entry in entries if label_id in (entry.labels or ()))


def run(ctx, sub: str, parsed):
    with ctx.ws() as client:
        labels = read_each(LabelEntry, client.run("label.list"))
        entities = read_each(EntityEntry, client.run("entity.list"))
        devices = read_each(DeviceEntry, client.run("device.list"))
        areas = read_each(AreaEntry, client.run("area.list"))

    if not labels:
        return empty_listing("labels", "0 labels defined in this installation")

    rows = [
        {
            "label_id": sent_or(label, "label_id"),
            "name": label.name or "",
            "color": label.color or "",
            "entities": _carrying(label.label_id, entities),
            "devices": _carrying(label.label_id, devices),
            "areas": _carrying(label.label_id, areas),
        }
        for label in labels
    ]
    rows.sort(key=lambda row: row["name"].lower())
    return {
        "count": plural(len(rows), "label"),
        "labels": rows,
        "help": HelpBlock(
            [
                "Run `hass-axi service call <domain>.<service> --target-label <label_id>` "
                "to preview a call on everything a label is on",
            ]
        ),
    }

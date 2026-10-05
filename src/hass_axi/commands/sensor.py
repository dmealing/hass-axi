"""`hass-axi sensor` -- find a reading by what it measures and where it is.

`state list` answers "what is this entity doing", and `entity list` answers
"what is it called and where is it". Finding the power draw in a room needs
both at once: the device class and unit live on the state, served over REST,
while the area -- usually inherited from the device -- and the entity category
live in the registry, served only over the WebSocket. This command reads both
on every run, which is what earns it a noun (promotion rule 1).

**Diagnostic and configuration entities are left out by default, by the
registry's own `entity_category`, never by name.** A battery voltage or a
signal strength is a sensor too, and on a real installation they outnumber the
readings anybody asks about; the category is how Home Assistant itself tells
them apart and it survives a rename where a name pattern would not. Hidden
entities are left out for the same reason. `--all` puts them back.
"""

from __future__ import annotations

from ..argspec import Command, Flag, Sub
from ..output import HelpBlock
from ..readonly import READ
from . import _window
from ._common import (
    area_name_map,
    count_line,
    device_area_map,
    device_name_map,
    domain_of,
    effective_area_id,
    empty_listing,
    filter_by_area,
    friendly_name,
    last_reported,
    listing_args,
    matches_search,
    plural,
    project,
    registry_name,
    see_all_line,
)

DEFAULT_LIMIT = 100
LIST_FIELDS = [
    "entity_id",
    "name",
    "value",
    "unit",
    "area",
    "area_id",
    "age",
    "device_class",
    "state_class",
    "last_reported",
    "entity_category",
]
#: Four columns: which sensor, what it reads and in what unit. Where it is and
#: how fresh the reading is are one `--fields` away; a default row is paid for
#: once per sensor, and most questions are answered by the reading itself.
DEFAULT_LIST_FIELDS = ["entity_id", "name", "value", "unit"]

#: Registry categories that mark an entity as not a reading. Home Assistant
#: defines exactly these two.
EXCLUDED_CATEGORIES = ("diagnostic", "config")

COMMAND = Command(
    name="sensor",
    summary="Find sensors by device class, unit, area or name, with value, unit, area and age",
    usage="usage: hass-axi sensor [list] [flags]",
    default_sub="list",
    subs=(
        Sub(
            name="list",
            access=READ,
            summary="List sensor readings",
            flags=(
                Flag("--device-class", "<class>", repeat=True, note="e.g. power, energy; repeat"),
                Flag("--unit", "<unit>", repeat=True, note="exact, e.g. W or kWh; repeat"),
                Flag("--area", "<id|name>", note="'none' selects sensors with no area"),
                Flag("--search", "<text>", note="matches entity_id and the displayed name"),
                Flag("--all", boolean=True, note="include diagnostic, config and hidden sensors"),
                Flag("--limit", "<n>", default=DEFAULT_LIMIT),
                Flag("--fields", "<a,b,c>", note=f"from {'|'.join(LIST_FIELDS)}"),
            ),
        ),
    ),
    notes=(
        "age is the time since the integration last reported a value; a sensor on an idle "
        "circuit can be old and healthy, so it is a fact about recency and not a fault",
        "an area is inherited from the sensor's device unless the entity sets its own",
        "run `hass-axi statistics get <entity_id>` for a total or an average over a window",
    ),
    examples=(
        "hass-axi sensor list --device-class power",
        "hass-axi sensor list --area 'Example Room' --unit kWh",
        "hass-axi sensor list --search temperature --fields entity_id,name,value,unit,age",
    ),
)


#: The filters a "see all" suggestion has to carry to list the same rows.
_FILTERS = ("--device-class", "--unit", "--area", "--search", "--all")


def run(ctx, sub: str, parsed):
    limit, fields = listing_args(
        parsed, LIST_FIELDS, DEFAULT_LIST_FIELDS, default_limit=DEFAULT_LIMIT
    )
    states = [s for s in ctx.rest().states() if domain_of(s.get("entity_id", "")) == "sensor"]
    with ctx.ws() as client:
        entities = client.run("entity.list") or []
        areas = client.run("area.list") or []
        devices = client.run("device.list") or []

    registry = {entry.get("entity_id"): entry for entry in entities}
    device_areas = device_area_map(devices)
    device_names = device_name_map(devices)
    area_names = area_name_map(areas)
    current = _window.now()

    rows: list = []
    set_aside = 0
    for state in states:
        entry = registry.get(state.get("entity_id"))
        if entry is not None and not parsed.get("all") and _set_aside(entry):
            set_aside += 1
            continue
        rows.append(_row(state, entry, device_names, device_areas, area_names, current))
    visible = rows
    total = len(rows)

    scope: list = []
    classes = [c.strip().lower() for c in parsed.get("device_class", []) if c.strip()]
    if classes:
        rows = [row for row in rows if row["device_class"].lower() in classes]
        scope.append(f"of device class {'|'.join(classes)}")
    units = [u.strip() for u in parsed.get("unit", []) if u.strip()]
    if units:
        # Exact, case included: `mW` and `MW` are nine orders of magnitude apart.
        rows = [row for row in rows if row["unit"] in units]
        scope.append(f"with unit {'|'.join(units)}")
    rows = filter_by_area(rows, areas, parsed.get("area"), scope)
    search = parsed.get("search")
    if search:
        rows = [row for row in rows if matches_search(search, row["entity_id"], row["name"])]
        scope.append(f"matching {search!r}")

    # Grouped by area, and the sensors in no area last rather than first.
    rows.sort(key=lambda r: (not r["area"], r["area"].lower(), r["name"].lower(), r["entity_id"]))
    matched = len(rows)
    shown = rows[:limit]

    doc: dict = {}
    if not shown:
        where = " ".join(scope) or "in this installation"
        doc.update(empty_listing("sensors", f"0 sensors found {where}"))
        # What is here, so an agent whose filter matched nothing can see the
        # values that would have matched rather than guessing again.
        doc["device_classes"] = sorted({r["device_class"] for r in visible if r["device_class"]})
        doc["units"] = sorted({r["unit"] for r in visible if r["unit"]})
        doc["help"] = HelpBlock(
            [
                "Run `hass-axi sensor list` with no filters to see every sensor",
                "Run `hass-axi sensor list --all` to include diagnostic and config sensors",
            ]
        )
        return doc

    doc["count"] = count_line(len(shown), matched, total, filtered=bool(scope))
    if set_aside:
        doc["set_aside"] = f"{plural(set_aside, 'diagnostic, config or hidden sensor')} not shown"
    doc["sensors"] = project(shown, fields)
    help_lines = ["Run `hass-axi statistics get <entity_id>` for a total or average over a window"]
    if not parsed.get("fields"):
        help_lines.append(
            "Add `--fields entity_id,name,value,unit,area,age` for where each sensor is and "
            "how old its reading is"
        )
    if len(shown) < matched:
        help_lines.append(see_all_line("sensor list", parsed, _FILTERS, matched))
    if set_aside:
        help_lines.append("Run `hass-axi sensor list --all` to include them")
    doc["help"] = HelpBlock(help_lines)
    return doc


def _set_aside(entry: dict) -> bool:
    return entry.get("entity_category") in EXCLUDED_CATEGORIES or bool(entry.get("hidden_by"))


def _row(state, entry, device_names, device_areas, area_names, current) -> dict:
    attributes = state.get("attributes") or {}
    name = registry_name(entry, device_names) if entry else ""
    area_id = effective_area_id(entry, device_areas) if entry else ""
    reported = last_reported(state)
    return {
        "entity_id": state.get("entity_id", ""),
        "name": name or friendly_name(state),
        "value": state.get("state", ""),
        "unit": attributes.get("unit_of_measurement") or "",
        "area": area_names.get(area_id, "") if area_id else "",
        "area_id": area_id,
        "age": _window.age_of(reported, current),
        "device_class": attributes.get("device_class") or "",
        "state_class": attributes.get("state_class") or "",
        "last_reported": reported,
        "entity_category": (entry or {}).get("entity_category") or "",
    }

"""`hass-axi history` -- what an entity's state was, and for how long, over a window.

The timeline is what Home Assistant's recorder holds, read over REST. What this
command adds is the summary an agent would otherwise compute from the rows by
hand: for a reading, its range; for anything else, how long it spent in each
state. Both are measured from the timeline itself and from the window's own
bounds, so the first row -- the state the entity was already in when the window
opened -- counts for the time it held, and is not reported as a change.
"""

from __future__ import annotations

from ..argspec import Command, Flag, Sub
from ..output import HelpBlock
from ..readonly import READ
from . import _window
from ._common import parse_limit

DEFAULT_LIMIT = 20

#: States that say nothing about the value and are left out of a reading's range.
NOT_A_VALUE = ("unavailable", "unknown", "")

COMMAND = Command(
    name="history",
    summary="Read entity state timelines from the recorder, with time in each state",
    usage="usage: hass-axi history get <entity_id>... [flags]",
    subs=(
        Sub(
            name="get",
            access=READ,
            args=("<entity_id>", "[entity_id...]"),
            summary="Show each entity's timeline over a window",
            flags=(
                Flag(
                    "--start", "<age|time>", default=_window.DEFAULT_START, note="e.g. 24h, 7d, ISO"
                ),
                Flag("--end", "<age|time>", note="default now"),
                Flag("--limit", "<n>", default=DEFAULT_LIMIT, note="latest rows per entity"),
            ),
        ),
    ),
    notes=(
        "the first row is the state the entity was already in when the window opened",
        "a bound with no offset is read as UTC",
        "run `hass-axi statistics get <entity_id>` for long-term totals and averages; the "
        "recorder purges this timeline after its retention period, statistics it keeps",
    ),
    examples=(
        "hass-axi history get light.example_lamp",
        "hass-axi history get binary_sensor.example_doorway --start 7d",
        "hass-axi history get sensor.example_temperature --start 2026-01-01T00:00:00Z --end 6h",
    ),
)


def run(ctx, sub: str, parsed):
    requested = _unique(parsed.positionals)
    start, end = _window.window(parsed)
    limit = parse_limit(parsed.get("limit"), default=DEFAULT_LIMIT)
    timelines = ctx.rest().history(requested, _window.iso(start), _window.iso(end))

    by_id: dict = {}
    for position, timeline in enumerate(timelines):
        if not isinstance(timeline, list) or not timeline:
            continue
        first = timeline[0] if isinstance(timeline[0], dict) else {}
        # Every timeline's first row carries its entity_id; the outer list is in
        # the order requested, which is the fallback if one ever does not.
        entity_id = first.get("entity_id") or (
            requested[position] if position < len(requested) else ""
        )
        by_id[entity_id.lower()] = timeline

    entities = []
    clipped = False
    for entity_id in requested:
        timeline = by_id.get(entity_id.lower())
        if not timeline:
            entities.append(
                {"entity_id": entity_id, "changes": 0, "note": "no recorded state in this window"}
            )
            continue
        summary = summarize(entity_id, timeline, start, end)
        if len(summary["timeline"]) > limit:
            summary["timeline"] = summary["timeline"][-limit:]
            clipped = True
        entities.append(summary)

    doc = {
        "window": f"{_window.iso(start)} to {_window.iso(end)} ({_window.span((end - start).total_seconds())})",
        "history": entities,
    }
    help_lines = []
    if clipped:
        help_lines.append(
            f"Showing the latest {limit} rows per entity; run with `--limit <n>` to see more"
        )
    if any(e.get("changes") == 0 and "note" in e for e in entities):
        help_lines.append("Run `hass-axi state get <entity_id>` to confirm the entity exists")
    help_lines.append("Run `hass-axi logbook get --entity <entity_id>` for what caused each change")
    doc["help"] = HelpBlock(help_lines)
    return doc


def summarize(entity_id: str, timeline: list, start, end) -> dict:
    """One entity's row: its name, its change count, its summary and its timeline."""
    first = timeline[0] if isinstance(timeline[0], dict) else {}
    name = (first.get("attributes") or {}).get("friendly_name") or ""
    points = []
    for row in timeline:
        if not isinstance(row, dict):
            continue
        moment = _window.parse_timestamp(row.get("last_changed") or row.get("last_updated"))
        if moment is None:
            continue
        points.append((max(moment, start), str(row.get("state", ""))))

    summary: dict = {"entity_id": entity_id}
    if name:
        summary["name"] = name
    summary["changes"] = max(0, len(points) - 1)
    values = _numbers(points)
    if values is not None:
        summary["min"] = min(values)
        summary["max"] = max(values)
        summary["last"] = points[-1][1]
    else:
        summary["time_in_state"] = _durations(points, end)
    summary["timeline"] = [{"at": _window.iso(moment), "state": state} for moment, state in points]
    return summary


def _numbers(points: list):
    """The numeric values in a timeline, or ``None`` when it is not a reading.

    A timeline is a reading when every state that carries a value parses as a
    number; `unavailable` and `unknown` say nothing about the value either way.
    """
    values = []
    for _, state in points:
        if state in NOT_A_VALUE:
            continue
        try:
            values.append(float(state))
        except ValueError:
            return None
    return values if values else None


def _durations(points: list, end) -> dict:
    """How long each state held within the window, longest first."""
    held: dict = {}
    for index, (moment, state) in enumerate(points):
        until = points[index + 1][0] if index + 1 < len(points) else end
        held[state] = held.get(state, 0.0) + max(0.0, (until - moment).total_seconds())
    ranked = sorted(held.items(), key=lambda item: -item[1])
    return {state: _window.span(seconds) for state, seconds in ranked}


def _unique(ids: list) -> list:
    seen: list = []
    for entity_id in ids:
        if entity_id not in seen:
            seen.append(entity_id)
    return seen

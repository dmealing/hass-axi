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
from ..model.readers import HistoryState
from ..output import HelpBlock
from ..readonly import READ
from ..rest import require_entity_id
from . import _window
from ._common import attributes_of, parse_limit, sent_or

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
    # Home Assistant lowercases the filter, so two spellings of one id are one
    # timeline; the first spelling asked for is the one reported back.
    first_spelling: dict = {}
    for entity_id in parsed.positionals:
        first_spelling.setdefault(entity_id.lower(), entity_id)
    requested = list(first_spelling.values())
    start, end = _window.window(parsed)
    limit = parse_limit(parsed.get("limit"), default=DEFAULT_LIMIT)
    for entity_id in requested:
        require_entity_id(entity_id)
    timelines = ctx.rest().history(requested, _window.iso(start), _window.iso(end))

    by_id: dict = {}
    for position, timeline in enumerate(timelines):
        if not isinstance(timeline, list) or not timeline:
            continue
        first = _first_row(timeline)
        # Every timeline's first row carries its entity_id; the outer list is in
        # the order requested, which is the fallback if one ever does not.
        entity_id = first.entity_id or (requested[position] if position < len(requested) else "")
        by_id[entity_id.lower()] = timeline

    entities = []
    clipped = 0
    for entity_id in requested:
        timeline = by_id.get(entity_id.lower())
        if not timeline:
            entities.append(
                {"entity_id": entity_id, "changes": 0, "note": "no recorded state in this window"}
            )
            continue
        summary = summarize(entity_id, timeline, start, end)
        if len(summary["timeline"]) > limit:
            clipped = max(clipped, len(summary["timeline"]))
            summary["rows"] = f"latest {limit} of {len(summary['timeline'])}"
            summary["timeline"] = summary["timeline"][-limit:]
        entities.append(summary)

    doc = {
        "window": _window.describe(start, end),
        "history": entities,
    }
    help_lines = []
    if clipped:
        help_lines.append(
            f"Showing the latest {limit} rows per entity; the longest timeline has {clipped}, "
            f"so run with `--limit {clipped}` to see all of them"
        )
    if any(e.get("changes") == 0 and "note" in e for e in entities):
        help_lines.append("Run `hass-axi state get <entity_id>` to confirm the entity exists")
    help_lines.append("Run `hass-axi logbook get --entity <entity_id>` for what caused each change")
    doc["help"] = HelpBlock(help_lines)
    return doc


def _first_row(timeline: list) -> HistoryState:
    """A timeline's first row, the one that is a whole state, or an empty one for none."""
    return HistoryState.read(timeline[0] if isinstance(timeline[0], dict) else {})


def summarize(entity_id: str, timeline: list, start, end) -> dict:
    """One entity's row: its name, its change count, its summary and its timeline."""
    name = attributes_of(_first_row(timeline)).friendly_name or ""
    points = []
    for answer in timeline:
        if not isinstance(answer, dict):
            continue
        row = HistoryState.read(answer)
        moment = _window.parse_timestamp(row.last_changed or row.last_updated)
        if moment is None:
            continue
        state = str(sent_or(row, "state"))
        # A row whose state repeats the previous one is an attribute change --
        # the recorder keeps whole states for climate and a few other domains --
        # and is neither a change nor a new timeline entry.
        if points and points[-1][1] == state:
            continue
        points.append((max(moment, start), state))

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
    # The recorder opens a timeline with the state already held at the start of
    # the window. When the first row is later than that, nothing was recorded
    # before it -- a new entity, or one older than the recorder keeps -- and
    # the durations above cover only the rest. Said, because `1d6h` in a window
    # labelled `2d` otherwise reads as a sum that does not add up.
    uncovered = (points[0][0] - start).total_seconds() if points else 0
    if uncovered >= 1:
        summary["note"] = (
            f"no recorded state before {_window.iso(points[0][0])}; "
            f"{_window.span(uncovered)} of the window is not covered"
        )
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

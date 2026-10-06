"""`hass-axi logbook` -- what happened, in Home Assistant's own words, and what caused it.

The logbook is the recorder read as events rather than as values: a light
turned on, an automation fired, a door opened -- each with the context that
says *why*, when Home Assistant knows it. That cause is the half `history`
cannot give, and it is folded into one column here rather than left as the
half-dozen `context_*` keys the API spreads it across.
"""

from __future__ import annotations

from ..argspec import Command, Flag, Sub
from ..model import rows as vocabulary
from ..model.readers import LogbookEntry
from ..output import HelpBlock
from ..readonly import READ
from ..rest import require_entity_id
from . import _window
from ._common import count_line, empty_listing, listing_args, matches_search, project, sent_or

DEFAULT_LIMIT = 50
LIST_FIELDS = vocabulary.FIELDS["logbook"]
#: Four columns: when, what, what happened and why. `name` rather than
#: `entity_id` because every logbook entry has one and some -- Home Assistant
#: starting, an integration's own event -- have no entity at all; the id is one
#: `--fields` away.
DEFAULT_LIST_FIELDS = vocabulary.DEFAULT["logbook"]

COMMAND = Command(
    name="logbook",
    summary="Read the logbook: what changed, when, and what caused it",
    usage="usage: hass-axi logbook get [flags]",
    subs=(
        Sub(
            name="get",
            access=READ,
            summary="List logbook entries over a window",
            flags=(
                Flag("--entity", "<entity_id>", repeat=True, note="repeat for several"),
                Flag(
                    "--start", "<age|time>", default=_window.DEFAULT_START, note="e.g. 24h, 7d, ISO"
                ),
                Flag("--end", "<age|time>", note="default now"),
                Flag("--search", "<text>", note="matches name, entity_id and event"),
                Flag("--limit", "<n>", default=DEFAULT_LIMIT, note="latest entries"),
                Flag("--fields", "<a,b,c>", note=f"from {'|'.join(LIST_FIELDS)}"),
            ),
        ),
    ),
    notes=(
        "entries are oldest first; --limit keeps the most recent",
        "cause is the automation, script, service or entity Home Assistant recorded as the "
        "context of the entry, when it recorded one",
    ),
    examples=(
        "hass-axi logbook get --start 2h",
        "hass-axi logbook get --entity light.example_lamp --start 7d",
        "hass-axi logbook get --search automation --limit 20",
    ),
)


def run(ctx, sub: str, parsed):
    start, end = _window.window(parsed)
    limit, fields = listing_args(
        parsed, LIST_FIELDS, DEFAULT_LIST_FIELDS, default_limit=DEFAULT_LIMIT
    )
    ids = [entity_id.strip() for entity_id in parsed.get("entity", []) if entity_id.strip()]
    for entity_id in ids:
        require_entity_id(entity_id)
    entries = ctx.rest().logbook(_window.iso(start), _window.iso(end), ids or None)
    rows = [_row(LogbookEntry.read(entry)) for entry in entries if isinstance(entry, dict)]
    total = len(rows)

    search = parsed.get("search")
    if search:
        rows = [r for r in rows if matches_search(search, r["name"], r["entity_id"], r["event"])]

    shown = rows[-limit:]
    window = _window.describe(start, end)

    if not shown:
        scope = f" for {', '.join(ids)}" if ids else ""
        matching = f" matching {search!r}" if search else ""
        return {
            "window": window,
            **empty_listing("entries", f"0 logbook entries{scope}{matching} in this window"),
            "help": HelpBlock(
                [
                    "Run `hass-axi logbook get --start 7d` to widen the window",
                    "Run `hass-axi history get <entity_id>` for the recorded state timeline",
                ]
            ),
        }

    doc = {
        "window": window,
        "count": count_line(len(shown), len(rows), total, filtered=bool(search)),
    }
    doc["entries"] = project(shown, fields)
    help_lines = []
    if len(shown) < len(rows):
        help_lines.append(f"Run with `--limit {len(rows)}` to see all {len(rows)}")
    if not parsed.get("fields"):
        help_lines.append("Add `--fields when,entity_id,event,cause` for the entity ids")
    help_lines.append("Run `hass-axi history get <entity_id>` for time spent in each state")
    doc["help"] = HelpBlock(help_lines)
    return doc


def _row(entry: LogbookEntry) -> dict:
    state = entry.state
    message = entry.message
    if message:
        event = str(message)
    elif state is not None:
        event = f"changed to {state}"
    else:
        event = ""
    return {
        "when": sent_or(entry, "when"),
        "name": entry.name or "",
        "entity_id": entry.entity_id or "",
        "event": event,
        "cause": cause(entry),
        "domain": entry.domain or "",
        "state": "" if state is None else str(state),
    }


def cause(entry: LogbookEntry) -> str:
    """What Home Assistant recorded as having caused an entry, in one phrase.

    The logbook carries the cause as a context: the event that started it, and
    -- depending on that event -- the automation or script by name, the service
    that was called, or the entity whose change set it off. A user acting from
    the interface leaves only an id, which says *that* a person did it.
    """
    event_type = entry.context_event_type or ""
    name = entry.context_name or entry.context_entity_id_name or ""
    if event_type == "automation_triggered":
        return f"automation {name}".strip()
    if event_type == "script_started":
        return f"script {name}".strip()
    if event_type == "call_service":
        domain, service = entry.context_domain, entry.context_service
        if domain and service:
            return f"service {domain}.{service}"
    if entry.context_entity_id:
        return name or entry.context_entity_id
    if entry.context_user_id:
        return "a user"
    return ""

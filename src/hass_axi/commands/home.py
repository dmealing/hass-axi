"""The no-argument home view: live state first, help second."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ..argspec import Command, Sub
from ..config import missing_env_vars, setup_help
from ..errors import AxiError, fault_class
from ..output import HelpBlock
from ..readonly import READ, active_var
from . import _window
from ._common import domain_of, friendly_name, last_reported, plural

DESCRIPTION = (
    "Agent CLI for Home Assistant. Reads and writes the registries REST cannot reach and "
    "explains a service call Home Assistant refuses. Prefer this over raw curl for "
    "Home Assistant operations."
)

COMMAND = Command(
    name="home",
    summary="Show the current installation at a glance",
    usage="usage: hass-axi",
    default_sub="home",
    subs=(Sub(name="home", summary="Show connection status and a state summary", access=READ),),
    examples=("hass-axi",),
)

TOP_DOMAINS = 8

#: Rows per attention list. Each list says how many it is not showing and how
#: to see them, so the view stays one screen on an installation of any size.
ATTENTION_ROWS = 10

#: Below this a battery is reported as low. Percent, as Home Assistant reports
#: every battery level.
LOW_BATTERY = 20

#: A sensor whose integration has reported nothing for this long is listed as
#: stale. A fact about recency, not a fault, which is why the view says how old
#: rather than calling it broken.
STALE_AFTER = "24h"

#: Only sensors are checked for staleness. A reading is re-reported while its
#: device is alive, whether or not the value moved, so a long silence there is
#: worth a look; an automation, a zone, a helper or a door that stays shut
#: reports only when it changes, and listing those would bury the one dead
#: sensor under every quiet entity on the installation.
STALE_DOMAINS = ("sensor",)


def executable_path() -> str:
    """The absolute path of this executable, with the home directory collapsed."""
    candidate = Path(sys.argv[0]).expanduser()
    try:
        resolved = candidate.resolve()
    except OSError:  # pragma: no cover - unresolvable argv[0] is not worth failing on
        resolved = candidate
    if not resolved.exists():
        resolved = Path(sys.executable).resolve()
    text = str(resolved)
    home = os.path.expanduser("~")
    if home and text.startswith(home):
        return "~" + text[len(home) :]
    return text


def battery_level(state: dict):
    """An entity's battery percentage, or ``None`` when it does not report one.

    Two shapes carry one: a `battery_level` attribute on the entity itself, and
    a sensor whose device class is `battery` and whose unit is `%`. A reading
    that is not a number -- `unavailable`, `unknown`, `charging` -- is not a
    level.
    """
    attributes = state.get("attributes") or {}
    raw = attributes.get("battery_level")
    if raw is None and (
        domain_of(state.get("entity_id", "")) == "sensor"
        and attributes.get("device_class") == "battery"
        and attributes.get("unit_of_measurement") == "%"
    ):
        raw = state.get("state")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def low_batteries(states: list) -> list:
    rows = []
    for state in states:
        if state.get("state") in ("unavailable", "unknown"):
            continue
        level = battery_level(state)
        if level is not None and level < LOW_BATTERY:
            rows.append(
                {
                    "entity_id": state.get("entity_id", ""),
                    "name": friendly_name(state),
                    "battery": f"{level:g}%",
                }
            )
    rows.sort(key=lambda row: float(row["battery"][:-1]))
    return rows


def stale_entities(states: list, current, after: str = STALE_AFTER) -> list:
    """Sensors whose integration has reported nothing for ``after``, oldest first.

    Entities that are `unavailable` or `unknown` are left out: they are listed
    on their own, and a second listing would count one fact twice.
    """
    threshold = _window.parse_age(after)
    rows = []
    for state in states:
        if domain_of(state.get("entity_id", "")) not in STALE_DOMAINS:
            continue
        if state.get("state") in ("unavailable", "unknown"):
            continue
        moment = _window.parse_timestamp(last_reported(state))
        if moment is None or current - moment < threshold:
            continue
        rows.append((moment, state))
    rows.sort(key=lambda item: item[0])
    return [
        {
            "entity_id": state.get("entity_id", ""),
            "name": friendly_name(state),
            "age": _window.age((current - moment).total_seconds()),
        }
        for moment, state in rows
    ]


def run(ctx, sub: str, parsed):
    doc = {"bin": executable_path(), "description": DESCRIPTION}
    missing = missing_env_vars(ctx.environ)
    if missing:
        # Coded like every other failure: a caller who asked for live state and
        # cannot have it has met a config fault, and scripts that gate on this
        # view rely on telling configured from not. The session hook prints
        # `hass-axi context` rather than this view, precisely because this branch
        # exits 1 -- see `commands/context.py`.
        doc["error"] = f"{' and '.join(missing)} not set in the environment"
        doc["code"] = "NOT_CONFIGURED"
        doc["class"] = fault_class("NOT_CONFIGURED")
        doc["help"] = HelpBlock(setup_help())
        doc["__exit_code__"] = 1
        return doc

    config = ctx.config()
    doc["url"] = config.base_url
    if config.candidate_note():
        doc["fallback"] = f"{config.candidate_note()}; the URLs before it in HA_URL did not answer"
    # Announced only when it is on, matching the `context` document the session
    # hook prints: an unset switch is not worth the tokens, but an agent that
    # cannot see a set one plans writes it will never be allowed to make, and
    # reads the refusals as a broken installation.
    if config.read_only:
        doc["read_only"] = "on"

    try:
        states = ctx.rest().states()
    except AxiError as exc:
        doc["error"] = exc.message
        if exc.code:
            doc["code"] = exc.code
            doc["class"] = exc.fault_class
        doc["help"] = HelpBlock(
            [*exc.help_lines, "Run `hass-axi doctor` to see which transport is failing"]
        )
        doc["__exit_code__"] = 1
        return doc

    counts: dict = {}
    # Counted apart, because they are different facts and `state list --state`
    # can be run against either. Summing them under one label called
    # `unavailable` contradicted `state list --state unavailable` outright on any
    # installation holding an entity that has simply not reported yet -- which is
    # most of them, and this is the live-state view an agent asks for first.
    unavailable = 0
    unknown = 0
    for state in states:
        domain = domain_of(state.get("entity_id", ""))
        counts[domain] = counts.get(domain, 0) + 1
        if state.get("state") == "unavailable":
            unavailable += 1
        elif state.get("state") == "unknown":
            unknown += 1

    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    doc["entities"] = f"{len(states)} in {len(counts)} domains"
    doc["unavailable"] = unavailable
    doc["unknown"] = unknown
    if ranked:
        doc["domains"] = [
            {"domain": name, "entities": total} for name, total in ranked[:TOP_DOMAINS]
        ]

    current = _window.now()
    help_lines = []
    not_reporting = [
        {
            "entity_id": state.get("entity_id", ""),
            "name": friendly_name(state),
            "state": state.get("state"),
            "for": _window.age_of(state.get("last_changed"), current),
        }
        for state in states
        if state.get("state") in ("unavailable", "unknown")
    ]
    if not_reporting:
        doc["not_reporting"] = not_reporting[:ATTENTION_ROWS]
        if len(not_reporting) > ATTENTION_ROWS:
            help_lines.append(
                f"Run `hass-axi state list --state unavailable` and `--state unknown` "
                f"for all {len(not_reporting)}"
            )
    low = low_batteries(states)
    if low:
        doc["low_battery"] = low[:ATTENTION_ROWS]
        if len(low) > ATTENTION_ROWS:
            help_lines.append(
                f"{len(low)} batteries are low; run `hass-axi sensor list --device-class "
                "battery --all` to read every battery sensor"
            )
    stale = stale_entities(states, current)
    if stale:
        doc["stale"] = f"{plural(len(stale), 'sensor')} not reported in {STALE_AFTER}"
        doc["stale_oldest"] = stale[:ATTENTION_ROWS]
        if len(stale) > ATTENTION_ROWS:
            help_lines.append(
                f"Run `hass-axi state list --domain sensor --stale {STALE_AFTER}` "
                f"to see all {len(stale)}"
            )

    help_lines.append("Run `hass-axi state list --domain <domain>` to list entity states")
    if len(ranked) > TOP_DOMAINS:
        help_lines.append(
            f"Run `hass-axi state list` for all {len(states)} entities across {len(counts)} domains"
        )
    help_lines.extend(
        [
            "Run `hass-axi entity list --area <id|name>` to read the registry, which REST cannot reach",
            "Run `hass-axi sensor list --device-class <class>` to find a reading by what it measures",
        ]
    )
    if config.read_only:
        help_lines.append(
            f"This session is read-only; unset {active_var(ctx.environ)} to allow writes"
        )
    else:
        help_lines.append(
            "Run `hass-axi service call <domain>.<service> --target-entity <entity_id>` to act"
        )
    doc["help"] = HelpBlock(help_lines)
    return doc

"""Helpers shared by the command modules."""

from __future__ import annotations

import json
from typing import Any

from .. import readonly
from ..argspec import Flag
from ..errors import AxiError, NotFound, UsageError
from ..output import truncate
from . import _window

#: Preview length for long free-text values before `--full` is needed.
PREVIEW_CHARS = 1200


def domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0] if "." in entity_id else ""


def friendly_name(state: dict) -> str:
    attributes = state.get("attributes") or {}
    return attributes.get("friendly_name") or state.get("entity_id", "")


def last_reported(state: dict) -> str:
    """When the integration last reported a value, whether or not it changed.

    `last_reported` moves on every report, `last_updated` only when the state or
    an attribute changed, so the former is the better freshness signal and the
    latter the fallback for an instance that predates it.
    """
    return state.get("last_reported") or state.get("last_updated") or ""


def not_reported_for(items: list, threshold, current) -> list:
    """``(moment, item)`` for each item silent for at least ``threshold``, oldest first.

    ``items`` are states or `state list` rows -- both carry `state` and the
    report times :func:`last_reported` reads. `unavailable` and `unknown` are
    left out: they are listed on their own, and a second listing would count
    one fact twice. The home view's stale count and `state list --stale` both
    come through here, which is what keeps the two numbers equal.
    """
    found = []
    for item in items:
        if item.get("state") in ("unavailable", "unknown"):
            continue
        moment = _window.parse_timestamp(last_reported(item))
        if moment is not None and current - moment >= threshold:
            found.append((moment, item))
    found.sort(key=lambda pair: pair[0])
    return found


def registry_name(entry: dict, device_names: dict) -> str:
    """The name Home Assistant displays for an entity registry entry.

    Home Assistant composes a display name from **two** registries, and reading
    the entity row alone gets the majority case wrong: most core entities carry
    no name of their own and take all or part of it from their device. The rule
    below is `helpers/entity_registry._async_get_full_entity_name`, called with
    `use_legacy_naming=True` and `parts=(DEVICE, ENTITY)` as
    `async_get_full_entity_name` calls it:

    * a user override (`name`) wins outright, device prefix and all;
    * otherwise the device's display name and `original_name` are joined,
      whichever of the two is present.

    `original_name` needs no unprefixing here: `RegistryEntry.as_partial_dict`
    already publishes `original_name_unprefixed` under that key when the
    integration's own name began with the device's, and `extended_dict` is built
    on top of it, so what arrives over the WebSocket is the entity half alone.

    `device_names` is required rather than optional on purpose: reading the
    entity row by itself is exactly the defect this replaces, and a default that
    permitted it would let the defect back in one omitted argument at a time.
    """
    if entry.get("name"):
        return entry["name"]
    device_name = device_names.get(entry.get("device_id")) or ""
    parts = [device_name, entry.get("original_name") or ""]
    return " ".join(part for part in parts if part)


def plural(count: int, singular: str, many: str = "") -> str:
    """Render a count with a correctly pluralized noun."""
    word = singular if count == 1 else (many or f"{singular}s")
    return f"{count} {word}"


def device_area_map(devices: list) -> dict:
    """Map each device id to the area it sits in."""
    return {device.get("id"): device.get("area_id") for device in devices}


def displayed_device_name(device: dict) -> str:
    """The name Home Assistant displays for one device.

    A user rename (`name_by_user`) wins over the integration's own `name`, which
    is the precedence `device list` reports, the one entity name composition
    needs, and the reason `device update --name` writes `name_by_user`: the
    integration's `name` is not a field Home Assistant lets anybody change.
    """
    return device.get("name_by_user") or device.get("name") or ""


def device_name_map(devices: list) -> dict:
    """Map each device id to the name Home Assistant displays for it."""
    return {device.get("id"): displayed_device_name(device) for device in devices}


def area_name_map(areas: list) -> dict:
    """Map each area id to its display name."""
    return {area.get("area_id"): area.get("name") or "" for area in areas}


def effective_area_id(entry: dict, device_areas: dict) -> str:
    """The area an entity actually belongs to.

    An entity with no area of its own inherits its device's. Every count and
    filter has to apply that fallback or it will disagree with what Home
    Assistant itself shows.
    """
    return entry.get("area_id") or device_areas.get(entry.get("device_id")) or ""


def reject_conflicting_flags(parsed, *pairs: tuple, invocation: str) -> None:
    """Reject each set flag used together with its ``--clear`` counterpart."""
    for set_flag, clear_flag in pairs:
        if parsed.get(clear_flag) and parsed.get(set_flag) is not None:
            raise UsageError(
                f"{set_flag} and {clear_flag} are mutually exclusive",
                help_lines=[f"Run `{invocation} {clear_flag}`"],
                code="CONFLICTING_FLAGS",
            )


def parse_limit(raw, *, default: int) -> int:
    if raw is None:
        return default
    try:
        value = int(str(raw))
    except ValueError:
        raise UsageError(
            f"--limit needs a whole number, got {raw!r}",
            help_lines=["Run the command again with `--limit 50`"],
            code="BAD_LIMIT",
        ) from None
    if value < 1:
        raise UsageError(
            f"--limit must be at least 1, got {value}",
            help_lines=["Run the command again with `--limit 50`"],
            code="BAD_LIMIT",
        )
    return value


def select_fields(raw: str | None, available: list, default: list) -> list:
    """Resolve ``--fields`` against the fields a view can actually produce."""
    if not raw:
        return list(default)
    wanted = [part.strip() for part in raw.split(",") if part.strip()]
    if not wanted:
        return list(default)
    unknown = [name for name in wanted if name not in available]
    if unknown:
        raise UsageError(
            f"unknown field{'s' if len(unknown) > 1 else ''}: {', '.join(unknown)}",
            help_lines=[f"available fields: {', '.join(available)}"],
            code="UNKNOWN_FIELD",
        )
    return wanted


def project(rows: list, fields: list) -> list:
    """Reduce rows to the requested fields, preserving field order."""
    return [{name: row.get(name) for name in fields} for row in rows]


def parse_value(raw: str) -> Any:
    """Interpret a ``key=value`` value as JSON when it parses, else as a string."""
    text = raw.strip()
    if text == "":
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return raw


def parse_pairs(pairs: list, *, flag: str) -> dict:
    """Turn repeated ``--flag key=value`` tokens into a dict."""
    out: dict = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise UsageError(
                f"{flag} needs key=value, got {pair!r}",
                help_lines=[f"Run the command again with `{flag} brightness=180`"],
                code="BAD_PAIR",
            )
        out[key.strip()] = parse_value(value)
    return out


def parse_json_flag(raw: str | None, *, flag: str) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UsageError(
            f"{flag} is not valid JSON: {exc.msg}",
            help_lines=[f"""Run the command again with `{flag} '{{"brightness": 180}}'`"""],
            code="BAD_JSON",
        ) from None
    if not isinstance(parsed, dict):
        raise UsageError(
            f"{flag} must be a JSON object",
            help_lines=[f"""Run the command again with `{flag} '{{"brightness": 180}}'`"""],
            code="BAD_JSON",
        )
    return parsed


def resolve_area(areas: list, needle: str) -> dict:
    """Find an area by ``area_id`` or by name, case-insensitively."""
    for area in areas:
        if area.get("area_id") == needle:
            return area
    lowered = needle.strip().lower()
    matches = [a for a in areas if (a.get("name") or "").strip().lower() == lowered]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        ids = ", ".join(a.get("area_id", "") for a in matches)
        # Exit 1, not 2: the command was well formed, and only a lookup
        # against the live registry could reveal the name is shared.
        raise AxiError(
            f"{needle!r} matches more than one area: {ids}",
            help_lines=[
                "Pass the area_id instead of the name",
                "Run `hass-axi area list` to see each area's id",
            ],
            code="AMBIGUOUS_AREA",
        )
    raise NotFound(
        f"no area with id or name {needle!r}",
        help_lines=[
            "Run `hass-axi area list` to see the areas that exist",
            f'Run `hass-axi area create --name "{needle}"` to add it',
        ],
        code="NO_SUCH_AREA",
    )


def _device_by_id(devices: list, needle: str) -> dict | None:
    for device in devices:
        if device.get("id") == needle:
            return device
    return None


def _no_such_device(needle: str, *, by_name: bool) -> NotFound:
    """The one failed-device-lookup error, phrased for the handle that was tried.

    Exit 1, the same side of the line `resolve_area` puts a missing area on: the
    command was well formed and only the live registry could say the subject is
    not there.
    """
    help_lines = ["Run `hass-axi device list --fields device_id,name` to see each device's id"]
    if by_name:
        help_lines.insert(0, f'Run `hass-axi device list --search "{needle}"` to find it')
    return NotFound(
        f"no device with {'id or name' if by_name else 'id'} {needle!r}",
        help_lines=help_lines,
        code="NO_SUCH_DEVICE",
    )


def resolve_device(devices: list, device_id: str) -> dict:
    """Find a device by its id alone, which is all `entity list --device` is given.

    That flag is declared `<device_id>` and means it: substring-matching an
    opaque hex id is an accident rather than a filter, so there is deliberately
    no name fallback here. An id no device answers to is a failed lookup and not
    an empty result, or an agent that truncated one loops on filter variations
    instead of re-reading the registry that holds the real spelling.
    :func:`resolve_device_ref` is the `<id|name>` form the `device` command
    itself takes.
    """
    needle = device_id.strip()
    device = _device_by_id(devices, needle)
    if device is not None:
        return device
    raise _no_such_device(needle, by_name=False)


def resolve_device_ref(devices: list, needle: str) -> dict:
    """Find a device by its ``id`` or by the name Home Assistant displays for it.

    The id is tried first, so a device whose displayed name happens to be
    another device's id still resolves to the thing that was named. The name is
    the composed one -- `name_by_user` over `name` -- because that is the only
    spelling a user has ever seen, and matching the integration's own name for a
    device somebody renamed would resolve a name nothing displays. Two devices
    may share a displayed name, which is an error rather than a guess for the
    same reason it is on an area.
    """
    text = needle.strip()
    device = _device_by_id(devices, text)
    if device is not None:
        return device
    lowered = text.lower()
    matches = [d for d in devices if displayed_device_name(d).strip().lower() == lowered]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        ids = ", ".join(d.get("id", "") for d in matches)
        raise AxiError(
            f"{needle!r} matches more than one device: {ids}",
            help_lines=[
                "Pass the device_id instead of the name",
                "Run `hass-axi device list --fields device_id,name` to see each device's id",
            ],
            code="AMBIGUOUS_DEVICE",
        )
    raise _no_such_device(text, by_name=True)


def area_is_placed(area_id: str, areas: list) -> bool:
    """Whether an `area_id` names an area that actually exists.

    Home Assistant accepts an `area_id` no area claims -- `entity.update` with a
    mistyped id is taken without complaint -- and an entity left holding one is
    in no area at all as far as every view is concerned. Treating it as placed
    is what made such an entity invisible to `--area <id>` and `--area none`
    alike, and made per-area counts stop summing to the total.
    """
    return bool(area_id) and any(area.get("area_id") == area_id for area in areas)


def filter_by_area(rows: list, areas: list, area_filter, scope: list) -> list:
    """Narrow rows to one area, or to the ones with none.

    Shared by the entity, device and state listings, which apply the identical
    rule: `none` selects the unassigned, anything else resolves by id or name.
    Appends a human-readable phrase to ``scope`` describing what was applied.
    """
    if not area_filter:
        return rows
    if area_filter.strip().lower() in ("none", "null", ""):
        scope.append("with no area")
        return [row for row in rows if not area_is_placed(row["area_id"], areas)]
    area = resolve_area(areas, area_filter)
    scope.append(f"in area {area.get('name')}")
    return [row for row in rows if row["area_id"] == area.get("area_id")]


def count_line(shown: int, matched: int, total: int, *, filtered: bool) -> str:
    """The `count:` value for a list view.

    Reports the filtered count against the installation total, so the agent
    never has to page to find out how much it is not seeing. A filter that
    happens to match everything still says so, because "did my filter apply?"
    and "is that all of them?" are different questions.
    """
    if filtered:
        return f"{shown} of {matched} matched ({total} total)"
    return f"{shown} of {total} total"


def matches_search(needle: str, *values) -> bool:
    lowered = needle.lower()
    return any(lowered in str(value or "").lower() for value in values)


# ------------------------------------------------------------ write previews

#: The one flag that turns a preview into a request, on every command that can
#: change something through a subject it does not declare: `service call`, a
#: write-method `api` request and a write `ws` command. One name on all three,
#: so an agent that learns it once has learnt it everywhere.
WRITE_FLAG_NAME = "--write"
WRITE_FLAG = Flag(
    WRITE_FLAG_NAME,
    boolean=True,
    note="send it; without this the command shows what would be sent and sends nothing",
)

#: The line a preview prints where a result would be, so that "would" is never
#: read as "did".
PREVIEW_LINE = "nothing was sent to Home Assistant"


def preview_note(environ) -> str:
    """What a preview says about itself, including a write that would be refused."""
    if readonly.enabled(environ):
        return f"{PREVIEW_LINE}; this session is read-only so {WRITE_FLAG_NAME} would be refused"
    return PREVIEW_LINE


def preview_help(environ) -> list:
    if readonly.enabled(environ):
        return [f"Unset {readonly.active_var(environ)} to allow writes in this session"]
    return [f"Run the same command with {WRITE_FLAG_NAME} to send it"]


# ------------------------------------------------- shortening a raw response

#: Items kept from each list in a raw `api` or `ws` response before `--full`.
RAW_ITEMS = 25


def shorten(result, hint: str) -> tuple:
    """Shorten an arbitrary JSON response, reporting what was withheld.

    The escape hatches hand back whatever Home Assistant answered, and some of
    those answers are every state or every registry entry in the installation.
    Structure is kept rather than cut mid-document, so what remains is still
    data: every list keeps its first :data:`RAW_ITEMS` items and every string
    its first :data:`PREVIEW_CHARS` characters, through :func:`truncate`, which
    appends the original length.

    Returns ``(result, note, hint)``. ``note`` is empty when nothing was cut;
    otherwise it says what was and how large the whole response is, and
    ``hint`` is handed back for the help block -- the escape hatch is suggested
    only when something was actually withheld.
    """
    cut_lists: list = []
    cut_strings = [0]

    def walk(node):
        if isinstance(node, str):
            text, note = truncate(node, PREVIEW_CHARS, hint)
            if note:
                cut_strings[0] += 1
            return text
        if isinstance(node, list):
            if len(node) > RAW_ITEMS:
                cut_lists.append(len(node))
            return [walk(item) for item in node[:RAW_ITEMS]]
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        return node

    shortened = walk(result)
    if not cut_lists and not cut_strings[0]:
        return result, "", ""

    parts = []
    if cut_lists:
        largest = max(cut_lists)
        if len(cut_lists) == 1:
            parts.append(f"first {RAW_ITEMS} of {largest} items shown")
        else:
            parts.append(
                f"{len(cut_lists)} lists cut to their first {RAW_ITEMS} items "
                f"(the largest holds {largest})"
            )
    if cut_strings[0]:
        parts.append(f"{plural(cut_strings[0], 'string')} cut to {PREVIEW_CHARS} chars")
    total = len(json.dumps(result, separators=(",", ":"), ensure_ascii=False, default=str))
    return shortened, f"{'; '.join(parts)} (truncated, {total} chars total)", hint

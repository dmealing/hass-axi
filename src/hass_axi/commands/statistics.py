"""`hass-axi statistics` -- the recorder's long-term statistics, summarised over a window.

Home Assistant keeps two kinds of long-term statistic and they answer different
questions. A **sum** statistic -- an energy or water meter -- answers "how much
in this window", and the answer is the sum of each bucket's `change`. A **mean**
statistic -- power, temperature -- answers "how high, on average, and how far
either way", and the answer is the buckets' means, minima and maxima. Which kind
a statistic is comes from the recorder's own metadata (`has_sum`, `mean_type`),
never from a device class or a name, and asking for the wrong kind is not an
error upstream: the buckets come back empty. So the kind is read first and the
right `types` are requested for it.

**Known data-quality problems are stated, not corrected.** The number reported
is what the recorder holds; when the buckets show something a reader should
know before trusting it -- buckets missing, a meter that went backwards, a
meter that resets every day -- a caveat says so beside the number. Every check
is a rule about the shape of the buckets and nothing about any particular
integration, so it holds on any installation.
"""

from __future__ import annotations

import math
from datetime import timedelta

from ..argspec import Command, Flag, Sub
from ..errors import NotFound, UsageError
from ..output import HelpBlock
from ..readonly import READ
from . import _window
from ._common import count_line, matches_search, parse_limit, project, select_fields

DEFAULT_LIMIT = 100
LIST_FIELDS = ["statistic_id", "name", "kind", "unit", "source", "unit_class"]
DEFAULT_LIST_FIELDS = ["statistic_id", "name", "kind", "unit"]

#: `mean_type` as the recorder publishes it.
MEAN_NONE, MEAN_ARITHMETIC, MEAN_CIRCULAR = 0, 1, 2

PERIODS = ("5minute", "hour", "day", "week", "month")
_PERIOD_SECONDS = {"5minute": 300, "hour": 3600, "day": 86400, "week": 604800}
_PERIOD_WORD = {
    "5minute": "5-minute",
    "hour": "hourly",
    "day": "daily",
    "week": "weekly",
    "month": "monthly",
}

#: The `types` to ask for, by kind. `state` rides along with a sum so a reset
#: can be seen; `change` is what the total is built from.
SUM_TYPES = ["change", "state", "sum"]
MEAN_TYPES = ["mean", "min", "max"]

COMMAND = Command(
    name="statistics",
    summary="Read recorder statistics: a total for meters, an average with min and max otherwise",
    usage="usage: hass-axi statistics <subcommand> [flags]",
    subs=(
        Sub(
            name="list",
            access=READ,
            summary="List the statistics the recorder keeps",
            flags=(
                Flag("--search", "<text>", note="matches statistic_id and name"),
                Flag("--kind", "<sum|mean>"),
                Flag("--limit", "<n>", default=DEFAULT_LIMIT),
                Flag("--fields", "<a,b,c>", note=f"from {'|'.join(LIST_FIELDS)}"),
            ),
        ),
        Sub(
            name="get",
            access=READ,
            args=("<statistic_id>", "[statistic_id...]"),
            summary="Summarise statistics over a window",
            flags=(
                Flag(
                    "--start", "<age|time>", default=_window.DEFAULT_START, note="e.g. 24h, 7d, ISO"
                ),
                Flag("--end", "<age|time>", note="default now"),
                Flag("--period", "<5minute|hour|day|week|month>", note="default by window"),
                Flag("--buckets", boolean=True, note="also print every bucket"),
            ),
        ),
    ),
    notes=(
        "a sum statistic (an energy or water meter) reports its total over the window; a mean "
        "statistic (power, temperature) reports its average with min and max",
        "an entity's statistic_id is its entity_id; Home Assistant keeps statistics only for "
        "sensors that declare a state_class",
        "the default period is hourly up to 3 days, daily up to 60, monthly beyond; the recorder "
        "keeps 5-minute buckets for about 10 days",
        "caveats state what the buckets show -- missing buckets, a meter that went backwards or "
        "resets -- and the number is never adjusted for them",
    ),
    examples=(
        "hass-axi statistics list --kind sum",
        "hass-axi statistics get sensor.example_legacy_meter --start 7d",
        "hass-axi statistics get sensor.example_temperature --start 24h --buckets",
    ),
)


def run(ctx, sub: str, parsed):
    if sub == "list":
        return _list(ctx, parsed)
    return _get(ctx, parsed)


# ---------------------------------------------------------------------- list


def kind_of(meta: dict) -> str:
    """``sum``, ``mean``, ``circular mean``, or ``""`` for neither.

    ``has_mean`` is read as well as ``mean_type`` because instances before
    ``mean_type`` existed publish only the former.
    """
    if meta.get("has_sum"):
        return "sum"
    mean_type = meta.get("mean_type")
    if mean_type == MEAN_CIRCULAR:
        return "circular mean"
    if mean_type == MEAN_ARITHMETIC or meta.get("has_mean"):
        return "mean"
    return ""


def unit_of(meta: dict) -> str:
    """The unit the values arrive in: the display unit, which the recorder converts to."""
    return (
        meta.get("display_unit_of_measurement") or meta.get("statistics_unit_of_measurement") or ""
    )


def _names(states: list) -> dict:
    return {
        s.get("entity_id"): (s.get("attributes") or {}).get("friendly_name") or ""
        for s in states
        if isinstance(s, dict)
    }


def _list(ctx, parsed):
    wanted_kind = (parsed.get("kind") or "").strip().lower()
    if wanted_kind and wanted_kind not in ("sum", "mean"):
        raise UsageError(
            f"--kind must be sum or mean, got {parsed.get('kind')!r}",
            help_lines=["Run `hass-axi statistics list --kind sum`"],
            code="BAD_KIND",
        )
    with ctx.ws() as client:
        params = {"statistic_type": wanted_kind} if wanted_kind else {}
        metadata = client.run("statistics.list", params) or []
        names = _names(client.run("state.list") or [])

    rows = [
        {
            "statistic_id": meta.get("statistic_id", ""),
            "name": meta.get("name") or names.get(meta.get("statistic_id")) or "",
            "kind": kind_of(meta),
            "unit": unit_of(meta),
            "source": meta.get("source") or "",
            "unit_class": meta.get("unit_class") or "",
        }
        for meta in metadata
        if isinstance(meta, dict)
    ]
    rows.sort(key=lambda row: row["statistic_id"])
    total = len(rows)
    search = parsed.get("search")
    if search:
        rows = [r for r in rows if matches_search(search, r["statistic_id"], r["name"])]
    limit = parse_limit(parsed.get("limit"), default=DEFAULT_LIMIT)
    fields = select_fields(parsed.get("fields"), LIST_FIELDS, DEFAULT_LIST_FIELDS)
    shown = rows[:limit]
    if not shown:
        return {
            "statistics": "0 statistics found" + (f" matching {search!r}" if search else ""),
            "help": HelpBlock(
                [
                    "Home Assistant keeps statistics only for sensors that declare a state_class",
                    "Run `hass-axi sensor list --fields entity_id,name,state_class` to see which do",
                ]
            ),
        }
    help_lines = ["Run `hass-axi statistics get <statistic_id> --start 24h` to summarise one"]
    if len(shown) < len(rows):
        help_lines.append(f"Run `hass-axi statistics list --limit {len(rows)}` to see all")
    return {
        "count": count_line(len(shown), len(rows), total, filtered=bool(search or wanted_kind)),
        "statistics": project(shown, fields),
        "help": HelpBlock(help_lines),
    }


# ----------------------------------------------------------------------- get


def default_period(seconds: float) -> str:
    if seconds <= 3 * 86400:
        return "hour"
    if seconds <= 60 * 86400:
        return "day"
    return "month"


def _period(parsed, seconds: float) -> str:
    raw = (parsed.get("period") or "").strip().lower()
    if not raw:
        return default_period(seconds)
    if raw not in PERIODS:
        raise UsageError(
            f"--period must be one of {', '.join(PERIODS)}, got {parsed.get('period')!r}",
            help_lines=["Run the command again with `--period hour`"],
            code="BAD_PERIOD",
        )
    return raw


def _get(ctx, parsed):
    requested = list(dict.fromkeys(parsed.positionals))
    start, end = _window.window(parsed)
    period = _period(parsed, (end - start).total_seconds())

    with ctx.ws() as client:
        metadata = client.run("statistics.metadata", {"statistic_ids": requested}) or []
        known = {m.get("statistic_id"): m for m in metadata if isinstance(m, dict)}
        missing = [sid for sid in requested if sid not in known]
        if missing:
            raise NotFound(
                f"no statistics are kept for {', '.join(missing)}",
                help_lines=[
                    "Home Assistant keeps statistics only for sensors that declare a state_class",
                    f"Run `hass-axi statistics list --search {missing[0].split('.')[-1]}` to find one",
                    f"Run `hass-axi history get {missing[0]}` for its recorded state timeline instead",
                ],
                code="NO_SUCH_STATISTIC",
            )
        # One request per set of types, because a type the statistic does not
        # keep comes back as an empty value rather than as an error.
        groups: dict = {}
        for sid in requested:
            types = SUM_TYPES if kind_of(known[sid]) == "sum" else MEAN_TYPES
            groups.setdefault(tuple(types), []).append(sid)
        names = _names(client.run("state.list") or [])
        buckets: dict = {}
        for types, ids in groups.items():
            result = client.run(
                "statistics.during_period",
                {
                    "start_time": _window.iso(start),
                    "end_time": _window.iso(end),
                    "statistic_ids": ids,
                    "period": period,
                    "types": list(types),
                },
            )
            buckets.update(result or {})

    rows = [
        summarize(
            {**known[sid], "name": known[sid].get("name") or names.get(sid)},
            buckets.get(sid) or [],
            start,
            period,
            parsed.get("buckets"),
        )
        for sid in requested
    ]
    span = _window.span((end - start).total_seconds())
    doc = {
        "window": f"{_window.iso(start)} to {_window.iso(end)} ({span}, {_PERIOD_WORD[period]} buckets)",
        "statistics": rows,
    }
    help_lines = []
    if not parsed.get("buckets") and any(row["buckets"] for row in rows):
        help_lines.append("Run again with `--buckets` to see every bucket")
    if any(row.get("caveats") for row in rows):
        help_lines.append(
            "Caveats describe the buckets; the numbers above are not adjusted for them"
        )
    doc["help"] = HelpBlock(help_lines)
    return doc


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _round(value: float) -> float:
    return round(value, 3)


def summarize(meta: dict, rows: list, start, period: str, with_buckets: bool = False) -> dict:
    """One statistic's summary over its buckets, with what the buckets show about themselves."""
    kind = kind_of(meta)
    unit = unit_of(meta)
    rows = sorted((r for r in rows if isinstance(r, dict)), key=lambda r: r.get("start") or 0)
    summary: dict = {"statistic_id": meta.get("statistic_id", "")}
    if meta.get("name"):
        summary["name"] = meta["name"]
    summary["kind"] = kind or "none"
    summary["unit"] = unit
    summary["buckets"] = len(rows)
    caveats: list = []

    if not rows:
        caveats.append("no statistics were recorded in this window")
    elif kind == "sum":
        changes = [c for c in (_number(r.get("change")) for r in rows) if c is not None]
        summary["total"] = _round(sum(changes)) if changes else None
        caveats.extend(_sum_caveats(rows, unit))
    else:
        means = [m for m in (_number(r.get("mean")) for r in rows) if m is not None]
        lows = [m for m in (_number(r.get("min")) for r in rows) if m is not None]
        highs = [m for m in (_number(r.get("max")) for r in rows) if m is not None]
        if kind == "circular mean":
            summary["mean"] = _round(_circular_mean(means)) if means else None
            caveats.append("a circular quantity such as a bearing: the mean is a circular mean")
        else:
            summary["mean"] = _round(sum(means) / len(means)) if means else None
        summary["min"] = _round(min(lows)) if lows else None
        summary["max"] = _round(max(highs)) if highs else None
        if not means:
            caveats.append("the buckets carry no mean value")

    gap = _missing_buckets(rows, start, period)
    if gap:
        caveats.append(gap)
    if caveats:
        summary["caveats"] = caveats
    if with_buckets:
        keys = ["change", "state"] if kind == "sum" else ["mean", "min", "max"]
        summary["series"] = [
            {
                "start": _window.iso(_window.parse_timestamp(r.get("start"))),
                **{k: _number(r.get(k)) for k in keys},
            }
            for r in rows
        ]
    return summary


def _sum_caveats(rows: list, unit: str) -> list:
    """What a meter's buckets show about the meter: going backwards, and resetting."""
    caveats = []
    negative = [c for c in (_number(r.get("change")) for r in rows) if c is not None and c < 0]
    if negative:
        amount = " ".join(part for part in (str(_round(-sum(negative))), unit) if part)
        caveats.append(
            f"{len(negative)} bucket{'s' if len(negative) > 1 else ''} went backwards by "
            f"{amount} in all; the total includes them"
        )

    # A drop in the meter's own reading between buckets, in a bucket whose
    # change is not negative, is a reset: the recorder started a new cycle and
    # carried its running sum across it, so the total is right -- but the
    # entity's live state is then a since-reset reading and not a lifetime one,
    # which is the misreading this exists to head off. A drop that the recorder
    # booked as a negative change is the case above, not a reset.
    resets = []
    previous = None
    for row in rows:
        state = _number(row.get("state"))
        if state is None:
            continue
        change = _number(row.get("change"))
        if previous is not None and state < previous and (change is None or change >= 0):
            resets.append(_window.parse_timestamp(row.get("start")))
        previous = state
    if resets:
        times = [moment for moment in resets if moment is not None]
        days = (times[-1] - times[0]).total_seconds() / 86400 if len(times) > 1 else 0
        if len(times) >= 2 and abs(days / (len(times) - 1) - 1) <= 0.1:
            caveats.append(
                f"the meter reset {len(resets)} times, about once a day, so its live state is a "
                "since-reset reading rather than a running total; the total here counts across "
                "the resets"
            )
        else:
            caveats.append(
                f"the meter reset {len(resets)} time{'s' if len(resets) > 1 else ''} in this "
                "window; the total here counts across the resets"
            )
    return caveats


def _missing_buckets(rows: list, start, period: str) -> str:
    """Buckets absent before the first one and between any two, but not after the last.

    The trailing edge is left alone on purpose: the recorder compiles a bucket
    only after its period ends, so the most recent one is routinely not there
    yet and calling it missing would put a caveat on every healthy statistic.
    Monthly buckets vary in length and are not checked.
    """
    step = _PERIOD_SECONDS.get(period)
    if not step or not rows:
        return ""
    starts = [_window.parse_timestamp(r.get("start")) for r in rows]
    starts = [s for s in starts if s is not None]
    if not starts:
        return ""
    first_slot = _align_up(start, step)
    missing = max(0, int((starts[0] - first_slot).total_seconds() // step))
    for earlier, later in zip(starts, starts[1:]):
        missing += max(0, int((later - earlier).total_seconds() // step) - 1)
    if not missing:
        return ""
    expected = missing + len(starts)
    return (
        f"{missing} of {expected} {_PERIOD_WORD[period]} buckets have no data; the result covers "
        f"only the {len(starts)} that {'does' if len(starts) == 1 else 'do'}"
    )


def _align_up(moment, step: int):
    epoch = moment.timestamp()
    aligned = math.ceil(epoch / step) * step
    return moment + timedelta(seconds=aligned - epoch)


def _circular_mean(degrees: list) -> float:
    sines = sum(math.sin(math.radians(d)) for d in degrees)
    cosines = sum(math.cos(math.radians(d)) for d in degrees)
    return math.degrees(math.atan2(sines, cosines)) % 360

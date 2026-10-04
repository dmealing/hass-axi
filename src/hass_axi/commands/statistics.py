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

from ..argspec import Command, Flag, Sub
from ..errors import NotFound, UsageError
from ..output import HelpBlock
from ..readonly import READ
from ..toolkit.recorder import (
    MEAN_TYPES,
    PERIOD_WORD,
    PERIODS,
    SUM_TYPES,
    default_period,
    kind_of,
    summarize,
    unit_of,
    wants_hourly,
)
from . import _window
from ._common import (
    count_line,
    empty_listing,
    listing_args,
    matches_search,
    project,
    search_term,
    see_all_line,
)

DEFAULT_LIMIT = 100
LIST_FIELDS = ["statistic_id", "name", "kind", "unit", "source", "unit_class"]
DEFAULT_LIST_FIELDS = ["statistic_id", "name", "kind", "unit"]

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
        "the total, mean, min and max cover the window asked for whatever the period: they are "
        "read from the hourly rows inside it, because a daily, weekly or monthly bucket starts "
        "before --start",
        "caveats state what the buckets show -- missing buckets, a meter that went backwards or "
        "resets, one bucket that dwarfs the rest -- and the number is never adjusted for them",
    ),
    examples=(
        "hass-axi statistics list --kind sum",
        "hass-axi statistics get sensor.example_legacy_meter --start 7d",
        "hass-axi statistics get sensor.example_temperature --start 24h",
    ),
)


def run(ctx, sub: str, parsed):
    if sub == "list":
        return _list(ctx, parsed)
    return _get(ctx, parsed)


# ---------------------------------------------------------------------- list


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
    limit, fields = listing_args(
        parsed, LIST_FIELDS, DEFAULT_LIST_FIELDS, default_limit=DEFAULT_LIMIT
    )
    with ctx.ws() as client:
        # Every statistic is read and the kind is filtered here, so `total` is
        # the installation's and not the size of the filtered answer.
        metadata = client.run("statistics.list", {}) or []
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
    if wanted_kind:
        # `mean` takes in a circular mean, as the recorder's own
        # `statistic_type` filter does: both are statistics that keep a mean.
        rows = [r for r in rows if r["kind"].endswith(wanted_kind)]
    search = parsed.get("search")
    if search:
        rows = [r for r in rows if matches_search(search, r["statistic_id"], r["name"])]
    shown = rows[:limit]
    if not shown:
        scope = (f" of kind {wanted_kind}" if wanted_kind else "") + (
            f" matching {search!r}" if search else ""
        )
        return {
            **empty_listing("statistics", f"0 statistics found{scope}"),
            "total": f"{total} statistics in this installation",
            "help": HelpBlock(
                [
                    "Home Assistant keeps statistics only for sensors that declare a state_class",
                    "Run `hass-axi sensor list --fields entity_id,name,state_class` to see which do",
                ]
            ),
        }
    help_lines = ["Run `hass-axi statistics get <statistic_id> --start 24h` to summarise one"]
    if len(shown) < len(rows):
        help_lines.append(
            see_all_line("statistics list", parsed, ("--kind", "--search"), len(rows))
        )
    return {
        "count": count_line(len(shown), len(rows), total, filtered=bool(search or wanted_kind)),
        "statistics": project(shown, fields),
        "help": HelpBlock(help_lines),
    }


# ----------------------------------------------------------------------- get


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
                    f"Run `hass-axi statistics list --search {search_term(missing[0])}` to find one",
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
        # The numbers come from the hourly rows inside the window whenever the
        # display period is one the recorder widens the window for.
        exact = wants_hourly(period, (end - start).total_seconds())
        buckets: dict = {}
        hourly: dict = {}
        for types, ids in groups.items():
            query = {
                "start_time": _window.iso(start),
                "end_time": _window.iso(end),
                "statistic_ids": ids,
                "types": list(types),
            }
            buckets.update(
                client.run("statistics.during_period", {**query, "period": period}) or {}
            )
            if exact:
                hourly.update(
                    client.run("statistics.during_period", {**query, "period": "hour"}) or {}
                )

    rows = [
        summarize(
            {**known[sid], "name": known[sid].get("name") or names.get(sid)},
            buckets.get(sid) or [],
            start,
            period,
            end=end,
            hourly=(hourly.get(sid) or []) if exact else None,
        )
        for sid in requested
    ]
    doc = {
        "window": _window.describe(start, end, f", {PERIOD_WORD[period]} buckets"),
        "statistics": rows,
    }
    if any(row.get("caveats") for row in rows):
        doc["help"] = HelpBlock(
            ["Caveats describe the buckets; the numbers above are not adjusted for them"]
        )
    return doc

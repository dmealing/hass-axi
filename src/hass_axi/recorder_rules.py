"""What recorder statistics say, and what their buckets show about themselves.

Pure rules over the rows `recorder/statistics_during_period` answers with and
the metadata `recorder/list_statistic_ids` publishes. Nothing here fetches,
prints or parses an argument: the caller reads the recorder and hands the rows
over, and gets back a summary and a list of caveats. That is deliberate -- the
rules are the same for any client of a Home Assistant recorder, and a module
with no transport, no CLI and no output in it can move to wherever a second
client can import it.

Three facts carry most of the weight, and each was read out of
`components/recorder/statistics.py` rather than guessed:

* **A statistic's kind comes from its metadata**, never from a device class or
  a name: `has_sum` is a meter; `mean_type` 1 an arithmetic mean, 2 a circular
  one.
* **The total is the sum of `change`.** Not `sum[-1] - sum[0]`, which loses the
  first bucket, and not the live state.
* **A coarse period widens the window.** The recorder aligns `start_time` back
  to the start of its day, week or month and `end_time` forward to the end of
  its own before it reads, so a daily, weekly or monthly bucket covers time
  outside the window that was asked for. The numbers are therefore read from
  the hourly rows that begin inside the window whenever the caller has them.

Caveats state, and the number is never adjusted. Every check is a rule about
the shape of the buckets; none names an integration.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone

#: `mean_type` as the recorder publishes it.
MEAN_NONE, MEAN_ARITHMETIC, MEAN_CIRCULAR = 0, 1, 2

PERIODS = ("5minute", "hour", "day", "week", "month")
PERIOD_SECONDS = {"5minute": 300, "hour": 3600, "day": 86400, "week": 604800}
PERIOD_WORD = {
    "5minute": "5-minute",
    "hour": "hourly",
    "day": "daily",
    "week": "weekly",
    "month": "monthly",
}

#: Periods the recorder widens the window for. It aligns both ends outward to
#: the period before it reads -- `start_time` back to local midnight, the
#: Monday or the first of the month, `end_time` forward to the next one -- so
#: a coarse bucket's `change` runs from the bucket's own start, which is before
#: `--start`. Seven days of daily buckets is eight buckets; seven days of
#: monthly ones is the whole month. Hourly and 5-minute rows are not widened.
COARSE_PERIODS = ("day", "week", "month")

#: The longest window whose numbers are read from hourly rows. Beyond it the
#: coarse buckets are summed as they come and the summary says what they cover.
MAX_HOURLY_SECONDS = 400 * 86400

#: One bucket is called out when it is this many times the median positive one
#: and more than half of everything.
OUTLIER_FACTOR = 20

#: The `types` to ask for, by kind. `change` is what the total is built from,
#: and `state` rides along so a reset can be seen; the running `sum` is not
#: read, so it is not asked for.
SUM_TYPES = ["change", "state"]
MEAN_TYPES = ["mean", "min", "max"]


def parse_timestamp(value) -> datetime | None:
    """A bucket boundary as the recorder sends one: epoch seconds or ms, or ISO text."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    text = str(value)
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def iso(moment: datetime) -> str:
    """ISO 8601, UTC, whole seconds."""
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


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


def default_period(seconds: float) -> str:
    if seconds <= 3 * 86400:
        return "hour"
    if seconds <= 60 * 86400:
        return "day"
    return "month"


def wants_hourly(period: str, seconds: float) -> bool:
    """Whether a window's numbers should be read from hourly rows rather than its buckets."""
    return period in COARSE_PERIODS and seconds <= MAX_HOURLY_SECONDS


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _round(value: float):
    """Three decimals, and a whole number as one: `13921777`, not `13921777.0`."""
    rounded = round(value, 3)
    return int(rounded) if rounded == int(rounded) else rounded


def inside(rows: list, start, end) -> list:
    """The rows that begin inside the window, oldest first."""
    kept = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        moment = parse_timestamp(row.get("start"))
        if moment is None or moment < start or (end is not None and moment >= end):
            continue
        kept.append(row)
    return sorted(kept, key=lambda r: r.get("start") or 0)


def overhang(rows: list, start, end) -> str:
    """Say so when the buckets summed cover more time than the window asked for."""
    first = parse_timestamp(rows[0].get("start"))
    last = parse_timestamp(rows[-1].get("end"))
    early = first is not None and first < start
    late = last is not None and end is not None and last > end
    if not (early or late):
        return ""
    covered = f"{iso(first or start)} to {iso(last or end)}"
    return (
        f"the buckets cover {covered}, which is more than the window asked for; the "
        "numbers here include the extra time"
    )


def summarize(meta: dict, rows: list, start, period: str, *, end=None, hourly=None) -> dict:
    """One statistic's summary over its buckets, with what the buckets show about themselves.

    ``hourly`` is the statistic's hourly rows for the same window, passed when
    the display period is one the recorder widens the window for. The numbers
    are then read from the hourly rows that begin inside the window, so the
    total covers what was asked for; the display buckets still say how many
    there are and carry the caveats. Without it the buckets are summed as they
    came, and the summary says so if they reach outside the window.
    """
    kind = kind_of(meta)
    unit = unit_of(meta)
    rows = sorted((r for r in rows if isinstance(r, dict)), key=lambda r: r.get("start") or 0)
    measured = rows if hourly is None else inside(hourly, start, end)
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
        changes = [c for c in (_number(r.get("change")) for r in measured) if c is not None]
        summary["total"] = _round(sum(changes)) if changes else None
        caveats.extend(sum_caveats(rows, unit))
    else:
        means = [m for m in (_number(r.get("mean")) for r in measured) if m is not None]
        lows = [m for m in (_number(r.get("min")) for r in measured) if m is not None]
        highs = [m for m in (_number(r.get("max")) for r in measured) if m is not None]
        if kind == "circular mean":
            # A bearing wraps at 360, so neither the arithmetic mean nor the
            # smallest and largest value say anything: 350 and 10 average 180
            # and span 340 degrees, for a wind that never left north.
            summary["mean"] = _round(circular_mean(means)) if means else None
            caveats.append(
                "a circular quantity such as a bearing: the mean is a circular mean, and min "
                "and max are not reported because they wrap"
            )
        else:
            summary["mean"] = _round(sum(means) / len(means)) if means else None
            summary["min"] = _round(min(lows)) if lows else None
            summary["max"] = _round(max(highs)) if highs else None
        if not means:
            caveats.append("the buckets carry no mean value")

    if rows and hourly is None and period in COARSE_PERIODS:
        outside = overhang(rows, start, end)
        if outside:
            caveats.append(outside)
    gap = missing_buckets(rows, start, period)
    if gap:
        caveats.append(gap)
    if caveats:
        summary["caveats"] = caveats
    return summary


def sum_caveats(rows: list, unit: str) -> list:
    """What a meter's buckets show about the meter: going backwards, and resetting."""
    caveats = []
    negative = [c for c in (_number(r.get("change")) for r in rows) if c is not None and c < 0]
    if negative:
        amount = " ".join(part for part in (str(_round(-sum(negative))), unit) if part)
        caveats.append(
            f"{len(negative)} bucket{'s' if len(negative) > 1 else ''} went backwards by "
            f"{amount} in all; the total includes them"
        )

    # One bucket that dwarfs the rest. A meter that briefly reported a lifetime
    # figure as a daily one, or one day's genuine heavy use: the buckets cannot
    # say which, so the caveat states the shape and the total stays as it is.
    positive = [
        (c, r) for c, r in ((_number(r.get("change")), r) for r in rows) if c is not None and c > 0
    ]
    if len(positive) >= 3:
        largest, row = max(positive, key=lambda pair: pair[0])
        median = statistics.median(c for c, _ in positive)
        whole = sum(c for c, _ in positive)
        if largest > OUTLIER_FACTOR * median and largest > whole / 2:
            moment = parse_timestamp(row.get("start"))
            when = f" starting {iso(moment)}" if moment is not None else ""
            amount = " ".join(part for part in (str(_round(largest)), unit) if part)
            caveats.append(
                f"one bucket{when} holds {amount}, {round(100 * largest / whole)}% of all the "
                f"increase and more than {OUTLIER_FACTOR} times the median bucket; "
                "the total includes it"
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
        moment = parse_timestamp(row.get("start"))
        dropped = previous is not None and state < previous
        if dropped and (change is None or change >= 0) and moment is not None:
            resets.append(moment)
        previous = state
    if resets:
        days = (resets[-1] - resets[0]).total_seconds() / 86400
        if len(resets) >= 2 and abs(days / (len(resets) - 1) - 1) <= 0.1:
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


def missing_buckets(rows: list, start, period: str) -> str:
    """Buckets absent before the first one and between any two, but not after the last.

    The trailing edge is left alone on purpose: the recorder compiles a bucket
    only after its period ends, so the most recent one is routinely not there
    yet and calling it missing would put a caveat on every healthy statistic.
    Monthly buckets vary in length and are not checked.

    The slots are aligned in UTC while daily and weekly buckets start at the
    installation's local midnight and Monday. That cannot invent a missing
    bucket: the offset is less than one period, and every count here is a whole
    number of periods rounded down, so only a gap of a full period or more is
    ever counted -- a 23- or 25-hour day across a clock change included.
    """
    step = PERIOD_SECONDS.get(period)
    if not step or not rows:
        return ""
    starts = [parse_timestamp(r.get("start")) for r in rows]
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
        f"{missing} of {expected} {PERIOD_WORD[period]} buckets have no data; the result covers "
        f"only the {len(starts)} that {'does' if len(starts) == 1 else 'do'}"
    )


def _align_up(moment, step: int):
    epoch = moment.timestamp()
    aligned = math.ceil(epoch / step) * step
    return moment + timedelta(seconds=aligned - epoch)


def circular_mean(degrees: list) -> float:
    sines = sum(math.sin(math.radians(d)) for d in degrees)
    cosines = sum(math.cos(math.radians(d)) for d in degrees)
    return math.degrees(math.atan2(sines, cosines)) % 360

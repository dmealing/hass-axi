"""Time windows for the recorder reads: `history`, `logbook` and `statistics`.

All three take `--start` and `--end`, and all three answer the same two
questions the same way, so the rules live here once:

- **A bound is an instant or an age.** `2026-01-01T06:00:00+00:00` is an
  instant; `24h` is "24 hours before now". An age is what an agent almost always
  means -- "what happened overnight" -- and it never has to work out the current
  time to say it. A bound with no offset is read as UTC, because the time zone
  of the machine running this is not the installation's and guessing one is how
  a window lands an hour off without anybody noticing.
- **The end is always sent.** Home Assistant's history and logbook views end a
  window that has no `end_time` one *day* after its start rather than now, so a
  week-long `--start 7d` without an explicit end silently answers for the first
  day only. Sending `now` is what makes the window the one that was asked for.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..errors import UsageError

#: `30m`, `24h`, `7d`, `2w`, optionally with a decimal: `1.5h`.
_AGE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhdw])\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

DEFAULT_START = "24h"


def now() -> datetime:
    """The current instant, in UTC. One function so a test can move it."""
    return datetime.now(timezone.utc)


def parse_age(raw: str) -> timedelta | None:
    """``24h`` as a :class:`timedelta`, or ``None`` when ``raw`` is not an age."""
    match = _AGE.match(raw or "")
    if match is None:
        return None
    return timedelta(seconds=float(match.group(1)) * _UNIT_SECONDS[match.group(2).lower()])


def parse_instant(raw: str, *, flag: str, current: datetime) -> datetime:
    """Read one bound: an age before ``current``, ``now``, or an ISO 8601 instant."""
    text = (raw or "").strip()
    if text.lower() == "now":
        return current
    age = parse_age(text)
    if age is not None:
        return current - age
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        moment = datetime.fromisoformat(candidate)
    except ValueError:
        raise UsageError(
            f"{flag} needs an age such as 24h or an ISO 8601 time, got {raw!r}",
            help_lines=[
                f"Run the command again with `{flag} 24h` (ages: s, m, h, d, w)",
                f"or with `{flag} 2026-01-01T06:00:00+00:00`",
            ],
            code="BAD_TIME",
        ) from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def window(parsed, *, default_start: str = DEFAULT_START) -> tuple:
    """Resolve ``--start``/``--end`` to two UTC instants, start strictly first."""
    current = now()
    start = parse_instant(parsed.get("start") or default_start, flag="--start", current=current)
    end_raw = parsed.get("end")
    end = parse_instant(end_raw, flag="--end", current=current) if end_raw else current
    if start >= end:
        raise UsageError(
            f"--start ({iso(start)}) must be before --end ({iso(end)})",
            help_lines=["Run the command again with `--start 24h` and no --end, which means now"],
            code="BAD_WINDOW",
        )
    return start, end


def iso(moment: datetime) -> str:
    """The wire form Home Assistant parses: ISO 8601, UTC, whole seconds."""
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_timestamp(value) -> datetime | None:
    """Read a timestamp as Home Assistant sends one: ISO text, or epoch seconds or ms."""
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


def age(seconds: float) -> str:
    """A duration in the largest whole unit that fits: ``45s``, ``12m``, ``3h``, ``2d``."""
    seconds = max(0, int(seconds))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def span(seconds: float) -> str:
    """A duration in two units where the second one matters: ``3h12m``, ``2d4h``."""
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d{hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h{minutes}m" if minutes else f"{hours}h"
    if minutes:
        return f"{minutes}m{secs}s" if secs else f"{minutes}m"
    return f"{secs}s"


def age_of(value, current: datetime) -> str:
    """How long ago ``value`` was, or ``""`` when it cannot be read."""
    moment = parse_timestamp(value)
    if moment is None:
        return ""
    return age((current - moment).total_seconds())

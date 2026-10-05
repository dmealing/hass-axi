"""P9: what the recorder reads say, against the recorder read directly.

The window is a pair of fixed instants that deliberately do not fall on a
bucket boundary, because that is where a total can cover more time than was
asked for: a daily bucket starts at local midnight, a monthly one on the first.
"""

from __future__ import annotations

import statistics as stats
import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest

#: How many statistics one run summarises. The whole registry is one raw
#: query; the tool is asked in batches so no single command is unreasonable.
BATCH = 20
LIMIT = 200


def window(days: float) -> tuple:
    """Two instants, off every boundary: now less a few minutes, back `days` and 3h17m."""
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=7)
    start = end - timedelta(days=days, hours=3, minutes=17)
    return start, end


def iso(moment) -> str:
    return moment.replace(microsecond=0).isoformat()


def metadata(target) -> dict:
    return {m["statistic_id"]: m for m in target.ws("recorder/list_statistic_ids")}


def hourly(target, ids, start, end, types) -> dict:
    return target.ws(
        "recorder/statistics_during_period",
        start_time=iso(start),
        end_time=iso(end),
        statistic_ids=ids,
        period="hour",
        types=types,
    )


def inside(rows, start, end) -> list:
    low, high = start.timestamp() * 1000, end.timestamp() * 1000
    return [r for r in rows if low <= r["start"] < high]


def summaries(target, ids, start, end, period=None) -> dict:
    found = {}
    for index in range(0, len(ids), BATCH):
        argv = [
            "statistics",
            "get",
            *ids[index : index + BATCH],
            "--start",
            iso(start),
            "--end",
            iso(end),
        ]
        if period:
            argv += ["--period", period]
        document = target.ok(*argv)
        found.update({row["statistic_id"]: row for row in document["statistics"]})
    return found


def test_statistics_list_agrees_with_the_recorders_metadata(house):
    raw = metadata(house)
    document = house.ok(
        "statistics", "list", "--limit", "100000", "--fields", "statistic_id,kind,unit"
    )
    mine = {row["statistic_id"]: row for row in document["statistics"]}
    assert sorted(mine) == sorted(raw)
    for statistic_id, meta in raw.items():
        kind = (
            "sum"
            if meta.get("has_sum")
            else ("circular mean" if meta.get("mean_type") == 2 else "mean")
        )
        assert mine[statistic_id]["kind"] in (kind, ""), statistic_id
    sums = house.ok("statistics", "list", "--kind", "sum", "--limit", "100000")
    if sums["statistics"]:
        assert sums["count"].endswith(f"({len(raw)} total)")


@pytest.mark.parametrize("period", [None, "hour", "day", "week", "month"])
@pytest.mark.parametrize("days", [3, 7])
def test_a_total_covers_the_window_that_was_asked_for(house, days, period):
    """Whatever the display period, the total is the change inside the window."""
    meters = sorted(i for i, m in metadata(house).items() if m.get("has_sum"))[:LIMIT]
    if not meters:
        pytest.skip("this installation keeps no sum statistics")
    start, end = window(days)
    raw = hourly(house, meters, start, end, ["change"])
    mine = summaries(house, meters, start, end, period)
    wrong = []
    for statistic_id in meters:
        changes = [
            r["change"]
            for r in inside(raw.get(statistic_id, []), start, end)
            if r.get("change") is not None
        ]
        total = mine[statistic_id].get("total")
        if not changes:
            continue
        expected = round(sum(changes), 3)
        if total is None or abs(total - expected) > max(0.002, abs(expected) * 1e-9):
            wrong.append(statistic_id)
    assert not wrong, (
        f"{len(wrong)} of {len(meters)} totals differ from the hourly change in the window"
    )


def test_a_mean_is_the_mean_inside_the_window(house):
    readings = sorted(
        i for i, m in metadata(house).items() if not m.get("has_sum") and m.get("mean_type") == 1
    )[:LIMIT]
    if not readings:
        pytest.skip("this installation keeps no mean statistics")
    start, end = window(7)
    raw = hourly(house, readings, start, end, ["mean", "min", "max"])
    mine = summaries(house, readings, start, end)
    wrong = []
    for statistic_id in readings:
        rows = inside(raw.get(statistic_id, []), start, end)
        means = [r["mean"] for r in rows if r.get("mean") is not None]
        if not means:
            continue
        row = mine[statistic_id]
        expected = (
            round(sum(means) / len(means), 3),
            round(min(r["min"] for r in rows if r.get("min") is not None), 3),
            round(max(r["max"] for r in rows if r.get("max") is not None), 3),
        )
        got = (row.get("mean"), row.get("min"), row.get("max"))
        if any(g is None or abs(g - e) > 0.002 for g, e in zip(got, expected)):
            wrong.append(statistic_id)
    assert not wrong, (
        f"{len(wrong)} of {len(readings)} means differ from the hourly rows in the window"
    )


def test_a_bucket_that_dwarfs_the_rest_is_called_out(house):
    """Detected here by the same shape rule, from the raw daily buckets."""
    meters = sorted(i for i, m in metadata(house).items() if m.get("has_sum"))[:LIMIT]
    if not meters:
        pytest.skip("this installation keeps no sum statistics")
    start, end = window(7)
    raw = house.ws(
        "recorder/statistics_during_period",
        start_time=iso(start),
        end_time=iso(end),
        statistic_ids=meters,
        period="day",
        types=["change"],
    )
    mine = summaries(house, meters, start, end, "day")
    expected, silent, noisy = 0, [], []
    for statistic_id in meters:
        positive = [r["change"] for r in raw.get(statistic_id, []) if (r.get("change") or 0) > 0]
        outlier = (
            len(positive) >= 3
            and max(positive) > 20 * stats.median(positive)
            and max(positive) > sum(positive) / 2
        )
        said = any("one bucket" in c for c in mine[statistic_id].get("caveats", []))
        expected += outlier
        if outlier and not said:
            silent.append(statistic_id)
        if said and not outlier:
            noisy.append(statistic_id)
    assert not silent, f"{len(silent)} of {expected} absurd buckets carry no caveat"
    assert not noisy, f"{len(noisy)} statistics carry an outlier caveat the buckets do not support"


def test_history_agrees_with_the_recorder_and_says_what_it_does_not_cover(house, snapshot):
    start, end = window(2)
    sample = [
        i
        for i in sorted(snapshot.state)
        if i.split(".")[0] in ("light", "switch", "binary_sensor", "lock", "cover")
    ]
    sample = sample[:: max(1, len(sample) // 25)][:25]
    if not sample:
        pytest.skip("nothing suitable to sample")
    checked, uncovered = 0, 0
    for entity_id in sample:
        query = urllib.parse.urlencode(
            {"filter_entity_id": entity_id, "end_time": iso(end), "minimal_response": ""}
        )
        raw = house.rest(f"/history/period/{urllib.parse.quote(iso(start), safe='')}?{query}")
        document = house.ok(
            "history",
            "get",
            entity_id,
            "--start",
            iso(start),
            "--end",
            iso(end),
            "--limit",
            "100000",
        )
        entity = document["history"][0]
        if not raw or not raw[0]:
            assert entity["changes"] == 0
            continue
        states = []
        for row in raw[0]:
            if not states or states[-1] != row["state"]:
                states.append(row["state"])
        assert [row["state"] for row in entity["timeline"]] == states, entity_id
        assert entity["changes"] == len(states) - 1
        first = datetime.fromisoformat(raw[0][0]["last_changed"].replace("Z", "+00:00"))
        if (first - start).total_seconds() >= 1:
            uncovered += 1
            assert "is not covered" in entity.get("note", ""), (
                f"{entity_id}: an uncovered start went unsaid"
            )
        else:
            assert "note" not in entity
        checked += 1
    assert checked


def test_the_logbook_returns_the_rows_the_recorder_holds(house):
    end = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
    start = end - timedelta(hours=2)
    query = urllib.parse.urlencode({"end_time": iso(end)})
    raw = house.rest(f"/logbook/{urllib.parse.quote(iso(start), safe='')}?{query}")
    document = house.ok(
        "logbook",
        "get",
        "--start",
        iso(start),
        "--end",
        iso(end),
        "--limit",
        "100000",
        "--fields",
        "when,entity_id",
    )
    assert len(document["entries"]) == len(raw)
    assert [row["when"] for row in document["entries"]] == [row["when"] for row in raw]


def test_a_limited_history_says_how_many_rows_there_are(house, snapshot):
    start, end = window(2)
    for entity_id in sorted(snapshot.state):
        if entity_id.split(".")[0] not in ("sensor", "binary_sensor"):
            continue
        document = house.ok(
            "history", "get", entity_id, "--start", iso(start), "--end", iso(end), "--limit", "2"
        )
        entity = document["history"][0]
        if "rows" in entity:
            total = int(entity["rows"].rsplit(" ", 1)[1])
            assert total > 2
            assert any(f"--limit {total}" in line for line in document["help"])
            return
    pytest.skip("no sampled entity had more than two rows in the window")

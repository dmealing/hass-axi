"""`history`, `logbook`, `statistics`, `sensor`, `ping` and the home view's attention lists.

Every case runs against the doubles in `conftest.py`, whose recorder endpoints
are transcribed from Home Assistant 2026.8.3, with "now" pinned to the end of
the fixture day so the default `--start 24h` window is that day exactly.
"""

from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from urllib.parse import unquote

import pytest

from conftest import FAKE_TOKEN, RECORDER_NOW, meter_rows
from hass_axi.commands import _window
from hass_axi.errors import UsageError


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    moment = datetime.fromisoformat(RECORDER_NOW)
    monkeypatch.setattr(_window, "now", lambda: moment)
    return moment


def as_json(run_cli, argv, env):
    code, out = run_cli(["--json", *argv], env)
    return code, json.loads(out)


# ------------------------------------------------------------------- windows


class _Parsed(dict):
    def get(self, key, default=None):
        return super().get(key, default)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("24h", "2026-01-01T00:00:00+00:00"),
        ("30m", "2026-01-01T23:30:00+00:00"),
        ("1.5h", "2026-01-01T22:30:00+00:00"),
        ("2d", "2025-12-31T00:00:00+00:00"),
        ("1w", "2025-12-26T00:00:00+00:00"),
        ("2026-01-01T06:00:00Z", "2026-01-01T06:00:00+00:00"),
        ("2026-01-01T06:00:00+02:00", "2026-01-01T04:00:00+00:00"),
        # No offset is UTC, never this machine's zone.
        ("2026-01-01T06:00:00", "2026-01-01T06:00:00+00:00"),
        ("now", "2026-01-02T00:00:00+00:00"),
    ],
)
def test_a_bound_is_an_age_or_an_instant(raw, expected, frozen_now):
    moment = _window.parse_instant(raw, flag="--start", current=frozen_now)
    assert _window.iso(moment) == expected


def test_an_unreadable_bound_is_a_usage_error_naming_both_forms(frozen_now):
    with pytest.raises(UsageError) as caught:
        _window.parse_instant("yesterday", flag="--start", current=frozen_now)
    assert caught.value.code == "BAD_TIME"
    assert "24h" in caught.value.help_lines[0]


def test_a_window_that_ends_before_it_starts_is_refused():
    with pytest.raises(UsageError) as caught:
        _window.window(_Parsed(start="1h", end="2h"))
    assert caught.value.code == "BAD_WINDOW"


@pytest.mark.parametrize(
    ("seconds", "age", "span"),
    [(45, "45s", "45s"), (754, "12m", "12m34s"), (11520, "3h", "3h12m"), (187200, "2d", "2d4h")],
)
def test_durations_read_the_way_a_person_says_them(seconds, age, span):
    assert _window.age(seconds) == age
    assert _window.span(seconds) == span


def test_a_timestamp_is_read_from_iso_text_or_epoch_seconds_or_milliseconds():
    expected = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert _window.parse_timestamp("2026-01-01T00:00:00Z") == expected
    assert _window.parse_timestamp(1767225600) == expected
    assert _window.parse_timestamp(1767225600000) == expected
    assert _window.parse_timestamp("not a time") is None


# ------------------------------------------------------------------- history


def test_history_sends_the_end_so_a_long_window_is_not_cut_to_one_day(
    run_cli, rest_env, rest_server
):
    """Home Assistant ends a window with no `end_time` a day after its start."""
    code, _ = run_cli(["history", "get", "light.example_lamp", "--start", "7d"], rest_env)
    assert code == 0
    request = rest_server.requests[-1]
    assert unquote(request["path"]) == "/api/history/period/2025-12-26T00:00:00+00:00"
    assert "end_time=2026-01-02T00%3A00%3A00%2B00%3A00" in request["query"]
    assert "filter_entity_id=light.example_lamp" in request["query"]
    assert "minimal_response" in request["query"]


def test_history_reports_time_in_each_state_counting_the_state_the_window_opened_in(
    run_cli, rest_env
):
    code, doc = as_json(run_cli, ["history", "get", "light.example_lamp"], rest_env)
    assert code == 0
    lamp = doc["history"][0]
    assert lamp["name"] == "Example Lamp"
    # Three rows, but the first is the state already held at midnight.
    assert lamp["changes"] == 2
    assert lamp["time_in_state"] == {"on": "12h", "off": "12h"}
    assert lamp["timeline"][0] == {"at": "2026-01-01T00:00:00+00:00", "state": "on"}


def test_history_summarises_a_reading_by_its_range_ignoring_unavailable(run_cli, rest_env):
    code, doc = as_json(run_cli, ["history", "get", "sensor.example_temperature"], rest_env)
    assert code == 0
    reading = doc["history"][0]
    assert (reading["min"], reading["max"], reading["last"]) == (19.0, 22.5, "21.5")
    assert "time_in_state" not in reading


def test_a_row_that_repeats_the_state_is_not_a_change(run_cli, rest_env, rest_server):
    """The recorder keeps whole states for climate, so an attribute change is a row too."""
    rest_server.state["history"]["climate.example_thermostat"] = [
        ("2025-12-31T20:00:00+00:00", "heat"),
        ("2026-01-01T03:00:00+00:00", "heat"),
        ("2026-01-01T12:00:00+00:00", "off"),
    ]
    code, doc = as_json(run_cli, ["history", "get", "climate.example_thermostat"], rest_env)
    assert code == 0
    row = doc["history"][0]
    assert row["changes"] == 1
    assert row["time_in_state"] == {"heat": "12h", "off": "12h"}


def test_history_answers_for_every_entity_asked_including_one_with_nothing(run_cli, rest_env):
    code, doc = as_json(
        run_cli,
        ["history", "get", "binary_sensor.example_doorway", "sensor.example_unrecorded"],
        rest_env,
    )
    assert code == 0
    assert [e["entity_id"] for e in doc["history"]] == [
        "binary_sensor.example_doorway",
        "sensor.example_unrecorded",
    ]
    assert doc["history"][0]["time_in_state"] == {"off": "23h30m", "on": "30m"}
    assert doc["history"][1]["note"] == "no recorded state in this window"


def test_history_keeps_the_latest_rows_and_says_how_to_see_the_rest(run_cli, rest_env):
    code, doc = as_json(
        run_cli, ["history", "get", "sensor.example_temperature", "--limit", "2"], rest_env
    )
    assert code == 0
    reading = doc["history"][0]
    assert [row["state"] for row in reading["timeline"]] == ["22.5", "21.5"]
    # The summary is over the whole window, not over the rows shown.
    assert reading["changes"] == 4
    assert any("--limit" in line for line in doc["help"])


def test_a_malformed_entity_id_is_refused_by_home_assistant_with_its_reason(run_cli, rest_env):
    code, out = run_cli(["history", "get", "nodot"], rest_env)
    assert code == 1
    assert "Invalid filter_entity_id" in out
    assert "code: BAD_REQUEST" in out


# ------------------------------------------------------------------- logbook


def test_logbook_folds_the_context_into_one_cause(run_cli, rest_env):
    code, doc = as_json(
        run_cli, ["logbook", "get", "--fields", "when,entity_id,event,cause"], rest_env
    )
    assert code == 0
    by_event = {(e["entity_id"], e["event"]): e["cause"] for e in doc["entries"]}
    assert by_event[("light.example_lamp", "changed to off")] == "automation Example Morning"
    assert by_event[("light.example_lamp", "changed to on")] == "service light.turn_on"
    assert by_event[("binary_sensor.example_doorway", "changed to on")] == ""
    assert by_event[("automation.example_morning", "triggered by time")] == ""


def test_logbook_sends_every_entity_in_one_request(run_cli, rest_env, rest_server):
    code, doc = as_json(
        run_cli,
        [
            "logbook",
            "get",
            "--entity",
            "light.example_lamp",
            "--entity",
            "binary_sensor.example_doorway",
        ],
        rest_env,
    )
    assert code == 0
    assert len(doc["entries"]) == 3
    request = rest_server.requests[-1]
    assert "entity=light.example_lamp%2Cbinary_sensor.example_doorway" in request["query"]
    assert "end_time=" in request["query"]


def test_logbook_keeps_the_most_recent_entries(run_cli, rest_env):
    code, doc = as_json(run_cli, ["logbook", "get", "--limit", "1"], rest_env)
    assert code == 0
    assert [e["when"] for e in doc["entries"]] == ["2026-01-01T18:00:00+00:00"]
    assert doc["count"] == "1 of 4 total"


def test_logbook_search_narrows_and_reports_against_the_total(run_cli, rest_env):
    code, doc = as_json(run_cli, ["logbook", "get", "--search", "doorway"], rest_env)
    assert code == 0
    assert doc["count"] == "1 of 1 matched (4 total)"


def test_an_empty_logbook_window_is_a_definitive_answer(run_cli, rest_env):
    code, out = run_cli(["logbook", "get", "--start", "1h"], rest_env)
    assert code == 0
    assert "0 logbook entries in this window" in out


# ---------------------------------------------------------------- statistics


def test_statistics_list_reads_the_kind_from_the_recorders_metadata(run_cli, installation_env):
    code, doc = as_json(run_cli, ["statistics", "list"], installation_env)
    assert code == 0
    kinds = {
        row["statistic_id"]: (row["kind"], row["unit"], row["name"]) for row in doc["statistics"]
    }
    assert kinds["sensor.example_legacy_meter"] == ("sum", "kWh", "Example Doorway Legacy Meter")
    assert kinds["sensor.example_temperature"] == ("mean", "C", "Example Hub Temperature")
    assert kinds["example:grid_import"] == ("sum", "kWh", "Example Grid Import")


def test_statistics_list_filters_by_kind_upstream(run_cli, ws_env, ws_server):
    code, doc = as_json(run_cli, ["statistics", "list", "--kind", "mean"], ws_env)
    assert code == 0
    assert [row["statistic_id"] for row in doc["statistics"]] == ["sensor.example_temperature"]
    sent = [c for c in ws_server.received if c["type"] == "recorder/list_statistic_ids"]
    assert sent[-1]["statistic_type"] == "mean"


def test_an_unknown_kind_is_refused_before_anything_is_sent(run_cli, ws_env, ws_server):
    code, out = run_cli(["statistics", "list", "--kind", "total"], ws_env)
    assert code == 2
    assert "code: BAD_KIND" in out
    assert ws_server.received == []


def test_a_meter_reports_its_total_and_a_reading_its_average(run_cli, ws_env):
    code, doc = as_json(
        run_cli,
        ["statistics", "get", "sensor.example_legacy_meter", "sensor.example_temperature"],
        ws_env,
    )
    assert code == 0
    meter, reading = doc["statistics"]
    assert (meter["kind"], meter["total"], meter["unit"], meter["buckets"]) == (
        "sum",
        12.0,
        "kWh",
        24,
    )
    assert (reading["mean"], reading["min"], reading["max"]) == (21.25, 19.5, 23.0)
    assert "caveats" not in meter and "caveats" not in reading
    assert "hourly buckets" in doc["window"]


def test_each_kind_is_asked_for_the_types_it_keeps(run_cli, ws_env, ws_server):
    """A type a statistic does not keep comes back empty, not as an error."""
    run_cli(
        ["statistics", "get", "sensor.example_legacy_meter", "sensor.example_temperature"],
        ws_env,
    )
    sent = [c for c in ws_server.received if c["type"] == "recorder/statistics_during_period"]
    by_ids = {tuple(c["statistic_ids"]): c for c in sent}
    assert set(by_ids[("sensor.example_legacy_meter",)]["types"]) == {"change", "state"}
    assert set(by_ids[("sensor.example_temperature",)]["types"]) == {"mean", "min", "max"}
    assert all(c["end_time"] == "2026-01-02T00:00:00+00:00" for c in sent)


def test_a_long_window_defaults_to_daily_buckets(run_cli, ws_env, ws_server):
    code, doc = as_json(
        run_cli, ["statistics", "get", "sensor.example_legacy_meter", "--start", "7d"], ws_env
    )
    assert code == 0
    assert "daily buckets" in doc["window"]
    assert doc["statistics"][0]["total"] == 12.0
    sent = [c for c in ws_server.received if c["type"] == "recorder/statistics_during_period"]
    assert sent[-1]["period"] == "day"


def test_an_unknown_period_is_refused(run_cli, ws_env):
    code, out = run_cli(
        ["statistics", "get", "sensor.example_legacy_meter", "--period", "fortnight"], ws_env
    )
    assert code == 2
    assert "code: BAD_PERIOD" in out


def test_an_entity_with_no_statistics_is_not_found_and_points_at_history(run_cli, ws_env):
    code, out = run_cli(["statistics", "get", "light.example_lamp"], ws_env)
    assert code == 1
    assert "code: NO_SUCH_STATISTIC" in out
    assert "class: not_found" in out
    assert "hass-axi history get light.example_lamp" in out


def test_a_statistic_with_no_buckets_in_the_window_says_so(run_cli, ws_env):
    code, doc = as_json(run_cli, ["statistics", "get", "example:grid_import"], ws_env)
    assert code == 0
    row = doc["statistics"][0]
    assert row["buckets"] == 0
    assert row["caveats"] == ["no statistics were recorded in this window"]
    assert "total" not in row


def test_a_meter_that_went_backwards_is_reported_and_not_corrected(run_cli, ws_env, ws_server):
    changes = [0.5] * 24
    changes[5] = -2.0
    ws_server.statistics["sensor.example_legacy_meter"] = meter_rows(changes)
    code, doc = as_json(run_cli, ["statistics", "get", "sensor.example_legacy_meter"], ws_env)
    assert code == 0
    row = doc["statistics"][0]
    assert row["total"] == 9.5  # 23 * 0.5 - 2, exactly what the buckets hold
    assert row["caveats"] == ["1 bucket went backwards by 2.0 kWh in all; the total includes them"]


def test_a_meter_that_resets_every_day_is_called_out(run_cli, ws_env, ws_server):
    """The generic shape of a daily-resetting meter: its reading drops once a day.

    The total is still right -- the recorder carries the sum across a reset --
    but the entity's live state is a since-reset reading, which is the thing a
    reader would otherwise take for a lifetime total.
    """
    rows = meter_rows([0.5] * 72)
    for row in rows:
        hour = (row["start"] // 3_600_000) % 24
        row["state"] = 0.5 * (hour + 1)
    ws_server.statistics["sensor.example_legacy_meter"] = rows
    code, doc = as_json(
        run_cli,
        [
            "statistics",
            "get",
            "sensor.example_legacy_meter",
            "--start",
            "2026-01-01T00:00:00Z",
            "--end",
            "2026-01-04T00:00:00Z",
            "--period",
            "hour",
        ],
        ws_env,
    )
    assert code == 0
    row = doc["statistics"][0]
    assert row["total"] == 36.0
    assert any("about once a day" in caveat for caveat in row["caveats"])


def test_a_single_reset_is_reported_without_claiming_a_daily_pattern(run_cli, ws_env, ws_server):
    rows = meter_rows([0.5] * 24)
    rows[10]["state"] = 0.5
    rows[11]["state"] = 1.0
    ws_server.statistics["sensor.example_legacy_meter"] = rows
    code, doc = as_json(run_cli, ["statistics", "get", "sensor.example_legacy_meter"], ws_env)
    assert code == 0
    caveats = doc["statistics"][0]["caveats"]
    assert caveats == [
        "the meter reset 1 time in this window; the total here counts across the resets"
    ]


def test_missing_buckets_between_two_are_counted_but_the_trailing_edge_is_not(
    run_cli, ws_env, ws_server
):
    changes = [0.5] * 20 + [None] * 4  # the last four not compiled yet: not a caveat
    changes[8] = changes[9] = None  # a real hole
    ws_server.statistics["sensor.example_legacy_meter"] = meter_rows(changes)
    code, doc = as_json(run_cli, ["statistics", "get", "sensor.example_legacy_meter"], ws_env)
    assert code == 0
    row = doc["statistics"][0]
    assert row["caveats"] == [
        "2 of 20 hourly buckets have no data; the result covers only the 18 that do"
    ]


def test_a_circular_statistic_is_averaged_as_an_angle(run_cli, ws_env, ws_server):
    ws_server.statistics_meta.append(
        {
            "statistic_id": "sensor.example_wind_bearing",
            "display_unit_of_measurement": "°",
            "has_mean": False,
            "mean_type": 2,
            "has_sum": False,
            "name": None,
            "source": "recorder",
            "statistics_unit_of_measurement": "°",
            "unit_class": None,
        }
    )
    ws_server.statistics["sensor.example_wind_bearing"] = [
        {"start": 1767225600000 + h * 3_600_000, "end": 0, "mean": m, "min": m, "max": m}
        for h, m in enumerate([350.0, 10.0])
    ]
    code, doc = as_json(run_cli, ["statistics", "get", "sensor.example_wind_bearing"], ws_env)
    assert code == 0
    row = doc["statistics"][0]
    # The arithmetic mean of 350 and 10 is 180 -- due south for a northerly wind.
    assert row["mean"] in (0.0, 360.0)
    assert row["kind"] == "circular mean"
    # And their min and max would claim a 340-degree spread, so neither is given.
    assert "min" not in row and "max" not in row


def test_history_asks_once_for_two_spellings_of_one_entity(run_cli, rest_env, rest_server):
    """Home Assistant lowercases the filter, so both spellings are one timeline."""
    code, doc = as_json(
        run_cli, ["history", "get", "light.Example_Lamp", "light.example_lamp"], rest_env
    )
    assert code == 0
    assert [e["entity_id"] for e in doc["history"]] == ["light.Example_Lamp"]
    assert "filter_entity_id=light.Example_Lamp&" in rest_server.requests[-1]["query"]


# -------------------------------------------------------------------- sensor


def test_sensor_list_sets_diagnostic_sensors_aside_by_registry_category(run_cli, installation_env):
    code, doc = as_json(run_cli, ["sensor", "list"], installation_env)
    assert code == 0
    ids = [row["entity_id"] for row in doc["sensors"]]
    assert "sensor.example_temperature" not in ids  # entity_category: diagnostic
    assert doc["set_aside"] == "1 diagnostic, config or hidden sensor not shown"
    code, doc = as_json(run_cli, ["sensor", "list", "--all"], installation_env)
    assert code == 0
    assert "sensor.example_temperature" in [row["entity_id"] for row in doc["sensors"]]


def test_a_hidden_sensor_is_set_aside_too(run_cli, installation_env, ws_server):
    for entry in ws_server.entities:
        if entry["entity_id"] == "sensor.example_reading":
            entry["hidden_by"] = "user"
    _, doc = as_json(run_cli, ["sensor", "list"], installation_env)
    assert "sensor.example_reading" not in [row["entity_id"] for row in doc["sensors"]]


def test_sensor_rows_carry_value_unit_area_from_the_device_and_age(run_cli, installation_env):
    code, doc = as_json(
        run_cli,
        [
            "sensor",
            "list",
            "--device-class",
            "energy",
            "--fields",
            "entity_id,name,value,unit,area,age",
        ],
        installation_env,
    )
    assert code == 0
    assert doc["sensors"] == [
        {
            "entity_id": "sensor.example_legacy_meter",
            "name": "Example Doorway Legacy Meter",
            "value": "7",
            "unit": "kWh",
            # Inherited: the entity names no area, its device sits in this one.
            "area": "Example Room",
            "age": "1d",
        }
    ]


def test_the_unit_filter_is_exact_including_case(run_cli, installation_env):
    code, doc = as_json(run_cli, ["sensor", "list", "--unit", "kwh"], installation_env)
    assert code == 0
    assert doc["sensors"] == "0 sensors found with unit kwh"
    # What is here, so the next attempt is not another guess.
    assert "kWh" in doc["units"]
    assert "energy" in doc["device_classes"]


def test_sensor_list_filters_by_area_name_and_by_no_area(run_cli, installation_env):
    _, doc = as_json(run_cli, ["sensor", "list", "--area", "Example Room"], installation_env)
    assert [row["entity_id"] for row in doc["sensors"]] == ["sensor.example_legacy_meter"]
    _, doc = as_json(run_cli, ["sensor", "list", "--area", "none"], installation_env)
    assert [row["entity_id"] for row in doc["sensors"]] == ["sensor.example_reading"]


def test_sensor_search_matches_the_displayed_name(run_cli, installation_env):
    _, doc = as_json(run_cli, ["sensor", "list", "--search", "hub reading"], installation_env)
    assert [row["entity_id"] for row in doc["sensors"]] == ["sensor.example_reading"]


def test_a_sensor_with_no_registry_entry_is_still_found(run_cli, installation_env, rest_server):
    rest_server.state["states"].append(
        {
            "entity_id": "sensor.example_yaml_power",
            "state": "42",
            "attributes": {
                "friendly_name": "Example Yaml Power",
                "unit_of_measurement": "W",
                "device_class": "power",
            },
            "last_changed": "2026-01-01T23:59:00+00:00",
            "last_reported": "2026-01-01T23:59:00+00:00",
            "last_updated": "2026-01-01T23:59:00+00:00",
        }
    )
    code, doc = as_json(
        run_cli,
        ["sensor", "list", "--device-class", "power", "--fields", "name,area,age"],
        installation_env,
    )
    assert code == 0
    row = doc["sensors"][0]
    assert (row["name"], row["area"], row["age"]) == ("Example Yaml Power", "", "1m")


# ---------------------------------------------------------------------- ping


def test_ping_times_one_authenticated_request(run_cli, rest_env):
    code, doc = as_json(run_cli, ["ping"], rest_env)
    assert code == 0
    assert doc["ok"] is True
    assert isinstance(doc["latency_ms"], int)
    assert doc["version"] == "2026.1.0"


def test_ping_reports_a_rejected_token_as_an_auth_fault(run_cli, rest_env):
    code, out = run_cli(["ping"], {**rest_env, "HA_TOKEN": "not-the-right-token"})
    assert code == 1
    assert "code: UNAUTHORIZED" in out
    assert "class: auth" in out


# ------------------------------------------------------------ URL fallback


@pytest.fixture
def dead_url():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def test_a_second_url_is_used_when_the_first_does_not_answer(run_cli, rest_server, dead_url):
    env = {"HA_URL": f"{dead_url},{rest_server.url}", "HA_TOKEN": FAKE_TOKEN}
    code, doc = as_json(run_cli, ["ping"], env)
    assert code == 0
    assert doc["url"] == rest_server.url
    assert doc["fallback"].startswith("candidate 2 of 2")


def test_the_first_url_wins_when_it_answers_and_nothing_is_said(run_cli, rest_server, dead_url):
    env = {"HA_URL": f"{rest_server.url},{dead_url}", "HA_TOKEN": FAKE_TOKEN}
    code, doc = as_json(run_cli, ["ping"], env)
    assert code == 0
    assert doc["url"] == rest_server.url
    assert "fallback" not in doc


def test_fallback_never_moves_past_an_answer(run_cli, rest_server, dead_url):
    """A 401 is an answer: the next URL is the same installation, and would say the same."""
    other = f"http://127.0.0.1:{rest_server.port}"
    env = {"HA_URL": f"{rest_server.url},{other}/x", "HA_TOKEN": "not-the-right-token"}
    code, out = run_cli(["ping"], env)
    assert code == 1
    assert "code: UNAUTHORIZED" in out


def test_when_no_url_answers_the_first_ones_failure_is_reported(run_cli, dead_url):
    env = {"HA_URL": f"{dead_url},{dead_url}/second", "HA_TOKEN": FAKE_TOKEN}
    code, out = run_cli(["ping"], env)
    assert code == 1
    assert "code: UNREACHABLE" in out
    assert "class: transport" in out


def test_both_transports_use_the_candidate_that_answered(run_cli, installation, dead_url):
    env = {**installation.environ, "HA_URL": f"{dead_url},{installation.url}"}
    code, doc = as_json(run_cli, ["sensor", "list"], env)
    assert code == 0
    assert doc["sensors"]


def test_userinfo_in_any_candidate_is_registered_as_a_secret():
    from hass_axi.config import parse_base_urls
    from hass_axi.output import redact

    urls = parse_base_urls(
        "https://ha.example.com, https://someone:example-secret-pass@remote.example.com"
    )
    assert urls == ("https://ha.example.com", "https://remote.example.com")
    assert "example-secret-pass" not in redact("leaked example-secret-pass here")


def test_a_list_of_nothing_but_commas_is_a_config_error():
    from hass_axi.config import parse_base_urls
    from hass_axi.errors import ConfigError

    with pytest.raises(ConfigError) as caught:
        parse_base_urls(" , ")
    assert caught.value.code == "BAD_URL"


# ----------------------------------------------------------------- the home view


def _state(entity_id, state, attributes=None, reported="2026-01-01T23:00:00+00:00"):
    return {
        "entity_id": entity_id,
        "state": state,
        "attributes": {
            "friendly_name": entity_id.split(".")[1].replace("_", " ").title(),
            **(attributes or {}),
        },
        "last_changed": reported,
        "last_reported": reported,
        "last_updated": reported,
    }


def test_the_home_view_lists_what_needs_attention(run_cli, rest_env, rest_server):
    rest_server.state["states"] = [
        _state(
            "sensor.example_cell", "12", {"device_class": "battery", "unit_of_measurement": "%"}
        ),
        _state(
            "sensor.example_full_cell",
            "90",
            {"device_class": "battery", "unit_of_measurement": "%"},
        ),
        _state("lock.example_door", "locked", {"battery_level": 7}),
        _state("sensor.example_silent", "3", reported="2025-12-30T00:00:00+00:00"),
        _state("automation.example_quiet", "on", reported="2025-12-01T00:00:00+00:00"),
        _state("switch.example_gone", "unavailable"),
    ]
    code, doc = as_json(run_cli, [], rest_env)
    assert code == 0
    assert doc["low_battery"] == [
        {"entity_id": "lock.example_door", "name": "Example Door", "battery": "7%"},
        {"entity_id": "sensor.example_cell", "name": "Example Cell", "battery": "12%"},
    ]
    assert doc["not_reporting"] == [
        {
            "entity_id": "switch.example_gone",
            "name": "Example Gone",
            "state": "unavailable",
            "for": "1h",
        }
    ]
    # Only a sensor's silence is listed: an automation that never fires is quiet, not stale.
    assert doc["stale"] == "1 sensor not reported in 24h"
    assert doc["stale_oldest"] == [
        {"entity_id": "sensor.example_silent", "name": "Example Silent", "age": "3d"}
    ]


def test_the_home_view_says_nothing_about_attention_when_nothing_needs_it(
    run_cli, rest_env, rest_server
):
    rest_server.state["states"] = [_state("sensor.example_fine", "1")]
    code, doc = as_json(run_cli, [], rest_env)
    assert code == 0
    assert not {"low_battery", "not_reporting", "stale", "stale_oldest"} & set(doc)


def test_long_attention_lists_are_capped_with_a_way_to_see_all(run_cli, rest_env, rest_server):
    rest_server.state["states"] = [
        _state(f"sensor.example_dead_{n:02}", "unavailable") for n in range(12)
    ]
    _, doc = as_json(run_cli, [], rest_env)
    assert len(doc["not_reporting"]) == 10
    assert any("12 entities are not reporting" in line for line in doc["help"])


def test_an_entity_whose_resting_state_is_unknown_is_not_listed_as_not_reporting(
    run_cli, rest_env, rest_server
):
    """A button never pressed is `unknown` by design; a sensor that is `unknown` is not."""
    rest_server.state["states"] = [
        _state("button.example_restart", "unknown"),
        _state("event.example_doorbell", "unknown"),
        _state("sensor.example_probe", "unknown"),
    ]
    code, doc = as_json(run_cli, [], rest_env)
    assert code == 0
    assert doc["unknown"] == 3
    assert [row["entity_id"] for row in doc["not_reporting"]] == ["sensor.example_probe"]


def test_state_list_stale_agrees_with_the_home_view(run_cli, rest_env, rest_server):
    rest_server.state["states"] = [
        _state("sensor.example_silent", "3", reported="2025-12-30T00:00:00+00:00"),
        _state("sensor.example_older", "3", reported="2025-12-20T00:00:00+00:00"),
        _state("sensor.example_fresh", "3"),
        _state("sensor.example_gone", "unavailable", reported="2025-12-01T00:00:00+00:00"),
    ]
    code, doc = as_json(
        run_cli, ["state", "list", "--stale", "24h", "--fields", "entity_id,age"], rest_env
    )
    assert code == 0
    assert doc["states"] == [
        {"entity_id": "sensor.example_older", "age": "13d"},
        {"entity_id": "sensor.example_silent", "age": "3d"},
    ]


def test_state_list_stale_needs_an_age(run_cli, rest_env):
    code, out = run_cli(["state", "list", "--stale", "yesterday"], rest_env)
    assert code == 2
    assert "code: BAD_TIME" in out


# ------------------------------------------------------------ default fields


def test_sensor_list_defaults_to_four_fields_and_keeps_the_rest_reachable(
    run_cli, installation_env
):
    """AXI section 2: a default row is paid for once per sensor."""
    code, doc = as_json(run_cli, ["sensor", "list"], installation_env)
    assert code == 0
    assert list(doc["sensors"][0]) == ["entity_id", "name", "value", "unit"]
    assert any("--fields entity_id,name,value,unit,area,age" in line for line in doc["help"])

    code, doc = as_json(
        run_cli, ["sensor", "list", "--fields", "entity_id,area,age,device_class"], installation_env
    )
    assert code == 0
    assert list(doc["sensors"][0]) == ["entity_id", "area", "age", "device_class"]
    assert not any("--fields" in line for line in doc["help"])


def test_logbook_get_defaults_to_four_fields_and_keeps_the_rest_reachable(run_cli, rest_env):
    code, doc = as_json(run_cli, ["logbook", "get"], rest_env)
    assert code == 0
    assert list(doc["entries"][0]) == ["when", "name", "event", "cause"]
    assert any("--fields when,entity_id,event,cause" in line for line in doc["help"])

    code, doc = as_json(run_cli, ["logbook", "get", "--fields", "entity_id,domain,state"], rest_env)
    assert code == 0
    assert list(doc["entries"][0]) == ["entity_id", "domain", "state"]

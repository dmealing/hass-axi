"""P10 and P13: large answers, generated invocations, concurrency, and redaction.

The grammar below cannot produce `--write`, an update, a create or a setup
command: every invocation it generates is a read or a preview.
"""

from __future__ import annotations

import concurrent.futures
import json
import random

import pytest

from .harness import contract, stable

READ_SHAPES = [
    ["state", "list"],
    ["state", "list", "--domain", "{v}"],
    ["state", "list", "--area", "{v}"],
    ["state", "list", "--search", "{v}"],
    ["state", "list", "--state", "{v}"],
    ["state", "list", "--limit", "{v}"],
    ["state", "list", "--fields", "{v}"],
    ["state", "list", "--stale", "{v}"],
    ["state", "get", "{v}"],
    ["sensor", "list", "--device-class", "{v}"],
    ["sensor", "list", "--unit", "{v}"],
    ["entity", "list", "--search", "{v}"],
    ["entity", "list", "--platform", "{v}"],
    ["entity", "list", "--device", "{v}"],
    ["entity", "get", "{v}"],
    ["area", "get", "{v}"],
    ["device", "get", "{v}"],
    ["device", "list", "--search", "{v}"],
    ["service", "get", "{v}"],
    ["service", "list", "--domain", "{v}"],
    ["service", "call", "{v}"],
    ["service", "call", "light.turn_on", "--target-entity", "{v}"],
    ["service", "call", "light.turn_on", "--target-area", "{v}"],
    ["service", "call", "light.turn_on", "--data", "{v}"],
    ["history", "get", "{v}"],
    ["history", "get", "light.zz", "--start", "{v}"],
    ["logbook", "get", "--entity", "{v}", "--start", "1h"],
    ["logbook", "get", "--start", "{v}"],
    ["statistics", "get", "{v}"],
    ["statistics", "list", "--search", "{v}"],
    ["template", "render", "--template", "{v}"],
    ["api", "{v}"],
    ["ws", "{v}"],
]

HOSTILE = [
    "",
    " ",
    "0",
    "-1",
    "1e9",
    "99999999999999999999",
    "NaN",
    "null",
    "true",
    "none",
    "*",
    "%",
    "%20",
    "%00",
    "..",
    "../../config",
    "/",
    "//",
    "\\",
    "light.",
    ".lamp",
    "a..b",
    "LIGHT.X",
    "a?b",
    "a#b",
    "a&b",
    "a/b",
    "$(id)",
    "'",
    '"',
    "\t",
    "a\nb",
    "a\rb",
    "café",
    "it’s",
    "中文",
    "\U0001f600",
    "‮abc",
    "x" * 300,
    "x" * 20000,
    "-",
    "--",
    "---",
    "=",
    "a=b",
    "a==b",
    "=b",
    "a=",
    "{}",
    "[]",
    "{",
    "24h",
    "7d",
    "0s",
    "soon",
    "2026-01-01",
    "2026-13-45T99:00:00",
    "light.turn_on",
    "nodomain.nothing",
    "/config",
    "/states/x y",
]


def test_generated_invocations_never_break_the_contract(house, snapshot):
    """600 reads and previews built from hostile values: exit 0, 1 or 2, always a document."""
    rng = random.Random(20261004)
    real = [snapshot.light(), snapshot.biggest_area(), snapshot.devices[0]["id"]]
    for _ in range(600):
        shape = rng.choice(READ_SHAPES)
        value = rng.choice(HOSTILE + real)
        argv = [part.replace("{v}", value) for part in shape]
        # After `--` everything is a positional, `--json` included.
        mode = "toon" if "--" in argv else rng.choice(["toon", "json"])
        result = house.run(*argv, *(["--json"] if mode == "json" else []), timeout=60)
        assert result.code in (0, 1, 2), f"{result.cmd}: exit {result.code}"
        if result.code == 0:
            assert "Traceback" not in result.out + result.err
            continue
        if mode == "json":
            try:
                json.loads(result.out)
            except ValueError:
                # `--json` taken as the value of a free-text flag: a template.
                assert shape[0] == "template", result.cmd
                continue
        contract(result, mode)


def test_large_answers_are_bounded_until_full_is_asked_for(house):
    from hass_axi.commands._common import RAW_BUDGET_CHARS

    for argv in (
        ["api", "/states"],
        ["api", "/services"],
        ["ws", "service.list"],
        ["ws", "entity.list"],
        ["ws", "device.list"],
        ["ws", "state.list"],
    ):
        short = house.ok(*argv)
        full = house.ok(*argv, "--full")
        if "truncated" in short:
            assert any("--full" in line for line in short["help"]), argv
            size = len(json.dumps(short["result"], separators=(",", ":"), ensure_ascii=False))
            assert size <= RAW_BUDGET_CHARS * 2, f"{argv}: {size} chars after truncation"
            assert "truncated" not in full
        decoded, document = stable(house, [*argv, "--full"])
        if argv[1] not in ("/states", "state.list"):
            assert decoded == document, f"{argv}: the full TOON does not decode to the full JSON"


def test_awkward_strings_survive_the_encoder(house):
    for text in [
        "a,b",
        "a: b",
        '"quoted"',
        "[x]",
        "{y}",
        "- dash",
        "line one\\nline two",
        "café ’ 中",
        " leading",
        "trailing ",
        "true",
        "null",
        "007",
        "1e3",
        "",
    ]:
        template = "{{ " + json.dumps(text) + " }}"
        decoded, document = stable(house, ["template", "render", "--template", template])
        assert decoded == document
    long_text = house.ok("template", "render", "--template", "{{ 'x' * 5000 }}")
    assert long_text["template"]["chars"] == 5000 and len(long_text["template"]["result"]) < 5000
    full = house.ok("template", "render", "--template", "{{ 'x' * 5000 }}", "--full")
    assert len(full["template"]["result"]) == 5000


def test_parallel_reads_answer_as_serial_ones_do(house, snapshot):
    light = snapshot.light()
    commands = [
        ["area", "list"],
        ["entity", "get", light],
        ["service", "get", "light.turn_on"],
        ["ws", "--list"],
        ["api", "/config"],
        ["entity", "list", "--area", snapshot.biggest_area(), "--fields", "entity_id"],
        ["device", "list", "--fields", "device_id,name"],
        ["statistics", "list", "--fields", "statistic_id,kind"],
    ]
    serial = [house.run(*argv, "--json").out for argv in commands]
    for width in (8, 16):
        with concurrent.futures.ThreadPoolExecutor(width) as pool:
            jobs = [pool.submit(house.run, *argv, "--json") for argv in commands * (width // 4)]
            results = [job.result() for job in jobs]
        for index, result in enumerate(results):
            assert result.code == 0
            assert result.out == serial[index % len(commands)], (
                f"{result.cmd} differs under width {width}"
            )


def test_session_ends_arriving_together_all_keep_their_record(house, tmp_path):
    env = {"XDG_STATE_HOME": str(tmp_path)}

    def end(index: int):
        payload = {
            "session_id": f"live-{index}",
            "cwd": f"/example/live-{index}",
            "commands": ["hass-axi state list"],
        }
        return house.run("context", "end", env=env, stdin=json.dumps(payload))

    with concurrent.futures.ThreadPoolExecutor(24) as pool:
        results = list(pool.map(end, range(24)))
    assert all(r.code == 0 and "recorded: 1" in r.out for r in results)
    records = json.loads(next(tmp_path.rglob("sessions.json")).read_text(encoding="utf-8"))
    assert len({r["session"] for r in records}) == 24


def test_repeated_reads_are_steady(house, snapshot):
    light = snapshot.light()
    timings = []
    for _ in range(10):
        for argv in (
            ["state", "get", light],
            ["area", "list"],
            ["service", "get", "light.turn_on"],
            ["ping"],
        ):
            result = house.run(*argv)
            assert result.code == 0, result.cmd
            timings.append(result.ms)
    assert max(timings) < 15000


# ----------------------------------------------------------------- redaction


@pytest.mark.parametrize(
    "shape",
    [
        ["state", "get", "{t}"],
        ["state", "get", "light.{t}"],
        ["entity", "get", "{t}"],
        ["statistics", "get", "{t}"],
        ["service", "get", "{t}"],
        ["history", "get", "{t}"],
        ["device", "get", "{t}"],
        ["area", "get", "{t}"],
        ["state", "list", "--search", "{t}"],
    ],
)
def test_no_part_of_the_token_is_printed_when_it_is_passed_as_an_argument(house, shape):
    """The harness fails any invocation that prints the token or a segment of it."""
    for debug in ([], ["--debug"]):
        result = house.run(*[part.replace("{t}", house.token) for part in shape], *debug)
        assert not result.leak, f"a slice of the token survived redaction: {shape}"


def test_a_token_returned_in_data_is_redacted_even_when_the_value_is_cut(house):
    from hass_axi.commands._common import PREVIEW_CHARS

    for padding in (0, PREVIEW_CHARS - 40, PREVIEW_CHARS - 90, PREVIEW_CHARS - 150):
        template = "{{ 'x' * " + str(padding) + " ~ " + json.dumps(house.token) + " ~ ' tail' }}"
        for extra in ([], ["--full"], ["--json"]):
            assert not house.run("template", "render", "--template", template, *extra).leak


def test_home_assistants_own_tokens_are_masked(house, snapshot):
    camera = snapshot.camera()
    raw = house.rest(f"/states/{camera}")["attributes"]
    secret = raw.get("access_token")
    if not secret:
        pytest.skip("this camera publishes no access token")
    for argv in (["state", "get", camera, "--full"], ["api", f"/states/{camera}", "--full"]):
        for mode in ([], ["--json"], ["--human"]):
            assert secret not in house.run(*argv, *mode).out, argv
    proxy = house.run("api", f"/camera_proxy/{camera}")
    assert "�" not in proxy.out

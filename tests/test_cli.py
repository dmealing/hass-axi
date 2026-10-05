"""Dispatch, global flags, help, exit codes and the home view."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from hass_axi import __version__
from hass_axi.cli import COMMAND_ORDER, command_specs


def test_no_arguments_shows_live_state_not_a_manual(run_cli, rest_env):
    code, out = run_cli([], rest_env)
    assert code == 0
    assert out.startswith("bin: ")
    assert "description: Agent CLI for Home Assistant." in out
    assert "entities: 11 in 7 domains" in out
    assert "domains[7]{domain,entities}:" in out
    assert "help[" in out


def test_the_home_view_reports_the_executable_path_with_home_collapsed(run_cli, rest_env):
    _, out = run_cli([], rest_env)
    bin_line = out.splitlines()[0]
    assert bin_line.startswith("bin: ")
    assert "/home/" not in bin_line


def test_an_unconfigured_home_view_explains_how_to_configure_and_exits_zero(run_cli):
    """AXI section 8: a bare run shows what is here, and describing a machine
    with no installation is an answer rather than a failure. The fault is still
    named, so a caller can tell configured from not without reading prose."""
    code, out = run_cli([], {})
    assert code == 0
    assert "error:" not in out
    assert "live_state: not available - HA_URL and HA_TOKEN not set" in out
    assert "code: NOT_CONFIGURED" in out
    assert "commands[" in out
    assert "export" not in out.lower() or "HA_URL" in out
    assert "Run `hass-axi doctor`" in out


def test_a_partially_configured_home_view_names_only_what_is_missing(run_cli):
    code, out = run_cli([], {"HA_URL": "https://ha.example.com"})
    assert code == 0
    assert "HA_TOKEN not set" in out
    assert "HA_URL and" not in out


def test_root_help_lists_every_command(run_cli):
    code, out = run_cli(["--help"], {})
    assert code == 0
    for name in COMMAND_ORDER:
        assert name in out
    assert "HA_TOKEN" in out
    assert "there is deliberately no --token flag" in out


@pytest.mark.parametrize("name", COMMAND_ORDER)
def test_every_command_has_usable_help(run_cli, name):
    code, out = run_cli([name, "--help"], {})
    assert code == 0
    assert out.startswith("usage: hass-axi")
    assert "examples:" in out
    assert command_specs()[name].summary in out


@pytest.mark.parametrize("name", COMMAND_ORDER)
def test_help_never_needs_configuration(run_cli, name):
    # An agent must be able to read the reference before anything is set up.
    code, _ = run_cli([name, "--help"], {})
    assert code == 0


@pytest.mark.parametrize(
    "argv",
    [
        ["--version"],
        ["-v"],
        ["-V"],
        ["device", "--version"],
        ["state", "list", "-v"],
        ["state", "list", "-V"],
        # The mode flags do not change it: a version probe compares the line.
        ["--json", "--version"],
        ["--human", "-V"],
        ["area", "list", "--json", "--version"],
    ],
)
def test_version_flag_prints_the_bare_version_in_any_position(run_cli, argv):
    """AXI principle 10: `-v`, `-V` and `--version` print the bare version."""
    code, out = run_cli(argv, {})
    assert code == 0
    assert out == f"{__version__}\n"


@pytest.mark.parametrize("flag", ["-v", "-V", "--version"])
def test_the_console_script_answers_a_bare_version_flag_itself(flag, capsys):
    from hass_axi import entry

    assert entry.main([flag]) == 0
    assert capsys.readouterr().out == f"{__version__}\n"


def _in_a_fresh_interpreter(code: str) -> str:
    """Run ``code`` in a clean interpreter and return what it printed."""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    )
    return result.stdout


def test_the_version_fast_path_loads_nothing_but_the_version():
    """The version is answered before the command graph loads.

    Asserted on what was imported rather than only on a stopwatch: the entry
    point may pull in the package's `__init__` and nothing else of this tool.
    """
    out = _in_a_fresh_interpreter(
        "import sys\n"
        "from hass_axi.entry import main\n"
        "code = main(['--version'])\n"
        "loaded = sorted(n for n in sys.modules if n.startswith(('hass_axi', 'axi_toolkit')))\n"
        "print(code, loaded)\n"
    )
    assert out == f"{__version__}\n0 ['hass_axi', 'hass_axi.entry']\n"


def test_one_command_does_not_import_the_others():
    """`cli` loads a command module when it is dispatched to, not at import.

    A version flag after a command, `--help` for one noun and the session
    hook's `context` all go through `cli.main`, and none of them should pay for
    sixteen modules and two transports to run one.
    """
    out = _in_a_fresh_interpreter(
        "import sys\n"
        "from hass_axi.cli import main\n"
        "main(['context'], environ={})\n"
        "print(sorted(n for n in sys.modules if n.startswith('hass_axi.commands.')))\n"
        "print([n for n in ('hass_axi.rest', 'hass_axi.ws', 'websockets') if n in sys.modules])\n"
    )
    commands, transports = out.splitlines()[-2:]
    assert "'hass_axi.commands.context'" in commands
    for other in ("state", "service", "entity", "api", "wscmd"):
        assert f"'hass_axi.commands.{other}'" not in commands
    assert transports == "[]"


def test_the_version_path_costs_about_what_starting_python_costs():
    """Measured against the interpreter's own floor, in the same run.

    A ratio rather than a millisecond budget, so it holds on a slow machine,
    and the best of several runs each, so one scheduling hiccup cannot fail it.
    Importing the whole CLI to answer measured about five times the floor.
    """

    def best(code: str) -> float:
        timings = []
        for _ in range(7):
            started = time.perf_counter()
            subprocess.run([sys.executable, "-c", code], capture_output=True, check=True)
            timings.append(time.perf_counter() - started)
        return min(timings)

    floor = best("print(1)")
    version = best("import sys; from hass_axi.entry import main; sys.exit(main(['--version']))")
    assert version < floor * 2.5, f"version {version * 1000:.1f} ms, floor {floor * 1000:.1f} ms"


def test_an_unknown_command_lists_the_real_ones(run_cli):
    code, out = run_cli(["nope"], {})
    assert code == 2
    assert "unknown command: nope" in out
    assert "state, sensor, history" in out


def test_a_plausible_alias_gets_a_targeted_hint(run_cli):
    code, out = run_cli(["entities"], {})
    assert code == 2
    assert "use `entity` instead" in out


def test_an_unknown_subcommand_lists_the_real_ones(run_cli):
    code, out = run_cli(["entity", "frobnicate"], {})
    assert code == 2
    assert "unknown subcommand `frobnicate`" in out
    assert "list, get, update" in out


def test_a_command_that_needs_a_subcommand_says_so(run_cli):
    code, out = run_cli(["entity"], {})
    assert code == 2
    assert "`entity` needs a subcommand" in out


def test_an_unknown_flag_is_rejected_with_the_valid_ones_inline(run_cli):
    code, out = run_cli(["state", "list", "--stat", "closed"], {})
    assert code == 2
    assert "unknown flag --stat for `state list`" in out
    assert "--domain, --state, --search, --stale, --limit, --fields" in out
    assert "--help always allowed" in out


def test_a_renamed_flag_points_at_its_replacement(run_cli):
    code, out = run_cli(["entity", "list", "--room", "x"], {})
    assert code == 2
    assert "use --area instead" in out


def test_flags_are_validated_per_subcommand(run_cli):
    # --search exists on `entity list` but not on `entity update`.
    assert run_cli(["entity", "list", "--search", "x"], {})[0] != 2
    code, out = run_cli(["entity", "update", "--write", "light.example_lamp", "--search", "x"], {})
    assert code == 2
    assert "unknown flag --search for `entity update`" in out


def test_a_missing_positional_is_a_usage_error(run_cli):
    code, out = run_cli(["entity", "get"], {})
    assert code == 2
    assert "needs <entity_id>" in out


def test_an_extra_positional_is_rejected_rather_than_ignored(run_cli):
    code, out = run_cli(["entity", "get", "light.example_lamp", "extra"], {})
    assert code == 2
    assert "unexpected argument 'extra'" in out


def test_a_flag_without_its_value_is_a_usage_error(run_cli):
    code, out = run_cli(["state", "list", "--domain"], {})
    assert code == 2
    assert "--domain needs a value" in out


def test_flags_accept_the_equals_form(run_cli, rest_env):
    code, out = run_cli(["state", "list", "--domain=light"], rest_env)
    assert code == 0
    assert "count: 2 of 2 matched (11 total)" in out


def test_repeatable_flags_accumulate(run_cli, rest_env):
    code, out = run_cli(["state", "list", "--domain", "light", "--domain", "sensor"], rest_env)
    assert code == 0
    assert "count: 5 of 5 matched (11 total)" in out


def test_human_mode_renders_a_table(run_cli, rest_env):
    code, out = run_cli(["--human", "state", "list"], rest_env)
    assert code == 0
    assert "-----" in out


def test_human_mode_is_accepted_after_the_subcommand_too(run_cli, rest_env):
    code, out = run_cli(["state", "list", "--human"], rest_env)
    assert code == 0
    assert "-----" in out


def test_json_mode_emits_parseable_json(run_cli, rest_env):
    import json

    code, out = run_cli(["--json", "state", "list"], rest_env)
    assert code == 0
    assert len(json.loads(out)["states"]) == 11


def test_a_global_flag_is_not_stolen_from_a_flag_value(run_cli, rest_env, rest_server):
    rest_server.state["template"] = "ok"
    code, _ = run_cli(["template", "render", "--template", "--human"], rest_env)
    assert code == 0
    posted = [r for r in rest_server.requests if r["path"] == "/api/template"]
    assert posted[0]["body"] == {"template": "--human"}


def test_timeout_must_be_a_positive_number(run_cli, rest_env):
    assert run_cli(["--timeout", "abc", "state", "list"], rest_env)[0] == 2
    assert run_cli(["--timeout", "0", "state", "list"], rest_env)[0] == 2


def test_doctor_fails_the_leg_that_cannot_connect(run_cli, rest_env):
    # The REST double answers; there is no WebSocket server on that authority,
    # so doctor must report per-transport rather than pass or fail wholesale.
    #
    # A failed check carries a code and a class as well as prose, which the
    # passing checks do not, so the rows stop being uniform and the block
    # renders in list form rather than tabular. That is section 9.4 doing its
    # job, not a defect: a run where something failed is the run that has more
    # to say. A doctor run where everything passes is still tabular, which
    # `test_doctor_is_healthy_when_both_transports_answer` pins.
    code, out = run_cli(["doctor"], rest_env)
    assert code == 1
    assert "check: environment\n    status: ok" in out
    assert "check: rest\n    status: ok" in out
    assert "check: websocket\n    status: fail" in out
    assert "version: 2026.1.0" in out


def test_doctor_reports_a_missing_environment_and_exits_non_zero(run_cli):
    # The failing row carries a code and class the passing read-only row does
    # not, so the checks render in list form rather than tabular -- the same
    # consequence a failed transport check already has.
    code, out = run_cli(["doctor"], {})
    assert code == 1
    assert "check: environment\n    status: fail" in out
    assert "code: NOT_CONFIGURED" in out
    assert "class: config" in out
    assert "detail: HA_URL and HA_TOKEN not set" in out
    assert "healthy: false" in out


def test_a_healthy_doctor_carries_no_exit_code_key(installation_env):
    """The success document must not smuggle the non-zero exit the failure sets.

    The end-to-end run lives in tests/test_cross_transport.py; this asserts the
    document shape the exit code is derived from.
    """
    from hass_axi.cli import Context
    from hass_axi.commands import doctor

    doc = doctor.run(Context(installation_env), "doctor", None)
    assert doc["healthy"] is True
    assert "__exit_code__" not in doc


def test_no_command_writes_progress_noise_to_stdout(run_cli, rest_env, capsys):
    code, out = run_cli(["state", "list"], rest_env)
    assert code == 0
    for line in out.splitlines():
        assert not line.lower().startswith(("fetching", "loading", "connecting"))


def test_help_is_not_stolen_from_a_flag_value(run_cli, rest_env, rest_server):
    """`--help` must obey the same value-consumption rule as every other flag."""
    rest_server.state["template"] = "ok"
    code, _ = run_cli(["template", "render", "--template", "--help"], rest_env)
    assert code == 0
    posted = [r for r in rest_server.requests if r["path"] == "/api/template"]
    assert posted[0]["body"] == {"template": "--help"}


def test_help_still_works_as_a_flag_in_every_position(run_cli):
    for argv in (["entity", "--help"], ["entity", "list", "--help"], ["--help", "entity"]):
        code, out = run_cli(argv, {})
        assert code == 0, argv
        assert out.startswith("usage: hass-axi"), argv


def test_a_usage_error_honours_json_after_the_subcommand(run_cli):
    """An agent piping to a parser needs machine output most when it got it wrong."""
    import json

    code, out = run_cli(["state", "list", "--json", "--bogus", "x"], {})
    assert code == 2
    payload = json.loads(out)
    assert payload["code"] == "UNKNOWN_FLAG"
    assert "--bogus" in payload["error"]


def test_a_usage_error_honours_human_after_the_subcommand(run_cli):
    code, out = run_cli(["state", "list", "--human", "--bogus", "x"], {})
    assert code == 2
    assert "error: unknown flag --bogus" in out


def test_timeout_without_a_value_is_rejected_not_swallowed(run_cli, rest_env):
    for argv in (["--timeout"], ["state", "list", "--timeout"]):
        code, out = run_cli([*argv], rest_env)
        assert code == 2, argv
        assert "--timeout needs a value" in out, argv


def test_the_api_command_reports_the_path_it_actually_requested(run_cli, rest_env, rest_server):
    code, out = run_cli(["api", "config"], rest_env)
    assert code == 0
    assert "path: /api/config" in out
    assert [r["path"] for r in rest_server.requests] == ["/api/config"]


def test_ambiguous_and_unknown_lookups_share_one_exit_code(run_cli, ws_env, ws_server):
    """Both are outcomes of a live lookup, so both exit 1, not one of each."""
    ws_server.areas.append(
        {
            "area_id": "example_room_two",
            "name": "Example Room",
            "icon": None,
            "floor_id": None,
            "aliases": [],
        }
    )
    ambiguous, out = run_cli(["entity", "list", "--area", "Example Room"], ws_env)
    assert ambiguous == 1
    assert "matches more than one area" in out

    unknown, out = run_cli(["entity", "list", "--area", "Nowhere"], ws_env)
    assert unknown == 1
    assert "no area with id or name" in out


def test_an_unknown_ws_command_is_a_usage_error_from_either_entry_point(run_cli, ws_env):
    """The command table is static, so both paths report a malformed invocation."""
    from hass_axi.errors import UsageError
    from hass_axi.ws import WsClient

    code, out = run_cli(["ws", "nope"], ws_env)
    assert code == 2
    assert "unknown websocket command" in out

    with pytest.raises(UsageError) as caught:
        WsClient.run(object.__new__(WsClient), "nope")
    assert caught.value.exit_code == 2


def test_ws_raw_without_a_command_is_an_error_not_a_listing(run_cli, ws_env):
    code, out = run_cli(["ws", "--raw"], ws_env)
    assert code == 2
    assert "--raw needs an API command type" in out

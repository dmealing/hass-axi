"""The session integration: what it installs, and what the thing it installs prints.

Two halves.

**Installation** is idempotent, repairing, and never destructive. Three of those
assertions came back from the sibling AXI CLI, which was given this design to
port and reviewed it on the way in, and they are stated here in the order that
review found them: a hook this tool did not write is never claimed, a second
managed entry is collapsed rather than ending the scan, and the Codex features
flag is rewritten rather than duplicated into a file its own parser would refuse.

**The document the hook prints** is the second half, and the reason it is a
document of its own. A SessionStart hook runs on every session, on every machine
that has the package, before anybody has decided to use the tool -- so the
no-argument home view cannot be it: that view needs a credential, opens a
connection, prints the installation's address, and has no live state to show
when nothing is configured, which is the state of exactly the machine ambient
context exists to help. The claims that make `hass-axi context` safe there are asserted rather than
described in a docstring:

- it reaches Home Assistant **zero times**, asserted on the doubles' request log
  rather than on an exit code, because a version that connected and printed the
  same document would pass on the exit code alone;
- it exits 0 and reports no error with **no environment at all**;
- it prints neither the base URL nor the token when both are set;
- and the command the installer records is the one all of that is true of, which
  is the join between the two halves and the test worth having.
"""

from __future__ import annotations

import json
import shlex
import sys

import pytest

from hass_axi import cli, hooks

EXECUTABLE = "hass-axi"

#: What a JSON hook entry records: the executable *and* the argument.
HOOK_LINE = f"{EXECUTABLE} {hooks.CONTEXT_COMMAND}"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def commands_in(settings) -> list:
    return [
        hook["command"] for group in settings["hooks"]["SessionStart"] for hook in group["hooks"]
    ]


def write_settings(tmp_path, document):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps(document), encoding="utf-8")
    return settings


# ------------------------------------------------------------- installation


def test_install_creates_hooks_for_every_default_target(tmp_path):
    report = hooks.install(tmp_path, command=EXECUTABLE)
    assert report["errors"] == []
    assert {t["target"] for t in report["targets"]} == {
        "claude-code",
        "claude-code-session-end",
        "codex",
        "codex-session-end",
        "codex-features",
        "opencode",
    }
    assert all(t["status"] == "installed" for t in report["targets"])

    claude = read(tmp_path / ".claude" / "settings.json")
    assert claude["hooks"]["SessionStart"][0]["hooks"][0] == {
        "type": "command",
        "command": HOOK_LINE,
        "timeout": hooks.DEFAULT_TIMEOUT_SECONDS,
        "managed_by": "hass-axi",
    }
    assert (tmp_path / ".codex" / "hooks.json").exists()
    assert "hooks = true" in (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert (tmp_path / ".config" / "opencode" / "plugins" / "axi-hass-axi.js").exists()


def test_repeat_installs_with_the_same_path_are_no_ops(tmp_path):
    hooks.install(tmp_path, command=EXECUTABLE)
    second = hooks.install(tmp_path, command=EXECUTABLE)
    assert all(t["status"] == "current" for t in second["targets"])


def test_a_changed_executable_path_is_repaired_not_duplicated(tmp_path):
    hooks.install(tmp_path, command="/old/bin/hass-axi")
    hooks.install(tmp_path, command="/new/bin/hass-axi")
    assert commands_in(read(tmp_path / ".claude" / "settings.json")) == [
        f"/new/bin/hass-axi {hooks.CONTEXT_COMMAND}"
    ]


def test_a_user_hook_that_names_this_tool_is_left_alone(tmp_path):
    """Ownership is decided by the marker key, never by a substring of the command.

    This tool takes its configuration from the environment, so an env-prefixed
    wrapper is a hook its users actually write; claiming it rewrote their
    wrapper out of their own global settings and reported the target installed.
    """
    wrapper = "env HA_URL=https://homeassistant.example.com ha-axi"
    settings = write_settings(
        tmp_path,
        {
            "hooks": {
                "SessionStart": [
                    {"matcher": "", "hooks": [{"type": "command", "command": wrapper}]}
                ]
            }
        },
    )
    hooks.install(tmp_path, command=EXECUTABLE)
    assert commands_in(read(settings)) == [wrapper, HOOK_LINE]


def test_a_shell_wrapper_and_another_interpreter_are_left_alone_too(tmp_path):
    """The wrapper above is not the only shape: what they share is not being our entry."""
    others = ["~/bin/ha-axi-wrapper.sh", "python -m ha_axi", "/usr/bin/env ha-axi"]
    settings = write_settings(
        tmp_path,
        {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [{"type": "command", "command": one} for one in others],
                    }
                ]
            }
        },
    )
    hooks.install(tmp_path, command=EXECUTABLE)
    assert commands_in(read(settings)) == [*others, HOOK_LINE]


def test_a_hook_written_before_the_marker_existed_is_adopted_not_duplicated(tmp_path):
    """The one divergence from the sibling, and the reason is that this tool shipped first.

    Every release up to 0.5.1 wrote an unmarked entry, so a marker-only test of
    ownership would append a second hook beside it on every machine that had
    already followed the README. An entry in the exact shape those releases could
    produce -- the executable and nothing else -- is adopted once and carries the
    marker from then on.
    """
    settings = write_settings(
        tmp_path,
        {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [{"type": "command", "command": "/old/bin/ha-axi", "timeout": 10}],
                    }
                ]
            }
        },
    )
    report = hooks.install(tmp_path, command=EXECUTABLE)
    data = read(settings)
    assert commands_in(data) == [HOOK_LINE]
    entry = data["hooks"]["SessionStart"][0]["hooks"][0]
    assert entry[hooks.MANAGED_KEY] == hooks.MARKER
    assert next(t for t in report["targets"] if t["target"] == "claude-code")["status"] == (
        "installed"
    )
    # Adopted once: the marker it gained is what matches it on the next install.
    again = hooks.install(tmp_path, command=EXECUTABLE)
    assert all(t["status"] == "current" for t in again["targets"])


def test_a_second_stale_managed_entry_gives_way_not_reported_current(tmp_path):
    """A restored backup, a hand repair or a partial install can leave two of ours.

    The scan used to stop at the first, so an already-correct entry ended it with
    nothing changed and the second was never repaired -- while every later
    install reported the target ``current`` with a dead path still in the file.
    """
    managed = {
        "type": "command",
        "command": HOOK_LINE,
        "timeout": hooks.DEFAULT_TIMEOUT_SECONDS,
        "managed_by": "hass-axi",
    }
    stale = {**managed, "command": f"/old/bin/hass-axi {hooks.CONTEXT_COMMAND}"}
    settings = write_settings(
        tmp_path, {"hooks": {"SessionStart": [{"matcher": "", "hooks": [managed, stale]}]}}
    )
    report = hooks.install(tmp_path, command=EXECUTABLE)
    assert next(t for t in report["targets"] if t["target"] == "claude-code")["status"] == (
        "installed"
    )
    assert commands_in(read(settings)) == [HOOK_LINE]


def test_a_stale_entry_in_a_later_group_is_repaired_too(tmp_path):
    """Groups are scanned to the end, not only up to the one holding a managed entry."""
    managed = {
        "type": "command",
        "command": HOOK_LINE,
        "timeout": hooks.DEFAULT_TIMEOUT_SECONDS,
        "managed_by": "hass-axi",
    }
    settings = write_settings(
        tmp_path,
        {
            "hooks": {
                "SessionStart": [
                    {"matcher": "", "hooks": [managed]},
                    {
                        "matcher": "startup",
                        "hooks": [
                            {**managed, "command": f"/old/bin/hass-axi {hooks.CONTEXT_COMMAND}"}
                        ],
                    },
                ]
            }
        },
    )
    hooks.install(tmp_path, command=EXECUTABLE)
    assert commands_in(read(settings)) == [HOOK_LINE]


def test_other_tools_hooks_are_left_alone(tmp_path):
    settings = write_settings(
        tmp_path,
        {
            "hooks": {
                "SessionStart": [
                    {"matcher": "", "hooks": [{"type": "command", "command": "other-tool"}]}
                ]
            },
            "unrelated": True,
        },
    )
    hooks.install(tmp_path, command=EXECUTABLE)
    data = read(settings)
    assert commands_in(data) == ["other-tool", HOOK_LINE]
    assert data["unrelated"] is True


def test_a_legacy_lowercase_hook_entry_is_cleaned_up(tmp_path):
    """The cleanup recognizes the entries this tool wrote, and only those.

    An entry under the old key that merely names this tool is a user's on that
    key as on any other, so the same wrapper survives here too.
    """
    wrapper = "env HA_URL=https://homeassistant.example.com ha-axi"
    settings = write_settings(
        tmp_path,
        {
            "hooks": {
                "session_start": [
                    {"type": "command", "command": "ha-axi"},
                    {"type": "command", "command": wrapper},
                ]
            }
        },
    )
    hooks.install(tmp_path, command=EXECUTABLE)
    data = read(settings)
    assert [hook["command"] for hook in data["hooks"]["session_start"]] == [wrapper]
    assert commands_in(data) == [HOOK_LINE]


def _session_start(*entries):
    return {"hooks": {"SessionStart": [{"matcher": "", "hooks": list(entries)}]}}


def test_a_hook_written_under_the_old_name_is_adopted_and_repointed(tmp_path):
    """0.5.1 to 0.7.x marked their entry `ha-axi` and ran `ha-axi context`.

    That executable is gone after the rename, so the entry is rewritten in
    place -- not left to fail at every session start beside a new one.
    """
    legacy = {
        "type": "command",
        "command": f"/old/bin/ha-axi {hooks.CONTEXT_COMMAND}",
        "timeout": 10,
        "managed_by": "ha-axi",
    }
    settings = write_settings(tmp_path, _session_start(legacy))
    hooks.install(tmp_path, command=EXECUTABLE)
    data = read(settings)
    assert commands_in(data) == [HOOK_LINE]
    assert data["hooks"]["SessionStart"][0]["hooks"][0][hooks.MANAGED_KEY] == hooks.MARKER


def test_the_other_tool_published_as_ha_axi_keeps_its_hook(tmp_path):
    """An unrelated `ha-axi` marks its entries with the same string.

    Its hook runs a different subcommand, so the marker alone must not be read
    as ownership: claiming it would delete another tool's hook from the user's
    own settings and report the target `installed`.
    """
    other = {
        "type": "command",
        "command": "ha-axi ping --ambient",
        "timeout": 10,
        "managed_by": "ha-axi",
    }
    settings = write_settings(tmp_path, _session_start(other))
    hooks.install(tmp_path, command=EXECUTABLE)
    assert commands_in(read(settings)) == ["ha-axi ping --ambient", HOOK_LINE]


def test_the_old_opencode_plugin_is_retired_when_it_is_ours(tmp_path):
    plugins = tmp_path / ".config" / "opencode" / "plugins"
    plugins.mkdir(parents=True)
    old = plugins / "axi-ha-axi.js"
    old.write_text("// ha-axi managed opencode plugin: ha-axi\nconst x = 1;\n", encoding="utf-8")
    report = hooks.install(tmp_path, command=EXECUTABLE)
    assert not old.exists()
    assert {"target": "opencode-legacy", "status": "removed"} in report["targets"]
    assert (plugins / "axi-hass-axi.js").exists()


def test_the_other_tools_opencode_plugin_at_the_old_path_is_left_alone(tmp_path):
    plugins = tmp_path / ".config" / "opencode" / "plugins"
    plugins.mkdir(parents=True)
    theirs = plugins / "axi-ha-axi.js"
    body = "// ha-axi managed opencode plugin: some other generator\n"
    theirs.write_text(body, encoding="utf-8")
    report = hooks.install(tmp_path, command=EXECUTABLE)
    assert theirs.read_text(encoding="utf-8") == body
    assert all(t["target"] != "opencode-legacy" for t in report["targets"])


def test_an_unmanaged_opencode_plugin_is_never_overwritten(tmp_path):
    plugin = tmp_path / ".config" / "opencode" / "plugins" / "axi-hass-axi.js"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("// hand written\n", encoding="utf-8")
    report = hooks.install(tmp_path, command=EXECUTABLE)
    assert plugin.read_text(encoding="utf-8") == "// hand written\n"
    assert any("refusing to overwrite" in error for error in report["errors"])


def test_setup_hooks_reports_failures_with_a_non_zero_exit(run_cli, tmp_path):
    plugin = tmp_path / ".config" / "opencode" / "plugins" / "axi-hass-axi.js"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("// hand written\n", encoding="utf-8")
    code, out = run_cli(["setup", "hooks", "--home", str(tmp_path)], {})
    assert code == 1
    assert "refusing to overwrite" in out


def test_setup_hooks_succeeds_and_says_what_to_do_next(run_cli, tmp_path):
    code, out = run_cli(["setup", "hooks", "--home", str(tmp_path)], {})
    assert code == 0
    assert "claude-code,installed" in out
    assert "Restart your agent session" in out


# ------------------------------------------------------- the Codex features flag


def _assert_features_enabled(content: str) -> None:
    """Assert the flag landed where Codex reads it, and that the file still parses.

    ``tomllib`` arrives with Python 3.11 and this project's floor is 3.9, so on
    the older legs the same claim is pinned on the shape that carries it: exactly
    one ``hooks`` key, set to the bare boolean. A second one is a duplicate key,
    which is what a TOML parser refuses.
    """
    if sys.version_info >= (3, 11):
        import tomllib

        assert tomllib.loads(content)["features"] == {"hooks": True}
    else:
        assert content.count("hooks =") == 1
        assert "hooks = true" in content


def test_codex_features_flag_is_added_without_disturbing_other_sections():
    updated, changed, problem = hooks.compute_codex_config_update('[model]\nname = "example"\n')
    assert changed and problem is None
    assert "[model]" in updated and 'name = "example"' in updated
    assert "[features]" in updated and "hooks = true" in updated


def test_codex_features_flag_already_true_is_a_no_op():
    content = "[features]\nhooks = true\n"
    assert hooks.compute_codex_config_update(content) == (content, False, None)


def test_codex_features_flag_set_to_false_is_flipped():
    updated, changed, problem = hooks.compute_codex_config_update("[features]\nhooks = false\n")
    assert changed and problem is None
    _assert_features_enabled(updated)


def test_codex_features_is_inserted_into_an_existing_features_table():
    updated, changed, problem = hooks.compute_codex_config_update(
        "[features]\nother = 1\n\n[model]\nx = 2\n"
    )
    assert changed and problem is None
    assert updated.index("hooks = true") < updated.index("[model]")


def test_a_features_flag_that_is_not_a_bare_boolean_is_rewritten_not_duplicated():
    """`hooks = "true"` and `hooks = 1` are not the bare boolean the flag needs.

    Recognizing only ``true``/``false`` let every other value fall through to the
    append at the end, which wrote a *second* ``hooks`` key into the same table.
    TOML rejects a duplicate key outright, so the tool broke the config it was
    configuring while exiting 0 and reporting the target ``installed``.
    """
    for value in ('"true"', "1", "0", '"yes"'):
        updated, changed, problem = hooks.compute_codex_config_update(
            f'[features]\nhooks = {value}\n\n[model]\nname = "example"\n'
        )
        assert changed and problem is None, value
        _assert_features_enabled(updated)
        assert updated.index("hooks = true") < updated.index("[model]"), value


def test_an_array_of_features_tables_is_refused_rather_than_corrupted(tmp_path):
    """`[[features]]` is not the features table, and nothing written beside it works.

    A key inside an array element enables nothing, and a `[features]` table
    appended beside the array is a declaration TOML refuses -- so the honest
    outcome is a refusal that leaves the file byte-identical.
    """
    content = '[[features]]\nname = "example"\n\n[model]\nname = "example"\n'
    updated, changed, problem = hooks.compute_codex_config_update(content)
    assert not changed and updated == content and problem is not None

    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(content, encoding="utf-8")
    report = hooks.install(tmp_path, command=EXECUTABLE)
    target = next(t for t in report["targets"] if t["target"] == "codex-features")
    assert target["status"] == "skipped"
    assert any("array of tables" in error for error in report["errors"])
    assert config.read_text(encoding="utf-8") == content


# ---------------------------------------------------------------- the command


def test_portable_command_prefers_a_path_entry_resolving_to_this_executable(tmp_path):
    binary = tmp_path / "hass-axi"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    assert hooks.portable_command(str(binary), [str(tmp_path)]) == "hass-axi"


def test_portable_command_falls_back_to_the_absolute_path(tmp_path):
    binary = tmp_path / "hass-axi"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    assert hooks.portable_command(str(binary), []) == str(binary)


def test_the_opencode_plugin_carries_a_managed_marker_and_the_context_argument():
    """It spawns without a shell, so the argument travels beside the path, not in it."""
    source = hooks.opencode_plugin_source(EXECUTABLE, hooks.DEFAULT_TIMEOUT_SECONDS)
    assert hooks.OPENCODE_MANAGED_PREFIX in source
    assert f'const executable = "{EXECUTABLE}"' in source
    assert f'const args = ["{hooks.CONTEXT_COMMAND}"]' in source


def test_a_path_with_a_space_survives_being_joined_with_the_argument():
    """A bare executable was one token and needed no quoting; a command line is not."""
    line = hooks.hook_command("/opt/an example/bin/hass-axi")
    assert shlex.split(line) == ["/opt/an example/bin/hass-axi", hooks.CONTEXT_COMMAND]


def test_the_installed_hook_runs_the_context_command_not_the_home_view(tmp_path):
    """The join between the two halves, and the defect this replaced.

    The no-argument view needs a credential, opens a connection and prints the
    installation's address, and at the time it exited 1 when nothing was
    configured. A hook that ran it failed on every machine that had the package and no installation --
    and a harness is entitled to drop a non-zero hook's output, so the reader
    who most needed telling that this tool exists was the one who never saw it.
    """
    report = hooks.install(tmp_path, command=EXECUTABLE)
    assert report["command"] == HOOK_LINE
    assert report["command"] != EXECUTABLE, "a bare executable would run the home view"
    assert commands_in(read(tmp_path / ".claude" / "settings.json")) == [HOOK_LINE]


# -------------------------------------------------- the document it prints


def test_the_context_command_reaches_home_assistant_zero_times(installation, run_cli):
    """The claim that makes it safe at session start, asserted where it can fail.

    Not the exit code: a version that connected and then printed the same
    document would pass on that, while paying a round-trip and reading a
    credential at the start of every session on the machine.
    """
    code, _ = run_cli(["context"], installation.environ)
    assert code == 0
    assert installation.rest.requests == []
    assert installation.ws.received == []


def test_the_context_command_is_clean_and_useful_with_no_environment_at_all(run_cli, capsys):
    """A machine that has the package and no installation is the ordinary case.

    This is the whole reason the command exists, and the half the home view
    could not do: exit 0, no `error:` line, and still enough to orient.
    """
    code, out = run_cli(["context"], {})
    assert code == 0
    assert "error:" not in out
    assert "NOT_CONFIGURED" not in out
    assert capsys.readouterr().err == ""
    for noun in cli.COMMAND_ORDER:
        assert noun in out
    assert "Set HA_URL" in out
    assert "Set HA_TOKEN" in out


def test_the_context_command_names_which_variables_are_set_and_never_their_values(
    run_cli, rest_env, capsys
):
    """Hook output lands in an agent's context and is logged: a wider surface, not a narrower one."""
    code, out = run_cli(["context"], rest_env)
    err = capsys.readouterr().err
    assert code == 0
    assert "HA_URL and HA_TOKEN are set" in out
    for leak in (rest_env["HA_URL"], rest_env["HA_TOKEN"]):
        assert leak not in out, "the ambient document printed a value it only reports the name of"
        assert leak not in err


def test_the_context_command_reports_a_closed_read_only_gate(run_cli, rest_env):
    """An agent that cannot see a closed gate plans writes it will never be allowed to make."""
    from hass_axi.readonly import ENV_VAR

    assert "read_only" not in run_cli(["context"], rest_env)[1]
    code, out = run_cli(["context"], {**rest_env, ENV_VAR: "1"})
    assert code == 0
    assert "read_only: on" in out
    assert f"unset {ENV_VAR}" in out


def test_the_context_command_never_reports_a_fault_however_it_is_configured(run_cli):
    """Every shape of the environment, and none of them is an error at session start."""
    partial = [{}, {"HA_URL": "https://homeassistant.example.com"}, {"HA_TOKEN": "example-token"}]
    for environ in partial:
        code, out = run_cli(["context"], environ)
        assert code == 0, environ
        assert "code:" not in out and "class:" not in out, environ


#: What one session's ambient context may cost. This loads on *every* session,
#: so the budget is asserted rather than intended -- a line added without
#: thinking about the cost fails here rather than being paid forever by
#: everybody who installed the hook. The ceiling leaves room for one more fact,
#: not for a manual.
CONTEXT_BUDGET_BYTES = 2048


@pytest.mark.parametrize("configured", [True, False])
def test_the_ambient_document_stays_within_its_token_budget(run_cli, rest_env, configured):
    _, out = run_cli(["context"], rest_env if configured else {})
    size = len(out.encode("utf-8"))
    assert size < CONTEXT_BUDGET_BYTES, f"ambient context is {size} bytes"


def test_the_context_command_needs_no_subcommand_and_takes_no_arguments(run_cli):
    assert run_cli(["context"], {})[0] == 0
    assert run_cli(["context", "extra"], {})[0] == 2


def test_the_context_document_never_pays_for_a_quoted_scalar(run_cli, rest_env):
    """It is TOON, so a scalar holding a delimiter or a colon is quoted.

    A pair of quotes on a line of prose is noise bought at the start of every
    agent session, which is the same reason `home.DESCRIPTION` is written
    without a comma.
    """
    for environ in ({}, rest_env):
        _, out = run_cli(["context"], environ)
        for line in out.splitlines():
            if line.startswith(" ") or ": " not in line:
                continue
            assert not line.split(": ", 1)[1].startswith('"'), line


# ------------------------------------------- session end, status and removal


def end_commands_in(settings) -> list:
    return [hook for group in settings["hooks"]["SessionEnd"] for hook in group["hooks"]]


def test_install_adds_a_session_end_hook_for_claude_code_and_codex(tmp_path):
    hooks.install(tmp_path, command=EXECUTABLE)
    claude = end_commands_in(read(tmp_path / ".claude" / "settings.json"))
    codex = end_commands_in(read(tmp_path / ".codex" / "hooks.json"))
    assert [hook["command"] for hook in claude] == [f"{EXECUTABLE} context end"]
    assert [hook["command"] for hook in codex] == [f"{EXECUTABLE} context end"]
    assert all(hook["managed_by"] == "hass-axi" for hook in claude + codex)
    # Codex allows a session-end hook three seconds at most.
    assert codex[0]["timeout"] == 3


def test_the_opencode_plugin_captures_as_well_as_injects(tmp_path):
    hooks.install(tmp_path, command=EXECUTABLE)
    source = (tmp_path / ".config" / "opencode" / "plugins" / "axi-hass-axi.js").read_text(
        encoding="utf-8"
    )
    assert '["context","end"]' in source.replace(", ", ",")
    assert '"tool.execute.before"' in source
    assert "session.idle" in source and "session.deleted" in source


def test_the_opencode_plugin_is_valid_javascript(tmp_path):
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    plugin = tmp_path / "plugin.mjs"
    plugin.write_text(hooks.opencode_plugin_source(EXECUTABLE, 10), encoding="utf-8")
    done = subprocess.run([node, "--check", str(plugin)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_a_session_end_entry_this_tool_did_not_mark_is_never_adopted(tmp_path):
    """No release before the marker wrote a session-end hook, so the two
    adoption rules that exist for session start have nothing to adopt here."""
    theirs = {"type": "command", "command": "ha-axi"}
    also_theirs = {"type": "command", "command": "ha-axi context", "managed_by": "ha-axi"}
    settings = write_settings(
        tmp_path, {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [theirs, also_theirs]}]}}
    )
    hooks.install(tmp_path, command=EXECUTABLE)
    kept = end_commands_in(read(settings))
    assert theirs in kept and also_theirs in kept
    assert len(kept) == 3


def statuses(report) -> dict:
    return {target["target"]: target["status"] for target in report["targets"]}


def snapshot(root) -> dict:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_status_reports_missing_before_and_installed_after_and_writes_nothing(tmp_path):
    before = hooks.status(tmp_path, command=EXECUTABLE)
    assert set(statuses(before).values()) == {"missing"}
    assert list(tmp_path.iterdir()) == [], "a status check created something"

    hooks.install(tmp_path, command=EXECUTABLE)
    written = snapshot(tmp_path)
    after = hooks.status(tmp_path, command=EXECUTABLE)
    assert statuses(after) == {
        "claude-code": "installed",
        "claude-code-session-end": "installed",
        "codex": "installed",
        "codex-session-end": "installed",
        "codex-features": "installed",
        "opencode": "installed",
    }
    assert snapshot(tmp_path) == written


def test_status_calls_a_moved_executable_stale(tmp_path):
    hooks.install(tmp_path, command="/old/bin/hass-axi")
    report = statuses(hooks.status(tmp_path, command="/new/bin/hass-axi"))
    assert report["claude-code"] == "stale"
    assert report["claude-code-session-end"] == "stale"
    assert report["opencode"] == "stale"
    assert report["codex-features"] == "installed"


def test_status_calls_an_entry_from_before_the_rename_stale(tmp_path):
    old = {"type": "command", "command": "ha-axi context", "managed_by": "ha-axi", "timeout": 10}
    write_settings(tmp_path, {"hooks": {"SessionStart": [{"matcher": "", "hooks": [old]}]}})
    assert statuses(hooks.status(tmp_path, command=EXECUTABLE))["claude-code"] == "stale"


def test_status_names_an_opencode_plugin_this_tool_did_not_write(tmp_path):
    plugin = tmp_path / ".config" / "opencode" / "plugins" / "axi-hass-axi.js"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("// somebody else's plugin\n", encoding="utf-8")
    assert statuses(hooks.status(tmp_path, command=EXECUTABLE))["opencode"] == "unmanaged"


def test_remove_takes_out_what_install_wrote_and_is_idempotent(tmp_path):
    hooks.install(tmp_path, command=EXECUTABLE)
    first = hooks.remove(tmp_path)
    assert first["errors"] == []
    assert statuses(first) == {
        "claude-code": "removed",
        "claude-code-session-end": "removed",
        "codex": "removed",
        "codex-session-end": "removed",
        "codex-features": "kept",
        "opencode": "removed",
    }
    assert read(tmp_path / ".claude" / "settings.json") == {}
    assert read(tmp_path / ".codex" / "hooks.json") == {}
    assert not (tmp_path / ".config" / "opencode" / "plugins" / "axi-hass-axi.js").exists()
    # Shared with every other tool that installs a Codex hook, so it stays.
    assert "hooks = true" in (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")

    second = statuses(hooks.remove(tmp_path))
    assert (
        second["claude-code"]
        == second["claude-code-session-end"]
        == second["codex"]
        == second["codex-session-end"]
        == second["opencode"]
        == "absent"
    )
    assert set(statuses(hooks.status(tmp_path, command=EXECUTABLE)).values()) == {
        "missing",
        "installed",
    }


def test_remove_leaves_everything_this_tool_did_not_write(tmp_path):
    """The whole point of the marker: removal claims exactly what install would.

    A user's wrapper that names this tool, another tool's hooks on the same
    events, an unrelated setting and the other `ha-axi`'s marked entry all
    survive, byte for byte, an install followed by a remove.
    """
    document = {
        "model": "example",
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "env HA_URL=https://homeassistant.example.com hass-axi",
                        },
                        {
                            "type": "command",
                            "command": "ha-axi ping --ambient",
                            "managed_by": "ha-axi",
                        },
                    ],
                },
                {"matcher": "startup", "hooks": [{"type": "command", "command": "other-tool"}]},
            ],
            "SessionEnd": [{"hooks": [{"type": "command", "command": "other-tool end"}]}],
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}],
        },
    }
    settings = write_settings(tmp_path, document)
    hooks.install(tmp_path, command=EXECUTABLE)
    assert read(settings) != document
    hooks.remove(tmp_path)
    assert read(settings) == document


def test_remove_never_deletes_an_opencode_plugin_this_tool_did_not_write(tmp_path):
    plugin = tmp_path / ".config" / "opencode" / "plugins" / "axi-hass-axi.js"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("// somebody else's plugin\n", encoding="utf-8")
    assert statuses(hooks.remove(tmp_path))["opencode"] == "unmanaged"
    assert plugin.read_text(encoding="utf-8") == "// somebody else's plugin\n"


def test_remove_takes_out_an_entry_an_earlier_release_wrote(tmp_path):
    """What this tool installed under its old name is still what this tool installed."""
    unmarked = {"type": "command", "command": "/usr/local/bin/ha-axi"}
    renamed = {"type": "command", "command": "ha-axi context", "managed_by": "ha-axi"}
    settings = write_settings(
        tmp_path, {"hooks": {"SessionStart": [{"matcher": "", "hooks": [unmarked, renamed]}]}}
    )
    assert statuses(hooks.remove(tmp_path))["claude-code"] == "removed"
    assert read(settings) == {}


def test_the_setup_command_installs_reports_and_removes(run_cli, tmp_path):
    home = ["--home", str(tmp_path)]
    code, out = run_cli(["setup", "hooks", "status", *home], {})
    assert code == 0
    assert "claude-code,missing" in out
    assert "Run `hass-axi setup hooks` to install" in out

    assert run_cli(["setup", "hooks", *home], {})[0] == 0
    assert run_cli(["setup", "hooks", "install", *home], {})[0] == 0
    code, out = run_cli(["setup", "hooks", "status", *home], {})
    assert code == 0
    assert "claude-code-session-end,installed" in out
    assert "Run `hass-axi setup hooks remove`" in out

    code, out = run_cli(["setup", "hooks", "remove", *home], {})
    assert code == 0
    assert "claude-code,removed" in out
    assert "codex-features,kept" in out
    code, out = run_cli(["setup", "hooks", "remove", *home], {})
    assert code == 0
    assert "claude-code,absent" in out


def test_an_unknown_hooks_action_is_a_usage_error_that_names_the_three(run_cli, tmp_path):
    code, out = run_cli(["setup", "hooks", "uninstall", "--home", str(tmp_path)], {})
    assert code == 2
    assert "code: UNKNOWN_SUBCOMMAND" in out
    assert "install, status, remove" in out
    assert list(tmp_path.iterdir()) == []


def test_removing_the_hooks_removes_the_session_record_they_wrote(run_cli, tmp_path, monkeypatch):
    from hass_axi import sessionlog

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    sessionlog.record(
        {"session_id": "one", "cwd": str(tmp_path), "commands": ["hass-axi doctor"]},
        {"doctor": ["doctor"]},
    )
    assert sessionlog.state_path().exists()
    code, out = run_cli(["setup", "hooks", "remove"], {})
    assert code == 0
    assert "session-record,removed" in out
    assert not sessionlog.state_path().exists()


def test_a_read_only_session_can_check_the_hooks_and_cannot_remove_them(run_cli, tmp_path):
    hooks.install(tmp_path, command=EXECUTABLE)
    written = snapshot(tmp_path)
    environ = {"HASS_AXI_READ_ONLY": "1"}
    code, out = run_cli(["setup", "hooks", "status", "--home", str(tmp_path)], environ)
    assert code == 0
    assert "targets[" in out
    code, out = run_cli(["setup", "hooks", "remove", "--home", str(tmp_path)], environ)
    assert code == 2
    assert "code: READ_ONLY" in out
    assert snapshot(tmp_path) == written


def test_the_context_document_with_a_recorded_session_stays_within_budget(
    run_cli, tmp_path, monkeypatch
):
    from hass_axi import sessionlog

    monkeypatch.chdir(tmp_path)
    nouns = {name: [sub.name for sub in spec.subs] for name, spec in cli.command_specs().items()}
    labels = [f"{noun} {subs[0]}" if subs else noun for noun, subs in nouns.items()]
    sessionlog.record(
        {"session_id": "many", "cwd": str(tmp_path), "commands": [f"hass-axi {x}" for x in labels]},
        nouns,
    )
    code, out = run_cli(["context"], {})
    assert code == 0
    line = next(row for row in out.splitlines() if row.startswith("last_session: "))
    assert not line.split(": ", 1)[1].startswith('"'), line
    assert len(out.encode("utf-8")) < CONTEXT_BUDGET_BYTES

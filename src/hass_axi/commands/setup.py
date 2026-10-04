"""`hass-axi setup` -- install the session integrations and the agent skill."""

from __future__ import annotations

from pathlib import Path

from .. import hooks, sessionlog, skill
from ..argspec import Command, Flag, Sub
from ..errors import UsageError
from ..output import HelpBlock
from ..readonly import DYNAMIC, READ, WRITE

COMMAND = Command(
    name="setup",
    summary="Install, check or remove the agent integrations for hass-axi",
    usage="usage: hass-axi setup <subcommand> [flags]",
    subs=(
        Sub(
            name="hooks",
            # `setup` writes to this machine rather than to Home Assistant, and
            # counts as a write anyway. HASS_AXI_READ_ONLY says this tool does not
            # write; splitting that into "not your house" and "not your
            # dotfiles" is a distinction nobody asked for, and the safe half of
            # it is refusing both. `status` is the read half, which `access`
            # below resolves.
            access=DYNAMIC,
            args=("[install|status|remove]",),
            summary="Install, check or remove the session hooks for Claude Code, Codex and OpenCode",
            flags=(Flag("--home", "<path>", note="act under a different home directory"),),
        ),
        Sub(
            name="skill",
            access=DYNAMIC,
            summary="Write or verify the installable Agent Skill",
            flags=(
                Flag("--path", "<dir>", default=".", note="repository root"),
                Flag(
                    "--check", boolean=True, note="exit non-zero when the committed copy is stale"
                ),
            ),
        ),
    ),
    notes=(
        "hooks give ambient context every session; the skill loads on demand instead -- install either",
        "hook installation is idempotent and repairs the path after a reinstall or a move",
        "`setup hooks status` reports each hook as installed, stale or missing and writes nothing",
        "`setup hooks remove` takes out only the entries this tool wrote, and leaves Codex's "
        "`[features] hooks = true` on because other tools' Codex hooks depend on it",
        "a session-end hook, `hass-axi context end`, records which hass-axi commands a session "
        "ran -- names and counts, never arguments -- so the next session's context in that "
        "directory can say so; OpenCode has no session-end event, so its plugin records when a "
        "session goes idle",
    ),
    examples=(
        "hass-axi setup hooks",
        "hass-axi setup hooks status",
        "hass-axi setup hooks remove",
        "hass-axi setup skill",
        "hass-axi setup skill --check",
    ),
)


HOOK_ACTIONS = ("install", "status", "remove")


def _hook_action(parsed) -> str:
    """Which of the three `setup hooks` does; installing is what a bare one means."""
    action = parsed.positionals[0] if parsed.positionals else "install"
    if action not in HOOK_ACTIONS:
        raise UsageError(
            f"unknown subcommand `{action}` for `setup hooks`",
            help_lines=[
                f"subcommands: {', '.join(HOOK_ACTIONS)}",
                "Run `hass-axi setup --help` for the full reference",
            ],
            code="UNKNOWN_SUBCOMMAND",
        )
    return action


def access(sub: str, parsed) -> str:
    """`skill --check` and `hooks status` only read; everything else here writes a file."""
    if sub == "hooks":
        return READ if _hook_action(parsed) == "status" else WRITE
    return READ if parsed.get("check") else WRITE


def run(ctx, sub: str, parsed):
    if sub == "hooks":
        home = parsed.get("home")
        home = Path(home) if home else None
        action = _hook_action(parsed)
        if action == "status":
            return _hooks_status(home)
        if action == "remove":
            return _hooks_remove(ctx, home)
        if home is not None and not Path(home).is_dir():
            # Created on demand, a mistyped home is a tree of hooks no agent
            # will ever read, reported `installed`.
            raise UsageError(
                f"--home {home} is not an existing directory",
                help_lines=[
                    "Pass the home directory the agents read their settings from",
                    "Run `hass-axi setup hooks` with no --home to use this user's own",
                ],
                code="UNWRITABLE",
            )
        return _hooks(home)
    return _skill(ctx, parsed)


def _hooks(home):
    report = hooks.install(home)
    doc = {
        "hooks": {"command": report["command"]},
        "targets": report["targets"],
    }
    if report["errors"]:
        doc["errors"] = report["errors"]
        doc["__exit_code__"] = 1
    else:
        doc["help"] = HelpBlock(
            [
                "Restart your agent session to receive hass-axi ambient context at session start",
                "Run `hass-axi setup hooks status` to check the hooks later",
                "Run `hass-axi setup hooks remove` to uninstall them",
            ]
        )
    return doc


def _hooks_status(home):
    report = hooks.status(home)
    doc = {
        "hooks": {"command": report["command"]},
        "targets": report["targets"],
    }
    if report["errors"]:
        doc["errors"] = report["errors"]
    # A read, so it exits 0 whatever it finds: "not installed" is an answer.
    states = {target["status"] for target in report["targets"]}
    if states - {"installed"}:
        doc["help"] = HelpBlock(
            ["Run `hass-axi setup hooks` to install the missing hooks and repair stale ones"]
        )
    else:
        doc["help"] = HelpBlock(["Run `hass-axi setup hooks remove` to uninstall them"])
    return doc


def _hooks_remove(ctx, home):
    report = hooks.remove(home)
    # The session record is this tool's own file and is only ever written by
    # the hooks being removed, so it goes with them. Under `--home` the hooks
    # belong to another home directory and the record here is not theirs.
    if home is None:
        try:
            state = "removed" if sessionlog.forget(ctx.environ) else "absent"
        except OSError as exc:
            report["errors"].append(f"session record: {exc}")
            state = "failed"
        report["targets"].append({"target": "session-record", "status": state})
    doc = {"targets": report["targets"]}
    if report["errors"]:
        doc["errors"] = report["errors"]
        doc["__exit_code__"] = 1
        return doc
    # Idempotent: a second removal reports every target `absent` and exits 0,
    # because the state it asks for already holds.
    doc["help"] = HelpBlock(["Run `hass-axi setup hooks` to install them again"])
    return doc


def _skill(ctx, parsed):
    from ..cli import COMMAND_ORDER, command_specs

    root = Path(parsed.get("path") or ".")
    path = skill.target_path(root)
    content = skill.render([command_specs()[name] for name in COMMAND_ORDER])

    if parsed.get("check"):
        if not path.exists():
            return {
                "skill": str(path),
                "status": "missing",
                "help": HelpBlock([f"Run `hass-axi setup skill --path {root}` to write it"]),
                "__exit_code__": 1,
            }
        try:
            committed = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise UsageError(
                f"could not read {path}: {exc.strerror or exc}",
                help_lines=[f"Run `hass-axi setup skill --path {root}` to rewrite it"],
                code="UNREADABLE",
            ) from None
        if committed != content:
            return {
                "skill": str(path),
                "status": "stale",
                "help": HelpBlock([f"Run `hass-axi setup skill --path {root}` to regenerate it"]),
                "__exit_code__": 1,
            }
        return {"skill": str(path), "status": "current"}

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        changed = not path.exists() or path.read_text(encoding="utf-8") != content
        if changed:
            path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise UsageError(
            f"could not write {path}: {exc.strerror or exc}",
            help_lines=["Pass a writable repository root with `--path <dir>`"],
            code="UNWRITABLE",
        ) from None

    return {
        "skill": str(path),
        "status": "written" if changed else "current",
        "help": HelpBlock(
            [
                f"Install it in an agent with `npx skills add dmealing/hass-axi --skill {skill.SKILL_NAME}`"
            ]
        ),
    }

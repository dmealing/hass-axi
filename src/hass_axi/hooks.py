"""Session-lifecycle integration for the agents that support it.

Ported from the shared AXI session-hook contract so hass-axi installs the same way
its sibling CLIs do: a SessionStart hook for Claude Code and Codex, and a managed
ambient-context plugin for OpenCode.

Installation happens only from `hass-axi setup hooks`, never as a side effect of an
ordinary command.

**What the hook runs is :data:`CONTEXT_COMMAND` and not the bare executable.**
The no-argument home view is live state: it needs a credential, opens a
connection, prints the installation's address, and -- the half that made this a
defect rather than a preference -- reports `NOT_CONFIGURED` and exits **1** on a
machine that has the package and no installation. That is the correct answer for
somebody who asked for live state, and the wrong thing to put at the start of
every session: it fails for exactly the reader ambient context exists to help,
and a harness is entitled to drop a non-zero hook's output. So the hook runs
`hass-axi context`, which reads the environment and the command table and nothing
else. See :mod:`hass_axi.commands.context`, which is where that argument is made
at length; the error taxonomy is untouched, because only the hook path changed.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

MARKER = "hass-axi"
BINARY_NAMES = ("hass-axi",)

#: What this tool was called before the rename, and what every hook, marker and
#: plugin it installed up to 0.7.x carries. Read only to adopt those entries;
#: nothing is ever written under these names again.
LEGACY_MARKER = "ha-axi"
LEGACY_BINARY_NAMES = ("ha-axi",)

#: The key that marks a JSON hook entry as one this installer wrote, read back by
#: exact equality on its value. Deliberately not a substring of the command: a
#: user's own entry that merely names this tool -- an environment prefix, another
#: interpreter, a shell wrapper -- is theirs, and an installer that claimed every
#: hook mentioning its own name would rewrite the wrapper out of their own
#: settings and report the target ``installed``.
MANAGED_KEY = "managed_by"

#: The subcommand a session hook runs. Not the bare executable: see the module
#: docstring for why the no-argument home view cannot be what a hook prints.
CONTEXT_COMMAND = "context"

DEFAULT_TIMEOUT_SECONDS = 10
OPENCODE_MANAGED_PREFIX = "hass-axi managed opencode plugin:"


def write_atomic(path: Path, text: str) -> None:
    """Replace a file's contents in one step.

    These are the user's own global agent settings; a partial write during a
    crash would leave them with a truncated, unparseable file.
    """
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def portable_command(exec_path: str, path_entries: list | None = None) -> str:
    """Prefer a bare binary name when PATH resolves it to this same executable.

    A global install stays portable across machines; anything else falls back to
    the absolute path so the hook can never invoke a different binary.
    """
    try:
        resolved = Path(exec_path).resolve()
    except OSError:
        return exec_path
    if not resolved.is_file():
        return exec_path

    entries = path_entries
    if entries is None:
        entries = [entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    for name in BINARY_NAMES:
        for directory in entries:
            candidate = Path(directory) / name
            try:
                if candidate.is_file() and candidate.resolve() == resolved:
                    return name
            except OSError:
                continue
    return exec_path


def current_executable() -> str:
    """The executable a hook should invoke to reach this installation."""
    argv0 = sys.argv[0] or ""
    which = shutil.which(BINARY_NAMES[0])
    exec_path = argv0
    if argv0 and Path(argv0).exists():
        exec_path = str(Path(argv0).resolve())
    elif which:
        exec_path = str(Path(which).resolve())
    return portable_command(exec_path)


def hook_command(executable: str) -> str:
    """The whole command line a JSON hook entry records.

    Quoted, which the bare executable never needed to be: a single token survives
    being written into a field the agent hands to a shell whatever it contains,
    and this one carries an argument after the path, so an unquoted space would
    split the executable in two.
    """
    return f"{shlex.quote(executable)} {CONTEXT_COMMAND}"


# ------------------------------------------------------------- JSON settings


def _managed_hook(command: str, timeout: int) -> dict:
    """The one construction site for a JSON hook entry this tool owns.

    Every entry written here carries the marker key and no entry written from
    now on is recognized as ours without it, so what is written and what is
    claimed cannot drift apart. The marker travels with an entry through a path
    repair, which is why ownership cannot be decided from the command string: a
    moved executable's stale entry is by definition a different string.
    """
    return {"type": "command", "command": command, "timeout": timeout, MANAGED_KEY: MARKER}


def _is_marked(hook) -> bool:
    return isinstance(hook, dict) and hook.get(MANAGED_KEY) == MARKER


def _is_unmarked_own_entry(hook) -> bool:
    """An entry a release before the marker existed wrote, and nothing else.

    **This is the one place this installer diverges from the sibling's, and the
    reason is that this tool shipped its hook first.** The sibling could make the
    marker key the sole test of ownership because no release of it had ever
    written a hook, so an unmarked entry there is necessarily a user's. Here
    every install up to 0.5.1 wrote an unmarked entry, so the same rule would
    append a second one beside it on the next `hass-axi setup hooks` --
    manufacturing exactly the duplicate :func:`compute_hook_update` now
    collapses, on every machine that had already followed the README.

    So an unmarked entry is adopted, but only in the exact shape those releases
    could produce: the command is the executable and nothing else. Those
    releases were published as ``ha-axi``, so that is the basename this matches.
    A wrapper is more than the executable -- ``env HA_URL=... ha-axi``,
    ``bash -c ...``, ``~/bin/ha-axi-wrapper.sh`` -- and keeps its own basename
    or its own extra tokens either way, so none of them answers to this.
    Adoption is one-way and happens once: the entry gains the marker on that
    install and is matched by it forever after.
    """
    if not isinstance(hook, dict) or MANAGED_KEY in hook:
        return False
    command = str(hook.get("command", "")).strip()
    return bool(command) and Path(command).name in LEGACY_BINARY_NAMES


def _is_renamed_own_entry(hook) -> bool:
    """An entry this tool wrote while it was still called ``ha-axi``.

    Releases from 0.5.1 to 0.7.x marked their entry ``managed_by: ha-axi`` and
    ran ``ha-axi context``. After the rename that executable no longer exists,
    so the entry is adopted and rewritten rather than left to fail at every
    session start beside a new one.

    **The marker alone is not enough, and that is the reason this predicate
    reads the command too.** A different, unrelated tool is also published as
    ``ha-axi`` -- the collision this rename exists to end -- and its installer
    marks its own entries with the same string. Its hook runs a different
    subcommand, so an entry is only ours if its command is exactly what this
    tool wrote: one executable whose basename is ``ha-axi``, followed by
    :data:`CONTEXT_COMMAND` and nothing else. Anything more is left alone.
    """
    if not isinstance(hook, dict) or hook.get(MANAGED_KEY) != LEGACY_MARKER:
        return False
    try:
        tokens = shlex.split(str(hook.get("command", "")))
    except ValueError:
        return False
    return (
        len(tokens) == 2
        and Path(tokens[0]).name in LEGACY_BINARY_NAMES
        and tokens[1] == CONTEXT_COMMAND
    )


def _is_managed(hook) -> bool:
    return _is_marked(hook) or _is_unmarked_own_entry(hook) or _is_renamed_own_entry(hook)


def compute_hook_update(settings: dict, command: str, timeout: int) -> tuple:
    """Return ``(settings, changed)`` with this tool's SessionStart hook current.

    Repeat installs with an unchanged path are silent no-ops; a changed path is
    repaired in place rather than duplicated. The scan covers every group rather
    than stopping at the first managed entry, and every managed entry beyond the
    first is collapsed: settings restored from a backup, repaired by hand, or
    left by a partial earlier install can hold two, and the stale one has to give
    way whichever position it sits in. Stopping early reported the target
    ``current`` while a dead path stayed in the file, which is the opposite of
    what `setup --help` and the README promise about repairing after a move.
    """
    updated = json.loads(json.dumps(settings)) if settings else {}
    changed = False

    hooks = updated.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        updated["hooks"] = hooks
        changed = True

    legacy = hooks.get("session_start")
    if isinstance(legacy, list):
        kept = [hook for hook in legacy if not _is_managed(hook)]
        if len(kept) != len(legacy):
            changed = True
            if kept:
                hooks["session_start"] = kept
            else:
                hooks.pop("session_start", None)

    groups = hooks.get("SessionStart")
    if not isinstance(groups, list):
        groups = []
        hooks["SessionStart"] = groups
        changed = True

    have_managed = False
    for group in list(groups):
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            continue
        kept_hooks = []
        for hook in group["hooks"]:
            if not _is_managed(hook):
                kept_hooks.append(hook)
                continue
            if have_managed:
                changed = True
                continue
            have_managed = True
            # The marker is part of being current: an entry adopted from a
            # release that predates it is otherwise identical and still has to
            # be written back, or it would be adopted again on every install.
            correct = (
                hook.get(MANAGED_KEY) == MARKER
                and hook.get("command") == command
                and hook.get("type") == "command"
                and hook.get("timeout") == timeout
            )
            if not correct:
                hook.update(_managed_hook(command, timeout))
                changed = True
            kept_hooks.append(hook)
        if kept_hooks != group["hooks"]:
            group["hooks"] = kept_hooks
            if not kept_hooks:
                groups.remove(group)

    if not have_managed:
        groups.append({"matcher": "", "hooks": [_managed_hook(command, timeout)]})
        return updated, True
    return (updated, True) if changed else (settings, False)


def compute_codex_config_update(content: str) -> tuple:
    """Ensure Codex has ``[features] hooks = true`` without disturbing the rest.

    Returns ``(content, changed, problem)``. Any existing ``hooks`` key in the
    features table is *rewritten*, whatever its value: recognizing only the bare
    booleans left ``hooks = "true"`` and ``hooks = 1`` falling through to the
    append at the end, which wrote a second ``hooks`` key into the same table.
    That is a duplicate key, which TOML rejects outright -- so the tool broke the
    config it was configuring while exiting 0 and reporting ``installed``.

    ``problem`` is set when the config cannot carry the flag at all: a
    ``[[features]]`` array of tables is not the features table -- a key written
    beside it lands inside one array element and enables nothing, and a
    ``[features]`` table appended beside it is a declaration TOML refuses -- so
    the caller reports a refusal rather than a target that only looks installed.
    """
    newline = "\r\n" if "\r\n" in content else "\n"
    if not content.strip():
        return f"[features]{newline}hooks = true{newline}", True, None

    lines = content.split("\n")
    lines = [line.rstrip("\r") for line in lines]
    in_features = False
    saw_features = False
    saw_features_array = False

    for index, line in enumerate(lines):
        section = re.match(r"^\s*(\[{1,2})([^\]]+)(\]{1,2})\s*(?:#.*)?$", line)
        if section:
            opener, name, closer = section.group(1), section.group(2).strip(), section.group(3)
            if len(opener) != len(closer):
                continue
            if in_features:
                lines.insert(index, "hooks = true")
                return newline.join(lines), True, None
            if name == "features":
                if len(opener) == 1:
                    in_features = True
                    saw_features = True
                else:
                    saw_features_array = True
            continue
        if not in_features:
            continue
        if re.match(r"^\s*hooks\s*=\s*true\s*(?:#.*)?$", line):
            return content, False, None
        if re.match(r"^\s*hooks\s*=", line):
            lines[index] = "hooks = true"
            return newline.join(lines), True, None

    if saw_features:
        suffix = "" if content.endswith(newline) else newline
        return f"{content}{suffix}hooks = true{newline}", True, None
    if saw_features_array:
        return (
            content,
            False,
            "`[features]` is an array of tables here; rewrite it as a `[features]` "
            "table and install again",
        )
    separator = newline if content.endswith(newline) else newline * 2
    return f"{content}{separator}[features]{newline}hooks = true{newline}", True, None


# ------------------------------------------------------------------ OpenCode


def opencode_plugin_source(executable: str, timeout: int) -> str:
    """The managed OpenCode plugin, which injects the same document as ambient system context.

    The executable and its argument are passed as separate values rather than as
    the joined command line the JSON hooks record: this spawns without a shell,
    so a joined string would be looked up as one filename.
    """
    header = f"{OPENCODE_MANAGED_PREFIX} {MARKER}"
    return f"""// {header}
// Generated by `hass-axi setup hooks`. Remove the managed marker above before editing.
import {{ spawn }} from "node:child_process";

const executable = {json.dumps(executable)};
const args = {json.dumps([CONTEXT_COMMAND])};
const marker = {json.dumps(MARKER)};
const ambientHeader = {json.dumps(f"## AXI ambient context: {MARKER}")};
const timeoutMs = {timeout * 1000};

function runContext(cwd) {{
  return new Promise((resolve) => {{
    const child = spawn(executable, args, {{
      cwd: cwd && cwd.length > 0 ? cwd : process.cwd(),
      env: process.env,
      shell: false,
      stdio: ["ignore", "pipe", "pipe"],
    }});
    let stdout = "";
    let stderr = "";
    let settled = false;
    const timer = setTimeout(() => {{
      if (settled) return;
      settled = true;
      child.kill("SIGTERM");
      resolve("error: " + marker + " ambient context timed out after " + timeoutMs + "ms");
    }}, timeoutMs);
    child.stdout?.setEncoding("utf-8");
    child.stderr?.setEncoding("utf-8");
    child.stdout?.on("data", (chunk) => {{ stdout += chunk; }});
    child.stderr?.on("data", (chunk) => {{ stderr += chunk; }});
    child.on("error", (error) => {{
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve("error: " + marker + " ambient context failed: " + error.message);
    }});
    child.on("close", (code) => {{
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (code === 0) {{ resolve(stdout.trim()); return; }}
      resolve((stderr || stdout || marker + " exited with code " + code).trim());
    }});
  }});
}}

export const HassAxiAmbientContextPlugin = async ({{ directory }}) => {{
  const sessionCache = new Map();
  return {{
    "experimental.chat.system.transform": async (input, output) => {{
      const sessionID = input.sessionID ?? "__global__";
      let context = sessionCache.get(sessionID);
      if (context === undefined) {{
        context = await runContext(directory);
        sessionCache.set(sessionID, context);
      }}
      if (context.length === 0) return;
      output.system.push(ambientHeader + "\\n" + context);
    }},
  }};
}};
"""


# ------------------------------------------------------------------- install


def install(
    home: Path | None = None,
    *,
    command: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """Install or repair every session integration. Returns a per-target report.

    ``command`` names the *executable*; the argument that makes it print the
    ambient document is this module's to add, so a caller cannot install a hook
    that runs the connecting home view by mistake.
    """
    home = Path(home) if home else Path.home()
    executable = command or current_executable()
    line = hook_command(executable)
    report: dict = {"command": line, "targets": [], "errors": []}

    for label, path in (
        ("claude-code", home / ".claude" / "settings.json"),
        ("codex", home / ".codex" / "hooks.json"),
    ):
        report["targets"].append(_install_json_hook(label, path, line, timeout, report))

    report["targets"].append(_install_codex_features(home / ".codex" / "config.toml", report))
    report["targets"].append(
        _install_opencode(
            home / ".config" / "opencode" / "plugins" / f"axi-{MARKER}.js",
            executable,
            timeout,
            report,
        )
    )
    retired = _retire_legacy_opencode(
        home / ".config" / "opencode" / "plugins" / f"axi-{LEGACY_MARKER}.js", report
    )
    if retired is not None:
        report["targets"].append(retired)
    return report


#: The first line of the OpenCode plugin this tool wrote while it was called
#: ``ha-axi``. Matched as a whole line: the unrelated tool also published as
#: ``ha-axi`` writes a plugin to the same path whose header begins with the same
#: words and continues differently, and that file is not ours to delete.
LEGACY_OPENCODE_HEADER = f"// {LEGACY_MARKER} managed opencode plugin: {LEGACY_MARKER}"


def _retire_legacy_opencode(path: Path, report: dict) -> dict | None:
    """Delete the plugin the pre-rename releases installed, if it is ours.

    It spawns ``ha-axi context``, which no longer exists once the package is
    renamed, so left in place it would inject an error at every OpenCode
    session beside the new plugin's context. Returns a target row only when
    there was something to retire, so a machine that never had one reports
    exactly the targets it always did.
    """
    try:
        if not path.is_file():
            return None
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines or lines[0].strip() != LEGACY_OPENCODE_HEADER:
            return None
        path.unlink()
        return {"target": "opencode-legacy", "status": "removed"}
    except OSError as exc:
        report["errors"].append(f"{path}: {exc}")
        return {"target": "opencode-legacy", "status": "failed"}


def _install_json_hook(label: str, path: Path, command: str, timeout: int, report: dict) -> dict:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(current, dict):
            current = {}
        updated, changed = compute_hook_update(current, command, timeout)
        if changed:
            write_atomic(path, json.dumps(updated, indent=2) + "\n")
        return {"target": label, "status": "installed" if changed else "current"}
    except (OSError, json.JSONDecodeError) as exc:
        report["errors"].append(f"{path}: {exc}")
        return {"target": label, "status": "failed"}


def _install_codex_features(path: Path, report: dict) -> dict:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        updated, changed, problem = compute_codex_config_update(current)
        if problem is not None:
            report["errors"].append(f"{path}: {problem}")
            return {"target": "codex-features", "status": "skipped"}
        if changed:
            write_atomic(path, updated)
        return {"target": "codex-features", "status": "installed" if changed else "current"}
    except OSError as exc:
        report["errors"].append(f"{path}: {exc}")
        return {"target": "codex-features", "status": "failed"}


def _install_opencode(path: Path, executable: str, timeout: int, report: dict) -> dict:
    managed = f"{OPENCODE_MANAGED_PREFIX} {MARKER}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current is not None and managed not in current:
            report["errors"].append(f"{path}: refusing to overwrite an unmanaged plugin")
            return {"target": "opencode", "status": "skipped"}
        source = opencode_plugin_source(executable, timeout)
        if current == source:
            return {"target": "opencode", "status": "current"}
        write_atomic(path, source)
        return {"target": "opencode", "status": "installed"}
    except OSError as exc:
        report["errors"].append(f"{path}: {exc}")
        return {"target": "opencode", "status": "failed"}

"""Session-end capture: what a session did with this tool, for the next one's context.

AXI section 7 asks for *lifecycle capture* as well as ambient context: a
session-end hook records what happened, so the session-start document gets
richer over time instead of saying the same thing forever. `hass-axi setup
hooks` installs `hass-axi context end` as that hook, and `hass-axi context`
reads back what it recorded for the directory it runs in.

**What is recorded is deliberately thin, and the thinness is the design.** One
record per session: the directory, the date, and how many times each command
ran -- ``state list x3, service call x2`` -- with a count of the invocations
that carried ``--write``. Never an argument. Arguments are where entity ids,
area names and device names live, and where a token typed on a command line
would be; the record is read back into an agent's context on every later
session, which is a wider surface than the terminal the command was typed in.
Command names say what the last session was doing without saying anything
about the house.

**It reads what the agent hands it and nothing else.** Claude Code and Codex
name a transcript file in the hook's payload; the commands are the shell
commands the agent issued through its tools, found by their tool-call shape
rather than by searching the prose -- a session that merely *discussed*
`hass-axi state list` did not run it. OpenCode hands a plugin no transcript, so
its plugin passes the commands themselves. Nothing here opens a connection, and
every failure -- no payload, an unreadable transcript, an unwritable state
directory -- records nothing and says so, because a hook that failed would be
reported to the user as their session failing to close.

**A transcript's format belongs to the agent that wrote it**, and neither one
promises to keep it. So the reader is tolerant rather than exact: it walks
every JSON line for a tool call carrying a shell command in any of the shapes
the two agents use, and a transcript it cannot find one in records nothing
rather than failing.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import re
import shlex
from pathlib import Path

from .hooks import write_atomic

#: How many sessions the state file remembers, across every directory.
KEEP = 50

#: How many command labels one line of context names before it counts the rest.
#: The line loads at the start of every session, so it is bounded.
SHOWN = 4

#: The flag whose presence means an invocation sent something rather than
#: previewing it. Written out rather than imported from the command modules:
#: this module is read by the hook path and must not pull the commands in.
APPLYING_FLAGS = ("--write",)

#: Global flags that ask about the tool rather than use it.
_PROBES = {"--version", "-v", "-V", "--help", "-h"}

#: Where an invocation of this tool starts inside a shell command line: in
#: command position -- the start of a line, or after ``;``, ``&``, ``|``, ``(``
#: or ``$(`` -- optionally behind environment assignments, a launcher such as
#: ``env`` or ``time``, and a path. Command position is the point: ``grep
#: hass-axi notes.md`` and a commit message that mentions the tool name it
#: without running it. A backtick is deliberately not an operator here: in an
#: agent's commands it is far more often Markdown quoting a command inside a
#: string than a shell substitution running one.
_INVOCATION = re.compile(
    r"(?:^|[;&|(]|\$\()[ \t]*"
    r"(?:(?:[A-Za-z_][A-Za-z0-9_]*=\S*|env|time|exec|command|nohup)[ \t]+)*"
    r"(?:[^\s;&|()`'\"]*/)?hass-axi(?=\s|$|[;&|)])",
    re.M,
)

#: Where that invocation ends: the next shell operator or line break.
_END = re.compile(r"\|\||&&|[;|&\n)]")

#: A here-document's body, which is data the command reads rather than more
#: commands: a script or a document written through one names the tool on every
#: other line without running it once.
_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1[^\n]*\n.*?^[ \t]*\2[ \t]*$", re.S | re.M)

#: What a recorded label and date look like, checked again on the way back in.
_LABEL = re.compile(r"^[a-z_]+(?: [a-z_]+)?\Z")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")

#: The node types the two agents give a tool call in a transcript.
_TOOL_CALL_TYPES = {"tool_use", "function_call", "local_shell_call", "custom_tool_call"}

#: The keys a shell command is carried under inside one.
_COMMAND_KEYS = ("command", "cmd")

#: Shells whose ``-c`` argument is the command line itself.
_SHELLS = {"bash", "sh", "zsh", "dash", "fish"}


def state_path(environ=None) -> Path:
    """The state file, under ``$XDG_STATE_HOME`` when it is set."""
    environ = os.environ if environ is None else environ
    root = environ.get("XDG_STATE_HOME") or os.environ.get("XDG_STATE_HOME")
    base = Path(root) if root else Path.home() / ".local" / "state"
    return base / "hass-axi" / "sessions.json"


def invocations(command: str, nouns: dict) -> list:
    """Every invocation of this tool in one shell command line, as ``(label, applied)``.

    ``nouns`` maps each command name to its subcommand names, so a label is the
    command and, where it has them, the subcommand -- and never an argument.
    """
    found = []
    command = _HEREDOC.sub("", command)
    for match in _INVOCATION.finditer(command):
        rest = command[match.end() :]
        end = _END.search(rest)
        segment = rest[: end.start()] if end else rest
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        label = _label(words, nouns)
        if label:
            found.append((label, any(flag in words for flag in APPLYING_FLAGS)))
    return found


def _label(words: list, nouns: dict) -> str:
    """The command and subcommand, ``home`` for a bare run, or ``""`` if neither.

    A command this tool does not have is not counted rather than counted as
    something it was not, and nor is a version or help probe, which used
    nothing.
    """
    for index, word in enumerate(words):
        if word in nouns:
            following = words[index + 1] if index + 1 < len(words) else ""
            return f"{word} {following}" if following in nouns[word] else word
    if any(not word.startswith("-") for word in words) or set(words) & _PROBES:
        return ""
    return "home"


def _shell_commands(transcript: Path):
    """Each shell command the agent issued through a tool, in order."""
    with transcript.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            yield from _tool_commands(entry)


def _tool_commands(node):
    if isinstance(node, dict):
        kind = node.get("type")
        # A string check first: a transcript also carries JSON Schemas, where
        # `type` is itself an object and is not hashable.
        if isinstance(kind, str) and kind in _TOOL_CALL_TYPES:
            for holder in (
                node.get("input"),
                _arguments(node.get("arguments")),
                node.get("action"),
            ):
                command = _command_text(holder)
                if command:
                    yield command
        for value in node.values():
            yield from _tool_commands(value)
    elif isinstance(node, list):
        for value in node:
            yield from _tool_commands(value)


def _arguments(raw):
    """A function call's arguments, which one agent writes as a JSON string."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return None
    return raw


def _command_text(holder) -> str:
    """The shell command line inside one tool call's input, or ``""``.

    Written as a string by one agent and as an argv list by the other; a list
    that is a shell and its ``-c`` script is that script.
    """
    if not isinstance(holder, dict):
        return ""
    for key in _COMMAND_KEYS:
        command = holder.get(key)
        if isinstance(command, str):
            return command
        if isinstance(command, list) and all(isinstance(word, str) for word in command):
            if (
                len(command) >= 3
                and Path(command[0]).name in _SHELLS
                and command[1].startswith("-")
                and "c" in command[1]
            ):
                return command[-1]
            return " ".join(shlex.quote(word) for word in command)
    return ""


def summarise(commands, nouns: dict) -> dict:
    """Count each command label and the invocations that sent something."""
    counts: dict = {}
    applied = 0
    for command in commands:
        for label, did_apply in invocations(command, nouns):
            counts[label] = counts.get(label, 0) + 1
            applied += did_apply
    return {"commands": counts, "applied": applied}


def record(payload: dict, nouns: dict, environ=None, *, today: str | None = None) -> dict:
    """Record one ended session from a hook payload.

    The payload names a transcript (``transcript_path``) or carries the shell
    commands themselves (``commands``). Returns the document the hook prints:
    what was recorded, and when it is nothing, the reason.
    """
    given = payload.get("commands")
    transcript = payload.get("transcript_path")
    try:
        if isinstance(given, list):
            summary = summarise([c for c in given if isinstance(c, str)], nouns)
        elif isinstance(transcript, str) and transcript:
            summary = summarise(_shell_commands(Path(transcript)), nouns)
        else:
            return _nothing("the hook payload names no transcript and carries no commands")
    except OSError as exc:
        return _nothing(f"the transcript could not be read ({type(exc).__name__})")
    total = sum(summary["commands"].values())
    if not total:
        return _nothing("this session ran no hass-axi command")

    path = state_path(environ)
    entry = {
        "session": str(payload.get("session_id") or ""),
        "cwd": str(payload.get("cwd") or os.getcwd()),
        "ended": today or datetime.date.today().isoformat(),
        **summary,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Held across the read and the write: several sessions end at once on a
        # machine running several agents, and each one replacing the file with
        # "what I read, plus mine" kept one record in four.
        with _locked(path):
            sessions = [
                s
                for s in _load(path)
                if not entry["session"] or s.get("session") != entry["session"]
            ]
            sessions.append(entry)
            write_atomic(path, json.dumps(sessions[-KEEP:], indent=2) + "\n")
    except OSError as exc:
        return _nothing(f"the state file could not be written ({type(exc).__name__})")
    return {"recorded": f"{total} hass-axi command(s) from this session"}


def _lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


@contextlib.contextmanager
def _locked(path: Path):
    """Hold an exclusive lock beside ``path`` for the length of the block.

    A separate lock file, because the state file itself is replaced by rename
    and a lock on the old inode would guard nothing. Where the platform has no
    `fcntl` the block runs unlocked, which is what every release before this
    one did everywhere.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - not POSIX
        yield
        return
    with open(_lock_path(path), "a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _nothing(reason: str) -> dict:
    return {"recorded": "nothing", "reason": reason}


def _load(path: Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []


def last_session(cwd: str, environ=None) -> str:
    """The most recent recorded session in this directory, as one line, or ``""``.

    Directory-scoped, as AXI asks of ambient context: a session in another
    project used the tool for something else, and saying so here would be noise.
    Written without a colon, a comma or a bracket, because the document it goes
    into is TOON and would quote a scalar holding one.
    """
    for entry in reversed(_load(state_path(environ))):
        if entry.get("cwd") != cwd or not isinstance(entry.get("commands"), dict):
            continue
        # Read back strictly: this line goes into an agent's context, and the
        # file is one anything on the machine could have edited. A label is a
        # command name and nothing else, and a date is a date.
        ranked = sorted(
            (
                (label, count)
                for label, count in entry["commands"].items()
                if isinstance(label, str) and _LABEL.match(label) and _count(count)
            ),
            key=lambda item: (-_count(item[1]), item[0]),
        )
        ended = entry.get("ended")
        if not ranked or not isinstance(ended, str) or not _DATE.match(ended):
            continue
        used = " and ".join(f"{label} x{_count(count)}" for label, count in ranked[:SHOWN])
        if len(ranked) > SHOWN:
            used += f" and {len(ranked) - SHOWN} more"
        applied = _count(entry.get("applied"))
        tail = f"with {applied} sent by --write" if applied else "with nothing sent"
        return f"{ended} ran {used} {tail}"
    return ""


def _count(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def forget(environ=None) -> bool:
    """Delete the state file. Returns whether there was one to delete."""
    path = state_path(environ)
    with contextlib.suppress(OSError):
        _lock_path(path).unlink()
    if not path.is_file():
        return False
    path.unlink()
    return True

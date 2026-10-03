"""Command declarations, argument parsing and ``--help`` rendering.

Every command declares its own flags per subcommand. Anything undeclared is
rejected by name with the subcommand's valid flags printed inline, so an agent
that guessed wrong corrects itself in one turn rather than two.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import UsageError

#: Flags accepted on every command, and therefore never reported as unknown.
GLOBAL_FLAGS = (
    "--help",
    "-h",
    "--human",
    "--json",
    "--timeout",
    "--debug",
    "--version",
    "-v",
    "-V",
)

#: Flags an agent might plausibly reach for, mapped to what actually exists.
#: A targeted hint beats the generic list when the intent is unambiguous.
RENAMED: dict = {
    "--room": "--area",
    "--zone": "--area",
    "--entity": "--target-entity",
    "--entity-id": "--target-entity",
    "--friendly-name": "--name",
    "--rename": "--name",
    "--state-filter": "--state",
    "--filter": "--search",
    "--query": "--search",
    "--count": "--limit",
    "--max": "--limit",
    "--format": "--fields",
    "--output": "--fields",
}


@dataclass(frozen=True)
class Flag:
    """One declared flag on one subcommand."""

    name: str
    metavar: str = ""
    repeat: bool = False
    default: Any = None
    boolean: bool = False
    note: str = ""

    @property
    def takes_value(self) -> bool:
        return not self.boolean

    def render(self) -> str:
        parts = [self.name]
        if self.metavar:
            parts.append(self.metavar)
        text = " ".join(parts)
        extras = []
        if self.repeat:
            extras.append("repeatable")
        if self.default not in (None, False):
            extras.append(f"default {self.default}")
        if self.note:
            extras.append(self.note)
        return f"{text} ({', '.join(extras)})" if extras else text


@dataclass(frozen=True)
class Sub:
    """One subcommand: its positional arguments, its flag set and its access.

    ``access`` is the read-only classification, one of the values in
    :data:`hass_axi.readonly.CLASSIFICATIONS`. It defaults to ``None`` -- an
    absence rather than a value -- because that is what makes forgetting
    visible: :func:`hass_axi.readonly.verdict` reads ``None`` as a write and
    refuses it, and the completeness sweep in ``tests/test_read_only.py`` fails
    on it rather than letting an unclassified command through.
    """

    name: str
    args: tuple = ()
    flags: tuple = ()
    summary: str = ""
    access: str | None = None

    def signature(self) -> str:
        return " ".join([self.name, *self.args]) if self.args else self.name


@dataclass(frozen=True)
class Command:
    """A top-level command grouping subcommands under one noun."""

    name: str
    summary: str
    subs: tuple = ()
    examples: tuple = ()
    default_sub: str | None = None
    notes: tuple = ()
    usage: str = ""

    def find(self, name: str) -> Sub | None:
        for sub in self.subs:
            if sub.name == name:
                return sub
        return None


@dataclass
class Parsed:
    """The result of parsing one invocation."""

    positionals: list = field(default_factory=list)
    flags: dict = field(default_factory=dict)
    globals: dict = field(default_factory=dict)

    def get(self, name: str, default=None):
        return self.flags.get(_key(name), default)

    def has(self, name: str) -> bool:
        return _key(name) in self.flags


def _key(flag_name: str) -> str:
    return flag_name.lstrip("-").replace("-", "_")


def parse(sub: Sub, argv: list, *, command: Command) -> Parsed:
    """Parse ``argv`` against ``sub``'s declaration, rejecting anything undeclared."""
    declared = {flag.name: flag for flag in sub.flags}
    result = Parsed()
    for flag in sub.flags:
        if flag.repeat:
            result.flags[_key(flag.name)] = []
        elif flag.boolean:
            result.flags[_key(flag.name)] = False
        elif flag.default is not None:
            result.flags[_key(flag.name)] = flag.default

    index = 0
    while index < len(argv):
        token = argv[index]
        index += 1

        if token == "--":
            result.positionals.extend(argv[index:])
            break

        if not token.startswith("-") or token == "-":
            result.positionals.append(token)
            continue

        name, _, inline = token.partition("=")
        has_inline = bool(_)

        if name in GLOBAL_FLAGS:
            # Globals are accepted after the subcommand as well as before it.
            # They are recorded rather than rejected, and applied by the caller.
            if name == "--timeout":
                if has_inline:
                    result.globals["timeout"] = inline
                elif index < len(argv):
                    result.globals["timeout"] = argv[index]
                    index += 1
                else:
                    raise UsageError(
                        "--timeout needs a value",
                        help_lines=["Run `hass-axi --timeout 60 <command>`"],
                        code="BAD_TIMEOUT",
                    )
            else:
                result.globals[name.lstrip("-")] = True
            continue

        flag = declared.get(name)
        if flag is None:
            raise _unknown_flag(name, sub, command)

        if flag.boolean:
            if has_inline and inline.lower() in ("false", "0", "no"):
                result.flags[_key(name)] = False
            else:
                result.flags[_key(name)] = True
            continue

        if has_inline:
            value = inline
        else:
            if index >= len(argv):
                raise UsageError(
                    f"{name} needs a value",
                    help_lines=[
                        f"Run `{invocation(command, sub)} {name} {flag.metavar or '<value>'}`"
                    ],
                    code="MISSING_VALUE",
                )
            value = argv[index]
            index += 1

        if flag.repeat:
            result.flags[_key(name)].append(value)
        else:
            result.flags[_key(name)] = value

    _check_positionals(sub, command, result.positionals)
    return result


def invocation(command: Command, sub: Sub) -> str:
    """How to spell one subcommand back at the caller.

    Public because the read-only gate in :mod:`hass_axi.cli` names the command it
    refused, and a refusal that spelled it differently from every other error
    would read as a different command.
    """
    if sub.name == command.default_sub and len(command.subs) == 1:
        return f"hass-axi {command.name}"
    return f"hass-axi {command.name} {sub.name}"


def _check_positionals(sub: Sub, command: Command, values: list) -> None:
    required = [a for a in sub.args if a.startswith("<")]
    if len(values) < len(required):
        missing = required[len(values)]
        raise UsageError(
            f"{invocation(command, sub)} needs {missing}",
            help_lines=[f"Run `{invocation(command, sub)} {' '.join(sub.args)}`"],
            code="MISSING_ARGUMENT",
        )
    # A trailing `[name...]` takes any number of further values.
    variadic = bool(sub.args) and sub.args[-1].endswith("...]")
    if len(values) > len(sub.args) and not variadic:
        extra = values[len(sub.args)]
        raise UsageError(
            f"unexpected argument {extra!r} for `{command.name} {sub.name}`",
            help_lines=[f"Run `{invocation(command, sub)} {' '.join(sub.args)}`"],
            code="UNEXPECTED_ARGUMENT",
        )


def _unknown_flag(name: str, sub: Sub, command: Command):
    replacement = RENAMED.get(name)
    valid = [flag.name for flag in sub.flags]
    if replacement and replacement in valid:
        return UsageError(
            f"unknown flag {name} for `{command.name} {sub.name}`; use {replacement} instead",
            help_lines=[f"Run `{invocation(command, sub)} {replacement} <value>`"],
            code="UNKNOWN_FLAG",
        )
    listing = ", ".join(valid) if valid else "(none)"
    return UsageError(
        f"unknown flag {name} for `{command.name} {sub.name}`",
        help_lines=[
            f"valid flags for `{command.name} {sub.name}`: {listing} (--help always allowed)",
            f"Run `hass-axi {command.name} --help` for the full reference",
        ],
        code="UNKNOWN_FLAG",
    )


# --------------------------------------------------------------------- help


def render_command_help(command: Command) -> str:
    """Render one command's concise, complete reference."""
    lines = [command.usage or f"usage: hass-axi {command.name} <subcommand> [flags]"]
    lines.append(f"description: {command.summary}")

    if command.subs and not (len(command.subs) == 1 and command.subs[0].name == command.name):
        signatures = [sub.signature() for sub in command.subs]
        lines.append(f"subcommands[{len(signatures)}]:")
        lines.append("  " + ", ".join(signatures))

    for sub in command.subs:
        label = sub.name
        rendered = [flag.render() for flag in sub.flags]
        lines.append(f"flags{{{label}}}:")
        lines.append("  " + (", ".join(rendered) if rendered else "(none)"))

    for note in command.notes:
        lines.append("note:")
        lines.append(f"  {note}")

    if command.examples:
        lines.append("examples:")
        lines.extend(f"  {example}" for example in command.examples)
    return "\n".join(lines)

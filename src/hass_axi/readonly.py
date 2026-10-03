"""The read-only gate: one environment variable, enforced at every dispatch.

``HASS_AXI_READ_ONLY`` makes a session incapable of changing anything. It is an
environment variable and deliberately not a flag: a flag is omitted by exactly
the caller that most needs it, and an operator who wants a session that cannot
write wants that of every command the session runs, including the ones it is
handed by an agent rather than typed by a person.

Three rules make the guard whole rather than partial, and a partial one is
worse than none -- it converts an understood risk into a false assurance about
the paths it missed.

**The default is a write.** Every subcommand and every WebSocket command
carries an explicit classification; anything unclassified is treated as a write
and refused, so the failure mode of forgetting is a refusal rather than an
unguarded mutation. :func:`verdict` is the one place that rule is applied, and
``tests/test_read_only.py`` enumerates both command tables and fails on the
first declaration that has none.

**The classification is deliberate, never inferred from a name or an HTTP
verb.** ``service call`` mutates through a surface that looks like any other
POST, and the WebSocket command set does not follow REST conventions at all.
The one place a verb is read is the raw REST escape hatch, where the caller
supplies an opaque path and the method is the only fact there is -- and there
it errs closed: only the methods HTTP itself defines as safe get through.

**It holds on both transports.** REST and WebSocket are separate code routes
into one server, so both :mod:`hass_axi.rest` and :mod:`hass_axi.ws` refuse a write
of their own accord, ahead of the request, whether or not the dispatch gate in
:mod:`hass_axi.cli` ran first. Enforcing it there rather than in each command
body is what stops a new command bypassing the guard by forgetting to call a
helper.
"""

from __future__ import annotations

from .errors import EXIT_USAGE, AxiError
from .output import deprecated_variable

#: The switch. There is no flag, and no other spelling.
ENV_VAR = "HASS_AXI_READ_ONLY"

#: The switch's name before this tool was renamed from ``ha-axi``. Still
#: honoured, because an upgrade that silently turned the guard off is the one
#: outcome this variable is not allowed to have; announced on stderr each run
#: so the operator moves to :data:`ENV_VAR`. It is a second *name*, not a second
#: rule: set and not blank enables it, exactly as for the new one.
LEGACY_ENV_VAR = "HA_AXI_READ_ONLY"

#: Cannot change anything on the installation or on this machine.
READ = "read"
#: May change something. The verdict for anything unclassified.
WRITE = "write"
#: Decided per invocation by the owning module's ``access()`` -- the two escape
#: hatches, whose subject is an argument rather than a declaration, and
#: ``setup skill``, which only writes without ``--check``.
DYNAMIC = "dynamic"

#: What a declaration may say. Anything else, ``None`` included, is unclassified.
CLASSIFICATIONS = (READ, WRITE, DYNAMIC)


class ReadOnlyRefused(AxiError):
    """A write refused because this session is read-only.

    Exit 2, with the static invocation problems: the verdict is reached without
    touching the installation and no argument to the same command changes it,
    which is exactly what separates exit 2 from exit 1 here. The ``READ_ONLY``
    code is what an agent switches on -- distinct from ``UNAUTHORIZED`` and
    from every transport failure, because "this session forbids writes" and
    "that server refused you" have different fixes.
    """

    exit_code = EXIT_USAGE


def enabled(environ) -> bool:
    """Whether this session is read-only.

    A switch, not a boolean: any value that is set and not blank enables it,
    ``0`` and ``false`` included. Parsing the value is how a guard comes to be
    off while an operator believes it is on -- one unrecognised spelling, one
    case that was not folded -- and the whole point of this variable is that
    being wrong about it is not survivable. Blank is treated as unset, matching
    how every other variable is read in :mod:`hass_axi.config`.
    """
    return bool(active_var(environ))


def active_var(environ) -> str:
    """The variable that made this session read-only, or ``""`` when none did.

    :data:`ENV_VAR` wins when both are set. Naming the one that is actually set
    is what lets `doctor` and the home view tell an operator which variable to
    unset -- telling them to unset the new name while the old one holds the
    switch would leave them with a guard they cannot find.
    """
    for name in (ENV_VAR, LEGACY_ENV_VAR):
        value = (environ or {}).get(name)
        if value and value.strip():
            if name == LEGACY_ENV_VAR:
                deprecated_variable(LEGACY_ENV_VAR, ENV_VAR)
            return name
    return ""


def verdict(access) -> str:
    """Reduce a declared classification to ``READ`` or ``WRITE``.

    Only an explicit ``READ`` is a read. ``None``, ``DYNAMIC`` left unresolved,
    a typo, a value from a newer version of this module -- every one of them is
    a write.
    """
    return READ if access == READ else WRITE


def refusal(operation: str) -> ReadOnlyRefused:
    """The refusal, naming what was refused and why it was.

    ``operation`` is whatever the caller can name most precisely: the CLI
    invocation at the dispatch gate, the request or the API command type at a
    transport.
    """
    return ReadOnlyRefused(
        f"`{operation}` is a write, and {ENV_VAR} is set",
        help_lines=[
            "This session is read-only; the command was refused before anything changed",
            "Reads still work, e.g. `hass-axi state list`, `hass-axi entity list`, `hass-axi area list`",
            f"Unset {ENV_VAR} (or {LEGACY_ENV_VAR}, its deprecated spelling) to allow writes; "
            "it is a switch, so any non-empty value enables it",
        ],
        code="READ_ONLY",
    )


def guard(enabled_: bool, access, operation: str) -> None:
    """Refuse ``operation`` when this session is read-only and it is not a read."""
    if enabled_ and verdict(access) != READ:
        raise refusal(operation)

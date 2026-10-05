"""Entry point: global flags, dispatch, help, and the single error boundary."""

from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping
from importlib import import_module
from typing import TYPE_CHECKING

from . import __version__, errors, output, readonly
from .argspec import (
    GLOBAL_FLAGS,
    Command,
    Parsed,
    command_help_doc,
    invocation,
    parse,
    render_command_help,
)
from .errors import EXIT_ERROR, EXIT_OK, AxiError, UsageError
from .output import MODE_HUMAN, MODE_JSON, MODE_TOON, HelpBlock
from .toolkit.names import close_matches

if TYPE_CHECKING:
    from .rest import RestClient
    from .ws import WsClient

#: Dispatch order, which is also the order `--help` and the skill list them in.
COMMAND_ORDER = (
    "state",
    "sensor",
    "history",
    "logbook",
    "statistics",
    "service",
    "template",
    "entity",
    "area",
    "device",
    "ws",
    "api",
    "ping",
    "doctor",
    "setup",
    "context",
)


class _LazyModules(MutableMapping):
    """The dispatch table, importing each command module the first time it is read.

    One invocation runs one command, so importing all of them up front is paid
    on every run for nothing -- and by `--version` and the session hook most of
    all. An entry is declared as the module's name under `commands` and becomes
    the module itself on first access. Iterating imports nothing; reading every
    value, as root help and the test sweeps do, imports the lot.
    """

    def __init__(self, names: dict) -> None:
        self._entries: dict = dict(names)

    def __getitem__(self, name: str):
        entry = self._entries[name]
        if isinstance(entry, str):
            entry = self._entries[name] = import_module(f".commands.{entry}", __package__)
        return entry

    def __setitem__(self, name: str, module) -> None:
        self._entries[name] = module

    def __delitem__(self, name: str) -> None:
        del self._entries[name]

    def __iter__(self):
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


#: Command name to its module under `commands`.
_MODULES = _LazyModules(
    {
        "state": "state",
        "sensor": "sensor",
        "history": "history",
        "logbook": "logbook",
        "statistics": "statistics",
        "service": "service",
        "template": "template",
        "entity": "entity",
        "area": "area",
        "device": "device",
        "ws": "wscmd",
        "api": "api",
        "ping": "ping",
        "doctor": "doctor",
        "setup": "setup",
        "context": "context",
        "home": "home",
    }
)

#: Commands an agent might reach for under a different noun.
_ALIASES = {
    "states": "state",
    "entities": "entity",
    "areas": "area",
    "devices": "device",
    "services": "service",
    "templates": "template",
    "rooms": "area",
    "room": "area",
    "registry": "entity",
    "websocket": "ws",
    "rest": "api",
    "health": "doctor",
    "status": "doctor",
    "sensors": "sensor",
    "stats": "statistics",
    "statistic": "statistics",
    "energy": "statistics",
    "events": "logbook",
    "log": "logbook",
    "timeline": "history",
}


def command_specs() -> dict:
    return {name: module.COMMAND for name, module in _MODULES.items()}


class Context:
    """Per-invocation state: configuration, transports and output mode."""

    def __init__(self, environ, *, mode: str = MODE_TOON, timeout: float | None = None) -> None:
        self.environ = environ
        self.mode = mode
        self.timeout = timeout
        self._config = None
        self._rest: RestClient | None = None

    # The transports and the configuration loader are imported where they are
    # first needed, so a run that reaches neither -- help, `context`, a usage
    # error -- does not pay for them.

    def config(self):
        from . import config as config_module

        if self._config is None:
            self._config = config_module.load(self.environ, timeout=self.timeout)
            # Registered before any transport runs, so a token can never appear
            # in an error message or a debug line.
            config_module.register_token(self._config.token)
            # Chosen once, here, so both transports talk to the same candidate
            # for the whole run rather than each settling on its own.
            self._config = config_module.select_reachable(self._config)
        return self._config

    def rest(self) -> RestClient:
        from .rest import RestClient

        if self._rest is None:
            self._rest = RestClient(self.config())
        return self._rest

    def ws(self) -> WsClient:
        from .ws import WsClient

        return WsClient(self.config())


# ----------------------------------------------------------------- help text


def render_root_help() -> str:
    specs = command_specs()
    names = ", ".join(COMMAND_ORDER)
    lines = [
        "usage: hass-axi [command] [subcommand] [args] [flags]",
        f"description: {_MODULES['home'].DESCRIPTION}",
        f"commands[{len(COMMAND_ORDER) + 1}]:",
        f"  (none)=home, {names}",
        "flags[6]:",
        "  --human (readable output), --json (raw JSON output), --timeout <seconds> (default 30),",
        "  --debug (diagnostics on stderr), --help, -v/-V/--version",
        "env[3]:",
        "  HA_URL (or HASS_SERVER) - Home Assistant base URL, e.g. https://homeassistant.example.com;",
        "    several, comma-separated, are tried in order and the first that answers is used",
        "  HA_TOKEN (or HASS_TOKEN) - long-lived access token; there is deliberately no --token flag",
        f"  {readonly.ENV_VAR} - set to any non-empty value to refuse every write, on both transports",
        f"summaries[{len(COMMAND_ORDER)}]:",
    ]
    width = max(len(name) for name in COMMAND_ORDER)
    lines.extend(f"  {name.ljust(width)}  {specs[name].summary}" for name in COMMAND_ORDER)
    lines.extend(
        [
            "examples:",
            "  hass-axi",
            "  hass-axi state list --domain light",
            "  hass-axi sensor list --device-class power --area 'Example Room'",
            "  hass-axi statistics get sensor.example_legacy_meter --start 7d",
            "  hass-axi entity list --area 'Example Room'",
            "  hass-axi entity update light.example_lamp --name 'Reading Lamp' --area example_room",
            "  hass-axi service call light.turn_on --target-entity light.example_lamp",
            "  hass-axi doctor",
        ]
    )
    return "\n".join(lines)


def root_help_doc() -> dict:
    """The root reference as data, for ``--json``; see `argspec.command_help_doc`."""
    specs = command_specs()
    return {
        "usage": "hass-axi [command] [subcommand] [args] [flags]",
        "description": _MODULES["home"].DESCRIPTION,
        "commands": ["(none)=home", *COMMAND_ORDER],
        "flags": [
            "--human (readable output)",
            "--json (raw JSON output)",
            "--timeout <seconds> (default 30)",
            "--debug (diagnostics on stderr)",
            "--help",
            "-v/-V/--version",
        ],
        "env": {
            "HA_URL": "Home Assistant base URL (or HASS_SERVER); several, comma-separated, "
            "are tried in order and the first that answers is used",
            "HA_TOKEN": "long-lived access token (or HASS_TOKEN); there is deliberately "
            "no --token flag",
            readonly.ENV_VAR: "set to any non-empty value to refuse every write, "
            "on both transports",
        },
        "summaries": {name: specs[name].summary for name in COMMAND_ORDER},
    }


def _write_help(text: str, doc: dict, mode: str) -> None:
    """Print help as text, or as a document when the caller asked for JSON."""
    if mode == MODE_JSON:
        output.write(doc, mode)
    else:
        output.write_text(text)


# ------------------------------------------------------------------ dispatch


#: Global flags that take no value, derived from the single declaration in
#: argspec so the two cannot drift apart.
_VALUELESS_GLOBALS = tuple(flag for flag in GLOBAL_FLAGS if flag != "--timeout")


def _help_requested(command: Command, argv: list) -> bool:
    """Whether ``--help`` appears as a flag rather than as a flag's value.

    `template render --template --help` must render the literal string, not
    print help. Scanning raw argv cannot tell the two apart, so this walks the
    tokens and skips the value of any flag the command declares as taking one.
    """
    value_flags = {flag.name for sub in command.subs for flag in sub.flags if flag.takes_value}
    index = 0
    while index < len(argv):
        token = argv[index]
        index += 1
        name, has_inline, _ = token.partition("=")
        if name in ("--help", "-h") and not has_inline:
            return True
        if name in value_flags and not has_inline:
            index += 1  # skip the value, whatever it looks like
        elif name == "--timeout" and not has_inline:
            index += 1
    return False


def _is_global_flag_token(token: str) -> bool:
    """Whether ``token`` is a global flag."""
    name = token.partition("=")[0]
    return name in _VALUELESS_GLOBALS or name == "--timeout"


def _prescan_mode(argv: list) -> str:
    """Decide the output mode from the whole invocation, before parsing.

    An agent that appends `--json` and pipes the result to a parser needs the
    machine-readable form most when the invocation is wrong, so the mode has to
    be known before any usage error can be raised -- including for a flag that
    appears after the subcommand.
    """
    seen: dict = {}
    for token in argv:
        if token == "--":
            # Everything after it is a positional, a mode flag included.
            break
        name = token.partition("=")[0]
        if name in ("--json", "--human"):
            seen[name.lstrip("-")] = True
    return _mode(seen)


def _split_globals(argv: list) -> tuple:
    """Pull global flags off the front of the invocation, before the command."""
    globals_: dict = {}
    index = 0
    while index < len(argv):
        token = argv[index]
        if not token.startswith("-") or token == "-":
            break
        name, sep, inline = token.partition("=")
        if name == "--timeout":
            index += 1
            if sep:
                globals_["timeout"] = inline
            elif index < len(argv) and not _is_global_flag_token(argv[index]):
                globals_["timeout"] = argv[index]
                index += 1
            elif index < len(argv):
                globals_["timeout"] = _MISSING_VALUE
            else:
                globals_["timeout"] = _MISSING_VALUE
            continue
        if name in _VALUELESS_GLOBALS:
            globals_[name.lstrip("-")] = True
            index += 1
            continue
        break
    return globals_, argv[index:]


#: Sentinel for `--timeout` given without a value, so it errors rather than
#: being silently swallowed the way an unvalidated global would be.
_MISSING_VALUE = object()


def _resolve_timeout(raw) -> float | None:
    if raw is None:
        return None
    if raw is _MISSING_VALUE:
        raise UsageError(
            "--timeout needs a value",
            help_lines=["Run `hass-axi --timeout 60 <command>`"],
            code="BAD_TIMEOUT",
        )
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise UsageError(
            f"--timeout needs a number of seconds, got {raw!r}",
            help_lines=["Run `hass-axi --timeout 60 <command>`"],
            code="BAD_TIMEOUT",
        ) from None
    if value <= 0:
        raise UsageError(
            f"--timeout must be greater than 0, got {value:g}",
            help_lines=["Run `hass-axi --timeout 60 <command>`"],
            code="BAD_TIMEOUT",
        )
    return value


def _mode(globals_: dict) -> str:
    if globals_.get("json"):
        return MODE_JSON
    if globals_.get("human"):
        return MODE_HUMAN
    return MODE_TOON


def _wants_version(globals_: dict) -> bool:
    return bool(globals_.get("version") or globals_.get("v") or globals_.get("V"))


def _write_version() -> None:
    """The bare version, whatever the output mode.

    A caller probing the version compares or parses the line itself, so it is
    the same string under `--json` and `--human` and matches what the fast path
    in `hass_axi.entry` prints for the bare flag.
    """
    output.write_text(__version__)


def _unknown_command(name: str):
    suggestion = _ALIASES.get(name.lower())
    if suggestion:
        return UsageError(
            f"unknown command: {name}; use `{suggestion}` instead",
            help_lines=[f"Run `hass-axi {suggestion} --help` for its subcommands"],
            code="UNKNOWN_COMMAND",
        )
    return UsageError(
        f"unknown command: {name}",
        help_lines=[
            f"commands: {', '.join(COMMAND_ORDER)}",
            "Run `hass-axi --help` for the full reference",
        ],
        code="UNKNOWN_COMMAND",
    )


def _pick_sub(command: Command, argv: list) -> tuple:
    leading = argv[0] if argv and not argv[0].startswith("-") else None
    if leading is not None:
        sub = command.find(leading)
        if sub is not None:
            return sub, argv[1:]

    default = command.find(command.default_sub) if command.default_sub else None
    # A default subcommand is what makes `hass-axi device` mean `hass-axi device
    # list`. It must not also swallow a *mistyped* subcommand name: on a command
    # that has others, a bare leading token the default sub declares no
    # positional to hold can only be one, and `unexpected argument 'updat' for
    # \`device list\`` names a subcommand nobody typed and sends the reader
    # looking for an argument mistake instead of a spelling one. A default sub
    # that does take positionals -- `ws <command>`, `api <path>` -- is given the
    # token, because there it is the subject rather than a name.
    if default is not None:
        swallows_a_typo = leading is not None and len(command.subs) > 1 and not default.args
        if not swallows_a_typo:
            return default, argv

    if leading is not None:
        close = close_matches(leading, ((s.name, s.name) for s in command.subs))
        help_lines = []
        if close:
            help_lines.append(f"did you mean: {', '.join(close)}")
        help_lines.append(f"subcommands: {', '.join(s.name for s in command.subs)}")
        help_lines.append(f"Run `hass-axi {command.name} --help` for the full reference")
        raise UsageError(
            f"unknown subcommand `{leading}` for `{command.name}`",
            help_lines=help_lines,
            code="UNKNOWN_SUBCOMMAND",
        )
    raise UsageError(
        f"`{command.name}` needs a subcommand",
        help_lines=[
            f"subcommands: {', '.join(s.name for s in command.subs)}",
            f"Run `hass-axi {command.name} --help` for the full reference",
        ],
        code="MISSING_SUBCOMMAND",
    )


def _access(module, sub, parsed) -> str:
    """The read-only verdict for one resolved invocation.

    The first of the three enforcement points, and the specific one: it names
    the command, and it runs before any transport is built, so a refused write
    reaches neither the network nor the credential loader. The transports guard
    themselves as well -- see :func:`hass_axi.rest.access_for_request` and
    :func:`hass_axi.ws.access_for_type` -- because this gate can only judge what
    the declaration says, and the two escape hatches carry their subject in
    their arguments.

    Everything unclassified is a write. ``DYNAMIC`` delegates to the owning
    module's ``access()``; a module that declares ``DYNAMIC`` and supplies none
    is unclassified in a costume, and is treated as one.
    """
    if sub.access != readonly.DYNAMIC:
        return readonly.verdict(sub.access)
    resolver = getattr(module, "access", None)
    if not callable(resolver):
        return readonly.WRITE
    return readonly.verdict(resolver(sub.name, parsed))


def _error_document(exc: AxiError) -> dict:
    """The one shape every failure is printed in.

    ``class`` sits beside ``code`` rather than replacing it, and it is derived
    from the code through :data:`hass_axi.errors.CODES` rather than declared a
    second time at each raise site -- one vocabulary, read two ways, so the two
    cannot drift. The code says which thing went wrong and the class says what
    kind of thing it is, which is what an agent needs before it can decide
    whether to retry, re-read the arguments, or fetch a different token.
    """
    doc: dict = {"error": exc.message}
    if exc.code:
        doc["code"] = exc.code
        doc["class"] = exc.fault_class
    if exc.help_lines:
        doc["help"] = HelpBlock(exc.help_lines)
    return doc


def main(argv: list | None = None, *, environ=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    environ = os.environ if environ is None else environ

    globals_, rest = _split_globals(argv)
    # Pre-scan so a usage error is reported in the mode the caller asked for,
    # then refine once the leading globals are known.
    mode = _prescan_mode(argv)
    if "--debug" in argv:
        output.set_debug(True)

    try:
        if _wants_version(globals_):
            _write_version()
            return EXIT_OK

        if not rest:
            if globals_.get("help") or globals_.get("h"):
                _write_help(render_root_help(), root_help_doc(), mode)
                return EXIT_OK
            module = _MODULES["home"]
            command = module.COMMAND
            sub, sub_name, sub_argv = command.subs[0], "home", []
        else:
            name = rest[0]
            if name.startswith("-") and name != "-":
                # `_split_globals` stopped here, so this is a flag in the
                # globals' position that is not one of them -- not a command.
                raise UsageError(
                    f"unknown global flag {name.partition('=')[0]}",
                    help_lines=[
                        "global flags: --human, --json, --timeout <seconds>, --debug, --help, "
                        "--version",
                        "A command's own flags go after the command, "
                        "e.g. `hass-axi state list --domain light`",
                    ],
                    code="UNKNOWN_FLAG",
                )
            module = _MODULES.get(name)
            if module is None or name == "home":
                raise _unknown_command(name)
            command = module.COMMAND
            if globals_.get("help") or globals_.get("h") or _help_requested(command, rest[1:]):
                _write_help(render_command_help(command), command_help_doc(command), mode)
                return EXIT_OK
            sub, sub_argv = _pick_sub(command, rest[1:])
            sub_name = sub.name

        if not rest:
            parsed = Parsed()
        else:
            parsed = parse(sub, sub_argv, command=command)
            globals_.update(parsed.globals)
            mode = _mode(globals_)
            if _wants_version(globals_):
                _write_version()
                return EXIT_OK

        if globals_.get("debug"):
            output.set_debug(True)

        readonly.guard(
            readonly.enabled(environ), _access(module, sub, parsed), invocation(command, sub)
        )

        ctx = Context(environ, mode=mode, timeout=_resolve_timeout(globals_.get("timeout")))
        doc = module.run(ctx, sub_name, parsed)
    except AxiError as exc:
        output.write(_error_document(exc), mode)
        return exc.exit_code
    except KeyboardInterrupt:  # pragma: no cover - interactive interruption
        output.write({"error": "interrupted"}, mode)
        return EXIT_ERROR
    except Exception as exc:
        # Without this, an unexpected exception prints a raw traceback on
        # stderr, bypassing redaction entirely and leaving stdout empty. Both
        # halves matter: the documented contract is that errors arrive on
        # stdout in the same structured shape, and that a credential can never
        # escape. Anything reaching here is a bug, so name it as one.
        output.write(
            {
                "error": f"internal error: {type(exc).__name__}: {exc}",
                "code": "INTERNAL_ERROR",
                "class": errors.fault_class("INTERNAL_ERROR"),
                "help": HelpBlock(
                    [
                        "This is a bug in hass-axi; the command did not complete",
                        "Re-run with `--debug` for a diagnostic trace on stderr",
                        "Report it at https://github.com/dmealing/hass-axi/issues",
                    ]
                ),
            },
            mode,
        )
        output.debug_exception(exc)
        return EXIT_ERROR

    exit_code = EXIT_OK
    if isinstance(doc, dict) and "__exit_code__" in doc:
        doc = dict(doc)
        exit_code = doc.pop("__exit_code__")
    output.write(doc, mode)
    return exit_code

"""`hass-axi context` -- the ambient document a SessionStart hook puts in front of an agent.

This is what `hass-axi setup hooks` installs, and everything about it follows from
*when* it runs: at the start of every session, on every machine that has the
package, before anybody has decided to use the tool.

**It exists because the no-argument home view cannot be what a hook prints, and
that was a defect rather than a preference.** The home view is live state: it
needs `HA_URL` and `HA_TOKEN`, opens a connection, and prints the installation's
base URL. Three consequences, any one of which is enough:

- **It has nothing to show the reader the hook exists to help.** With no
  configuration the home view can only report `NOT_CONFIGURED` and the setup
  lines, and it once exited 1 doing so -- which a harness is entitled to treat
  as a failed hook and drop. The one reader who most needs to be told this tool
  exists is the one on a machine that has never been pointed at an installation.
- **It touches the network.** A session start pays a round-trip and reads a
  credential for a tool the session may never use.
- **It prints an address.** Hook output lands in an agent's context and is
  routinely logged and transcribed, which is a wider surface than a terminal
  rather than a narrower one.

So the hook runs this instead. It reads the environment, the command table and
the local session record, and nothing else: no connection, no token, no address,
and exit 0 whether or not this machine has ever been pointed at a Home Assistant
installation. See :mod:`hass_axi.hooks` and :mod:`hass_axi.commands.home`.

`context end` is the other half of the lifecycle: the session-end hook, which
records which commands the session ran so the next `context` in the same
directory can say so. See :mod:`hass_axi.sessionlog`.
"""

from __future__ import annotations

import json
import os
import sys

from .. import sessionlog
from ..argspec import Command, Sub
from ..config import describe_environment, missing_env_vars, setup_help
from ..output import HelpBlock
from ..readonly import READ, active_var, enabled
from .home import DESCRIPTION, executable_path

COMMAND = Command(
    name="context",
    summary="Print the ambient context a session hook puts in front of an agent",
    usage="usage: hass-axi context [end]",
    default_sub="context",
    subs=(
        Sub(
            name="context",
            access=READ,
            summary="Describe this installation without connecting to it",
        ),
        Sub(
            name="end",
            # It writes one file, and it is declared a read: nothing of the
            # user's or the installation's is touched, and a refusal here would
            # be reported as every read-only session failing to close. The
            # switch is honoured inside instead -- a read-only session records
            # nothing and says so.
            access=READ,
            summary="Record what this session ran, from a session-end hook's payload on stdin",
        ),
    ),
    notes=(
        "this is the document `hass-axi setup hooks` installs a SessionStart hook to print",
        "it reads the environment, the command table and the local session record only: no "
        "connection, no token, no installation address, and it exits 0 whether or not this "
        "machine has Home Assistant",
        "for live state -- how many entities there are and what is unavailable -- run `hass-axi` "
        "with no arguments instead",
        "`context end` is the session-end hook: it reads the hook's JSON payload on stdin, "
        "counts the hass-axi commands the session ran -- command names only, never arguments "
        "-- and `context` then reports the last session in the same directory",
    ),
    examples=("hass-axi context",),
)

#: What this tool is *for*, in one line each. Written without a colon, a comma
#: or a bracket in any of them: this document is TOON, a scalar holding one of
#: those is quoted, and a pair of quotes on three lines is paid at the start of
#: every agent session for nothing. The same rule `home.DESCRIPTION` is held to.
#: The README argues at length that
#: the registries and service-call judgement are the two things worth picking
#: this tool for; these are the same two claims compressed to what an agent can
#: act on, plus the identity trap that makes a correctly-spelled query answer
#: nothing.
REGISTRY_RULE = (
    "names and areas live in the registry which only the WebSocket API serves -- `entity list` "
    "and `area list` read it; `state list` reads REST and cannot see either"
)

IDENTITY_RULE = (
    "an entity_id is not stable identity and its words mean nothing -- reach an entity with "
    "`entity list --search '<the name a user sees>'` or `--area <id|name>` rather than guess one"
)

READINGS_RULE = (
    "find a reading by what it measures with `sensor list --device-class <class>` and get its "
    "total or average over a window with `statistics get <entity_id>`"
)

SERVICE_RULE = (
    "prefer `service call` over `api POST /services/...` -- it explains a refusal Home Assistant "
    "returns with no body at all and tells reaching nothing apart from changing nothing"
)


def run(ctx, sub: str, parsed):
    from ..cli import COMMAND_ORDER

    if sub == "end":
        return _end(ctx)
    environ = ctx.environ
    missing = missing_env_vars(environ)

    doc = {
        "bin": executable_path(),
        "description": DESCRIPTION,
        "config": config_line(environ, missing),
    }
    # Announced only when it is on, which is the home view's rule and is kept
    # here for the reason that view gives: this loads at the start of every
    # session and an unset switch is not worth the tokens, while an agent that
    # cannot see a set one plans writes it will never be allowed to make.
    if enabled(environ):
        doc["read_only"] = "on"
    doc["registries"] = REGISTRY_RULE
    doc["entity_ids"] = IDENTITY_RULE
    doc["services"] = SERVICE_RULE
    doc["readings"] = READINGS_RULE
    doc["commands"] = list(COMMAND_ORDER)
    # What the last session in this directory did with the tool, captured by
    # the session-end hook. Absent rather than "none" when there is nothing: a
    # line on every session saying nothing happened is pure cost.
    last = sessionlog.last_session(os.getcwd(), environ)
    if last:
        doc["last_session"] = last
    doc["help"] = HelpBlock(_help(environ, missing))
    return doc


def _end(ctx):
    """The session-end hook: record this session, and exit 0 whatever happens.

    A hook that failed would be reported to the user as their session failing to
    close, so every problem -- no payload, an unreadable transcript, an
    unwritable state file -- is recorded as nothing and said, never raised.
    """
    from ..cli import command_specs

    if enabled(ctx.environ):
        return {
            "recorded": "nothing",
            "reason": f"this session is read-only because {active_var(ctx.environ)} is set",
        }
    payload = {}
    if not sys.stdin.isatty():
        try:
            loaded = json.loads(sys.stdin.read() or "{}")
        except (ValueError, OSError):
            loaded = {}
        payload = loaded if isinstance(loaded, dict) else {}
    nouns = {name: [sub.name for sub in spec.subs] for name, spec in command_specs().items()}
    try:
        return sessionlog.record(payload, nouns, ctx.environ)
    except Exception as exc:
        return {"recorded": "nothing", "reason": f"the record failed ({type(exc).__name__})"}


def config_line(environ, missing: list) -> str:
    """Which variables are set -- never what they hold.

    Named from :func:`hass_axi.config.describe_environment` rather than from the
    primary spellings, so an installation configured through one of the accepted
    aliases is told the name of the variable it actually set.

    Reported as an ordinary fact rather than as an error even when both are
    absent, because a hook that opened a session with a failure would be
    reporting the machine's ordinary state as a fault.
    """
    if missing:
        return f"{' and '.join(missing)} not set so no command here can reach an installation yet"
    described = describe_environment(environ)
    return f"{described['url_var']} and {described['token_var']} are set"


def _help(environ, missing: list) -> list:
    if missing:
        # Leading with the home view would be advice to run something that
        # cannot work yet. What this reader needs is the two exports.
        return [*setup_help(), "Run `hass-axi --help` for the whole command reference"]
    lines = [
        "Run `hass-axi` for this installation at a glance: entity counts by domain and what needs "
        "attention",
        "Run `hass-axi entity list --area <id|name>` to read the registry, which REST cannot reach",
    ]
    if enabled(environ):
        lines.append(f"This session is read-only; unset {active_var(environ)} to allow writes")
    else:
        lines.append(
            "Run `hass-axi service call <domain>.<service> --target-entity <entity_id>` to preview "
            "an action and add --write to send it"
        )
    lines.append(
        "Run `hass-axi <command> --help` for its flags, or `hass-axi --help` for all of them"
    )
    return lines

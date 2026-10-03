"""`hass-axi context` -- the ambient document a SessionStart hook puts in front of an agent.

This is what `hass-axi setup hooks` installs, and everything about it follows from
*when* it runs: at the start of every session, on every machine that has the
package, before anybody has decided to use the tool.

**It exists because the no-argument home view cannot be what a hook prints, and
that was a defect rather than a preference.** The home view is live state: it
needs `HA_URL` and `HA_TOKEN`, opens a connection, and prints the installation's
base URL. Three consequences, any one of which is enough:

- **It fails for exactly the reader the hook exists to help.** With no
  configuration the home view reports `NOT_CONFIGURED` and exits 1 -- correctly,
  by the error taxonomy, which gives every `config` fault that code. As a hook
  that means every session on a machine that has the package and no installation
  opens with a failure, and a harness is entitled to drop a non-zero hook's
  output rather than put it in front of the agent. The one reader who most needs
  to be told this tool exists is the one who would never see it.
- **It touches the network.** A session start pays a round-trip and reads a
  credential for a tool the session may never use.
- **It prints an address.** Hook output lands in an agent's context and is
  routinely logged and transcribed, which is a wider surface than a terminal
  rather than a narrower one.

So the hook runs this instead. It reads the environment and the command table
and nothing else: no connection, no token, no address, and exit 0 whether or not
this machine has ever been pointed at a Home Assistant installation. The
taxonomy is untouched -- `hass-axi` with no configuration still reports
`NOT_CONFIGURED` and still exits 1, because a caller who asked for live state and
cannot have it *has* met a fault. This command asks a different question, so it
gets a different answer rather than a softened one. See
:mod:`hass_axi.hooks` and :mod:`hass_axi.commands.home`.
"""

from __future__ import annotations

from ..argspec import Command, Sub
from ..config import describe_environment, missing_env_vars, setup_help
from ..output import HelpBlock
from ..readonly import READ, active_var, enabled
from .home import DESCRIPTION, executable_path

COMMAND = Command(
    name="context",
    summary="Print the ambient context a session hook puts in front of an agent",
    usage="usage: hass-axi context",
    default_sub="context",
    subs=(
        Sub(
            name="context",
            access=READ,
            summary="Describe this installation without connecting to it",
        ),
    ),
    notes=(
        "this is the document `hass-axi setup hooks` installs a SessionStart hook to print",
        "it reads the environment and the command table only: no connection, no token, no "
        "installation address, and it exits 0 whether or not this machine has Home Assistant",
        "for live state -- how many entities there are and what is unavailable -- run `hass-axi` "
        "with no arguments instead",
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

    environ = ctx.environ
    missing = missing_env_vars(environ)

    doc = {
        "bin": executable_path(),
        "description": DESCRIPTION,
        "config": _config(environ, missing),
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
    doc["help"] = HelpBlock(_help(environ, missing))
    return doc


def _config(environ, missing: list) -> str:
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
            "Run `hass-axi service call <domain>.<service> --target-entity <entity_id>` to act"
        )
    lines.append(
        "Run `hass-axi <command> --help` for its flags, or `hass-axi --help` for all of them"
    )
    return lines

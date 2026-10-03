"""Generate the installable Agent Skill from the CLI's own declarations.

The skill is the lower-overhead discovery path: it loads on demand instead of
on every session, and works in agents without hook support. Generating it from
the same command table the CLI dispatches on keeps the two from drifting, and
`hass-axi setup skill --check` fails when the committed copy is stale.
"""

from __future__ import annotations

from pathlib import Path

from .commands.home import DESCRIPTION
from .readonly import ENV_VAR as READ_ONLY_VAR

SKILL_NAME = "hass-axi"
SKILL_RELATIVE_PATH = Path("skills") / SKILL_NAME / "SKILL.md"

FRONTMATTER_DESCRIPTION = (
    "Operate a Home Assistant installation through the hass-axi CLI - read and update the "
    "entity, area and device registries that only the WebSocket API exposes, and call "
    "services with a capability pre-check and an explained refusal. It also reads entity "
    "states and renders templates. Use whenever a task touches home automation: renaming "
    "an entity or the device behind it, moving things between areas, checking what a "
    "device is doing, or turning something on."
)


def _fence(lines) -> str:
    return "\n".join(["```sh", *lines, "```"])


def render(commands) -> str:
    """Render SKILL.md from the live command table.

    ``commands`` is the ordered mapping the CLI dispatches on. Live state is
    deliberately excluded: a skill is static, so anything installation-specific
    would be wrong the moment it was written.
    """
    sections = [
        "---",
        f"name: {SKILL_NAME}",
        f"description: {FRONTMATTER_DESCRIPTION}",
        "---",
        "",
        f"# {SKILL_NAME}",
        "",
        DESCRIPTION,
        "",
        "## Configuration",
        "",
        "Both values come from the environment. There is no `--token` flag and no credential",
        "file: a token on a command line leaks into shell history and the process table.",
        "",
        _fence(
            [
                "export HA_URL=https://homeassistant.example.com   # or HASS_SERVER",
                "export HA_TOKEN=<long-lived access token>          # or HASS_TOKEN",
            ]
        ),
        "",
        "Create the token on the Home Assistant profile page, under Security.",
        "Run `hass-axi doctor` to confirm both transports work; it exits non-zero when they do not.",
        "",
        "## Read-only sessions",
        "",
        f"A third variable makes the session incapable of changing anything. `{READ_ONLY_VAR}` is a",
        "switch rather than a boolean: **any** non-empty value enables it, `0` and `false` included,",
        "and unsetting it is how writes are allowed again.",
        "",
        _fence([f"export {READ_ONLY_VAR}=1"]),
        "",
        "Every write is then refused before it is sent, over REST and over the WebSocket alike,",
        "with `code: READ_ONLY` and exit 2. The commands stay visible in `--help` and in the",
        "command table, so a plan that needs one can be recognised as impossible rather than",
        "mysterious. `hass-axi doctor` reports the mode, and the no-argument view shows",
        "`read_only: on` when it is set.",
        "",
        "## Running without a global install",
        "",
        _fence(["uvx hass-axi state list --domain light", "pipx run hass-axi area list"]),
        "",
        "## Output",
        "",
        "Commands print TOON on stdout and exit non-zero on failure. Add `--human` for a",
        "readable table, or `--json` for raw JSON. Errors are structured on stdout too, and",
        "carry the command that fixes them.",
        "",
        "## Commands",
        "",
    ]

    for command in commands:
        if command.name == "home":
            continue
        sections.append(f"### `hass-axi {command.name}`")
        sections.append("")
        sections.append(command.summary + ".")
        sections.append("")
        if command.examples:
            sections.append(_fence(list(command.examples)))
            sections.append("")
        for note in command.notes:
            sections.append(f"- {note}")
        if command.notes:
            sections.append("")

    sections.extend(
        [
            "## Rules of thumb",
            "",
            "- `entity_id` is not stable identity. Find entities by area or by search, and read",
            "  the registry (`hass-axi entity list`) rather than assuming an id means what it says.",
            "- States come from REST; names, areas and platforms come from the WebSocket registry.",
            "  `hass-axi state` and `hass-axi entity` are different views of the same installation.",
            "- An entity with no area of its own inherits its device's area.",
            "- Every command supports `--help`, which is the authoritative reference for its flags.",
            "",
        ]
    )
    return "\n".join(sections).rstrip() + "\n"


def target_path(root: Path) -> Path:
    return Path(root) / SKILL_RELATIVE_PATH

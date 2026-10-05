"""Golden snapshots of the text an agent reads: help, errors, the context document.

Text only, and only text this tool writes from nothing but its own declarations
and the arguments it was given -- never live data, and never an answer from an
installation. A snapshot of data would be a recording of somebody's house; a
snapshot of `--help` is the interface, and a change to it should be a change
somebody chose.

Each case is one file under `tests/snapshots/`. To accept a deliberate change:

    HASS_AXI_UPDATE_SNAPSHOTS=1 .venv/bin/pytest tests/test_snapshots.py

and read the diff before committing it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hass_axi import cli

SNAPSHOTS = Path(__file__).parent / "snapshots"
UPDATE = bool(os.environ.get("HASS_AXI_UPDATE_SNAPSHOTS"))

HELP = {"help-root": ["--help"]}
HELP.update({f"help-{name}": [name, "--help"] for name in cli.COMMAND_ORDER})

#: One of each kind of error the parser and the commands write without asking
#: the installation anything, so the text does not depend on one.
ERRORS = {
    "error-unknown-command": ["nosuchcommand"],
    "error-command-alias": ["rooms"],
    "error-unknown-global-flag": ["--nosuchglobal", "state", "list"],
    "error-missing-subcommand": ["state"],
    "error-unknown-subcommand": ["device", "updat", "x"],
    "error-unknown-flag": ["state", "list", "--nosuch"],
    "error-renamed-flag": ["state", "list", "--room", "x"],
    "error-format-json": ["sensor", "list", "--format", "json"],
    "error-missing-argument": ["state", "get"],
    "error-unexpected-argument": ["ping", "extra"],
    "error-positional-for-a-flag": ["area", "create", "Example Annex"],
    "error-missing-value": ["state", "list", "--domain"],
    "error-flag-as-value": ["state", "list", "--domain", "--limit", "3"],
    "error-bad-limit": ["state", "list", "--limit", "abc"],
    "error-unknown-field": ["state", "list", "--fields", "nope"],
    "error-bad-timeout": ["--timeout", "abc", "state", "list"],
    "error-bad-time": ["history", "get", "light.example_lamp", "--start", "soon"],
    "error-bad-period": ["statistics", "get", "sensor.example_meter", "--period", "fortnight"],
    "error-bad-service": ["service", "call", "turn_on"],
    "error-bad-json": ["service", "call", "light.turn_on", "--data-json", "{"],
    "error-missing-template": ["template", "render"],
    "error-no-changes": ["entity", "update", "light.example_lamp"],
    "error-conflicting-flags": [
        "entity",
        "update",
        "light.example_lamp",
        "--name",
        "a",
        "--clear-name",
    ],
    "error-bad-icon": ["area", "update", "example_room", "--icon", "sofa"],
    "error-missing-name": ["area", "create"],
    "error-bad-method": ["api", "BOGUS", "/config"],
    "error-not-an-entity-id": ["state", "get", "Example Reading Lamp"],
    "error-not-configured": ["state", "list"],
    "error-read-only": ["entity", "update", "light.example_lamp", "--name", "x", "--write"],
    "preview-api-post": [
        "api",
        "POST",
        "/services/light/turn_on",
        "--field",
        "entity_id=light.example_lamp",
    ],
}

#: Environment each case runs in. Nothing here names a reachable installation.
ENVIRONMENTS = {"error-read-only": {"HASS_AXI_READ_ONLY": "1"}}

CASES = {**HELP, **ERRORS}


def _check(name: str, text: str) -> None:
    path = SNAPSHOTS / f"{name}.txt"
    if UPDATE:
        SNAPSHOTS.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    assert path.exists(), f"no snapshot for {name}; see this file's docstring"
    assert text == path.read_text(encoding="utf-8"), (
        f"{name} changed; if that was deliberate, regenerate it as this file's docstring says"
    )


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_text_is_the_text_that_was_agreed(run_cli, name):
    _, out = run_cli(CASES[name], ENVIRONMENTS.get(name, {}))
    _check(name, out)


@pytest.mark.parametrize("name", sorted(ERRORS))
def test_an_error_document_is_the_same_document_in_json(run_cli, name):
    """The JSON form carries the fields the text form does, so one snapshot covers both."""
    import json

    _, text = run_cli(ERRORS[name], ENVIRONMENTS.get(name, {}))
    _, raw = run_cli([*ERRORS[name], "--json"], ENVIRONMENTS.get(name, {}))
    document = json.loads(raw)
    for key in document:
        assert f"{key}" in text


def test_the_context_document_is_the_one_that_was_agreed(run_cli):
    """What a session hook prints, with nothing configured: the machine it exists to reach.

    The first line names this checkout's own executable, which is a fact about
    where the suite ran; everything after it is the interface.
    """
    _, out = run_cli(["context"], {})
    body = "\n".join(line for line in out.splitlines() if not line.startswith("bin:")) + "\n"
    _check("context-unconfigured", body)


def test_no_snapshot_has_outlived_its_case():
    present = {path.stem for path in SNAPSHOTS.glob("*.txt")}
    assert present == set(CASES) | {"context-unconfigured"}

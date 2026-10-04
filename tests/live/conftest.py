"""The live suite's gate and fixtures. Nothing here runs unless it is asked for, twice.

Once by selection -- `pyproject.toml` deselects the `live` marker, so a plain
`pytest`, `scripts/ci-local.sh` and the gate never collect a live test -- and
once by environment: without ``HASS_AXI_LIVE=1`` every test here skips. The
tiers stack on that:

| tier | what runs | where | needs |
| --- | --- | --- | --- |
| A | reads | the installation in `HA_URL` | `HASS_AXI_LIVE=1` |
| B | previews of every write, with a fingerprint before and after | the same | the same |
| C | self-reversing writes, each restored and read back | the same | `HASS_AXI_LIVE_WRITES=1` |
| D | faults, from loopback stubs with a synthetic token | this machine | `HASS_AXI_LIVE=1` |
| E | rejected credentials and destructive writes | a disposable container | `HASS_AXI_LIVE_LAB=1` |

Nothing in tier A to D sends an invalid credential to the installation, calls a
service on a real device, or renames or moves anything real. Tier E never
touches the installation at all.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from .harness import Target
from .targets import Snapshot

HERE = Path(__file__).parent


def pytest_collection_modifyitems(config, items):
    enabled = bool(os.environ.get("HASS_AXI_LIVE"))
    skip = pytest.mark.skip(reason="the live suite is opt-in: run scripts/live-test.sh")
    for item in items:
        if HERE in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.live)
            if not enabled:
                item.add_marker(skip)


def _first_reachable(raw: str, token: str) -> str:
    """The first candidate in a comma-separated `HA_URL` that answers."""
    candidates = [part.strip().rstrip("/") for part in raw.split(",") if part.strip()]
    for candidate in candidates:
        request = urllib.request.Request(
            f"{candidate}/api/", headers={"Authorization": f"Bearer {token}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=5):
                return candidate
        except (urllib.error.URLError, OSError):
            continue
    pytest.skip("no candidate in HA_URL answered; the live suite needs a reachable installation")


def _finish(target: Target) -> None:
    target.flush()
    leaks = target.leaks()
    assert not leaks, f"the token or a segment of it was printed by: {leaks[:5]}"


@pytest.fixture(scope="session")
def house(tmp_path_factory):
    """The installation named by `HA_URL` and `HA_TOKEN`: tiers A, B and C."""
    url = os.environ.get("HA_URL") or os.environ.get("HASS_SERVER")
    token = os.environ.get("HA_TOKEN") or os.environ.get("HASS_TOKEN")
    if not url or not token:
        pytest.skip("HA_URL and HA_TOKEN are not set")
    target = Target(_first_reachable(url, token), token, tmp_path_factory.mktemp("state"))
    yield target
    _finish(target)


@pytest.fixture(scope="session")
def snapshot(house):
    return Snapshot(house)


@pytest.fixture(scope="session")
def writes():
    """Tier C: self-reversing writes against the installation."""
    if not os.environ.get("HASS_AXI_LIVE_WRITES"):
        pytest.skip(
            "tier C is opt-in: set HASS_AXI_LIVE_WRITES=1 (scripts/live-test.sh --house-writes)"
        )


@pytest.fixture(scope="session")
def lab(tmp_path_factory):
    """Tier E: a disposable Home Assistant container on loopback, removed afterwards."""
    if not os.environ.get("HASS_AXI_LIVE_LAB"):
        pytest.skip("tier E is opt-in: set HASS_AXI_LIVE_LAB=1 (scripts/live-test.sh --lab)")
    from .lab import container

    running = container.start()
    target = Target(running.url, running.token, tmp_path_factory.mktemp("lab-state"), "lab")
    target.container = running
    try:
        yield target
        _finish(target)
    finally:
        container.remove(running)


@pytest.fixture(scope="session")
def lab_snapshot(lab):
    return Snapshot(lab)


@pytest.fixture(scope="session")
def nowhere(tmp_path_factory):
    """Tier D: a target for stub servers, holding a synthetic token and no installation."""
    from .stubs import synthetic_token

    target = Target(
        "http://127.0.0.1:9", synthetic_token(), tmp_path_factory.mktemp("stub"), "stubs"
    )
    yield target
    _finish(target)

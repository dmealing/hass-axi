"""Start, onboard and remove a throwaway Home Assistant on loopback.

The destructive tier needs a server it is allowed to damage: one that takes a
rejected credential without raising a notification in somebody's house, an
area that can be created and deleted, a light that can really be switched. This
is that server. It mints its own credential, which exists only in this process,
and `remove` deletes the container whether the tests passed or not.

    python -m tests.live.lab.container   # start one, print its URL, wait, remove it

The image is pinned through ``HASS_AXI_LAB_IMAGE`` so a run means the same
thing twice; point it at another tag to check another Home Assistant release.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_IMAGE = "ghcr.io/home-assistant/home-assistant:2026.8.3"

#: The demo integration supplies lights, covers, climate and media players with
#: registry entries and devices, which is the distribution the tool is for. The
#: template entities add what the demo lacks: a cover that can only open and
#: close, so a capability requirement has something to refuse.
CONFIGURATION = """\
default_config:
demo:
template:
  - cover:
      - name: Example Blind
        unique_id: example_blind
        state: "{{ 'open' }}"
        open_cover: []
        close_cover: []
  - sensor:
      - name: Example Reading
        unique_id: example_reading
        state: "{{ 21 }}"
        unit_of_measurement: "°C"
        device_class: temperature
        state_class: measurement
"""

START_TIMEOUT = 240


@dataclass
class Running:
    name: str
    url: str
    token: str


def _docker(*args, check=True) -> str:
    done = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and done.returncode != 0:
        raise RuntimeError(f"docker {args[0]} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def _request(url, *, method="GET", body=None, form=None, token=None, timeout=20):
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


def _wait(describe: str, probe, timeout: float = START_TIMEOUT):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            answer = probe()
            if answer:
                return answer
        except (urllib.error.URLError, OSError, ValueError, RuntimeError) as exc:
            last = exc
        time.sleep(2)
    raise RuntimeError(f"the lab did not reach '{describe}' in {timeout:g}s: {last}")


def _long_lived_token(url: str, access_token: str) -> str:
    from websockets.sync.client import connect

    with connect(url.replace("http", "ws", 1) + "/api/websocket", open_timeout=20) as connection:
        json.loads(connection.recv())
        connection.send(json.dumps({"type": "auth", "access_token": access_token}))
        assert json.loads(connection.recv())["type"] == "auth_ok"
        connection.send(
            json.dumps(
                {
                    "id": 1,
                    "type": "auth/long_lived_access_token",
                    "client_name": "hass-axi live suite",
                    "lifespan": 1,
                }
            )
        )
        frame = json.loads(connection.recv())
    if not frame.get("success"):
        raise RuntimeError(f"the lab refused to mint a token: {frame.get('error')}")
    return frame["result"]


def start(image: str | None = None) -> Running:
    """Create, configure, start and onboard one container; returns its URL and token."""
    image = image or os.environ.get("HASS_AXI_LAB_IMAGE") or DEFAULT_IMAGE
    name = f"hass-axi-lab-{secrets.token_hex(4)}"
    _docker("create", "--name", name, "-p", "127.0.0.1::8123", image)
    try:
        with tempfile.TemporaryDirectory() as scratch:
            config = Path(scratch) / "configuration.yaml"
            config.write_text(CONFIGURATION, encoding="utf-8")
            _docker("cp", str(config), f"{name}:/config/configuration.yaml")
        _docker("start", name)
        port = _docker("port", name, "8123/tcp").splitlines()[0].rsplit(":", 1)[1]
        url = f"http://127.0.0.1:{port}"
        client_id = f"{url}/"

        _wait("onboarding", lambda: _request(f"{url}/api/onboarding"))
        code = _request(
            f"{url}/api/onboarding/users",
            method="POST",
            body={
                "client_id": client_id,
                "name": "Example Owner",
                "username": "example",
                "password": secrets.token_urlsafe(24),
                "language": "en",
            },
        )["auth_code"]
        access = _request(
            f"{url}/auth/token",
            method="POST",
            form={"grant_type": "authorization_code", "code": code, "client_id": client_id},
        )["access_token"]
        token = _long_lived_token(url, access)
        for step, body in (
            ("core_config", {}),
            ("analytics", {}),
            ("integration", {"client_id": client_id, "redirect_uri": client_id}),
        ):
            # A step an image does not have, or has already done, is not a
            # reason to stop: the API answers either way.
            with contextlib.suppress(urllib.error.HTTPError):
                _request(f"{url}/api/onboarding/{step}", method="POST", body=body, token=access)

        def demo_is_up():
            states = _request(f"{url}/api/states", token=token)
            return any(s["entity_id"].startswith("light.") for s in states)

        _wait("demo entities", demo_is_up)
        return Running(name, url, token)
    except BaseException:
        _docker("rm", "-f", name, check=False)
        raise


def remove(running: Running) -> None:
    _docker("rm", "-f", running.name, check=False)


if __name__ == "__main__":  # pragma: no cover - a manual aid
    lab = start()
    print(f"lab at {lab.url} in container {lab.name}; press Enter to remove it")
    try:
        input()
    finally:
        remove(lab)

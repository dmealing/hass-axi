"""`hass-axi ping` -- the cheapest proof that Home Assistant answers and accepts the token.

`doctor` checks the environment, the REST API and the WebSocket API, and reads
two registries to do it. That is the right answer to "what is wrong", and too
much for "is it up": a script or a loop asking that question wants one
authenticated round-trip and a number. `GET /api/` requires the token, so a
rejected one surfaces here as `UNAUTHORIZED` and a dead host as a transport
fault, in the same taxonomy as every other command.
"""

from __future__ import annotations

import time

from ..argspec import Command, Sub
from ..errors import AxiError
from ..output import HelpBlock
from ..readonly import READ

COMMAND = Command(
    name="ping",
    summary="Check Home Assistant answers and accepts the token, and how fast",
    usage="usage: hass-axi ping",
    default_sub="ping",
    subs=(Sub(name="ping", summary="Time one authenticated request", access=READ),),
    notes=(
        "one authenticated REST round-trip, timed; exits non-zero when it fails, so it "
        "works as a liveness gate",
        "run `hass-axi doctor` to check the WebSocket API and the environment as well",
    ),
    examples=("hass-axi ping",),
)


def run(ctx, sub: str, parsed):
    config = ctx.config()
    rest = ctx.rest()
    started = time.perf_counter()
    rest.health()
    latency_ms = round((time.perf_counter() - started) * 1000)

    doc: dict = {"ok": True, "url": config.base_url}
    note = config.candidate_note()
    if note:
        doc["fallback"] = f"{note}; the URLs before it in HA_URL did not answer"
    doc["latency_ms"] = latency_ms
    try:
        info = rest.config_info()
    except AxiError:
        # The probe itself answered and was authenticated, which is the
        # question asked. A version that could not be read is not a reason to
        # report an installation that just answered as down.
        info = None
    if isinstance(info, dict) and info.get("version"):
        doc["version"] = info["version"]
    doc["help"] = HelpBlock(["Run `hass-axi` for this installation at a glance"])
    return doc

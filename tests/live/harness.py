"""The live harness: run the build under test, and get a second opinion on its answer.

Every invocation is the real console entry point in a child process, with the
credential in its environment and nowhere else. Three things are true of every
result, whatever the test meant to check:

* the token, and each of its JWT segments, is scrubbed before anything is kept;
* any of them appearing in stdout or stderr is a failed check on that
  invocation, raised when the test ends;
* the invocation is logged -- command, exit, latency, sizes, checks -- and its
  output is not, because the output of a real installation is that
  installation's data.

The second opinion is the raw API, read here with nothing from `hass_axi`: an
answer compared only with the tool's own code proves the tool agrees with
itself.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RESULTS = Path(__file__).parent / ".results"

#: Variables that would change what a run means if they leaked in from the
#: shell that started the suite.
_AMBIENT = (
    "HASS_AXI_READ_ONLY",
    "HA_AXI_READ_ONLY",
    "HASS_AXI_DEBUG",
    "HA_AXI_DEBUG",
    "HASS_SERVER",
    "HASS_TOKEN",
)

_MIN_SEGMENT = 16


class Result:
    """One invocation: its exit, its scrubbed output and the checks made of it."""

    def __init__(self, argv, code, out, err, ms, secrets):
        self.argv = argv
        self.code = code
        self.ms = ms
        self.leak = any(secret in out or secret in err for secret in secrets)
        for secret in secrets:
            out = out.replace(secret, "<LEAKED>")
            err = err.replace(secret, "<LEAKED>")
        self.out = out
        self.err = err
        self.checks: list = []

    @property
    def cmd(self) -> str:
        return "hass-axi " + " ".join(shlex.quote(part) for part in self.argv)

    def json(self):
        return json.loads(self.out)

    def record(self) -> dict:
        return {
            "cmd": self.cmd,
            "exit": self.code,
            "ms": self.ms,
            "out_bytes": len(self.out),
            "err_bytes": len(self.err),
            "leak": self.leak,
        }


class Target:
    """One installation to run against: its URL, its token, and a log of what ran."""

    def __init__(self, url: str, token: str, state_home: Path, label: str = "house") -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.label = label
        self.state_home = state_home
        self.secrets = [token, *(s for s in token.split(".") if len(s) >= _MIN_SEGMENT)]
        self.results: list = []

    # ------------------------------------------------------------ the tool

    def environ(self, env=None, unset=()) -> dict:
        base = {k: v for k, v in os.environ.items() if k not in _AMBIENT}
        base.update({"HA_URL": self.url, "HA_TOKEN": self.token})
        base["XDG_STATE_HOME"] = str(self.state_home)
        for name in unset:
            base.pop(name, None)
        base.update(env or {})
        return base

    def run(self, *argv, env=None, unset=(), stdin=None, timeout=120) -> Result:
        argv = [str(part) for part in argv]
        started = time.perf_counter()
        try:
            done = subprocess.run(
                [sys.executable, "-m", "hass_axi", *argv],
                env=self.environ(env, unset),
                input=stdin if stdin is not None else "",
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            code, out, err = done.returncode, done.stdout, done.stderr
        except subprocess.TimeoutExpired:
            code, out, err = -999, "", "HARNESS TIMEOUT"
        ms = int((time.perf_counter() - started) * 1000)
        result = Result(argv, code, out, err, ms, self.secrets)
        self.results.append(result)
        return result

    def doc(self, *argv, **kwargs):
        """Run with `--json` and return ``(result, document)``."""
        result = self.run(*argv, "--json", **kwargs)
        return result, json.loads(result.out)

    def ok(self, *argv, **kwargs):
        """Run with `--json`, require exit 0, and return the document."""
        result, document = self.doc(*argv, **kwargs)
        assert result.code == 0, f"{result.cmd} exited {result.code}: {document.get('error')}"
        return document

    # ------------------------------------------- the raw API, read directly

    def rest(self, path: str, method: str = "GET", body=None, timeout: float = 60):
        request = urllib.request.Request(
            f"{self.url}/api{path}",
            method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            data=json.dumps(body).encode() if body is not None else None,
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        try:
            return json.loads(raw)
        except ValueError:
            return raw.decode("utf-8", errors="replace")

    def ws_many(self, commands: list, timeout: float = 60) -> list:
        """Several WebSocket commands on one connection; the result frames, in order."""
        from websockets.sync.client import connect

        url = self.url.replace("http", "ws", 1) + "/api/websocket"
        answers = []
        with connect(url, max_size=None, open_timeout=timeout) as connection:
            json.loads(connection.recv())
            connection.send(json.dumps({"type": "auth", "access_token": self.token}))
            assert json.loads(connection.recv())["type"] == "auth_ok"
            for index, command in enumerate(commands, 1):
                connection.send(json.dumps({"id": index, **command}))
                while True:
                    frame = json.loads(connection.recv(timeout=timeout))
                    if frame.get("id") == index and frame.get("type") == "result":
                        answers.append(frame)
                        break
        return answers

    def ws(self, type_: str, **params):
        frame = self.ws_many([{"type": type_, **params}])[0]
        if not frame.get("success"):
            raise RuntimeError(frame.get("error"))
        return frame["result"]

    # ------------------------------------------------------------ the log

    def flush(self) -> None:
        RESULTS.mkdir(exist_ok=True)
        with (RESULTS / f"{self.label}.jsonl").open("a", encoding="utf-8") as handle:
            for result in self.results:
                handle.write(json.dumps(result.record()) + "\n")

    def leaks(self) -> list:
        return [result.cmd for result in self.results if result.leak]


# -------------------------------------------- TOON, by an independent decoder

_HELP = re.compile(r"^(\s*)help\[(\d+)\]:\s*$")


def toon_decode(text: str):
    """Decode with `toon-format`, not with this tool's own encoder.

    The one documented departure from strict TOON, `help[N]:` with one bare
    suggestion per line, is rewritten into strict list form first.
    """
    import toon_format

    lines = text.split("\n")
    fixed, index = [], 0
    while index < len(lines):
        match = _HELP.match(lines[index])
        if match:
            count = int(match.group(2))
            fixed.append(lines[index])
            for offset in range(1, count + 1):
                body = lines[index + offset]
                indent = len(body) - len(body.lstrip(" "))
                fixed.append(" " * indent + "- " + json.dumps(body.strip(), ensure_ascii=False))
            index += count + 1
        else:
            fixed.append(lines[index])
            index += 1
    return toon_format.decode("\n".join(fixed).rstrip("\n"))


COUNT = re.compile(r"^(\d+) of (\d+) matched \((\d+) total\)$|^(\d+) of (\d+) total$")


def counts(line: str) -> tuple:
    """``(shown, matched, total)`` from a `count:` line."""
    match = COUNT.match(line)
    assert match, f"not a count line: {line!r}"
    if match.group(1) is not None:
        return int(match.group(1)), int(match.group(2)), int(match.group(3))
    return int(match.group(4)), int(match.group(5)), int(match.group(5))


# ------------------------------------------------------- the error contract


def codes() -> dict:
    """The closed vocabulary, read from the build under test."""
    from hass_axi import errors

    return dict(errors.CODES)


def contract(result: Result, mode: str, *, code: str | None = None, exit_code: int | None = None):
    """Assert the error contract on one result and return its document.

    A structured document on stdout, nothing on stderr, a code from the closed
    vocabulary, the class derived from it, exit 2 exactly for `usage`, and at
    least one help line.
    """
    assert "Traceback" not in result.out + result.err, result.cmd
    assert result.err == "", f"{result.cmd} wrote to stderr"
    if exit_code is not None:
        assert result.code == exit_code, f"{result.cmd} exited {result.code}, expected {exit_code}"
    if mode == "human":
        assert result.out.strip(), result.cmd
        return None
    document = json.loads(result.out) if mode == "json" else toon_decode(result.out)
    if result.code == 0:
        return document
    vocabulary = codes()
    assert {"error", "code", "class"} <= set(document), f"{result.cmd}: {sorted(document)}"
    if code is not None:
        assert document["code"] == code, f"{result.cmd}: {document['code']} ({document['error']})"
    assert document["code"] in vocabulary, f"{result.cmd}: {document['code']}"
    assert document["code"] != "INTERNAL_ERROR", f"{result.cmd}: {document['error']}"
    assert document["class"] == vocabulary[document["code"]], result.cmd
    assert (result.code == 2) == (document["class"] == "usage"), f"{result.cmd}: exit {result.code}"
    assert document.get("help"), f"{result.cmd}: an error with no help line"
    return document


MODES = (("toon", ()), ("json", ("--json",)), ("human", ("--human",)))


def three_modes(target: Target, argv, *, code=None, exit_code=None, **kwargs) -> dict:
    """Run one invocation in the three modes; every mode must reach the same verdict."""
    documents = {}
    exits = set()
    for mode, extra in MODES:
        result = target.run(*argv, *extra, **kwargs)
        documents[mode] = contract(result, mode, code=code, exit_code=exit_code)
        exits.add(result.code)
    assert len(exits) == 1, f"{argv}: the modes exit differently: {sorted(exits)}"
    if documents["toon"] is not None and exits != {0}:
        assert documents["toon"].get("code") == documents["json"].get("code"), argv
    return documents


def stable(target: Target, argv, attempts: int = 3):
    """The TOON and JSON documents of one read, taken until they agree.

    A live installation changes between two invocations -- a reading moves, an
    age ticks over -- so a single mismatched pair proves nothing. Agreement on
    any attempt is the property; disagreement on all of them is a failure.
    """
    last = None
    for _ in range(attempts):
        toon = target.run(*argv)
        raw = target.run(*argv, "--json")
        assert toon.code == raw.code == 0, f"{toon.cmd}: exit {toon.code} / {raw.code}"
        decoded, document = toon_decode(toon.out), json.loads(raw.out)
        for doc in (decoded, document):
            if isinstance(doc, dict):
                doc.pop("latency_ms", None)
        if decoded == document:
            return decoded, document
        last = (decoded, document)
    return last

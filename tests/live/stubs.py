"""Loopback servers for the faults a real installation must not be asked to produce."""

from __future__ import annotations

import base64
import http.server
import json
import socket
import threading
import time


def synthetic_token() -> str:
    """A JWT-shaped token that is no credential, built at run time."""

    def segment(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return ".".join(
        [
            segment(b'{"alg":"HS256","typ":"JWT"}'),
            segment(b'{"iss":"live-suite-synthetic","iat":1,"exp":2}'),
            segment(b"live-suite-synthetic-signature-0000"),
        ]
    )


class Stub:
    """An HTTP server that answers every request with one behaviour."""

    def __init__(self, behaviour, **options):
        self.seen: list = []  # (method, path, carried Authorization)
        stub = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _answer(self):
                stub.seen.append((self.command, self.path, "Authorization" in self.headers))
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                behaviour(self, **options)

            do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = do_PATCH = _answer

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        ).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _send(handler, status, body=b"", content_type="text/plain", headers=()):
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    for name, value in headers:
        handler.send_header(name, value)
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(body)


def status(handler, code=401, body=None):
    _send(handler, code, (f"{code}: status" if body is None else body).encode())


def redirect(handler, to=""):
    _send(handler, 302, headers=(("Location", to + handler.path),))


def html(handler):
    _send(handler, 200, b"<html><body>router login</body></html>", "text/html")


def empty_json(handler):
    _send(handler, 200, b"", "application/json")


def truncated_json(handler):
    _send(handler, 200, b'{"message": "API running."', "application/json")


def other_json(handler):
    _send(handler, 200, json.dumps({"status": "ok"}).encode(), "application/json")


def slow(handler, seconds=30):
    time.sleep(seconds)
    _send(handler, 200, b"{}", "application/json")


def jpeg(handler):
    _send(handler, 200, b"\xff\xd8\xff\xe0" + bytes(range(128, 256)) * 8, "image/jpeg")


class Blackhole:
    """Accepts the connection and never says anything."""

    def __init__(self):
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(16)
        self.url = f"http://127.0.0.1:{self.socket.getsockname()[1]}"
        self.connections: list = []

        def loop():
            while True:
                try:
                    connection, _ = self.socket.accept()
                except OSError:
                    return
                self.connections.append(connection)

        threading.Thread(target=loop, daemon=True).start()

    def stop(self) -> None:
        for connection in self.connections:
            connection.close()
        self.socket.close()

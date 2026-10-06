"""Home Assistant REST API client, built on the standard library.

Covers the read-and-act half of the API: entity states, service calls and
template rendering. The registries are not reachable here -- see :mod:`hass_axi.ws`.
"""

from __future__ import annotations

import http.client
import json
import shlex
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import Config
from .errors import (
    UNAVAILABLE_STATUSES,
    ApiError,
    AuthFailed,
    ConfigError,
    ConnectionFailed,
    Forbidden,
    NotFound,
)
from .output import debug
from .readonly import READ, WRITE, guard
from .toolkit.shapes import describe, health_fault, is_entity_id, is_text, shape_fault

_JSON = "application/json"

#: Characters a request path may carry unescaped. Everything else is
#: percent-encoded, so a path holding a space is a path Home Assistant does not
#: have rather than a URL `http.client` refuses to send.
_PATH_SAFE = "/%:@!$&'()*+,;=~-._"


class BinaryResponse:
    """A response body that is not text: an image, a stream, an archive.

    Decoding one as UTF-8 prints replacement characters at exit 0, which is
    neither the data nor an error. It is reported by type and size instead.
    """

    __slots__ = ("content_type", "size")

    def __init__(self, content_type: str, size: int) -> None:
        self.content_type = content_type
        self.size = size


def not_home_assistant(path: str, found: str) -> ConfigError:
    """The answer was a 200 that Home Assistant's API does not give.

    A captive portal, a proxy's own error page and another web application on
    the port all answer 200. Reading any of them as an installation with no
    entities, or as a healthy one, is a wrong answer at exit 0 -- so the shape
    is checked and this is raised instead. `config`, because what has to change
    is where HA_URL points.
    """
    return ConfigError(
        f"{api_path(path)} answered, but not as Home Assistant's API does: {found}",
        help_lines=[
            "Check HA_URL points at Home Assistant itself, not at a proxy, a login page "
            "or another service on that host",
            "Run `hass-axi doctor` to see what each transport reaches",
        ],
        code="NOT_HOME_ASSISTANT",
    )


def _shape(value) -> str:
    """What a response turned out to be, in words, for the error above."""
    if isinstance(value, BinaryResponse):
        return f"{value.size} bytes of {value.content_type or 'untyped binary data'}"
    return describe(value)


#: The methods HTTP itself defines as safe. On a declared command the read-only
#: classification is deliberate and a verb is never consulted, but `hass-axi api`
#: hands an opaque path straight to the installation and the method is the only
#: fact there is -- so this errs closed: safe methods pass, everything else is a
#: write, including a POST that happens not to change anything.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: The one exception, and it is named rather than inferred. Rendering a template
#: is a POST because the template travels in the body, and Home Assistant's
#: template sandbox cannot call a service or write a state -- so `template
#: render`, the most useful read this tool has, would otherwise be refused by
#: its own verb.
READ_ONLY_POSTS = frozenset({"/api/template"})


def access_for_request(method: str, path: str) -> str:
    """The read-only classification of one REST request.

    Named to match :func:`hass_axi.ws.access_for_type`: one function per
    transport, answering the same question about the thing that transport is
    about to send. ``path`` is expected in the form :func:`api_path` returns.
    """
    method = method.upper()
    if method in SAFE_METHODS:
        return READ
    if method == "POST" and path in READ_ONLY_POSTS:
        return READ
    return WRITE


def api_path(path: str) -> str:
    """Normalize a REST path the single way every caller must agree on.

    Shared so a command can report exactly the path it requested; reimplementing
    the rule at the reporting site is how `api config` came to say `/apiconfig`
    while requesting `/api/config`.
    """
    path = path if path.startswith("/") else f"/{path}"
    if path != "/api" and not path.startswith("/api/"):
        path = f"/api{path}"
    return path


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse a redirect that would carry the Authorization header elsewhere.

    urllib copies every header except content-length/type onto the redirected
    request and permits scheme changes, so an instance behind an auth proxy or
    a captive portal answering `302 Location: http://other.host/login` would be
    handed the long-lived token in cleartext. Home Assistant's API does not
    redirect, so only a same-origin redirect (a proxy normalising a path) is
    worth following.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        origin = urllib.parse.urlsplit(req.full_url)
        target = urllib.parse.urlsplit(newurl)
        if (origin.scheme, origin.netloc) != (target.scheme, target.netloc):
            raise ConnectionFailed(
                "refusing to follow a redirect to "
                f"{target.scheme}://{target.netloc}: it would send the access token there",
                help_lines=[
                    "Point HA_URL directly at Home Assistant rather than at a proxy that redirects",
                    "Run `hass-axi doctor` to see which transport is failing",
                ],
                code="REDIRECT_REFUSED",
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def no_such_entity(entity_id: str) -> NotFound:
    """The one failed-entity lookup, with next steps that can be run as written.

    The search term is quoted, because a name passed where an id belongs is
    usually several words, and it is the whole argument rather than a slice of
    it: a slice of an argument that turns out to be a credential is a fragment
    no redaction rule recognises.
    """
    shaped = is_entity_id(entity_id)
    term = entity_id.split(".", 1)[1] if shaped else entity_id
    help_lines = [f"Run `hass-axi state list --search {shlex.quote(term)}` to find it by name"]
    if shaped:
        help_lines.append(
            f"Run `hass-axi entity get {entity_id}` if it may be disabled: "
            "a disabled entity has a registry entry and no state"
        )
        help_lines.append("Run `hass-axi state list --domain <domain>` to browse one domain")
        message = f"no entity with id {entity_id}"
    else:
        message = f"no entity with id {entity_id} (an entity id has the form domain.object_id)"
    return NotFound(message, help_lines=help_lines, code="NO_SUCH_ENTITY")


def require_entity_id(value: str) -> None:
    """Refuse, before anything is sent, a value that cannot be an entity id.

    A display name where an id belongs reaches Home Assistant as a filter it
    answers with a bare 400 or 500, which says nothing about what was wrong.
    The answer is the one a missing entity gets, because that is what it is.
    """
    if not is_entity_id(value):
        raise no_such_entity(value)


class RestClient:
    """A thin, synchronous wrapper over the Home Assistant REST endpoints."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._opener = urllib.request.build_opener(_SameOriginRedirectHandler)

    # ------------------------------------------------------------- transport

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        query: dict | None = None,
    ) -> Any:
        """Perform one authenticated request and decode the response.

        Returns parsed JSON when the response is JSON, the response text when
        it is some other text -- ``/api/template`` answers in ``text/plain`` --
        and a :class:`BinaryResponse` when it is not text at all. Nothing is
        assumed about the shape here, because `hass-axi api` reaches endpoints
        this module knows nothing about; the typed endpoints below check theirs.

        The read-only gate is applied here, ahead of the request, because this
        is the one place every REST call passes through: a command added later
        is guarded whether or not its author knew there was a gate.
        """
        resolved = api_path(path)
        guard(
            self.config.read_only,
            access_for_request(method, resolved),
            f"{method.upper()} {resolved}",
        )
        url = self._url(path, query)
        data = None
        headers = dict(self.config.auth_header)
        headers["Accept"] = f"{_JSON}, text/plain"
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = _JSON

        debug(f"{method} {url}")
        try:
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            with self._opener.open(request, timeout=self.config.timeout) as response:
                raw = response.read()
                content_type = response.headers.get("Content-Type", "")
        except (http.client.InvalidURL, ValueError) as exc:
            # Raised before anything is sent: the URL itself cannot be a
            # request. `InvalidURL` is an `HTTPException`, so without this
            # branch it was reported as a connection that dropped mid-response,
            # with advice to retry a command that can never work.
            raise ConfigError(
                f"HA_URL does not make a usable request URL: {exc}",
                help_lines=[
                    "Set HA_URL to your Home Assistant base URL, "
                    "e.g. https://homeassistant.example.com",
                    "Run `hass-axi doctor` to check the environment",
                ],
                code="BAD_URL",
            ) from None
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc, method, path) from None
        except urllib.error.URLError as exc:
            raise self._url_error(exc) from None
        except (TimeoutError, socket.timeout) as exc:
            # A timeout waiting for the response is not wrapped in a URLError
            # -- `http.client` raises it out of `getresponse()` and urllib lets
            # it through -- so it arrives here rather than above. Same
            # classifier either way: which half of the exchange ran out of time
            # is not a fact the caller can act on.
            raise self._url_error(exc) from None
        except (http.client.HTTPException, OSError) as exc:
            raise ConnectionFailed(
                f"the connection to Home Assistant dropped mid-response: {exc}",
                help_lines=[
                    "Retry the command; a dropped connection is often a one-off",
                    "Run `hass-axi doctor` to test the connection if it keeps happening",
                ],
                code="CONNECTION_DROPPED",
            ) from None

        if not is_text(content_type, raw):
            return BinaryResponse(content_type.split(";", 1)[0].strip(), len(raw))
        payload = raw.decode("utf-8", errors="replace")
        if _JSON in content_type:
            if not payload.strip():
                return None
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                # Handed back as text: the raw escape hatch shows what arrived,
                # and every typed endpoint refuses a string through `_json`.
                return payload
        return payload

    def _json(self, method: str, path: str, kind: type, **kwargs) -> Any:
        """One request whose answer has a known JSON shape.

        Every typed endpoint goes through here, so "the server answered 200"
        is never enough by itself: `/api/states` is a list and `/api/config`
        an object on every Home Assistant there is.
        """
        result = self.request(method, path, **kwargs)
        if shape_fault(result, kind) is not None:
            raise not_home_assistant(path, _shape(result))
        return result

    def _url(self, path: str, query: dict | None) -> str:
        # A query written into the path itself (`/states?x=y`) stays a query:
        # only the part before the first `?` is a path to escape.
        route, mark, embedded = api_path(path).partition("?")
        path = urllib.parse.quote(route, safe=_PATH_SAFE)
        if mark:
            path = f"{path}?{urllib.parse.quote(embedded, safe=_PATH_SAFE + '?')}"
        url = f"{self.config.base_url}{path}"
        if query:
            pairs = [(k, v) for k, v in query.items() if v is not None]
            if pairs:
                url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(pairs)}"
        return url

    def _http_error(self, exc, method: str, path: str):
        """Classify one HTTP status, once, for every REST command there is.

        The status is the only fact a refusal reliably carries -- Home Assistant
        renders most of them through aiohttp with a plain-text ``"<status>:
        <reason>"`` body and nothing else -- so the mapping has to be by status
        and the resulting code has to be a literal. It used to end in
        ``code=f"HTTP_{exc.code}"``, which minted a fresh code from whatever the
        server said: no caller could switch on the result, because the set it
        was switching over was the set of HTTP statuses rather than a vocabulary
        anybody had written down.

        Three splits here are the substance of the taxonomy, and each is a fact
        about Home Assistant rather than a preference:

        - **401 and 403 are not one answer.** 401 is a rejected credential and a
          new token fixes it. 403 is what the IP-ban middleware raises
          (``components/http/ban.py`` answers a banned address with a bare
          ``HTTPForbidden`` before any view runs), and a new token cannot fix
          it -- worse, minting one and failing the login again is what deepened
          the ban in the first place.
        - **502, 503 and 504 are transport, not refusal.** ``hass.is_stopping``
          answers every request with a bodyless 503 while an instance restarts,
          and a reverse proxy in front of one answers 502 or 504 for the same
          window. The request was never seen, so retrying it as-is is correct --
          which is exactly what the transport class means and what a `refused`
          would tell an agent not to do.
        - **500 is a refusal.** It is what a ``HomeAssistantError`` renders as:
          a named entity lacking a capability, or a ``--response`` call that
          matched nothing. Reached, permitted, and refused.
        """
        detail = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
            # `message` is the key `HomeAssistantView.json_message` writes, and
            # the only one a refusal was observed to carry. A body that says it
            # another way, or whose message is not text -- the mobile app's
            # webhook nests an object under `error` -- is quoted whole.
            message = json.loads(raw).get("message")
            detail = message if message and isinstance(message, str) else raw
        except Exception:
            detail = ""
        detail = detail.strip()

        if exc.code == 401:
            return AuthFailed(
                "Home Assistant rejected the access token",
                help_lines=[
                    "Check HA_TOKEN holds a current long-lived access token",
                    "Create a new one on your Home Assistant profile page, under Security",
                ],
                code="UNAUTHORIZED",
            )
        if exc.code == 403:
            return Forbidden(
                "Home Assistant refused this client: the token was not the problem",
                help_lines=[
                    "A new token will not help; the request was refused before any view ran",
                    "Home Assistant bans an address after repeated failed logins -- "
                    "clear it from `ip_bans.yaml` on the instance and restart it",
                    "Check whether a proxy in front of Home Assistant is refusing the request",
                ],
                code="FORBIDDEN",
            )
        if exc.code == 404:
            if detail:
                # Home Assistant answers an unrouted path with plain text and no
                # body worth reading, but a routed one whose *subject* is missing
                # says so in JSON -- `/states/<id>` answers `Entity not found.`.
                # Reporting the path as wrong in that case sends an agent looking
                # for a spelling mistake that is not there.
                return NotFound(
                    f"Home Assistant answered 404 for {path}: {detail}",
                    help_lines=[
                        "Run `hass-axi state list --search <text>` to find an entity by name",
                        "Run `hass-axi --help` to see the available commands",
                    ],
                    code="NOT_FOUND",
                )
            return NotFound(
                f"no such API path: {path}",
                help_lines=["Run `hass-axi --help` to see the available commands"],
                code="NOT_FOUND",
            )
        if exc.code == 405:
            return ApiError(
                f"{method} is not allowed on {path}",
                help_lines=["Run `hass-axi api --help` for the supported methods"],
                code="METHOD_NOT_ALLOWED",
            )
        if exc.code in UNAVAILABLE_STATUSES:
            return ConnectionFailed(
                f"Home Assistant is not serving requests right now (HTTP {exc.code})"
                + (f": {detail}" if detail else ""),
                help_lines=[
                    "Retry the command; an instance answers this way while it restarts",
                    "Run `hass-axi doctor` if it keeps answering this way",
                ],
                code="UNAVAILABLE",
            )
        suffix = f": {detail}" if detail else ""
        if exc.code == 400:
            return ApiError(
                f"Home Assistant refused the request (HTTP 400){suffix}",
                help_lines=[
                    "The request reached Home Assistant and its arguments were refused; "
                    "change them rather than retrying",
                    "Run the command with `--help` to see what it accepts",
                ],
                code="BAD_REQUEST",
            )
        if exc.code == 500:
            return ApiError(
                f"Home Assistant failed while handling the request (HTTP 500){suffix}",
                help_lines=[
                    "Home Assistant answers this way for a request it could not carry out, "
                    "most often an id that names nothing; check the ids passed",
                    "The reason is in Home Assistant's own log, under Settings > System > Logs",
                ],
                code="SERVER_ERROR",
            )
        return ApiError(
            f"Home Assistant returned HTTP {exc.code}{suffix}",
            help_lines=[
                "Run `hass-axi doctor` to check the installation is answering normally",
            ],
            code="API_ERROR",
        )

    def _url_error(self, exc):
        """Classify a failure to complete the exchange at all.

        A timeout arrives by two routes and used to be reported as two
        different faults. urllib wraps an ``OSError`` raised while sending into
        a ``URLError`` (``AbstractHTTPHandler.do_open``), so a connect timeout
        landed here and was reported as ``UNREACHABLE``, while a timeout
        waiting for the response propagates as a bare ``TimeoutError`` and was
        reported as ``TIMEOUT``. One fault, one fix, and which half of the
        exchange ran out of time is not the caller's business.
        """
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLError):
            return ConnectionFailed(
                f"TLS handshake with Home Assistant failed: {reason}",
                help_lines=["Confirm HA_URL uses the scheme your instance actually serves"],
                code="TLS_ERROR",
            )
        # `socket.timeout` is an alias of `TimeoutError` from 3.10; on 3.9 it is
        # a distinct OSError subclass, so both are named.
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return ConnectionFailed(
                f"timed out after {self.config.timeout:g}s waiting for Home Assistant",
                help_lines=["Raise the limit with `hass-axi --timeout 60 <command>`"],
                code="TIMEOUT",
            )
        return ConnectionFailed(
            f"could not reach Home Assistant: {reason}",
            help_lines=[
                "Check HA_URL points at a reachable Home Assistant instance",
                "Run `hass-axi doctor` to test the connection",
            ],
            code="UNREACHABLE",
        )

    # ------------------------------------------------------------- endpoints

    def health(self) -> dict:
        """`GET /api/`, which Home Assistant answers with `{"message": "API running."}`.

        The message is checked, not just the status: this is the liveness probe,
        and a liveness probe that passes against any web server is not one.
        """
        result = self.request("GET", "/")
        fault = _shape(result) if isinstance(result, BinaryResponse) else health_fault(result)
        if fault is not None:
            raise not_home_assistant("/", fault)
        return result

    def config_info(self) -> dict:
        return self._json("GET", "/config", dict)

    def states(self) -> list:
        return self._json("GET", "/states", list)

    def state(self, entity_id: str) -> dict:
        require_entity_id(entity_id)
        try:
            return self._json("GET", f"/states/{urllib.parse.quote(entity_id, safe='')}", dict)
        except NotFound:
            raise no_such_entity(entity_id) from None

    def services(self) -> list:
        return self._json("GET", "/services", list)

    def call_service(
        self, domain: str, service: str, data: dict, *, return_response: bool = False
    ) -> Any:
        query = {"return_response": ""} if return_response else None
        result = self.request("POST", f"/services/{domain}/{service}", body=data, query=query)
        if isinstance(result, (str, BinaryResponse)) and result != "":
            raise not_home_assistant(f"/services/{domain}/{service}", _shape(result))
        return result

    def history(self, entity_ids: list, start: str, end: str) -> list:
        """State timelines, one list per requested entity, in the order asked.

        ``minimal_response`` keeps every row after the first to a state and a
        time, which is all a timeline needs; the first row still carries the
        attributes, so the entity's displayed name comes back with it. The end
        is always sent -- see :mod:`hass_axi.commands._window` for why an
        omitted one answers for a single day rather than up to now.
        """
        result = self.request(
            "GET",
            f"/history/period/{urllib.parse.quote(start, safe='')}",
            query={
                "filter_entity_id": ",".join(entity_ids),
                "end_time": end,
                "minimal_response": "",
            },
        )
        if not isinstance(result, list):
            raise not_home_assistant("/history/period", _shape(result))
        return result

    def logbook(self, start: str, end: str, entity_ids: list | None = None) -> list:
        """Logbook entries between two instants, optionally for named entities only."""
        result = self.request(
            "GET",
            f"/logbook/{urllib.parse.quote(start, safe='')}",
            query={"end_time": end, "entity": ",".join(entity_ids) if entity_ids else None},
        )
        if not isinstance(result, list):
            raise not_home_assistant("/logbook", _shape(result))
        return result

    def render_template(self, template: str) -> str:
        result = self.request("POST", "/template", body={"template": template})
        if isinstance(result, BinaryResponse):
            raise not_home_assistant("/template", _shape(result))
        return result if isinstance(result, str) else json.dumps(result)

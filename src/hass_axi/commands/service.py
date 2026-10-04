"""`hass-axi service` -- discover and call Home Assistant services.

This is the command that reaches every service the installation has, which is
why it is also the one that has to explain itself when a call is refused. Home
Assistant answers an unknown service, an undeclared field and a missing required
one with an empty ``400``: the status and nothing else. Everything an agent
could act on lives in the model it publishes at ``GET /api/services``, so that
model is read here -- on the failure path, where it costs nothing, and before
dispatch only where the alternative is a call the installation will silently
drop on the floor.

Nothing is dispatched at all without ``--write``. Without it `service call` is a
preview, which reads the same model and the same registries to say what the
call would send, what it would reach and whether the capability pre-check
passes -- see :func:`_preview`.
"""

from __future__ import annotations

from axi_toolkit.ha import services as model

from ..argspec import Command, Flag, Sub
from ..errors import ApiError, AxiError, NotFound, UsageError
from ..output import HelpBlock, truncate
from ..readonly import DYNAMIC, READ, WRITE
from ._common import (
    WRITE_FLAG,
    WRITE_FLAG_NAME,
    device_area_map,
    domain_of,
    effective_area_id,
    friendly_name,
    parse_json_flag,
    parse_pairs,
    plural,
    preview_help,
    preview_note,
    project,
    select_fields,
)

GET_FIELDS = ["field", "required", "type", "description", "options", "example", "section"]
DEFAULT_GET_FIELDS = ["field", "required", "type", "description"]

#: Descriptions are prose written for a UI, so they are previewed rather than
#: printed whole; `--full` is the escape hatch, as it is on `state get`.
DESCRIPTION_CHARS = 120

#: Entities a preview lists by name before it only counts the rest.
PREVIEW_ROWS = 20

COMMAND = Command(
    name="service",
    summary="List Home Assistant services, read one's fields, and call them",
    usage="usage: hass-axi service <subcommand> [flags]",
    subs=(
        Sub(
            name="list",
            access=READ,
            summary="List service domains, or the services in one domain",
            flags=(Flag("--domain", "<name>", note="show the services in one domain"),),
        ),
        Sub(
            name="get",
            access=READ,
            args=("<domain.service>",),
            summary="Show one service's fields, target and response mode, read live",
            flags=(
                Flag("--fields", "<a,b,c>", note=f"from {'|'.join(GET_FIELDS)}"),
                Flag("--full", boolean=True, note="do not truncate field descriptions"),
            ),
        ),
        Sub(
            name="call",
            # Every service call is a write, including one that only asks for a
            # response: `weather.get_forecasts` reads, `conversation.process`
            # can turn the house upside down, and nothing Home Assistant
            # publishes tells the two apart. The safe half of that ignorance is
            # to refuse both -- and, for the same reason, to send neither until
            # the caller says so: without the write flag the call is checked
            # and shown, which only reads, and `access` below says which.
            access=DYNAMIC,
            args=("<domain.service>",),
            summary="Preview a service call, or send it with --write",
            flags=(
                Flag("--target-entity", "<entity_id>", repeat=True),
                Flag("--target-area", "<area_id>", repeat=True),
                Flag("--target-device", "<device_id>", repeat=True),
                Flag(
                    "--data", "<key=value>", repeat=True, note="value parsed as JSON when it parses"
                ),
                Flag("--data-json", "<object>", note="merged over --data"),
                Flag("--response", boolean=True, note="request the service response payload"),
                Flag(
                    "--no-check",
                    boolean=True,
                    note="skip the capability pre-check on --target-area/--target-device",
                ),
                WRITE_FLAG,
            ),
        ),
    ),
    notes=(
        f"`service call` sends nothing without {WRITE_FLAG_NAME}: it checks the call against the "
        "published service, resolves the target, runs the capability pre-check and shows the "
        "request -- and fails, as the call would, for a service that does not exist or a target "
        "that reaches nothing",
        "--data-json takes a whole JSON object; --data takes repeated key=value pairs",
        "a refused call is explained from `/api/services`, which a sent call reads on failure only",
        "--target-area and --target-device pre-check the published capability, because"
        " Home Assistant drops an entity that lacks it without saying so",
    ),
    examples=(
        "hass-axi service list",
        "hass-axi service list --domain light",
        "hass-axi service get light.turn_on",
        "hass-axi service call light.turn_on --target-entity light.example_lamp",
        "hass-axi service call light.turn_on --target-entity light.example_lamp --write",
        "hass-axi service call light.turn_on --target-area example_room --data brightness=180 --write",
        "hass-axi service call climate.set_temperature --target-entity climate.example_thermostat --data-json '{\"temperature\": 21}' --write",
    ),
)


def access(sub: str, parsed) -> str:
    """`service call` writes only when it is told to; the preview only reads."""
    return WRITE if parsed.get("write") else READ


def run(ctx, sub: str, parsed):
    if sub == "list":
        return _list(ctx, parsed)
    if sub == "get":
        return _get(ctx, parsed)
    return _call(ctx, parsed)


def _split_name(raw: str) -> tuple:
    domain, sep, service = raw.partition(".")
    if not sep or not domain or not service:
        raise UsageError(
            f"expected <domain>.<service>, got {raw!r}",
            help_lines=[
                "Run `hass-axi service call light.turn_on --target-entity <entity_id>`",
                "Run `hass-axi service list` to see the available domains",
            ],
            code="BAD_SERVICE",
        )
    return domain, service


# ------------------------------------------------------------------- listing


def _list(ctx, parsed):
    domains = ctx.rest().services()
    wanted = parsed.get("domain")

    if not wanted:
        rows = [
            {
                "domain": entry.get("domain", ""),
                "services": len(entry.get("services") or {}),
            }
            for entry in domains
        ]
        rows.sort(key=lambda row: row["domain"])
        if not rows:
            return {"services": "0 service domains registered in this installation"}
        return {
            "count": plural(len(rows), "domain"),
            "domains": rows,
            "help": HelpBlock(
                [
                    "Run `hass-axi service list --domain <domain>` to see one domain's services",
                    "Run `hass-axi service get <domain>.<service>` to see one service's fields",
                    "Run `hass-axi service call <domain>.<service> --target-entity <entity_id>` to "
                    "preview a call, and add --write to send it",
                ]
            ),
        }

    match = model.find_domain(domains, wanted)
    if match is None:
        raise _no_such_domain(domains, wanted)

    services = match.get("services") or {}
    rows = [
        {
            "service": f"{wanted}.{name}",
            "name": (spec or {}).get("name") or name,
            # Counted through the model's own flattening: a section is not a
            # field, and reporting it as one invites an agent to send it.
            "fields": len(model.field_names(spec if isinstance(spec, dict) else {})),
        }
        for name, spec in sorted(services.items())
    ]
    if not rows:
        return {"services": f"0 services registered in domain {wanted}"}
    return {
        "count": f"{plural(len(rows), 'service')} in {wanted}",
        "services": rows,
        "help": HelpBlock(
            [
                f"Run `hass-axi service get {rows[0]['service']}` to see its fields",
                f"Run `hass-axi service call {rows[0]['service']} --target-entity <entity_id>` to "
                "preview a call, and add --write to send it",
            ]
        ),
    }


# ------------------------------------------------------------ one service


def _get(ctx, parsed):
    domain, service = _split_name(parsed.positionals[0])
    published = ctx.rest().services()
    spec = model.find_service(published, domain, service)
    if spec is None:
        raise _no_such_service(published, domain, service)

    full = parsed.get("full", False)
    hint = ""
    rows = []
    for name, field, section in model.fields(spec):
        description = str(field.get("description") or "")
        if not full:
            description, note = truncate(
                description,
                DESCRIPTION_CHARS,
                f"Run `hass-axi service get {domain}.{service} --full` for complete descriptions",
            )
            hint = hint or note
        rows.append(
            {
                "field": name,
                "required": bool(field.get("required")),
                "type": model.selector_kind(field),
                "description": description,
                "options": ", ".join(model.selector_options(field)),
                "example": str(field.get("example") or ""),
                "section": section,
            }
        )

    fields = select_fields(parsed.get("fields"), GET_FIELDS, DEFAULT_GET_FIELDS)
    response = model.response_mode(spec)
    doc = {
        "service": f"{domain}.{service}",
        "name": spec.get("name") or service,
        "description": spec.get("description") or "",
        "response": response,
    }

    target = _target_summary(spec, domain)
    if target:
        doc["target"] = target
    doc["fields"] = project(rows, fields) if rows else f"0 fields declared on {domain}.{service}"

    required = model.required_field_names(spec)
    example = f"hass-axi service call {domain}.{service} --target-entity <entity_id>"
    if required:
        example += "".join(f" --data {name}=<value>" for name in required)
    if response == model.RESPONSE_REQUIRED:
        example += " --response"
    help_lines = [f"Run `{example}` to preview the call, and add --write to send it"]
    if response == model.RESPONSE_REQUIRED:
        help_lines.append(
            f"{domain}.{service} answers with a payload or not at all, so --response is required"
        )
    elif response == model.RESPONSE_OPTIONAL:
        help_lines.append(
            f"Add --response to `hass-axi service call {domain}.{service}` for its payload"
        )
    if model.feature_masks(spec, domain):
        help_lines.append(
            "Run `hass-axi state get <entity_id>` and compare its supported_features attribute"
        )
    if hint:
        help_lines.append(hint)
    doc["help"] = HelpBlock(help_lines)
    return doc


def _target_summary(spec, domain: str) -> str:
    """What the service says about the entities it can be aimed at."""
    target = spec.get("target")
    if not isinstance(target, dict) or not target:
        return ""
    parts = []
    entries = target.get("entity")
    if isinstance(entries, list):
        wanted = sorted(
            {
                name
                for entry in entries
                if isinstance(entry, dict)
                for name in (
                    [entry["domain"]]
                    if isinstance(entry.get("domain"), str)
                    else entry.get("domain") or []
                )
            }
        )
        if wanted:
            parts.append(f"entity domain {'|'.join(wanted)}")
    masks = model.feature_masks(spec, domain)
    if masks:
        joined = ", ".join(str(mask) for mask in masks)
        parts.append(f"supported_features matching any of {joined}")
    if "device" in target:
        parts.append("device")
    return "; ".join(parts)


# ---------------------------------------------------------------- calling


class _Live:
    """What this invocation has read from the installation, read at most once.

    The service model and the registries are both worth having and both cost a
    round-trip, and two of the three paths that want them run only when
    something has already gone ambiguous. Memoising here is what keeps a call
    that succeeds outright at exactly one request, while letting the capability
    gate, the empty-result report and the refusal enrichment share whatever any
    one of them had to fetch.
    """

    __slots__ = ("_ctx", "_failure", "_model", "_read_model", "_resolved")

    def __init__(self, ctx):
        self._ctx = ctx
        self._model = None
        self._read_model = False
        self._resolved = None
        self._failure = None

    def model(self):
        """The published service model, or None when it could not be read.

        An installation with no services at all does not exist, so an empty
        answer means the read failed rather than that there is nothing to say.
        """
        if not self._read_model:
            self._read_model = True
            try:
                published = self._ctx.rest().services()
            except AxiError:
                published = None
            self._model = published if isinstance(published, list) and published else None
        return self._model

    def use_model(self, published) -> None:
        """Hand over a model the caller has already read, so it is not read twice."""
        self._read_model = True
        self._model = published if isinstance(published, list) and published else None

    def spec(self, domain: str, service: str):
        published = self.model()
        return None if published is None else model.find_service(published, domain, service)

    def resolved(self, parsed):
        if self._failure is not None:
            raise self._failure
        if self._resolved is None:
            try:
                self._resolved = _resolve(self._ctx, parsed)
            except AxiError as exc:
                # Remembered rather than retried: a transport that would not
                # answer once will not answer the second caller either, and two
                # timeouts cost twice as much as one to learn the same thing.
                self._failure = exc
                raise
        return self._resolved


def _call(ctx, parsed):
    domain, service = _split_name(parsed.positionals[0])

    data = parse_pairs(parsed.get("data", []), flag="--data")
    data.update(parse_json_flag(parsed.get("data_json"), flag="--data-json"))

    # The REST endpoint passes this body straight through as the service data
    # and never unwraps a `target` key, while entity services validate
    # entity_id / device_id / area_id flat at the top level under
    # PREVENT_EXTRA. A nested target is rejected twice over, so emit them flat.
    # (The WebSocket `call_service` command does take a nested target; this
    # shape is specific to REST.)
    for flag, key in (
        ("target_entity", "entity_id"),
        ("target_area", "area_id"),
        ("target_device", "device_id"),
    ):
        selected = parsed.get(flag)
        if selected:
            data[key] = selected

    targeted = bool(
        parsed.get("target_entity") or parsed.get("target_area") or parsed.get("target_device")
    )
    live = _Live(ctx)
    if not parsed.get("write"):
        return _preview(ctx, live, domain, service, data, parsed, targeted)
    _precheck(live, domain, service, parsed)

    try:
        result = ctx.rest().call_service(
            domain, service, data, return_response=parsed.get("response", False)
        )
    except AxiError as exc:
        raise _explain(live, exc, domain, service, data, parsed) from None

    changed, response = _split_result(result)
    doc = {"service": f"{domain}.{service}"}
    if changed:
        doc["changed"] = [
            {
                "entity_id": state.get("entity_id", ""),
                "name": friendly_name(state),
                "state": state.get("state", ""),
            }
            for state in changed
        ]
        doc["count"] = f"{plural(len(changed), 'state')} changed"
        if response is not None:
            doc["response"] = response
        return doc

    doc["changed"] = f"{domain}.{service} accepted with 0 states changed"
    if response is not None:
        doc["response"] = response
    if targeted:
        # An empty change set is the honest answer to two different questions --
        # "there was nothing to do" and "there was nothing to do it to" -- and
        # Home Assistant gives the same one to both. Resolving the target says
        # which, and is paid for only here, where the answer is ambiguous.
        _report_target(live, doc, domain, service, parsed)
    return doc


def _preview(ctx, live: _Live, domain: str, service: str, data: dict, parsed, targeted: bool):
    """Show what `service call` would send, checked as far as reading allows.

    A preview that only echoed the arguments would say nothing the caller did
    not already know. This one asks the installation the questions the call
    itself would have answered -- does the service exist, does it take a
    response, what does the target reach, does anything it reaches carry the
    capability -- and raises the same error the call would have earned for each
    one Home Assistant enforces. Only reads are made: the service model, the
    states, and the registries when an area or a device is named.
    """
    published = ctx.rest().services()
    wants_response = parsed.get("response", False)
    request = {
        "method": "POST",
        "path": f"/api/services/{domain}/{service}",
        "body": data or "empty",
    }
    if wants_response:
        request["response"] = "requested"
    if not (isinstance(published, list) and published):
        # No installation has no services, so this is a model that could not be
        # read. The request can still be shown; nothing about it can be checked,
        # and saying so beats passing checks that never ran.
        return {
            "service": f"{domain}.{service}",
            "preview": preview_note(ctx.environ),
            "request": request,
            "checks": "not run - the service model could not be read",
            "help": HelpBlock([*preview_help(ctx.environ), *_generic_help(domain, service)]),
        }

    fault = _model_fault(published, domain, service, wants_response)
    if fault is not None:
        raise fault
    spec = model.find_service(published, domain, service)
    live.use_model(published)

    doc: dict = {
        "service": f"{domain}.{service}",
        "preview": preview_note(ctx.environ),
        "request": request,
    }

    field_fault = _field_fault(spec, domain, service, data)
    doc["fields"] = (
        f"{field_fault.message}, which Home Assistant refuses on most services"
        if field_fault is not None
        else "every field sent is one the service publishes and no required one is missing"
    )

    masks = model.feature_masks(spec, domain)
    if not targeted:
        doc["target"] = "none given"
        doc["capability_check"] = "not applicable without a target"
    else:
        _precheck(live, domain, service, parsed)
        fault = _incapable_fault(live, spec, domain, service, parsed)
        if fault is not None:
            raise fault
        resolved = live.resolved(parsed)
        reached = _reached(resolved, spec, domain)
        matched = resolved.within(model.target_domains(spec))
        if wants_response:
            if not reached:
                unreached = _unreached_target(live, spec, domain, service, parsed)
                if unreached is not None:
                    raise unreached
        elif not matched:
            raise _no_entities_targeted(resolved, domain, service, parsed)
        acted_on = reached or _available(matched)
        parts = list(resolved.problems)
        parts.append(
            f"{_scope_phrase(parsed)} would reach {plural(len(acted_on), 'entity', 'entities')}"
        )
        skipped = [s.get("entity_id", "") for s in matched if s.get("state") == "unavailable"]
        if skipped:
            parts.append(f"{', '.join(skipped)} unavailable, which Home Assistant skips")
        doc["target"] = "; ".join(parts)
        doc["would_reach"] = [
            {
                "entity_id": state.get("entity_id", ""),
                "name": friendly_name(state),
                "state": state.get("state", ""),
            }
            for state in acted_on[:PREVIEW_ROWS]
        ]
        if not masks:
            doc["capability_check"] = "nothing to check - this service publishes no requirement"
        elif parsed.get("no_check") and not reached:
            doc["capability_check"] = (
                "skipped by --no-check - no entity reached reports a supported_features value "
                f"containing any of {', '.join(str(mask) for mask in masks)}"
            )
        else:
            doc["capability_check"] = (
                f"passed - {len(reached)} of {len(_available(matched))} available can do this "
                f"(supported_features contains any of {', '.join(str(mask) for mask in masks)})"
            )

    help_lines = preview_help(ctx.environ)
    if targeted and len(doc.get("would_reach") or []) < len(acted_on):
        help_lines.append(
            f"{len(acted_on)} entities would be reached; the first {PREVIEW_ROWS} are listed"
        )
    if field_fault is not None:
        help_lines.extend(field_fault.help_lines)
    doc["help"] = HelpBlock(help_lines)
    return doc


def _split_result(result):
    """Separate the changed-state list from an optional service response payload."""
    if isinstance(result, list):
        return result, None
    if isinstance(result, dict):
        changed = result.get("changed_states")
        response = result.get("service_response")
        return (changed if isinstance(changed, list) else []), response
    return [], None


# ------------------------------------------------------- the capability gate


def _precheck(live: _Live, domain: str, service: str, parsed) -> None:
    """Refuse before dispatch a call the installation would silently drop.

    Home Assistant refuses an entity named outright that lacks the capability a
    service needs, but an entity reached through an area or a device is skipped
    in silence -- the call comes back 200 with an empty list and the agent
    learns nothing. So the gate runs for those two target kinds and not for
    `--target-entity`, where the refusal already arrives loudly and is enriched
    on the failure path for free.

    The rule applied is Home Assistant's own, over the same published masks, so
    refusing here reaches the same outcome the instance would and cannot block a
    call that would have done something. `--no-check` exists regardless: a
    published requirement is an integration's claim about itself, and a wrong
    one must not become a wall.
    """
    if parsed.get("no_check"):
        return
    if not (parsed.get("target_area") or parsed.get("target_device")):
        return

    spec = live.spec(domain, service)
    if spec is None:
        return
    masks = model.feature_masks(spec, domain)
    if not masks:
        # Nothing is gated, so nothing is worth resolving: the check stops here
        # for every service that publishes no capability requirement, which is
        # all but a handful.
        return

    try:
        resolved = live.resolved(parsed)
    except AxiError:
        # The registries are the only way to know what an area holds. Failing
        # to read them is a reason not to check, never a reason to refuse.
        return
    reached = _reached(resolved, spec, domain)
    if reached:
        return
    available = _available(resolved.within(model.target_domains(spec)))
    if not available:
        return

    reported = ", ".join(
        f"{state.get('entity_id', '')} reports {model.entity_features(state)}"
        for state in available
    )
    raise ApiError(
        f"{domain}.{service} needs a supported_features bitmask containing any of "
        f"{', '.join(str(mask) for mask in masks)}, and no entity the target matched has one: "
        f"{reported}",
        help_lines=[
            *_target_help(domain, service, parsed),
            "Run the same command with --no-check to send it anyway",
        ],
        code="UNSUPPORTED_CAPABILITY",
    )


# ------------------------------------------------------- resolving a target


class _Resolved:
    """What the target flags actually name in this installation.

    Built from the same registries `state list --area` reads, with the same
    device-area fallback: an entity with no area of its own is in its device's,
    and any count that ignores that disagrees with Home Assistant.
    """

    __slots__ = ("problems", "states")

    def __init__(self, states, problems):
        self.states = states
        self.problems = problems

    def within(self, domains) -> list:
        """The matched entities a service restricted to ``domains`` can reach.

        An empty list of domains is no restriction, which is what a service
        that publishes no entity target -- or one whose filter names no domain,
        as `homeassistant.turn_on` does -- is entitled to.
        """
        if not domains:
            return list(self.states)
        return [state for state in self.states if domain_of(state.get("entity_id", "")) in domains]


def _resolve(ctx, parsed) -> _Resolved:
    """Expand the target flags into the entities Home Assistant would reach."""
    entity_ids = list(parsed.get("target_entity") or [])
    area_ids = list(parsed.get("target_area") or [])
    device_ids = list(parsed.get("target_device") or [])

    states = {state.get("entity_id"): state for state in ctx.rest().states()}
    problems: list = []
    matched: list = []

    for entity_id in entity_ids:
        if entity_id in states:
            matched.append(entity_id)
        else:
            problems.append(f"no entity with id {entity_id!r} in this installation")

    if area_ids or device_ids:
        with ctx.ws() as client:
            entries = client.run("entity.list") or []
            areas = client.run("area.list") or []
            devices = client.run("device.list") or []
        device_areas = device_area_map(devices)
        known_areas = {area.get("area_id") for area in areas}
        by_name = {(area.get("name") or "").strip().lower(): area.get("area_id") for area in areas}
        known_devices = {device.get("id") for device in devices}

        for area_id in area_ids:
            if area_id in known_areas:
                matched.extend(
                    entry.get("entity_id")
                    for entry in entries
                    if effective_area_id(entry, device_areas) == area_id
                )
                continue
            real = by_name.get(area_id.strip().lower())
            if real:
                # Home Assistant matches an area by id, so a name reaches
                # nothing at all -- indistinguishable from an empty area unless
                # it is said outright.
                problems.append(
                    f"--target-area takes an area_id; {area_id!r} is an area name, "
                    f"whose id is {real}"
                )
            else:
                problems.append(f"no area with id or name {area_id!r}")

        for device_id in device_ids:
            if device_id in known_devices:
                matched.extend(
                    entry.get("entity_id")
                    for entry in entries
                    if entry.get("device_id") == device_id
                )
            else:
                problems.append(f"no device with id {device_id!r}")

    seen: set = set()
    resolved = []
    for entity_id in matched:
        if entity_id in seen or entity_id not in states:
            continue
        seen.add(entity_id)
        resolved.append(states[entity_id])
    return _Resolved(resolved, problems)


def _available(states: list) -> list:
    """The matched entities Home Assistant has not already written off.

    An `unavailable` entity is skipped in silence however well it fits the
    service's target -- even one named outright, which is what separates it
    from an entity that lacks a capability and is refused over.
    """
    return [state for state in states if state.get("state") != "unavailable"]


def _reached(resolved: _Resolved, spec, domain: str) -> list:
    """The matched entities a call would actually act on, not merely match.

    `helpers/service.py` narrows its candidate entities to the available ones
    and to the ones carrying a capability the service publishes before it
    decides a target reached anything, so an entity can be matched by domain and
    still never arrive. The pre-dispatch gate and the re-derived `--response`
    verdict read a target through this one predicate, or the two halves of one
    outcome -- the 200 with an empty change set, and the bodyless 500 the same
    call becomes under `--response` -- would be diagnosed from different facts.
    """
    masks = model.feature_masks(spec, domain)
    return [
        state
        for state in _available(resolved.within(model.target_domains(spec)))
        if not masks or model.satisfies(model.entity_features(state), masks)
    ]


def _report_target(live: _Live, doc, domain: str, service: str, parsed) -> None:
    """Say what the target resolved to, and refuse to call reaching nothing a success."""
    scope = _scope_phrase(parsed)
    try:
        resolved = live.resolved(parsed)
    except AxiError as exc:
        # The call itself succeeded, so a transport that would not answer the
        # follow-up question must not turn it into a failure. Say what could
        # not be read instead of implying the target was fine.
        doc["target"] = f"{scope} could not be resolved: {exc.message}"
        doc["help"] = HelpBlock(_target_help(domain, service, parsed))
        return
    spec = live.spec(domain, service)
    # Which domains the service can reach is published; the service's own name
    # is only a good guess at it, and a wrong guess would either miscount or
    # invent a failure. When the model could not be read, no filter is applied.
    reachable = resolved.within(model.target_domains(spec) if spec else [])

    parts = list(resolved.problems)

    if not reachable:
        raise _no_entities_targeted(resolved, domain, service, parsed)

    parts.append(f"{scope} matched {plural(len(reachable), 'entity', 'entities')}")
    unavailable = [state for state in reachable if state.get("state") == "unavailable"]
    if unavailable:
        named = ", ".join(state.get("entity_id", "") for state in unavailable)
        parts.append(f"{named} unavailable, which Home Assistant skips without a word")
    elif len(reachable) == 1:
        parts.append("which reported no state change")
    else:
        parts.append("none of which reported a state change")

    doc["target"] = "; ".join(parts)
    doc["help"] = HelpBlock(_target_help(domain, service, parsed))


def _no_entities_targeted(
    resolved: _Resolved, domain: str, service: str, parsed, skipped: str = ""
):
    """The one answer to "the target named nothing this service can act on".

    Built here rather than at each site because the two sites are the same
    question asked on opposite sides of the same outcome -- a 200 with an empty
    change set, and the 500 the identical call gets when `--response` is set --
    and an agent that saw them phrased differently would have no way to tell they
    were the same finding. ``skipped`` carries the opening for a target that
    *did* match entities Home Assistant then filtered out, so the verdict names
    them as the reason nothing was reached rather than reporting a match that
    did not happen.
    """
    parts = list(resolved.problems)
    scope = _scope_phrase(parsed)
    opening = skipped or f"matched 0 entities {domain}.{service} can act on"
    return NotFound(
        f"{scope} {opening}, so the call did nothing" + (f" ({'; '.join(parts)})" if parts else ""),
        help_lines=_target_help(domain, service, parsed),
        code="NO_ENTITIES_TARGETED",
    )


def _scope_phrase(parsed) -> str:
    parts = []
    for flag, label in (
        ("target_entity", "entity"),
        ("target_area", "area"),
        ("target_device", "device"),
    ):
        values = parsed.get(flag) or []
        if values:
            parts.append(f"{label} {'|'.join(values)}")
    return ", ".join(parts) or "the target"


def _target_help(domain: str, service: str, parsed) -> list:
    lines = []
    areas = parsed.get("target_area") or []
    devices = parsed.get("target_device") or []
    if areas:
        lines.append(
            f"Run `hass-axi state list --area {areas[0]} --domain {domain}` to see what is there"
        )
        lines.append("Run `hass-axi area list` to see each area's id and how much it holds")
    if devices:
        # `--device`, not `--search`: a device id is opaque and was never in the
        # search haystack, so the line this replaces named a command that
        # answered `0 registry entries found` every single time it was run.
        lines.append(f"Run `hass-axi entity list --device {devices[0]}` to see a device's entities")
    if not areas and not devices:
        lines.append("Run `hass-axi state get <entity_id>` to see an entity's current state")
    lines.append(f"Run `hass-axi service get {domain}.{service}` to see what this service targets")
    return lines


# ------------------------------------------------- explaining a refused call


def _explain(live: _Live, exc: AxiError, domain: str, service: str, data: dict, parsed):
    """Turn a refusal into the next command to run.

    Home Assistant answers an unknown service, an undeclared field and a missing
    required one with an empty ``400`` -- `APIDomainServicesView.post` raises
    `HTTPBadRequest` from the underlying error and aiohttp renders the status
    alone. So the wire carries nothing to act on, and everything below comes
    from the model instead, fetched here and nowhere else: a call that succeeds
    pays for none of it.
    """
    if not isinstance(exc, ApiError):
        # Only a refusal is worth explaining from the model. A connection or
        # credential failure already says what it is, and fetching the model to
        # find out more would fail the same way and take just as long.
        return exc

    wants_response = parsed.get("response", False)
    detail = exc.message or ""

    # Answered before the model is read, because it is knowable without one and
    # must never be printed as Home Assistant phrased it: its message names a
    # query parameter of its own REST API, which an agent driving this CLI has
    # no way to set. AXI: suggestions reference this tool's flags, never the
    # vocabulary of what it wraps.
    if "return_response" in detail:
        return _response_mismatch(domain, service, wants_response)

    published = live.model()
    if published is None:
        # The enrichment failed; the original refusal stands, with the generic
        # way forward it should have carried all along.
        return _with_help(exc, _generic_help(domain, service))

    fault = _model_fault(published, domain, service, wants_response)
    if fault is not None:
        return fault
    spec = model.find_service(published, domain, service)
    fault = _field_fault(spec, domain, service, data)
    if fault is not None:
        return fault

    fault = _incapable_fault(live, spec, domain, service, parsed)
    if fault is not None:
        return fault

    # Last, because everything above explains the refusal from what was *sent*,
    # and this explains it from what was *reached*. A call whose target names
    # nothing is a 200 with an empty change set -- which `_report_target` answers
    # -- unless `--response` is set, where `helpers/service.py` raises
    # `HomeAssistantError("Service call requested response data but did not match
    # any entities")` and aiohttp renders it as a bare 500 with no body. The
    # only command that can fail this way is the one whose failure carries
    # nothing to read, so the diagnosis has to be re-derived here or it is lost.
    unreached = _unreached_target(live, spec, domain, service, parsed)
    if unreached is not None:
        return unreached

    return _with_help(exc, _generic_help(domain, service))


def _model_fault(published, domain: str, service: str, wants_response: bool):
    """What the published model says is wrong with a call, or None.

    Whether the service exists and whether it answers with a payload are
    things Home Assistant itself enforces, so they read the same before a call
    as after a refused one: the preview and the failure path both come through
    here, which is what makes a preview fail where the call would have.
    """
    if model.find_domain(published, domain) is None:
        return _no_such_domain(published, domain)

    spec = model.find_service(published, domain, service)
    if spec is None:
        return _no_such_service(published, domain, service)

    response = model.response_mode(spec)
    if response == model.RESPONSE_REQUIRED and not wants_response:
        return _response_mismatch(domain, service, False)
    if response == model.RESPONSE_NONE and wants_response:
        return _response_mismatch(domain, service, True)
    return None


def _field_fault(spec, domain: str, service: str, data: dict):
    """A field the service does not publish, or a required one left out, or None.

    Kept apart from :func:`_model_fault` because it is a weaker fact. After a
    refusal it is the explanation; before a call it is only likely, since a
    published field list is an integration's claim about itself and a script
    takes variables it never declared. So the failure path raises it and the
    preview reports it without refusing.
    """
    declared = model.field_names(spec)
    sent = [key for key in data if key not in model.TARGET_KEYS]
    unknown = [key for key in sent if key not in declared]
    if unknown:
        listing = ", ".join(declared) if declared else "(none)"
        return ApiError(
            f"{domain}.{service} does not accept "
            f"{'fields' if len(unknown) > 1 else 'field'} {', '.join(sorted(unknown))}",
            help_lines=[
                f"fields for {domain}.{service}: {listing}",
                f"Run `hass-axi service get {domain}.{service}` for their types and which are required",
            ],
            code="UNKNOWN_SERVICE_FIELD",
        )

    missing = [name for name in model.required_field_names(spec) if name not in data]
    if missing:
        supplied = "".join(f" --data {name}=<value>" for name in missing)
        return ApiError(
            f"{domain}.{service} requires "
            f"{'fields' if len(missing) > 1 else 'field'} {', '.join(missing)}",
            help_lines=[
                f"Run `hass-axi service call {domain}.{service}"
                f" --target-entity <entity_id>{supplied}`",
                f"Run `hass-axi service get {domain}.{service}` to see every field it takes",
            ],
            code="MISSING_SERVICE_FIELD",
        )

    return None


def _incapable_fault(live: _Live, spec, domain: str, service: str, parsed):
    """`UNSUPPORTED_CAPABILITY` for an entity named outright that lacks it, or None."""
    masks = model.feature_masks(spec, domain)
    if masks:
        incapable = _incapable(live, parsed, masks)
        if incapable:
            reported = ", ".join(
                f"{entity_id} reports {features}" for entity_id, features in incapable
            )
            return ApiError(
                f"{domain}.{service} needs a supported_features bitmask containing any of "
                f"{', '.join(str(mask) for mask in masks)}, which is not what "
                f"{reported}",
                help_lines=[
                    f"Run `hass-axi service get {domain}.{service}` to see what it targets",
                    "Run `hass-axi state get <entity_id>` to read an entity's supported_features",
                ],
                code="UNSUPPORTED_CAPABILITY",
            )

    return None


def _unreached_target(live: _Live, spec, domain: str, service: str, parsed):
    """`NO_ENTITIES_TARGETED` when the target reached nothing, else None.

    Resolving costs the registries, so it is asked only of a call that named a
    target and that nothing else could explain. A transport that will not answer
    is no reason to invent a verdict: the original refusal stands instead.
    """
    if not (
        parsed.get("target_entity") or parsed.get("target_area") or parsed.get("target_device")
    ):
        return None
    try:
        resolved = live.resolved(parsed)
    except AxiError:
        return None
    if _reached(resolved, spec, domain):
        return None
    matched = resolved.within(model.target_domains(spec))
    if not matched:
        return _no_entities_targeted(resolved, domain, service, parsed)
    reasons = " and ".join(_skipped_reasons(matched, model.feature_masks(spec, domain)))
    skipped = (
        f"matched {plural(len(matched), 'entity', 'entities')}, but {reasons}, "
        "which Home Assistant skips before matching"
    )
    return _no_entities_targeted(resolved, domain, service, parsed, skipped)


def _skipped_reasons(matched: list, masks: list) -> list:
    """One clause per reason Home Assistant filtered out a matched entity.

    Only two filters stand between a matched entity and the service: it has to
    be available, and it has to carry the capability when the service publishes
    one. `--no-check` is what lets an incapable entity stay in the target after
    the gate, so both reasons reach this verdict, and each is named for the
    entity it struck.
    """
    reasons = []
    unavailable = [state for state in matched if state.get("state") == "unavailable"]
    if unavailable:
        named = ", ".join(state.get("entity_id", "") for state in unavailable)
        reasons.append(f"{named} {'is' if len(unavailable) == 1 else 'are'} unavailable")
    wanted = ", ".join(str(mask) for mask in masks)
    for state in matched:
        if state.get("state") == "unavailable":
            continue
        features = model.entity_features(state)
        if masks and not model.satisfies(features, masks):
            reasons.append(f"{state.get('entity_id', '')} reports {features}, not any of {wanted}")
    return reasons


def _incapable(live: _Live, parsed, masks) -> list:
    """Named entities that cannot satisfy the masks, read from live state.

    Only the entities the caller named outright: those are the ones Home
    Assistant refuses over, and the ones it can be held to. An area- or
    device-resolved entity is skipped in silence instead, which the pre-check
    covers before the call ever goes out. An unavailable one is skipped before
    its capability is ever read, so it is not blamed for lacking one.
    """
    if not (parsed.get("target_entity") or []):
        return []
    try:
        resolved = live.resolved(parsed)
    except AxiError:
        return []
    out = []
    for state in _available(resolved.states):
        if state.get("entity_id") not in (parsed.get("target_entity") or []):
            continue
        features = model.entity_features(state)
        if not model.satisfies(features, masks):
            out.append((state.get("entity_id", ""), features))
    return out


def _response_mismatch(domain: str, service: str, wants_response: bool):
    if wants_response:
        return ApiError(
            f"{domain}.{service} does not return a response, so --response cannot be used",
            help_lines=[
                f"Run `hass-axi service call {domain}.{service} --target-entity <entity_id>`",
                f"Run `hass-axi service get {domain}.{service}` to see its response mode",
            ],
            code="RESPONSE_NOT_SUPPORTED",
        )
    return ApiError(
        f"{domain}.{service} answers with a response payload or not at all, "
        "so it must be called with --response",
        help_lines=[
            f"Run `hass-axi service call {domain}.{service} --response`",
            f"Run `hass-axi service get {domain}.{service}` to see the fields it takes",
        ],
        code="RESPONSE_REQUIRED",
    )


def _no_such_domain(published, domain: str):
    known = model.domains(published)
    close = model.near(domain, known)
    help_lines = []
    if close:
        help_lines.append(f"did you mean: {', '.join(close)}")
    help_lines.append(f"domains registered here: {', '.join(known[:12])}")
    help_lines.append("Run `hass-axi service list` to see every domain")
    return NotFound(
        f"no service domain named {domain!r} in this installation",
        help_lines=help_lines,
        code="NO_SUCH_DOMAIN",
    )


def _no_such_service(published, domain: str, service: str):
    names = model.service_names(published, domain)
    close = model.near(service, names)
    help_lines = []
    if close:
        help_lines.append(f"did you mean: {', '.join(f'{domain}.{name}' for name in close)}")
    help_lines.append(
        f"Run `hass-axi service list --domain {domain}` to see all "
        f"{plural(len(names), 'service')} in {domain}"
    )
    help_lines.append(f"Run `hass-axi service get {domain}.<service>` to see one service's fields")
    return NotFound(
        f"no service {domain}.{service} in this installation",
        help_lines=help_lines,
        code="NO_SUCH_SERVICE",
    )


def _generic_help(domain: str, service: str) -> list:
    return [
        f"Run `hass-axi service get {domain}.{service}` to see the fields it takes",
        f"Run `hass-axi service list --domain {domain}` to see the domain's services",
    ]


def _with_help(exc: AxiError, help_lines: list):
    """Keep a refusal exactly as it was, but never leave it a dead end."""
    exc.help_lines = exc.help_lines or help_lines
    return exc

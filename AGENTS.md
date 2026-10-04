# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test,
release, architecture, and sharp-edge notes that should travel with the code.

## The name: `hass-axi`, formerly `ha-axi`

This tool was published as `ha-axi` up to 0.7.1. An unrelated TypeScript Home Assistant CLI is also
published as `ha-axi`, holds that slot in the community AXI catalog, and installs a binary of the
same name — so this one was renamed: distribution and console script `hass-axi`, package
`hass_axi`, variables `HASS_AXI_*`. `HA_URL`/`HA_TOKEN` and their `HASS_*` aliases did not change.
Three things outlive the rename, and each is deliberate:

- **The old variables still work.** `readonly.LEGACY_ENV_VAR` (`HA_AXI_READ_ONLY`) still switches a
  session read-only and `HA_AXI_DEBUG` still enables diagnostics; each prints one deprecation notice
  per process on stderr (`output.deprecated_variable`). The read-only one must never be dropped
  quietly: an upgrade that turned the guard off is the one outcome that variable cannot have.
- **`setup hooks` adopts what the old name installed** — see "The session-hook installer".
- **`legacy/ha-axi/` is the final `ha-axi` release**: a package with no code of its own that
  depends on `hass-axi` and whose `ha-axi` command forwards to it with a stderr notice. It is built
  and published by hand, once, after `hass-axi` is on PyPI; no workflow builds it, and
  `tests/test_legacy_shim.py` is the only thing that runs it before somebody's upgrade does.

Historical prose below that names `ha-axi` (a release number, a defect) describes the tool under its
old name and is left as it was.

## The hard constraint: this repository is public and must stay generic

`hass-axi` talks to home automation installations. The failure that matters is not a bug — it is a
commit that describes, or grants access to, someone's house. Before writing **anything** into this
repo, including tests, fixtures, docs, examples and commit messages:

- **No host addresses.** No RFC1918 addresses, no install-specific hostnames or ports. The base URL
  comes from `HA_URL`.
- **No credentials.** Home Assistant long-lived tokens are JWTs (`eyJ...`). One must never appear
  in a commit, test, fixture, doc, example or log line.
- **No real entity data.** No real `entity_id`s, friendly names, area names, device names or person
  names. Invent obviously-synthetic ones: `light.example_lamp`, area `Example Room`,
  `https://homeassistant.example.com`.
- **No local paths or personal identifiers.**

`scripts/leakcheck.py` enforces this — do not rely on remembering it:

```sh
scripts/leakcheck.py                     # every tracked file
scripts/leakcheck.py --staged            # what a commit would record (pre-commit hook)
scripts/leakcheck.py --commit-msg PATH   # the message itself (commit-msg hook)
scripts/leakcheck.py --pull-request N    # a pull request's title and body (hygiene.yml)
scripts/leakcheck.py --rules             # the live rule list
scripts/leakcheck.py --demo              # self-test: proves every rule still fires
scripts/install-hooks.sh                 # sets core.hooksPath to .githooks
```

`scripts/ci-local.sh` runs `--demo` before the real scan, so a scanner that stopped detecting
anything fails the check rather than passing silently. If the scanner flags a line that legitimately needs the shape, add
`leakcheck: allow=<rule>` on that line — scoped to that one rule, never blanket. Do not weaken a
rule to make a commit pass, and do not bypass the hooks.

**A file that cannot carry a marker** — JSON has no comment syntax, and vendored third-party data
must stay byte-for-byte — is exempted in `PATH_ALLOWANCES` in `scripts/leakcheck.py` instead, per
path *and* per rule, and `--rules` prints the table so the exemption is visible where the rules
are. There is one entry today: the vendored TOON fixture whose backslash-escaping case is a
synthetic Windows drive path. `tests/test_leakcheck.py` re-scans each exempted file with the table
switched off and asserts the rules that fire are exactly the ones the entry names — an entry that
has outlived its cause fails the suite rather than quietly covering something new.

**Writing tests for the guard:** build credential shapes at run time — `leakcheck.synthetic_jwt()`
base64-encodes a payload rather than embedding an `eyJ...` literal — because the condensed pass
joins the whole file before re-scanning and will (correctly) find a literal split across lines.
Address shapes may be written as fragments, since the condensed pass deliberately runs only the
token rules.

**There are three surfaces, and the third one is not a file.** A pull request title and body are
published the moment they are written, are in no checkout, pass under no hook, and can be edited
after every other check has run — so neither the tracked-file scan nor `--commit-msg` has ever seen
one. That is not theoretical: the pipeline's own document step writes into the body, pasting
captured pytest output, and a pytest header carries a `rootdir:` line holding an absolute path. It
has published a home directory twice, once here and once on the sibling project, with every check
green both times, because the only check that read the body at all — `commitcheck --pull-request` —
was reading it for a different question and answering that one correctly. **The rules were never the
problem; the reach was**, which is why `--pull-request` reuses `RULES` and `scan_text` outright
rather than growing a second pattern list. A rule added later covers all three surfaces with nobody
remembering to wire it up.

**Captured tool output was the leak source twice over, and the second one was the scanner's own
self-test.** `--demo` prints what each rule caught to prove the rules fire — and those samples are
leak-shaped by construction, so pasting the demo's output into a body as evidence fails the very
check it opens, which is what happened to the pull request that introduced that check. The demo
therefore reports every finding without the value, the way the pull request reporter already did,
and `tests/test_leakcheck.py` pins that the demo's output passes the pull request scan.

Three things about that scan are load-bearing:

- **`edited` in `hygiene.yml`'s trigger list is the whole mechanism.** The document step writes the
  evidence into the body *after* the pull request is opened, so a check firing only on `opened` and
  `synchronize` would scan the empty original body and pass. Two separate guards now depend on that
  trigger; `tests/test_leakcheck.py` asserts it is still there.
- **It fails closed, in both directions.** No token, no `owner/name`, a fetch that does not answer,
  an answer that is not a pull request, or an empty `RULES` — every one of them fails the check
  rather than reporting a clean it cannot support. `0 findings` from a guard that never saw the
  artefact converts an unknown risk into a false assurance, which is worse than not running.
- **The report names the field, line, rule and offset, and never the match.** A pull request check
  runs on a public log; printing the excerpt the file report prints would republish the leak to a
  wider audience than the pull request page. The offset is printed only when the finding's pass read
  the text as written — one surfaced in a percent-decoded or joined view indexes a string that
  exists nowhere the reader can open, so it prints `-` and the pass column instead. For the same
  reason a pull request cannot carry a `leakcheck: allow=` marker — in a file that marker is
  committed, diffed and reviewed, and in a body it is an off-switch anyone can add after every
  check has run. An attribution trailer is still
  exempt from the address rule alone, because GitHub's squash box offers the body as the commit
  message.

`leakcheck.py` borrows `commitcheck.py`'s GitHub reader rather than growing its own — same token
resolution, same slug resolution, same error taxonomy — and imports it at the point of use so the
hooks never pay for it. `commitcheck.py` is byte-identical with the sibling project's copy and must
stay that way: this reuse deliberately runs one way only.

**Coverage is bounded, and the README says so.** Do not restore any claim that the guard makes
review unnecessary: it narrows how a leak can happen, and misses generic public hostnames, secrets
that are neither JWT-shaped nor bearer-prefixed, and anything inside a binary.

## Architecture

- `toon.py` — a strict TOON encoder (spec v4.1). Encoding happens **only** at the output boundary;
  command modules return plain JSON-shaped dicts. Do not loosen it to make output prettier. Two
  suites cover it and they are not interchangeable: `tests/test_toon.py` states the behaviour in
  this project's words, and `tests/test_toon_conformance.py` runs the specification's own encode
  fixtures — every one of them, vendored byte-for-byte from `toon-format/spec` under
  `tests/fixtures/toon-spec/` (MIT; provenance, checksums and the refresh recipe live in
  `PROVENANCE.md` beside them). `CASE_COUNT` there is the only place the case count is written,
  and it is asserted, so a fixture that stops being collected fails instead of shrinking the
  score. A rule nobody thought to write a test for reads as passing, which is how 0.3.0 shipped
  two failing cases while the README claimed strictness.
- `output.py` — the single place anything reaches stdout, and therefore the only place redaction
  has to hold. `HelpBlock` is the one deliberate departure from strict TOON: `help[N]:` blocks
  render one suggestion per line, matching the AXI standard and the sibling AXI CLIs, because the
  suggestions are command lines full of commas. Data structures stay strict TOON.
- `rest.py` — REST over the standard library. `ws.py` — WebSocket over `websockets`' sync client,
  the only runtime dependency that carries a transport.
- `argspec.py` — per-subcommand flag declarations. Unknown flags are rejected by name with the
  valid ones inlined; `RENAMED` maps plausible wrong guesses to the real flag.
- `commands/` — one module per noun, each exposing `COMMAND` and `run(ctx, sub, parsed)`, and
  `access(sub, parsed)` as well if any of its subcommands is `DYNAMIC`. Every `Sub` declares
  `access`; one that does not is refused under `HASS_AXI_READ_ONLY` and fails the completeness sweep.
  Adding a noun is one new file plus two lines in `cli.py` (`COMMAND_ORDER` and `_MODULES`); root
  help, `SKILL.md` and the parametrised test sweeps all derive from those. A `pkgutil` scan would save
  the two lines, cost static analysis, and still need an explicit order — it has been costed and
  is not worth it.
- `axi_toolkit.ha.services` — a pure reader for what `GET /api/services` publishes, imported by
  `commands/service.py` under the local alias `model`. No I/O and no cache: the caller fetches,
  and decides whether the answer is worth the round-trip. It is **not in this repository**; see
  "The service model is a dependency now" below.
- `commands/context.py` — the document a session hook prints, in both halves of a session's
  lifecycle, and the only command whose contract is *when* it runs rather than what it answers:
  `context` describes the installation without connecting to it, and `context end` records what
  the ended session ran. Neither reaches a transport or reads a credential, so neither can fail;
  `hooks.py` is their only intended caller and a human running them is reading what their agents
  were told. See "The session-hook installer" below.
- `sessionlog.py` — the session record `context end` writes and `context` reads back: one entry
  per session holding command names and counts, never an argument, in one file under
  `$XDG_STATE_HOME/hass-axi/`, and read back strictly because that file is one anything on the
  machine could have edited. Arguments are where entity ids, area names and a typed token would
  live, and the line lands in an agent's context, a wider surface than the terminal the command
  was typed in. See "The session-hook installer" below.
- `errors.py` — the error types, the eight fault classes, and `CODES`: the closed vocabulary of
  every code this tool can print, each mapped to its class. It imports nothing at all, so every
  other module can depend on it. See "The error taxonomy" below; the short version is that the class
  is derived from the code and never declared beside it, and that a code is always a literal.
- `readonly.py` — the `HASS_AXI_READ_ONLY` gate: the classification vocabulary, the switch reader,
  the refusal, and the one `guard()` all three enforcement points call. It imports nothing but
  `errors` and `output` (for the deprecation notice of the pre-rename variable), so `config`,
  `rest`, `ws` and `cli` can all depend on it without a cycle.
- `commands/_window.py` — the `--start`/`--end` rules the three recorder reads share: an age (`24h`)
  or an ISO instant, no offset means UTC, and the end is always sent. `_window.now()` is the one
  clock, so tests pin it with `monkeypatch` rather than racing the wall clock.
- `commands/sensor.py`, `history.py`, `logbook.py`, `statistics.py`, `ping.py` — the reads that
  close the gap with the other `ha-axi` and add the sensor and energy reads; see "The recorder
  reads" below.

### The service model is a dependency now

`servicemodel.py` was moved to `axi_toolkit.ha.services` — whole, with a single edit: one docstring
sentence cited this file by name and would have dangled in the other repository. Everything below
that docstring is byte-for-byte what this repository used to carry. `commands/service.py` imports it
under the same local alias, `model`, which is why the swap is one line and nothing else in that file
moved: all nineteen names reached through that alias resolve on the new module, and that was checked
rather than assumed.

**Why a package rather than two copies.** The two AXI CLIs measured 1,378 identical lines of toolkit
between them, and the duplication had already cost a TOON specification fix that landed in one copy
and not the other — invisibly, until somebody ran both encoders against the same fixtures. The
reader is the first module of the Home Assistant tier to move. Its own tests moved with it: the four
cases in `tests/test_service_model.py` that addressed the module rather than the command path are
stated there now and are deliberately not restored here, because two copies of one test is the same
divergence one layer up. The ~600 lines that drive `service call` and `service get` against the REST
double are this repository's own and stay — they are the evidence that the swap changed no
behaviour, and the capability rule that matters most to this tool is still exercised end to end by
`test_a_service_with_an_upstream_fallback_is_not_gated`.

**The floor is `>=0.3.0` because that is the first release containing the module.** It is a floor
and not a pin, so the command-path suite is what checks the reader still reads the way this tool
needs it to.

**Two direct dependencies, one added to the installed closure, and that is measured rather than
asserted.** `axi-toolkit` declares no runtime dependency of its own — its `ha` extra is empty,
because what the reader needs is `difflib` — and its own purity tests are what keep that true. A
clean-environment install of the built wheel arrives with exactly `axi-toolkit` and `websockets`.
Re-measure when the floor moves; a transitive dependency appearing there is a reason to reconsider
this dependency, not something to absorb quietly.

### Security invariants — do not regress these

- **Every output path is redacted, stdout and stderr alike.** `output.write`, `output.write_text`,
  `output.debug` and `output.debug_exception` all pass through `redact()`. stderr is not a safe
  channel just because agents ignore it: it reaches terminals, logs and CI output.
- **`cli.main` has a last-resort `except Exception`** that renders a structured, redacted error on
  **stdout**. Without it an unexpected exception prints a raw traceback on stderr, bypassing
  redaction entirely and leaving stdout empty. Both halves are the documented contract.
- **The token is registered as a secret in `config.load`**, at the moment it is read, so no later
  code path can print it. It is also rejected there if it contains whitespace or a control
  character, because `http.client` raises a `ValueError` embedding the whole `Bearer ...` header.
- **Redirects never carry the token off-origin.** `rest._SameOriginRedirectHandler` refuses any
  redirect that changes scheme or netloc; urllib would otherwise copy `Authorization` onto it.
- **URL userinfo is stripped in `normalize_base_url` and registered as a secret.** The no-argument
  home view prints the base URL, so userinfo must not survive into it.
- **A bare host defaults to `https://`**, never `http://`.
- **`HA_URL` may hold several comma-separated candidates, and each is normalised alone** — so
  userinfo on the second is stripped and registered exactly as on the first. `config.select_reachable`
  picks one in `cli.Context.config()`, once, so both transports use the same candidate for the run.
  It moves on only on a transport failure (no TCP connection, or no TLS handshake for `https`),
  never on an HTTP answer: a 401 from the first candidate is the installation answering, and the
  next candidate is the same installation. One candidate is never probed. When none answers the
  first is kept, so the request that follows reports the fault in the ordinary taxonomy.
- **`HASS_AXI_READ_ONLY` holds at three points, and the two transports are the load-bearing ones.**
  See "The read-only gate" below. Do not move enforcement into command bodies, do not add a
  fourth classification, and do not make the switch parse its value.
- Tests for all of this live in `tests/test_credentials.py`, which asserts `capsys` **stderr** is
  clean — the assertion that was missing when two escapes shipped.

### Sharp edges

- **The canonical decimal range is wider than Python's float repr.** Spec section 2 makes decimal
  form a MUST for `0` and for `1e-6 <= |n| < 1e21`; `repr` leaves decimal form outside roughly
  `[1e-4, 1e16)`, so `json.dumps` alone violates that MUST in the band at each end — and both bands
  are ordinary sensor data (a current reading in amps, a byte counter), reachable through
  `state get --full`. `_number` formats through `Decimal(repr(value))` inside the range and defers
  to `json.dumps` outside it, where an exponent is permitted. `Decimal(value)` would be wrong:
  it expands the exact binary value instead of the shortest round-tripping digits.
- **Tabular form is not available in list-item position.** A tabular header on a hyphen line is a
  keyless fields-bearing header, which section 6 allows only at the document root, so section 9.4
  requires list form however uniform the items are. `array()` carries `allow_tabular` and
  `list_item()` passes `False`; the restriction is the position, not the depth, so a *key* inside a
  list-item object still reaches tabular form. Reachable through `api` and `ws --raw`, which hand
  arbitrary Home Assistant JSON to the encoder.
- **Null is meaningful over WebSocket.** `config/entity_registry/update` with `name: null` is how a
  user override is cleared, so `WsClient.send_command` must not filter `None` out of a payload.
  It did once; `--clear-name` silently did nothing. There is a test for this.
- **`entity_id` is not stable identity.** Filter by area or search; do not infer meaning from an id.
- **An entity's displayed name comes from two registries, and most entities do not carry their
  own.** `registry_name` transcribes Home Assistant's `_async_get_full_entity_name` as
  `async_get_full_entity_name` calls it — `parts=(DEVICE, ENTITY)`, `use_legacy_naming=True` — which
  is two rules: a `name` somebody set wins **outright**, device prefix and all, and everything else
  is the device's display name (`name_by_user or name`) joined to `original_name`, whichever of the
  two is present. There is no third rule, and in particular **`has_entity_name` is not a gate**:
  Home Assistant applies it on the way out, publishing `original_name_unprefixed` under the
  `original_name` key in `as_partial_dict` (and therefore in `extended_dict` too), so what arrives
  over the WebSocket is already the entity's half alone and both settings of the flag compose
  identically here. That the state's `friendly_name` and this view agree is not a coincidence to be
  maintained: `Entity.__async_calculate_state` calls the same `async_get_full_entity_name`, so there
  is one rule and two readers of it. Measured against a live 2026.8.3 instance, reading the entity
  row alone agreed with the displayed name for **14 of 88** entities; this rule agrees for **88 of
  88**. Every earlier proposal that gated composition on `has_entity_name` reaches 83 — the five it
  misses are exactly the entries whose whole name is their device's and whose flag is unset.
  `matches_search` sees the composed name, which is what makes `entity list --search '<the name a
  user sees>'` work; it answered `0` for four entities in five in 0.3.2.
- **An entity with no `area_id` inherits its device's area.** Any per-area count or filter that
  ignores the device fallback will be wrong.
- **An `area_id` no area answers to is not a placement.** Home Assistant accepts
  `entity.update --param area_id=<typo>` without complaint, and the entity is then invisible to
  `--area <id>` (there is no such area) *and* to `--area none` (it has an `area_id`), while
  `area list`'s per-area counts and `unassigned_entities` quietly stop summing to the size of the
  registry. `area_is_placed` is the one predicate for this: `filter_by_area`'s `none` branch and
  `area._entity_counts` both treat an unplaced id as unassigned, so the totals reconcile and the
  entity is findable, and `entity get`/`entity update` report `area_source: no area has this id`
  rather than `entity`, because "unassigned" and "holding an id nothing answers to" are different
  facts. A real area delete does not cause this — Home Assistant clears the id itself — so it takes
  a typo, through a *declared* command rather than `--raw`.
- **Every view that reports an entity's area builds it with `_row()`.** `effective_area_id` is
  applied there; its other call sites (`state list --area`'s filter, `area list`'s counts) never
  print a per-entity area. A view that reads `entry["area_id"]` directly reports `""` for an entity
  whose area comes from its device. `entity update` did exactly that: it answered from the update
  payload and the pre-update entry, without ever reading the device registry, so a rename of a
  device-placed entity replied `area: ""` while `entity get` on the same entity said otherwise. The
  data was never damaged — but an agent reads the update response, and an empty area there reads as
  "unassigned". Update and get now share `_row()` and both carry `area_source`.
- **A repeated row shape gets one constructor, and the row's key order is part of its output.**
  `state._row` and `doctor._check` are each the only place their shape is built, so a field added
  later cannot reach some callers and miss others — `state get` and `doctor`'s four passing checks
  had each open-coded a copy. Two rules fall out of collapsing them. `state._row` defaults a missing
  `entity_id` to `""` and not to the id `state get` was asked for, because `state list` has no
  requested id to fall back to and echoing the caller's own argument back would dress a malformed
  answer as a well-formed one; neither default is reachable, since `/api/states/<id>` answers with
  `State.as_dict()` and a missing subject is a 404 long before a row is built. And `doctor._check`
  assigns `detail` after the optional `code`/`class` rather than inside the literal, because rows
  render in insertion order and a failing row puts prose after the two fields a caller switches on
  — folding `detail` into the literal would silently reorder every failing check row.
- **`state` (REST) and `entity` (WebSocket) are different views.** Names and areas exist only in the
  registry; states exist only over REST. `state list --area` therefore reads the registry over the
  WebSocket — the only data command that crosses transports; `doctor` uses both too, but only to
  check them — and pays that round-trip only when the flag is passed. The flag exists because an
  agent that learns `--area` on `entity list` will reach for it on `state list`; a filter is not
  the same as importing registry columns into the runtime view, which is why `area` is still not a
  `state list --fields` choice.
- **A device id is opaque, so `entity list --device` is the only route from a device to its
  entities.** It is not searchable (and should not be: substring-matching an opaque hex id is an
  accident, not a filter), and `device list --fields entities` prints a *count*, not ids. Before the
  flag existed there was no route at all — and `service call`'s help line for a device target that
  reached nothing suggested `entity list --search <device_id>`, which answered
  `0 registry entries found` every time it was run. Suggestions have to be runnable; that one was
  checkable with no server at all.
- **There are two device resolvers and the split is the flag's contract, not an oversight.**
  `resolve_device` takes an id and nothing else, because `entity list --device` is declared
  `<device_id>` and substring-matching an opaque hex id is the accident above. `resolve_device_ref`
  is the `<id|name>` form the `device` command's own `get` and `update` take: id first — so a device
  whose displayed name happens to be another device's id cannot shadow it — then the **displayed**
  name, `name_by_user` over `name`, because the integration's own name for a device somebody renamed
  is a name nothing shows. Two devices sharing a displayed name is `AMBIGUOUS_DEVICE`, exit 1, for
  the same reason it is on an area.
- **A device has two names and only one of them is writable, and the flag has to say so.**
  `config/device_registry/update` accepts `name_by_user` and not `name`: the integration owns `name`
  and nothing can change it. So `device update --name` writes the override, `--clear-name` falls
  back to the integration's name rather than to blank, and `device get` reports `name`,
  `name_by_user` and a `name_source` naming which of the two is showing. `displayed_device_name` in
  `_common.py` is the one site of the `name_by_user or name` precedence — `device_name_map` is built
  on it, so entity name composition and every device row read the same rule.
- **A `default_sub` must not swallow a mistyped subcommand name.** `device` is the only command with
  a default sub *and* others, which is the shape where the hazard exists: `hass-axi device updat X`
  used to fall through to `list` and report `unexpected argument 'updat' for \`device list\``, naming
  a subcommand nobody typed and sending the reader after an argument mistake instead of a spelling
  one. `cli._pick_sub` hands the default sub a bare leading token only when the default sub declares
  a positional to hold it (`ws <command>`, `api <path>`) or when the command has no other sub;
  otherwise it is `UNKNOWN_SUBCOMMAND`. `hass-axi device` and `hass-axi device --fields …` are untouched.
- **Adding a WebSocket command** is one entry in `REGISTRY` in `src/hass_axi/ws.py`. It becomes
  reachable through `hass-axi ws <name>` immediately; a typed subcommand is optional on top.
- **`--json` is the global output mode.** Command flags carrying JSON payloads are named
  `--data-json` and `--params-json` so no precedence rule is needed. The output mode is decided by
  a pre-scan of the whole argv before parsing, so a usage error still honours it.
- **REST service targets go flat.** `entity_id` / `area_id` / `device_id` sit at the top level of
  the body; the REST endpoint hands the body to the service as its data and never unwraps a
  `target` key, so a nested one is rejected as an extra key. (The WebSocket `call_service` command
  *does* take a nested target — the shapes genuinely differ.) The REST test double enforces this,
  so a client that only agrees with itself cannot pass.
- **Exit codes follow one rule.** A static invocation problem — unknown flag, unknown subcommand,
  unknown WebSocket command name — exits 2. An outcome of a lookup against live state — no such
  area, an ambiguous area name — exits 1. Put a new error on the right side of that line. A
  read-only refusal is a 2 by that same rule and not by a new one: it is decided without touching
  the installation, and no argument to the same command changes the verdict. It is `AuthFailed`
  that shows why the line is drawn where it is — a 401 is exit 1, because only the server could
  have said it.
- **`--help` obeys value consumption.** `_help_requested` skips the value of any declared
  value-taking flag, so `template render --template --help` renders the literal.
- **Home Assistant refuses a service call with an *empty* 400.** `APIDomainServicesView.post`
  raises `HTTPBadRequest` from the underlying `ServiceNotFound` or `vol.Invalid`, and aiohttp
  renders the status line with no JSON body. An unknown service, an undeclared field and a missing
  required one are therefore indistinguishable on the wire. Everything `service call` says about a
  refusal comes from the model instead, read in `_explain` — which is why that path must never be
  made to depend on the message text or the status number. **And the status number is not even
  always 400:** the two refusals that come from a `HomeAssistantError` rather than a `vol.Invalid` —
  a named entity lacking a capability, and a `--response` call that matched nothing — arrive as a
  plain-text `500` with a fixed apology for a body. Three distinct statuses, none of them carrying a
  reason. A client that switched on either would be right by accident at best.
- **The one message that must never be printed verbatim.** A response-only service refused without
  `?return_response` comes back with Home Assistant's own wording, naming a query parameter of its
  REST API that an agent driving this CLI cannot set. `_explain` answers that case *before* it
  reads the model, because it is knowable without one and the leak must not survive a failed fetch.
  The flag is `--response`, in both directions.
- **`supported_features` is published as integers, and the list is a disjunction.** Home Assistant
  resolves the enum names in `services.yaml` before publishing, and its own rule is
  `any(features & mask == mask for mask in masks)` — any one mask, but every bit of that one. That
  disjunction is how an upstream fallback is encoded: `media_player.volume_up` publishes VOLUME_SET
  *and* VOLUME_STEP because core backs a player that cannot step with one that can set. Reading the
  list as a conjunction would gate exactly the behaviour that works today, which is the A11 caveat
  in the maintainer's tool-design guide. `axi_toolkit.ha.services.satisfies` is that rule and
  nothing else.
- **A capability requirement is only read for the service's own domain.** `reolink.ptz_move`
  targets `button` entities and names a `camera` feature; checking a button against a camera's bits
  would refuse every call. `feature_masks` returns nothing unless the published entity filter names
  exactly the service's own domain, and nothing if a value did not resolve to an integer.
- **The capability gate is a pre-check for area and device targets only.** Home Assistant refuses
  an entity *named outright* that lacks the feature, but skips one reached through an area or a
  device in silence — 200 with an empty list, and nothing said. An `unavailable` entity is skipped
  just as silently however it was named — its capability is never read — so neither the pre-check
  nor the failure-path enrichment blames it for lacking one. The pre-check covers the silent half,
  and the loud half is enriched on the failure path where it is free. `--no-check` exists because
  a published requirement is an integration's claim about itself, and a wrong one must not become
  a wall.
- **An empty change set is two different answers.** Home Assistant returns the states that actually
  changed, so `[]` means both "everything was already as asked" and "nothing was reached at all" —
  and it never says which. `service call` resolves the target when, and only when, the change set
  is empty and a target was given: reaching nothing exits 1, reaching something exits 0 with the
  count. Which domains a service can reach is read from its published `target`, never guessed from
  its name — an integration is free to act on another domain's entities, and several do.
- **`--response` turns the empty-change-set question into a 500.** The two-worlds answer above is
  only reachable on the success path, because reaching nothing is a `200 []`. With `return_response`
  set it is not: `helpers/service.py` raises
  `HomeAssistantError("Service call requested response data but did not match any entities")` after
  filtering candidates by availability, device class and feature, and aiohttp renders that as a
  **bodyless 500** — the one command shape whose failure carries nothing at all to read. So
  `_explain` re-derives the same verdict from the target, last, after every check that explains the
  refusal from what was *sent*; `_no_entities_targeted` is shared with `_report_target` so the two
  sides of one outcome cannot be phrased differently. In 0.3.2 this fell through to `_generic_help`
  and offered help about *fields*, which were never the problem.
- **`unavailable` and `unknown` are different facts and the home view counts them apart.** `unknown`
  means reachable and not yet reporting, which is the common one — a live instance had 12 `unknown`
  and 0 `unavailable`. Summing them under the name of one of them made the home view contradict
  `state list --state unavailable` outright.
- **A 404 that carries a message is not a wrong path.** aiohttp answers an unrouted path with
  plain-text `404: Not Found` and no body; a routed path whose *subject* is missing answers in JSON
  — `/states/<id>` says `Entity not found.`. `rest._http_error` quotes the message when there is one
  and only says `no such API path` when there is not, because telling an agent the path is wrong
  sends it looking for a spelling mistake that is not there.
- **A diagnostic read must not fail a call that worked.** The target report needs the registries,
  which the REST-only path cannot supply. If that read fails, the report says so and the exit code
  stays 0: the call itself was accepted, and turning it into an error would be a fresh untruth.

### The `websockets` API span, and why the floor is unbounded on purpose

`websockets` is the only runtime dependency that carries a transport, it makes breaking API changes,
and the declared range is `>=13.0` with no ceiling. That combination is safe **only** because the one
call into it is written in the form the library supports across the whole range, and it is worth
writing down what that form is, because the obvious spelling is the wrong one.

**`connect()` is entered, never assigned.** `WsClient.connect` holds the entered context in a
`contextlib.ExitStack` that `close()` unwinds, because the connection has to outlive the call that
opens it — a `with` block would close it on the way out. The three states of the library:

| release | what `connect()` returns | assigning it |
| --- | --- | --- |
| 13.0 – 16.x | the `ClientConnection`; `__enter__` is `return self` | works |
| 17.1 | the `ClientConnection`, marked | `DeprecationWarning` on the first `send`/`recv` |
| after the flip | a `reconnect`; `__enter__` connects and returns the connection | no `send`/`recv` at all |

Entering is correct in all three, so there is no version sniff and no `try`/`except` here to go
stale. **`legacy=True` is not the portable answer** — the parameter does not exist before 17.1,
where it reaches `socket.create_connection()` through `**kwargs` and raises `TypeError:
create_connection() got an unexpected keyword argument 'legacy'`. And **no upper bound was added**:
a cap would pin every user behind current `websockets` to solve a problem the call form already
solves, and the forward claim is measured rather than promised —
`test_the_client_works_against_the_future_websockets_api` asks the real 17.1 library for
`legacy=False`, which *is* the post-flip behaviour, and drives a real command through it.
`test_the_connection_is_entered_and_not_merely_assigned` states the same rule without naming a
version, so it guards the class of defect on every release including the ones that predate it.

**Why only two legs of the matrix went red, and why that is the confusing part.** `websockets` 17.0,
17.0.1 and 17.1 wheels declare `requires-python >=3.11`. A Python 3.9 or 3.10 leg therefore resolves
16.1.1 and cannot see the change at all, so the failure presents as "3.11 and 3.12 are broken",
which reads like a Python incompatibility and is not one. Before concluding anything about a version
of Python from this matrix, print the resolved dependency on each leg. For the same reason a local
run on one interpreter proves nothing here: the four legs resolve two different libraries.

**The nightly and `filterwarnings` are the early-warning system, and they earned their keep.** The
nightly `schedule` in `ci.yml` re-ran the matrix against freshly resolved dependencies on an
unchanged commit — while GitHub Actions was enabled; it is disabled now, so nothing re-runs it on a
schedule and `scripts/ci-local.sh --matrix` is the by-hand equivalent — and `filterwarnings = ["error::DeprecationWarning:hass_axi.*"]` in `pyproject.toml`
promotes a deprecation raised *through this package* into a failure. Together they converted a
future hard break into a red build on the day upstream published, on a commit that had not changed —
which is the signature to look for: **green then red on the same SHA is an external release, not a
regression.** Do not relax that filter to quiet a third-party deprecation; it is scoped to
`hass_axi.*` precisely so it stays sensitive without being fragile. Note also what it does *not* mean:
a `DeprecationWarning` is ignored by Python's default filters, so the released CLI kept working for
ordinary users while the suite was red. The suite failing is the point — it is the notice, not the
outage.

## The error taxonomy

Every failure carries a `code` and a `class`. The code names the one thing that went wrong; the
class names the kind of thing it is, and the class is what a caller switches on before it can decide
whether to retry, change the arguments, or fetch a different token. Both are printed by
`cli._error_document`, and the class is **derived** from the code through `errors.CODES` rather than
declared a second time at each raise site — one vocabulary read two ways, so the two cannot drift.

**The vocabulary is closed, and that is the whole mechanism.** `errors.CODES` is every code this
tool can print, mapped to its class. Two places used to mint codes from whatever a server said —
`f"HTTP_{exc.code}"` in `rest._http_error` and `error["code"].upper()` in `ws._command_error` — so
the published vocabulary was "the set of HTTP statuses" plus "anything Home Assistant might ever
name". No caller can switch over that exhaustively, and no table can ever claim to be complete
against it. `tests/test_error_codes.py` sweeps the source with `ast` and fails on a `code=` that is
**built** rather than named: an f-string, a concatenation, a method call. A bare name is allowed,
because `ws._command_error` genuinely has to hand one along, and the one table it reads —
`ws.WS_ERROR_CODES` — is pinned separately. The same file fails on a declared code that nothing
raises, so an entry that has outlived its cause surfaces here rather than in a caller's `match`.

**`errors.CODES` and the README's error-code section are pinned to each other.** `documented` means
documented: the section between the `<!-- error-codes:start -->` markers is parsed and compared for
equality, the way the generated skill is compared against the command table. Do not put a backticked
all-capitals token inside those markers that is not a code — the check reads them all.

**The classes are eight, and the two people get wrong are `auth`/`permission` and
`transport`/`refused`.**

| class | what happened | what to change |
| --- | --- | --- |
| `usage` | the invocation is wrong; nothing was sent | the command line |
| `config` | this machine is not set up | the environment |
| `transport` | not reached, or reached and not serving | nothing — retry |
| `auth` | the credential was rejected | the token |
| `permission` | the credential was accepted; the caller is not permitted | the account, or a block on the instance |
| `not_found` | the subject does not resolve to one thing that exists here | what you asked for |
| `refused` | the subject exists and this request was refused | the arguments |
| `internal` | a bug in hass-axi | nothing; report it |

`fault_class` fails closed to `unclassified`, which is deliberately **not** a member of `CLASSES`:
it is the absence of an answer, it is unreachable while the sweep passes, and it exists so that a
code that escaped the sweep says so out loud instead of being filed under a class it does not belong
to. Guessing the class from the exception type is the tempting alternative and it is wrong — one
`ConnectionFailed` is a missing Python package (`config`) and another is a dropped socket
(`transport`).

**Enforcement is at the transport boundary, never in a command body** — `rest._http_error` /
`rest._url_error` and `ws._connect_error` / `ws._command_error` — for exactly the reason the
read-only gate enforces at `RestClient.request` and `WsClient.send_command`: a command added later is
classified whether or not its author knew the taxonomy existed. `tests/test_error_codes.py` runs
**every** subcommand from `cli._MODULES` against **every** fault on **both** transports and fails on
the first one that cannot name its class. `INVOCATIONS` there is maintained by hand, because only a
person knows what arguments a command needs, but it is never *enumerated* by hand: it is reconciled
against the dispatch table, and `LOCAL_ONLY` names the subcommands that reach no transport, so
"it does not touch Home Assistant" is a claim somebody made rather than a gap nobody noticed.

### What Home Assistant actually returns, and why each split is a fact rather than a preference

Every mapping below was read out of `home-assistant/core` at 2026.8.3 rather than guessed, which is
the same rule the doubles are held to.

- **401 and 403 are not one answer.** 401 is a bare `HTTPUnauthorized` from `helpers/http.py` when a
  view requires auth and the request has none — a rejected credential, fixed by a new token. 403 is a
  bare `HTTPForbidden` from `components/http/ban.py`, raised by a **middleware** for an address in
  `ip_bans_lookup`, before any view reads the token. Telling an agent to mint a token there sends it
  to fail another login against an instance that already banned it, which is how the ban got deeper.
  `UNAUTHORIZED` and `FORBIDDEN`; `auth` and `permission`.
- **`unauthorized` over the WebSocket is a permission, not an auth failure.** It can only arrive
  *after* `auth_ok`: `connection.async_handle_exception` maps `exceptions.Unauthorized` to it, which
  is what `@require_admin` raises for an account that is not an administrator. The token is valid.
  A new one for the same account changes nothing. `auth_invalid` during the handshake is the auth
  failure, and it is the only one on that transport.
- **`unknown_command` is a `not_found` with a code of its own.** Home Assistant answers
  `{"code": "unknown_command", "message": "Unknown command."}` — a fixed string; the type it did not
  recognise goes to `logger.info` and never onto the wire. Uppercased, that became `UNKNOWN_COMMAND`,
  which is already what this CLI calls a command *it* does not have: one string for "read `--help`"
  and for "this Home Assistant version cannot do that", which are opposite next moves. It is
  `NO_SUCH_WS_COMMAND` now, and the collision is what the split exists to remove.
- **502, 503 and 504 are transport, not refusal.** `helpers/http.py` answers every request with
  `web.Response(status=SERVICE_UNAVAILABLE)` while `hass.is_stopping` — a plain response, so not even
  aiohttp's `"503: ..."` line, nothing to read at all — and a proxy in front of a restarting instance
  answers 502 or 504 for the same window. The request was never seen, so retrying it unchanged is
  correct, which is precisely what `transport` means and what `refused` would tell an agent not to do.
  500 stays `refused`: it is what a `HomeAssistantError` renders as, and the request was seen.
- **`id_reuse` is `internal`.** This client mints its own ids, so only a bug here can produce it.
  That the class is not simply a relabel of the server's code is the point of having one.

### The two transports had disagreed about one fault, and that was the quiet half of the defect

`ssl.SSLError` and `socket.timeout` are both `OSError` subclasses, so `ws.connect`'s
`except (TimeoutError, OSError)` reported a certificate this machine will not accept, a host that
never answered and a refused connection all as `WS_UNREACHABLE`, while REST called the same three
faults `TLS_ERROR`, `TIMEOUT` and `UNREACHABLE`. An agent that learnt the vocabulary on one transport
was wrong on the other, for faults with one cause and one fix each. `ws._connect_error` now reaches
the same three codes, and which transport met the fault stays in the *message*, where it belongs,
because it is not what the caller has to change. `WS_HANDSHAKE` survives as the fault genuinely
specific to this transport — the TCP connection was made and the HTTP upgrade was refused, which is
what a proxy that does not forward WebSockets does — and a refusal that carries a status is
classified by it, so a 404 at the upgrade is `NO_WEBSOCKET_API` rather than a vague handshake
failure. The status is read by attribute (`.response.status_code`, then `.status_code`) rather than
by catching `websockets.exceptions`, because `InvalidStatus` replaced `InvalidStatusCode` and this
project declares a floor rather than a pin: a version bump must not silently downgrade a classified
failure to an unclassified one.

**A timeout is one fault however the exchange ran out of time.** urllib wraps an `OSError` raised
while *sending* into a `URLError` (`AbstractHTTPHandler.do_open`), so a connect timeout arrived at
`_url_error` and was reported as `UNREACHABLE`, while a timeout waiting for the *response*
propagates as a bare `TimeoutError` and was reported as `TIMEOUT`. Which half ran out is not
something a caller can act on.

### The two views that reported a failure with no code at all

`home` and `doctor` catch `AxiError` and fold it into their own document rather than letting it
reach `cli._error_document`, so neither printed a code. `home` is the live-state view an agent asks
for once it has a reason to — the most-read error surface the tool has, the session hook's
`context` document being one that cannot fail — and the one that could not be classified. Both
carry `code` and `class` now: `home` at the top level beside `live_state: not available`, at exit
0, and `doctor` on the failing check row, which is
what makes the case a reverse proxy actually produces — REST answering
and the WebSocket upgrade refused — two readable facts instead of one unhealthy instance. A failing
`doctor` therefore renders its `checks` block in **list** form rather than tabular, because the rows
stop being uniform; a healthy one is still tabular and `test_doctor_still_answers_healthy_in_tabular_form`
pins that.

### Fixtures for this live in the doubles, and they were too generous

`FakeRestServer` answered 401 with `{"message": "Unauthorized"}` — a field no real instance sends —
and modelled neither 403 nor 503 at all. It now answers 401 and 403 with aiohttp's own plain-text
status line, 503 with nothing whatsoever, and applies them in Home Assistant's order: the ban
middleware, then `is_stopping`, then the router, then the token. `forbidden`, `stopping` and
`unrouted` are the switches; `FakeWsServer.fail_all` is the WebSocket equivalent, persistent rather
than one-shot because that is the shape the interesting faults have — a non-admin token is refused by
every `@require_admin` command it ever sends. `tests/test_double_fidelity.py` asserts each of those
shapes, so a fixture edit that tidies one away fails where the reason is written down.


## The read-only gate

`HASS_AXI_READ_ONLY` makes a session incapable of changing anything. The design is written down here
because a guard that covers most of the write paths is **worse than no guard** — it converts an
understood risk into a false assurance about the paths it missed, and the operator who sets it is
the one who finds out. The comparable upstream CLI ships a read-only flag covering part of its
surface; that is the failure this is built not to repeat.

**It is a variable and never a flag, and it is a switch and never a boolean.** A flag is omitted by
exactly the caller that most needs it — an agent composing a command line does not know the
operator wanted a safe session. `readonly.enabled` treats any value that is set and not blank as on,
`0` and `false` included, and blank as unset, matching how `config._first_env` reads everything
else. Do not teach it to parse the value: one unrecognised spelling or one case that was not folded
is a guard that is off while an operator believes it is on, and being wrong in that direction is
the only outcome that is not survivable. Being wrong in the other direction — `HASS_AXI_READ_ONLY=false`
refusing a write — is loud, immediate and fixed by unsetting the variable.

**Every subcommand and every WebSocket command carries an explicit classification, and the default
is a write.** `Sub.access` and `WsCommand.access` both default to `None`, which is an *absence*
rather than a value: `readonly.verdict` reads anything that is not exactly `READ` as a write, so
forgetting refuses rather than mutating, and `tests/test_read_only.py` enumerates both tables from
`cli._MODULES` and `ws.REGISTRY` and fails on the first declaration that has none. That sweep is
the deliverable — the guard is whole because of it, not because anybody remembered every command.
Nothing is inferred from a name or an HTTP verb: `service call` mutates through a surface that
looks like any other POST, `template render` is a POST that changes nothing, and the WebSocket
command set does not follow REST conventions at all.

**`DYNAMIC` is a fourth answer, not a hole.** Five subcommands carry their subject in their
arguments rather than in their declaration — `api`, `ws`, `service call`, `setup skill` and
`setup hooks` — so they declare
`DYNAMIC` and their module exposes `access(sub, parsed)`. A module that declares `DYNAMIC` and
supplies no resolver is unclassified in a costume and `cli._access` treats it as a write; a test
asserts every `DYNAMIC` sub has one. `wscmd` resolves the *type* through the same `_resolve` that
`run` dispatches on, deliberately: two readings of the same arguments are how a gate comes to guard
a different command from the one that runs.

**There are three enforcement points and the last two are the ones that make it hold.**

| point | what it knows | why it exists |
| --- | --- | --- |
| `cli.main` → `cli._access` | the declaration and the parsed arguments | names the command, refuses before any transport or even `config.load` runs |
| `rest.RestClient.request` | the method and the resolved path | every REST call passes through it |
| `ws.WsClient.send_command` | the API type | every WebSocket command passes through it, and it refuses *before* `connect()` |

Enforcement is at dispatch, **never in a command body**: a new command is guarded whether or not
its author knew there was a gate. The transports are not belt-and-braces either — a classification
is a claim a module makes about itself, and `test_a_command_classified_read_still_cannot_write`
pins that a module claiming `READ` and posting anyway is still refused. A guard on one transport
only is the partial guard above, so both refuse of their own accord.

**Where a verb *is* read, and why that is not a contradiction.** `hass-axi api` hands an opaque path
straight to the installation, so the method is the only fact the caller supplied.
`rest.access_for_request` errs closed on it: `SAFE_METHODS` pass, everything else is a write —
including a POST that happens not to change anything. `READ_ONLY_POSTS` names the single exception,
`/api/template`, because Home Assistant's template sandbox cannot call a service or set a state and
refusing the tool's most useful read by its own verb would be a guard nobody keeps switched on. The
residual risk is stated in the README rather than hidden: a Home Assistant endpoint that mutated on
a GET would pass, and none does. `ws --raw` is judged by `ws.access_for_type`, which scans
`REGISTRY` live rather than a map built at import — so a command added by a later release, or by a
test proving the fail-closed default, is classified by the same rule as every other, and a type no
declaration names is a write.

**`setup` writes to this machine rather than to Home Assistant, and counts as a write anyway.** The
variable says this tool does not write; splitting that into "not your house" and "not your
dotfiles" is a distinction nobody asked for. `setup skill --check` and `setup hooks status` are
the read half, which is what `DYNAMIC` buys there.

**Refused commands stay visible, and that is the opposite of the sibling project's playback gate —
deliberately.** There the capability was hidden from help and from the command table, because a
second tool offered the same capability and an agent that could see both could pick the wrong one;
invisibility was the point. Nothing else here reaches Home Assistant, so there is no wrong tool to
pick, and an agent that cannot see `entity update` cannot work out why its plan is impossible — it
reads a missing command as a missing feature and starts inventing routes around it. Visible and
refused is right for this gate; hidden is right for that one. The two are not an inconsistency, and
this paragraph exists so nobody "fixes" one to match the other.

**The refusal carries its own code.** `READ_ONLY`, exit 2, distinct from `UNAUTHORIZED` and from
every transport failure, because "this session forbids writes" and "that server refused you" have
different fixes and an agent that cannot tell them apart retries the wrong one. `doctor` reports the
mode as its first check — the only check needing neither configuration nor a connection — and the
home view and the `context` document a session hook prints both report `read_only: on` when it is
set and stay silent when it is not, because an unset switch is not worth the tokens.

**Verified against a live installation, not only against the doubles.** Both transports were
refused live and both worked again with the variable unset; the area registry was counted before
and after to prove the refused create reached nothing. The recipe is the one in "Build, test, lint"
below — credentials fetched per command, never stored — and the suite itself stays offline.

## The session-hook installer

`hooks.py` writes into files nobody in this project owns: the user's `~/.claude/settings.json`,
their `~/.codex/hooks.json` and `~/.codex/config.toml`, and an OpenCode plugin directory. That is what makes its failure mode
different from every other module here — a mistake does not produce a wrong answer, it damages a
file the user has to repair by hand — and all three rules below were paid for by a defect that
shipped in 0.5.1 and was found in the sibling AXI CLI, which had been given this design to port.

**Ownership is a key this installer writes, never a substring of the command.** An entry it wrote
carries `managed_by: hass-axi`, and `_managed_hook` is the single construction site so what is written
and what is claimed cannot drift. Matching `"hass-axi" in command` claimed hooks this tool never wrote
— a user's `env HA_URL=… hass-axi`, another interpreter, a shell wrapper — and silently rewrote them
out of the user's own global settings while reporting the target `installed`. The marker also has to
travel *with* an entry rather than be re-derived, because a path repair changes the command string
by definition: ownership decided from the command cannot survive the operation the installer exists
to perform.

**The one divergence from the sibling, and it is about release history rather than about the two
products.** There the marker key is the sole test of ownership, because no release of that tool had
ever written a hook and an unmarked entry is therefore necessarily a user's. Here every release up
to 0.5.1 wrote an unmarked one, so the same rule would append a *second* hook beside it on every
machine that had followed the README — manufacturing exactly the duplicate the scan fix below
exists to collapse. So `_is_unmarked_own_entry` adopts an unmarked entry, in the one shape those
releases could produce: `Path(command).name in BINARY_NAMES`, which is `current_executable()`'s
whole output and nothing else. Every wrapper shape fails it — a prefix or another interpreter
leaves extra tokens in the string, and a wrapper script has its own basename. Adoption is one-way
and happens
once; the entry gains the marker on that install and is matched by it forever after. Delete this
rule only when no installation predating the marker can plausibly remain. The basename it matches is
`LEGACY_BINARY_NAMES` — `ha-axi`, the only name those releases were published under.

**The rename added a second adoption rule, and a marker is not enough for it.** Releases 0.5.1 to
0.7.x marked their entry `managed_by: ha-axi` and ran `ha-axi context`, an executable that is gone
once the package is renamed. `_is_renamed_own_entry` adopts it and rewrites it. But the unrelated
tool published as `ha-axi` writes `managed_by`-style markers with **the same string** and runs `ha-axi
ping --ambient`, so the predicate also requires the command to be exactly what this tool wrote —
one executable whose basename is `ha-axi`, then `context`, nothing more. The OpenCode plugin is
retired the same way: `axi-ha-axi.js` is deleted only when its first line is this tool's old header
in full, because the other tool writes a plugin to the same path whose header starts with the same
words. `tests/test_hooks.py` holds both halves: ours adopted, theirs untouched.

**The scan covers every group and every entry, and collapses the extras.** It used to `return` at
the first managed entry, so an already-correct first entry ended it and a second one pointing at a
dead path was never repaired — while every later `setup hooks` reported the target `current`, which
is the opposite of what `setup --help` and the README promise about repairing a path after a move.
Two entries are ordinary: a restored backup, a hand repair, a partial earlier install. `changed`
therefore accumulates across the whole sweep instead of deciding the return at the first hit.

**`compute_codex_config_update` rewrites the `hooks` key whatever its value, and returns a
`problem` when it cannot.** Recognising only a bare `hooks = true|false` let `hooks = "true"` and
`hooks = 1` fall through to the append at the end, which wrote a *second* `hooks` key into the same
table — a duplicate key, which TOML rejects outright. The tool broke the config it was configuring
while exiting 0 and reporting `installed`, and the damage surfaced the next time Codex started
rather than in any output this tool produced. `[[features]]` is the case where there is no correct
edit at all — a key beside an array of tables lands inside one element and enables nothing, and a
`[features]` table declared beside it is refused — so the third element of the return carries a
refusal and `_install_codex_features` reports `skipped` with the file byte-identical. A target that
only looks installed is the failure this whole section is about.

**The hook runs `hass-axi context`, never the bare executable.** The no-argument home view is live
state: it needs a credential, opens a connection and prints the installation's address. That is the
right answer to "show me this installation" and the wrong thing to run at session start, because it
has nothing to show the machine ambient context exists to reach — the one that has the package and
has never been pointed at Home Assistant — and it pays a round-trip and prints an address on every
session, into a channel that is logged and transcribed.

**The home view exits 0 whatever it finds, and names the fault anyway.** With nothing configured,
or with an installation that does not answer, it prints `live_state: not available`, the `code` and
`class` of what stood in the way, the command names and the help lines, and exits 0: a bare run asks
what is here, and "nothing yet" is an answer. A caller that has to tell configured from not reads
`code`, and `ping` and `doctor` are the commands whose exit code reports it. `context` asks a
different question — describe this installation without connecting to it.

**What `context` may cost, and what it may say.** It loads on every session, so
`CONTEXT_BUDGET_BYTES` in `tests/test_hooks.py` asserts the ceiling rather than intending it: a line
added without thinking about the cost fails there instead of being paid forever by everybody who
installed the hook. Two content rules fall out of the same place it is printed. It reports *which*
variables are set and never what they hold, because hook output is a wider surface than a terminal
rather than a narrower one. And every scalar in it is written without a colon, a comma or a bracket
— the document is TOON, which quotes a scalar holding any of those, and a pair of quotes on a line
of prose is noise bought at the start of every session; this is the same rule `home.DESCRIPTION` is
held to, and `test_the_context_document_never_pays_for_a_quoted_scalar` is what keeps it.

**The two views are not redundant and the split is the point.** `context` is what an agent is *told*
at session start, from the environment alone; the home view is what an agent *asks* once it has a
reason to. A fact that needs a connection belongs in the home view and nowhere near this one.

**Status and removal reuse the install rules in reverse, and every subcommand reports the same
rows.** `status` writes nothing: each JSON target is `installed` (current), `stale` (this tool's
entry recording another executable, a duplicate, or one an earlier release wrote — what `setup
hooks` repairs), `missing`, or `unmanaged` for an OpenCode plugin at this tool's path that this
tool did not write. `remove` deletes only what the ownership rules above claim — a marked entry,
or one of the two adopted shapes — and reports rather than deletes an unmanaged plugin, and it
leaves Codex's `[features] hooks = true` on because every other tool's Codex hooks depend on it:
the key is not this tool's to switch off. One settings file holds both of an agent's hooks and is
rewritten once, and install, status and remove each report **both** rows for it, so a missing row
reads as a fault rather than as nothing installed.

**The session-end capture is the same boundary with the same rules.** `hass-axi context end`
records one entry per session — the directory, the date, and how many times each command ran,
with a count of the ones that carried `--write` — and `context` reads the entry for its own
directory back into the document it prints; `sessionlog.py` is the module and its Architecture
bullet above carries the privacy rule. A read-only session records nothing and says so: the
switch says this tool does not write, and the record is a write, so `context end` is classified
`READ` and honours the switch inside instead — the one arrangement under which a read-only
session still closes cleanly. Every failure — no payload, an unreadable transcript, an
unwritable state file — records nothing and says so, because a hook that failed would be
reported as the session failing to close. `setup hooks remove` deletes the file with the hooks.

## The command contract

`hass-axi` reaches every service through `service call` and every WebSocket type through `ws --raw`.
Coverage is already complete, so a typed command is never justified by reach. What the typed
commands add is judgement, and judgement is the one thing a generator cannot emit — measured:
Home Assistant's model is complete enough to generate every flag (99% of 1,939 declared fields
carry a typed selector) and structurally incapable of generating the checks that stop the bugs,
because the requirement Home Assistant actually *enforces* is the `required_features` argument to
`async_register_entity_service`, which 79 entity services pass and none export.

Two things are easy to confuse here, and the gate depends on telling them apart. The enforced
requirement lives in Python and is invisible. A **separate** declaration, `target.entity[]`'s
`supported_features` in `services.yaml`, *is* published — 91 services in the bundled catalogue carry
one, `media_player.media_next_track` among them — and Home Assistant resolves it to integers on the
way out. That published declaration is what the pre-check reads. It normally mirrors the enforced
one, but it is an integration's claim about itself and the two can disagree, which is the whole
reason `--no-check` exists.

**Do not generate commands from the service model.** At this scale it would mean 77 nouns and ~327
subcommands where there are 11 and 21, roughly 30× the `--help` budget, `light turn_on` colliding
with `service call light.turn_on` for every service, and 19 flags on one subcommand of which 17 are
conditional on capabilities nothing checks. Consuming the same model to *validate, explain and
recover* has none of those costs and is what `axi_toolkit.ha.services` is for.

**The promotion rule.** A service becomes a typed command only when the command would do something
`service call` cannot — and that something must be named in the PR. In the order to check it:

1. **It crosses transports or registries.** The answer needs the WebSocket registry as well as
   REST, or needs the device-area fallback. (`state list --area` is the existing instance.)
2. **It needs a capability check before dispatch,** and Home Assistant publishes no fallback for it.
3. **It needs a state-aware no-op** — the idempotent "already matches the requested values" answer,
   which is only possible by reading current state first.
4. **Its failure needs candidates** from an open, installation-specific set — `source`,
   `sound_mode`, `effect`, `preset_mode`, `hvac_mode`. These live in the entity's *attributes*
   (`source_list`, `effect_list`, `hvac_modes`), not in the service schema.
5. **It needs a shaped result** — a derived summary rather than a change list, the way
   `entity update` answers with the resulting registry row.

If none of the five applies, it does not get a command: `service call` already covers it, and
adding one is two spellings for one operation.

**Open, and deliberately not answered here:** `service get`'s default field list is
`field,required,type,description`, and `description` is empty on **every row of a real
installation** — the prose moved into the translation files years ago and `/api/services` does not
serve them, while `example` (published by more than half of all fields) and the
`filter.supported_features` marker are not shown by default. The double now publishes a service and
fields with no prose, so the emptiness is visible in the suite rather than being a surprise. Changing
the default is a judgement about what an agent most needs to see, not a defect, and it belongs in its
own change with its own argument.

**And whichever way that goes, the new subcommand declares `access`.** It is not optional and it is
not inferable; see "The read-only gate" above.

**Demotion, and the standing cap.** If a typed command's body reduces to flag-mapping plus a
request, delete it — the measure is the diff, not the intention. Eleven nouns once fit in a root
help block an agent reads in one glance, and every one after has had to argue that it earns its
line. `context` earned its own by being the thing a hook can safely run, which no existing noun was
— see "The session-hook installer". The five added with the rename (`sensor`, `history`, `logbook`,
`statistics`, `ping`) close named gaps against the other `ha-axi`, and each argues by the promotion
rule rather than by reach: `sensor` crosses transports on every run (1); `statistics` reads the
recorder's metadata to choose what to ask for and reports a derived summary with caveats (5);
`history` and `logbook` answer with derived summaries — time in each state, one folded `cause` —
rather than the raw rows (5), and reach a recorder no existing noun reads; `ping` is the one
authenticated round-trip a liveness gate needs, which `doctor`'s four checks are not. Sixteen is the
ceiling this argument supports; the next noun has to displace one or fold into one. `--data key=value`
stays first-class in every case, because it reaches every field of every service forever with no
metadata to go stale.

**Never validate an argument against metadata you cannot refresh.** Undeclared flags are rejected by
name, so stale metadata converts a valid operation into a hard failure with a "valid flags" list
that is wrong. Either read the model live at the moment you enforce it — as `service call` and
`service get` do — or do not enforce it and let the value through to Home Assistant, which owns the
schema. This is also why the model is never cached: an integration added or removed rewrites it and
nothing signals when.

## The recorder reads

`sensor`, `history`, `logbook` and `statistics` read what the recorder and the registries hold, and
every rule below was read out of `components/history`, `components/logbook` and
`components/recorder` at 2026.8.3 rather than guessed. The doubles in `tests/conftest.py` transcribe
the same views, and `tests/test_double_fidelity.py` pins the shapes that matter.

- **A history or logbook window with no `end_time` ends one day after its start, not now.**
  `HistoryPeriodView` and `LogbookView` both default the end to `start + 1 day` (the logbook to
  `start + period` days). A client that omits it and asks for `--start 7d` gets the first day only,
  with no error. `_window.window` therefore always produces an end, and `rest.history`/`rest.logbook`
  always send it. The other `ha-axi` omits it; do not copy that.
- **History answers in request order, one list per entity, empty where nothing was recorded.**
  `_sorted_states_to_dict` seeds the result with every requested id before filling it. With
  `minimal_response` only the first row of each list is a whole state; the rest are `state` and
  `last_changed`. The first row is the state already held when the window opened, timed at the
  window's start — so it counts for the time it held and is not a change.
- **A statistic's kind comes from the recorder's metadata, never from a device class or a name.**
  `has_sum` means a meter; `mean_type` 1 an arithmetic mean, 2 a circular one (`has_mean` is the
  pre-`mean_type` spelling and is still read). Asking `recorder/statistics_during_period` for a type
  a statistic does not keep is not an error — the value comes back `None` — so `statistics get`
  groups ids by kind and requests `change,state` or `mean,min,max` per group. A statistic with no
  rows in the window is absent from the answer, not an empty list.
- **The total is the sum of `change`.** Not `sum[-1] - sum[0]`, which loses the first bucket, and not
  the live state. Values arrive in the display unit, which the recorder converts to.
- **Caveats state, and the number is never adjusted.** A negative `change` is a meter that went
  backwards and the total includes it. A drop in `state` in a bucket whose `change` is not negative
  is a *reset* — the recorder started a new cycle and carried the sum across it — and a reset about
  every 24 hours is called out, because it means the entity's live state is a since-reset reading
  rather than a running total. A drop booked as a negative change is the first case, not a reset.
  Missing buckets are counted before the first one and between any two, never after the last: the
  recorder compiles a bucket only once its period ends, so the newest is routinely not there yet.
  Every check is a rule about the buckets' shape. **No caveat may name an integration**: rules about
  a particular vendor's sensors belong to whoever runs that installation, not to this public tool.
- **Diagnostic sensors are set aside by `entity_category`, never by name**, and hidden ones too. A
  state with no registry entry is kept: absence of registry data is not a reason to exclude.
- **The home view's staleness is sensors only, by `last_reported`.** A reading is re-reported while
  its device is alive whether or not the value moved; an automation, a zone or a closed door reports
  only on change, and counting those would bury a dead sensor under every quiet entity.
  `state list --stale` is the generic form, any domain, and agrees with the home view's count for
  `--domain sensor` because both go through `_common.not_reported_for`.
- **A circular statistic (a bearing) reports a circular mean and no min or max**: across the 0/360
  wrap both lie. Daily and weekly buckets start at the installation's local midnight while the
  missing-bucket count aligns in UTC; that is safe only because every count rounds down whole
  periods, so keep it rounding down.

## What the README leads with, and why it is not a feature list

**The two things the tool is for are the registries and service-call judgement, and the README says
so before it says anything else.** `state`, `template`, `api` and `ws` are documented — they are
useful, they are load-bearing for the other two, and an agent that cannot find them will go looking
for `curl` — but they are *not* pitched, because every Home Assistant client has them and several
have them more thoroughly. A reader who judges this tool on them has no reason to pick it.

That ordering is a measured conclusion rather than a taste. A head-to-head against a comparable CLI
on a real installation found three **wrong answers at exit 0** on exactly the registry questions
this tool is built to get right — a device-inherited area invisible to the discoverable area
filter, a safety mode that did not hold on the transport where registry writes live, and a service
call that reached nothing reported as a success — plus no typed entity-registry write at all. Those
are the differentiators, and they are the ones that survived contact with a server. An earlier
README opened on "token-efficient structured output, discoverable subcommands, useful `--help`, no
interactive prompts, and a non-zero exit on every failure", a list on which the comparison scores at
least as well line by line, and buried the registry story in paragraph three. **Do not restore a
feature list to the top**, and do not promote `state`/`template`/`api`/`ws` back into the pitch; the
capability list further down is where they belong.

**Every `$ hass-axi …` block in the README is real output, and re-running them is part of editing
them.** An example that does not run is a defect. They are checked by running each block against a
throwaway Home Assistant in the order a reader meets them, from a known starting state, because the
blocks mutate the installation: the first one places `light.example_lamp` in `Example Room`, and
`area list` further down prints the counts that write produced. Two consequences:

- **The lab has to be shaped to the repository's synthetic vocabulary before anything is pasted.**
  Real output carries whatever names the installation has, and this repository may not carry an
  installation's names. Build the lab from the recipe under "Build, test, lint" and then *make* it
  say `light.example_lamp`, `cover.example_blind`, `Example Room`: modern `template:` entities with
  a `unique_id` (without one there is no registry entry to update at all, and the legacy
  `light: - platform: template` form is refused outright by current Home Assistant), and
  `entity update --new-id` plus `ws device.update --param name_by_user=…` for anything a core
  integration named. A template cover with only `open_cover`/`close_cover` publishes
  `supported_features: 3`, which is what makes the `cover.set_cover_position` capability refusal
  reproducible; an empty area is what makes `NO_ENTITIES_TARGETED` reproducible.
- **One line in the README is not reproducible and is the documentation placeholder instead**: the
  home view's `url:`, which is the reader's own base URL. The `bin:` line above it *is* real —
  `executable_path()` collapses `$HOME`, so running the console script from a throwaway `HOME`
  whose `.local/bin` holds the shim prints `~/.local/bin/hass-axi` exactly as a `--user` install does.
  Nothing else is substituted, and nothing else should be.

**A comma in `home.DESCRIPTION` costs a pair of quotes on every session start.** The home view is
TOON, so a scalar containing the delimiter is quoted — the description is printed into every agent
session by the `context` document the hook runs, and `description: "…"` there is noise for no gain.
Write it without commas. The same string is the root `--help` description line and the `SKILL.md`
body, neither of which quotes, so the constraint comes from the one reader that does.

## Build, test, lint

```sh
scripts/dev-setup.sh                     # creates .venv and installs this checkout into it
.venv/bin/pytest                         # ~1300 tests, a few seconds
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/hass-axi setup skill --check     # SKILL.md is generated, never hand-edited
```

**Never install this checkout into an ambient interpreter, and that is why the setup is a committed
script rather than a documented command.** This tool is normally installed as an isolated
user-level tool — `uv tool`, `pipx`, a `--user` install — with a launcher in `~/.local/bin` and its
own environment behind it. An editable install into whatever interpreter is on `PATH` **replaces
that launcher** with one bound to the ambient interpreter, and leaves an editable pointer
(`_editable_impl_hass_axi.pth`, plus a `.dist-info` whose `direct_url.json` records the checkout
path) in the user site. Deleting the checkout is the ordinary end of a throwaway clone, and it
leaves the reader's own installed command dead with `ModuleNotFoundError: No module named
'hass_axi'` — and nothing announces it. It has already happened: one AXI CLI was left completely
broken this way and a sibling was silently pinned two releases behind its published version, with
no symptom until somebody ran them. A contributor's checkout must not be able to break the
reader's installation of the tool they are contributing to.

`scripts/dev-setup.sh` is the single entry point, and it is a script rather than a line of prose
because a documented command is copied, edited and shortened while a script is run. It creates
`.venv`, installs `-e ".[dev]"` into it, and prints the `.venv/bin/<tool>` forms — the same
directory and the same invocation style `.github/workflows/ci.yml` uses for the same reason (see
"Continuous integration" below: a venv sets `ENABLE_USER_SITE = False`, so neither a job nor a
developer can reach the user site by accident). One pattern, two readers. `PYTHON=python3.9
scripts/dev-setup.sh` builds it from another interpreter; re-running reuses an existing `.venv`
rather than clearing it, which is where it deliberately differs from CI, whose workspace outlives
the job.

`tests/test_dev_setup.py` is what keeps this true: it sweeps every tracked file outside
`.github/` and the setup script itself and fails on a bare editable install, so the next reader who
"simplifies" the setup block back to one `pip` line fails the suite instead of the maintainer's
machine. The cost is that the prose here cannot quote the dangerous command literally — describe
it, the way this paragraph does.

**Do not edit a vendored conformance fixture.** If one fails, the encoder is wrong until proven
otherwise; the checksum test will catch the edit anyway. Refreshing them from upstream is its own
commit, separate from any encoder change made to satisfy it, and `PROVENANCE.md` carries the recipe.

**Tests never need a live installation or a live token, and must not start to.** They run against
real loopback servers in `tests/conftest.py`: an `http.server` for REST and a real `websockets`
server that performs the Home Assistant `auth_required` / `auth` / `auth_ok` handshake. If a
behaviour cannot be tested that way, say so in the PR rather than reaching for real credentials.

**Calibrating a fixture against reality is a different job from testing, and it has its own lab.**
The suite must stay offline; deciding *what shape the data has* cannot be done offline at all, and
guessing it is what produced every defect below. The recipe is a throwaway container on loopback,
which mints its own credential and is discarded afterwards — never a real installation:

```sh
docker run -d --name ha-lab -p 127.0.0.1:<port>:8123 -v "$PWD/haconfig":/config \
  ghcr.io/home-assistant/home-assistant:stable
#  POST /api/onboarding/users {client_id,name,username,password,language} -> auth_code
#  POST /auth/token  grant_type=authorization_code                        -> access_token
#  WS   auth -> {"type": "auth/long_lived_access_token", "client_name": ..., "lifespan": 30}
#  POST /api/onboarding/core_config and /analytics; then append `demo:` plus a couple of
#  template sensors to /config/configuration.yaml *inside* the container and restart.
```

That yields ~126 states over ~91 registry entries with the real distribution, and the upstream source
is readable at `/usr/src/homeassistant/homeassistant/` in the same container — which is how the name
rule above was transcribed rather than guessed. Nothing measured there may be written into this
repository: the numbers are, the data is not.

**The doubles must answer like Home Assistant, not like the client.** A double that accepts and
echoes whatever it is sent can only prove the client agrees with itself, which is how the service
call wire-shape defect and the `entity update` area defect both reached a live installation with a
green suite. Two rules follow, and neither is optional:

- **Model the refusals, not just the successes.** The REST double rejects a nested `target` because
  Home Assistant's `PREVENT_EXTRA` schema does; the WebSocket double rejects any key outside
  `WS_COMMAND_KEYS` with `invalid_format` for the same reason. That table is deliberately *not*
  imported from `hass_axi.ws.REGISTRY` — a second opinion that is a copy of the first is not one. A
  new client parameter will be refused here until it is added to the table too; adding it is how
  the parameter gets confirmed rather than assumed.
- **Answer with resulting state.** `config/entity_registry/update` returns the stored entry — every
  field, including ones the request never mentioned, and an `area_id` that stays `null` when the
  area belongs to the device. Every result is JSON round-tripped on the way out, so a client can
  never hold a reference into the double's state and pass by sharing an object with it.
- **Refuse the way Home Assistant refuses, including when that means saying nothing.** The REST
  double answers an unknown service, an undeclared field and a missing required one with a bare
  `400` and no body, because that is what `HTTPBadRequest from vol.Invalid` renders. A double that
  helpfully explained itself would let a client pass that could never explain a real refusal. It
  also returns only the states that actually *changed*, skips an `unavailable` entity in silence,
  and skips one lacking a published capability — which is what makes "nothing to do" and "nothing
  targeted" two testable worlds rather than one string. `SERVICES`, `capability_masks`,
  `target_domains` and `entities_targeted` in `tests/conftest.py` are that second opinion and are
  deliberately not imported from `axi_toolkit.ha.services`, the reader the client uses. That the
  reader ships in a shared package rather than in `hass_axi` does not soften the rule: it is still
  the client's reading, and a second opinion is only one if it was arrived at independently.
- **Not every refusal is a `400`, and two of them carry nothing.** A `HomeAssistantError` is not
  caught anywhere, so aiohttp renders it as a plain-text `500` with a fixed apology and no message:
  that is what a named entity lacking a capability gets (`ServiceNotSupported`), and what a
  `return_response` call that matched no entity gets. The double answers both with `_server_error`.
  Modelling them as helpful JSON `400`s — which it did — licensed a client to read a status number
  and a message that never arrive, and made the second case unreachable altogether.

**Make the fixtures less convenient, not more.** The refusals above were modelled with unusual care
and the *data* was not, and that is where every defect in the 0.3.2 audit came from: a registry where
every entry named itself, a state that was never `unknown`, a service that documented itself, an
entity that was never disabled. All four are the majority case upstream, all four were reachable by
hand, and none of them was visible to 715 passing tests. The fixture set now carries the
distribution a real installation has — entries named entirely by their device, entries naming only
their own half, both settings of `has_entity_name`, a disabled entry with **no state at all**, a
state with no registry entry, an entity with no device, a device in no area, an `unknown` alongside
an `unavailable`, a service and fields publishing no prose, all 21 keys of `as_partial_dict`, and the
larger `extended_dict` (with `aliases: [null]`) from `get`/`update` but not from `list`.
`tests/test_double_fidelity.py` asserts each of those shapes is still present and that every
registry entry's composed name equals the `friendly_name` on its state, so a fixture edit that
quietly tidies one away fails where the reason is written down. The bar for a fixture change is that
**the shipped code before the fix would fail against it**; if the suite still passes against the old
behaviour, the fixtures have not been corrected.

**`tests/test_read_only.py` breaks two of this suite's habits on purpose, and both are in its
docstring.** The variable name and the error code are written as literals rather than imported from
`hass_axi.readonly`, for the same reason the doubles transcribe upstream rather than importing the
client: a test that imported them would agree with a rename that broke every caller. And everything
touching the new module imports it *inside* the test body, so that at the commit before the gate
existed each test fails on its own account instead of the file collapsing into one collection
error — which is what let the change be reported as "60 of 86 fail before, all pass after" rather
than "the file does not load". Keep both if the file is extended.

Two more rules that fall out of that:

- **Every command in `hass_axi.ws.REGISTRY` gets a branch in `_respond`.** Six of the fourteen fell
  through to `unknown_command` and therefore had no coverage at all, five of them while being
  declared in `WS_COMMAND_KEYS`. `test_every_websocket_command_the_cli_ships_is_modelled` is
  parametrised over the registry, so a new command is untestable until the double answers it.
- **The double's own helpers are transcriptions, not imports.** `displayed_name` and `slugify` in
  `tests/conftest.py` are written from `helpers/entity_registry`, the same way `capability_masks` is
  written from `helpers/service`. `displayed_name` is what makes the state/registry agreement an
  assertion rather than a coincidence; importing `hass_axi.commands._common.registry_name` there would
  have made the test pass with the bug in place.

Three testing gotchas already paid for:

- `websockets`' sync `Server.serve_forever()` takes **no** arguments. Only the stdlib HTTP server
  accepts `poll_interval`, which the REST double uses to keep teardown off the critical path.
- The two doubles listen on different ports. `FakeInstallation` puts a front door in front of both
  and gives out one `HA_URL`: it reads each connection's request line and splices the connection to
  the WebSocket double for `/api/websocket` and to the REST double for everything else, which is
  the topology a real instance has. Use the `installation_env` fixture for anything that crosses
  transports — `state list --area`, `doctor` — and `rest_env` / `ws_env` when only one is in play.
  Routing on the request line alone is what keeps request bodies out of it; do not teach the front
  door to parse a body.
- Closing a listening socket does not reliably wake a thread blocked in `accept()`, so
  `FakeInstallation.stop()` opens one throwaway connection to knock, then joins. A fixture that
  merely closes the socket leaks a thread per test.

`skills/hass-axi/SKILL.md` is generated from the CLI's command table. Change the commands, then run
`hass-axi setup skill` and commit the result; `scripts/ci-local.sh` fails if the two disagree.

Supported Pythons are 3.9 through 3.12. `from __future__ import annotations` is what makes the
`X | None` annotation syntax safe on 3.9 — keep it at the top of every module.

## Continuous integration

Three workflows, split by where the work is cheap:

- **`.github/workflows/ci.yml`** — the heavy matrix (leak scan, lint, `pytest` on 3.9 through 3.12,
  the generated-skill check) on the maintainer's self-hosted runner. Triggers: push to `main`, a
  nightly `schedule`, and `workflow_dispatch`. Never pull requests. Each job runs one section of
  `scripts/ci-local.sh`. GitHub Actions is disabled on this repository, so today those checks run
  only through that script, which the no-mistakes gate runs on every change (`.no-mistakes.yaml`).
- **`.github/workflows/hygiene.yml`** — the leak scan alone, on `ubuntu-latest`, on `pull_request`
  (including `edited`). Scans the tracked tree *and* the pull request's own title and body. Exactly
  one GitHub-hosted check per PR, and it takes seconds.
- **`.github/workflows/release.yml`** — GitHub-hosted, and to stay that way: OIDC trusted publishing
  needs `id-token: write` on a GitHub-hosted runner.

**`ci.yml` must never gain a `pull_request` trigger.** This repository is public and the runner
is the maintainer's own workstation. Every trigger it has requires write access, so fork-submitted
code cannot reach the machine; `pull_request` would hand any contributor on the internet code
execution on it, in one line, with no other visible symptom. The reasoning is repeated at the top of
the file so it survives someone later "helpfully" adding PR coverage.

**A thin PR check is the design, not an oversight.** Every change goes through the local no-mistakes
gate — review, tests, lint, docs — before a PR is opened, so GitHub-hosted CI is not the primary
quality signal here. Do not add jobs to `hygiene.yml` to make pull requests look better covered. The
arrangement this replaced triggered the full matrix on both `push: branches: ["**"]` and
`pull_request`, so every PR branch ran it twice on identical commits; one copy went green while its
twin sat queued for over an hour, leaving the PR permanently "unstable".

The nightly cron deliberately avoids 08:17 UTC, which a sibling project's self-hosted workflow holds
on the same workstation.

**A workflow can only be dispatched if the file is already on the default branch.** `workflow_dispatch`
resolves the workflow id against `main`, so a brand-new workflow file 404s on its own branch and
cannot be proven to work until after it merges. That is why the self-hosted workflow kept the
filename `ci.yml` instead of taking the sibling project's `local-ci.yml`: reusing the registered name
is what allowed the real file to be dispatched on its branch and watched through to completion on the
runner before anyone merged it. Same trick applies to any future workflow worth verifying early.

`actions/setup-python` does supply all four versions on that runner — actions/python-versions has
`linux` / `x64` / `22.04` builds for 3.9 through 3.12, and they land in the runner's persistent tool
cache, so only the first run pays the download. If that ever stops holding, `uv` is on the runner's
`PATH` and `uv python install` is the fallback; do not answer it by dropping a version from the
matrix.

Checkouts on the self-hosted runner pass `persist-credentials: false`. That workspace outlives the
job, and a token left behind in its `.git/config` would outlive it too.

**A self-hosted runner runs as a real user, and that user's `~/.local` is on every job's path.**
`~/.local/lib/python3.X/site-packages` is keyed by X.Y only, so it is picked up by an interpreter
`actions/setup-python` just unpacked into the tool cache, and `~/.local/bin` sits ahead of that
interpreter's `bin` on `PATH`. A bare `pytest`, `ruff` or `hass-axi` therefore runs the maintainer's
copy, under `/usr/bin/python3.X`, against whatever checkout that copy points at. The first run of
this workflow demonstrated both halves: py3.9 and py3.12 passed because no user site exists for those
versions, while py3.10 and py3.11 failed with `ModuleNotFoundError: No module named 'hass_axi'`, and
the lint and skill jobs went green having exercised the maintainer's binaries rather than the
commit's. Every job that needs third-party packages therefore does `python -m venv --clear .venv` and
calls tools as `.venv/bin/<tool>`; a venv sets `ENABLE_USER_SITE = False`, so the leak cannot happen.
`--clear` because the workspace is reused between jobs. Do not "simplify" these back to bare tool
names.

## Releasing

release-please owns the version. `.release-please-manifest.json` records the **last released**
version, which is not the same thing as the version in `pyproject.toml` and `src/hass_axi/__init__.py`
— those hold the version a release will *write*. During bootstrap, before the first publish, the
manifest deliberately trailed the source: baseline `0.0.0` with source `0.1.0` meant "nothing
released yet, the next `feat:` lands 0.1.0". That period is over — PyPI hosts 0.1.0 and 0.2.0, and
the manifest, `pyproject.toml` and `src/hass_axi/__init__.py` all sit at `0.2.0` — but the rule it
taught still holds: never "fix" a mismatch by raising the baseline to match the source; that tells
release-please the version is already out and it bumps past it, permanently skipping a version
number PyPI will never let us reuse.

**A correctly typed commit can still cut no release at all, and that is the changelog sections
working as intended.** The distinction that matters is between types that render changelog notes
and types that do not: a `build`, `ci`, `chore`, `refactor`, `style` or `test` commit is parsed
and counted but renders none, the changelog comes out empty, release-please finds no user-facing
commits and opens no release pull request, and the workflow reports success while shipping
nothing. The release run for the `build` commit that prompted this release logged
`Considering: 1 commits` and then `No user facing commits found since be7dae0f8840712f7b449760ba66067488b09c7d - skipping`:
the commit was counted and then skipped as non-user-facing, the skip gated on the generated
changelog notes coming out empty rather than on the count of commits considered, and the whole
thing reads like a clean run with nothing left to do. That is right for almost everything, and
wrong for the one case where a merged change alters what a contributor or a user gets while having
no behaviour to describe. The lever is a `Release-As: <version>` footer, on a commit of its own.

**No version string is touched by hand in that commit: release-please owns every one of them.**
`pyproject.toml`, `src/hass_axi/__init__.py` and `.release-please-manifest.json` are all written by
the release pull request release-please opens once the footer has forced it, and hand-bumping any of
them there is how the manifest comes to be raised to a version PyPI has never seen — the mistake the
paragraph above exists to prevent. The forcing commit is prose plus the footer and nothing else, and
typing it `docs` rather than `chore` is deliberate: `Documentation` is a section release-please
prints, so the release gets a real entry instead of a bare header. That entry describes the *forcing
commit*, though, never the hidden-type change that prompted the release — which is still invisible —
so the wording that says what actually shipped is a separate edit on release-please's own release
branch. 0.7.1 was cut this way, from a `build` commit.

**The footer has to survive the squash, and both ways of losing it are silent.** Run through
`vendor/conventional-commits-parser/`, a message whose `Release-As` line is followed by prose parses
with **no footers at all** — the whole footer block is re-read as body, so the version is never
seen. Another *footer* after it is harmless; a sentence is not. And GitHub's own default squash
message for a branch carrying more than one commit is the title plus a list of the commits, which
yields zero footers by the same measurement — so the squash message has to be set to the branch
commit's message verbatim, footer last, and the pull request body has to say so, because the
pipeline's own document step routinely adds a second commit. A release lost either way gives a green
run, no release and no error anywhere: the same silent class as an unparseable message, reached from
the other side. `scripts/commitcheck.py --message` cannot be the guard here: it answers only whether
the message parses at all, and both loss modes above are perfectly parseable, so it exits 0 on
each of them — a false assurance, which is worse than no check. Whether a footer survives is only
measurable by driving the parser in `vendor/conventional-commits-parser/` and counting the footer
nodes it returns.

**A commit message release-please cannot parse is dropped silently, and the run stays green.**
`parseConventionalCommits` wraps every parse in `try { … } catch { logger.debug(…) }`, so an
unreadable message costs a commit and reports nothing: no changelog entry, no version bump, and a
release run that exits 0. It cost the sibling AXI project a release — a merged fix left unpublished
behind a green workflow — and **this repository's history parses by one word.** `46c25f9` carries
the same paragraph about the same fix as the message that broke over there, and the only difference
is that the term reaching the parser sits a few words into its line here rather than starting one.
Both messages are vendored under `tests/fixtures/commit-messages/` and the suite asserts exactly
that: same prose, same term, opposite verdicts.

**The rule, established against the parser rather than guessed at.** release-please 17.3.0 parses
with `@conventional-commits/parser` (`^0.4.1`), whose grammar offers **every physical body line** to
`<footer> ::= <token> <separator> <whitespace>* <value>`. `<token>` is `<type> ["(" <scope> ")"]`,
and `<type>` consumes from the line start until whitespace, a newline, `!`, `:`, `(` or `)`. If it
stops on `(` the parser is committed to a scope: it reads to the next `(`, `)` or newline, and if
that is not `)` it **throws** (`lib/parser.js:177`) — the only `throw` reachable from the body, and
the only production that raises rather than returning an `Error` its caller can back out of. So

- `` `Decimal(repr(value))` inside … `` at a line start — **refused**;
- `… through `Decimal(repr(value))` inside … ` — **fine**, one word further along.

It is not parentheses, not backticks, not the `-` used as a dash, and not position alone: it is the
interaction. This repository's own copy of that paragraph parsed for exactly this reason, which is
luck, not design. `scripts/commitcheck.py --rules` prints the rule with its citation, and
`--demo` proves the checker still tells the shapes apart.

**Two engines, and the reason there are two.** `vendor/conventional-commits-parser/` is a
byte-for-byte copy of the four dependency-free upstream modules (ISC; provenance, checksums,
refresh recipe and the reason `utils.js` is excluded are in its `PROVENANCE.md`), so `--engine node`
runs *the* parser with no `npm install` and no network. `--engine python` is a transcription of the
same grammar, so a machine without `node` gets a verdict rather than a skip. `--engine auto` — the
default, and what the hooks use — prefers `node`. `tests/test_commit_message.py` runs the whole
corpus through both and compares the verdict, line, column and token; the transcription is only
worth anything because that comparison passes, and CI installs `node` so it is never skipped there.
It has already earned its keep: it caught the node path's error regex failing to match when the
offending token was a newline, which would have silently downgraded a real rejection.

**Do not solve this by banning rich commit bodies.** The bodies carry the reasoning that makes this
history worth reading, and a guard that made prose the problem would be answered by writing less of
it. `DEMO_ACCEPTED` in `commitcheck.py` pins the shapes that must keep working — nested parentheses
mid-line, markdown bullets, footers, breaking-change notes, a full rich body — and
`test_the_rule_is_the_interaction_and_not_any_one_ingredient` asserts each ingredient alone is fine.
A change that makes one of those fail is a regression in the guard, not a discovery about the prose.

**Three layers, and one of them is the real fix.**

- `.githooks/commit-msg` runs `commitcheck.py` after `leakcheck.py`. This is the one that matters:
  it rejects the message before it can reach `main`, and it names the line, the column and what to
  change.
- `.github/workflows/release.yml` has a `commit-audit` job that re-checks every commit since the
  last release tag. It deliberately does **not** `needs:` the release-please job — it has to fail on
  its own account, including on a run where release-please itself errored. It exists because a hook
  cannot see a message typed into GitHub's squash-merge box.
- `scripts/ci-local.sh --only commits` runs the same audit on every gate run — without a GitHub
  token the git-side half still runs and only the pull request bodies go unread, which its SKIP
  line says — and `ci.yml` calls it nightly, so an allowance that has outlived its cause surfaces
  without waiting for a merge; `ci.yml` also installs `node` in the `test` job so the agreement
  between the engines is enforced rather than skipped, which a local run does only where `node` is
  on `PATH`. With Actions disabled the nightly runs nowhere, and that matters more than it looks
  now that the audit reads pull request bodies: a body edited a week after the merge changes what
  the next release contains, with nothing else having run in between.
- `.github/workflows/hygiene.yml` gained a step, which is the one exception to "keep this workflow
  to the one cheap job". It is not coverage for its own sake: it checks the pull request body, which
  exists *only* on a pull request, never passes under a hook, and can replace the merged commit
  message outright. Its trigger list carries `edited` for the same reason. Still one job, still
  seconds. The leak scan of the same two fields rides the same trigger, for the same reason from the
  other direction — see "There are three surfaces" above.

The claim that used to sit here — "`hygiene.yml` was deliberately left alone; the release audit
already covers what a PR-time check would" — was false, and cost the sibling project its second
release in a row. The release audit runs *after* the merge. Nothing looked at the body before it.

**The audit's verdict is only as wide as its reach, and it has to say when it is narrower.**
`resolve_bodies` has three modes and no fourth: `require` (the workflows) fails without a token
rather than checking a different artefact and calling it green, `auto` (a developer's checkout)
consults GitHub when it can and prints `NOT consulted` in the output when it cannot, and `skip` is
git only, on purpose, which is what the unit tests pass so they never reach the network. There is
deliberately no silent fallback: silent fallback to the wrong string is precisely the state the
first version of this guard shipped in. A per-commit miss is not an
outage either: the commits endpoint answers 422 for a SHA GitHub does not have, which is the
ordinary state of a local branch, so `resolve_bodies` reaches the repository once before the loop
and only then reads a miss as "no pull request" — and names the commits it applied to. Reading a 404
as "no pull request" without that probe would let a token with no access report an all-clear for
every commit, which is this same blind spot from the other side.

**The audit reads `--first-parent`, because that is what release-please reads.** It asks GitHub for
the *merge commits on the branch*, not for everything reachable from it. A plain `git log` would
report a work-in-progress message inside a merged branch as a commit release-please dropped, and a
guard that cries wolf gets switched off.

**`KNOWN_UNPARSEABLE` is the `PATH_ALLOWANCES` pattern applied to a commit.** One full SHA, one
reason, printed by `--rules`, and pinned by the suite: an entry whose commit now parses fails
`test_every_known_unparseable_entry_is_still_earning_its_place` rather than quietly covering
something new. It is matched on the **full** SHA — a prefix is not an identifier, and that is the
same defect the leak scanner's trailing path match had, one layer down. **It is empty here, and that
is a measured fact**: every commit in this repository was run through the parser and all of them
parse, which `test_every_commit_in_this_repositorys_history_is_readable` re-checks on every run. An
entry is only ever added for a message that has already been lost and whose content has been
accounted for somewhere the changelog names.

**The message is not always the message, and that is what the first version of this guard got
wrong.** It diagnosed the grammar rule correctly, shipped three layers built on it, and then passed
— green, twice, in the sibling project — on a release that considered zero commits. release-please
does not parse the commit message. It parses `splitMessages(preprocessCommitMessage(commit))`, and
`preprocessCommitMessage` is four lines:

```js
const overrideMessage = (commit.pullRequest.body.split('BEGIN_COMMIT_OVERRIDE')[1] || '')
  .split('END_COMMIT_OVERRIDE')[0]
  .trim()
if (overrideMessage) return overrideMessage
```

`String.split` finds that literal **anywhere in a pull request body**, including in a sentence that
merely names it. The pull request that shipped the guard had a body explaining this very mechanism,
so release-please threw the commit message away and parsed the paragraph after the word instead. It
began `block from the PR body when there is one`; `block` is five characters; the parser stopped on
the space after it and reported `unexpected token ' ' at 1:6`. Column 6 makes no sense on a
`fix(ci):` subject, which is the clue that the text being parsed was not the subject at all. The
commit message parsed perfectly — and a checker that reads commit messages therefore said so.

**Three artefacts reach release-please and only one of them passes under a commit-msg hook.**

| artefact | written | checked by |
| --- | --- | --- |
| the commit message | locally, by a developer | `--commit-msg` (the hook) |
| the merge commit's message | in GitHub's squash box | `--since-release` (after the merge) |
| the pull request body | in GitHub's editor | `--pull-request` (`hygiene.yml`) |

The body is the dangerous one. It *replaces* the other two, it can be edited after every check has
run, and nothing in the repository records it — so `--since-release` and `--commit` resolve it from
the GitHub API rather than trusting `git log`, and `--pull-requests require` (what the workflows
pass) makes a missing credential a failure. This is also why
`test_every_commit_in_this_repositorys_history_is_readable` is not the whole claim it looks like: it
proves every *message* here parses, which is a statement about git, not about what release-please
read.

**One rule here is stricter than upstream, deliberately.** Upstream is content with an override
block that is never closed — it simply reads to the end of the body. That is exactly the shape an
accidental mention takes, so `override_faults` refuses a block that names the marker and never closes
it. Without that rule an accidental mention whose next paragraph *happened* to parse would silently
become the changelog entry. An empty block is not refused: upstream's `if (overrideMessage)` is falsy
on an empty string, so a body ending on the marker loses nothing and crying wolf at it would train
somebody to stop reading the output.

**The other fidelity note.** One commit may carry several conventional commits, split on
`BEGIN_NESTED_COMMIT` or on a blank line before a new `type:` line. `split_messages` transcribes that
so a message that loses only *part* of itself is still refused.

**If a fix ever is dropped here, releasing it.** Landing a parseable commit makes release-please
re-scan the range, but the unparseable commit is dropped again and never reaches the changelog — so
the release notes would omit the very fix being shipped. The route is to restate it: give the new
commit message a second conventional-commit section for the dropped work, which `splitMessages`
turns into its own changelog entry, and record the dropped SHA in `KNOWN_UNPARSEABLE` saying where
its content went. Do not rewrite `main` to fix the original message; a tag or a published sha is not
worth the history.

**`scripts/commitcheck.py` and `vendor/conventional-commits-parser/` are shared with the sibling AXI
project, and are byte-identical apart from `KNOWN_UNPARSEABLE`.** Two copies that behave differently
are worse than one that is wrong — the same rule `toon.py` is held to. A change to the grammar
transcription, the engines or the audit belongs in both repositories in the same sitting.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.

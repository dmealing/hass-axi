# hass-axi

An Agent eXperience Interface (AXI) CLI for Home Assistant.

> **Renamed from `ha-axi`.** Up to 0.7.1 this tool was published as `ha-axi`. An unrelated Home
> Assistant CLI is also published under that name, and the two install the same binary, so this one
> is now `hass-axi`. See [Moving from `ha-axi`](#moving-from-ha-axi) — the old variables keep
> working, and `hass-axi setup hooks` repairs the hooks the old name installed.

Home Assistant's REST API will happily tell you a lamp is off, and even the name it displays. It
will not tell you where that name came from, which room the lamp is in, or whether that room came
from the entity or from the device behind it — and it cannot change any of them. **The entity,
area and device registries are reachable only over the WebSocket API**, so administering an
installation from a script means hand-rolling a WebSocket client, or clicking through the UI
instead.

That is the first of the two jobs `hass-axi` exists for. The second is getting a service call *right*
— checked before it is sent, and explained when Home Assistant refuses it with a status code and no
body. The same judgement carries over to what the recorder holds: [readings and their
history](#readings-history-and-energy-summarised-with-their-caveats) are found by what they measure
and where they are, and summarised with the data-quality problems they show stated beside the
number. Everything else the tool does — states, templates, arbitrary REST paths, arbitrary
WebSocket commands — is [plumbing those two need](#everything-else-it-reaches), and is table stakes
anywhere.

---

## The registries REST does not expose

One command renames an entity and moves it to a room, over the WebSocket API, resolving the area by
name or by id. Without `--write` it shows what it would change and sends nothing; with it:

```
$ hass-axi entity update light.example_lamp --name 'Reading Lamp' --area 'Example Room' --write
entity: light.example_lamp
updated[2]: area_id,name
name: Reading Lamp
area: Example Room
area_id: example_room
area_source: entity
```

The answer is the **resulting registry row**, not an echo of the request: `updated` names the fields
that actually changed, and `area_source` says where the area in that row came from. That last field
is the one a caller cannot work out for itself, and it matters because of this:

```
$ hass-axi entity get sensor.example_bridge_dawn
entity:
  entity_id: sensor.example_bridge_dawn
  name: Example Bridge Next dawn
  area: Example Hall
  area_id: example_hall
  platform: sun
  domain: sensor
  device_id: 8465672c0d0394de228e85c53dcc041b
  original_name: Next dawn
  disabled: false
  hidden: false
  entity_category: diagnostic
  unique_id: 01M0QEH70H9SQQBCX9E2GF8RKM-next_dawn
  icon: ""
  area_source: device
```

That entity's own registry row holds `area_id: null` and `name: null`. Home Assistant still places
it in Example Hall, because **an entity with no area of its own inherits its device's**, and it
still displays it as *Example Bridge Next dawn*, because **an entity's name is composed from two
registries**: a name somebody set wins outright, and otherwise the device's display name is joined
to the entity's own half. A client that reads the entity row alone reports that entity as
unassigned and nameless. Both are wrong, and both are wrong silently.

`hass-axi` applies both rules in **every** view that names an entity, so the filters agree with what
a user sees:

```
$ hass-axi entity list --area 'Example Hall' --limit 3 --fields entity_id,name,area,platform
count: 3 of 9 matched (23 total)
entities[3]{entity_id,name,area,platform}:
  sensor.example_bridge_dawn,Example Bridge Next dawn,Example Hall,sun
  binary_sensor.example_bridge_rising,Example Bridge Solar rising,Example Hall,sun
  sensor.example_bridge_dusk,Example Bridge Next dusk,Example Hall,sun
help[3]:
  Run `hass-axi entity get <entity_id>` for one entry in full
  Run `hass-axi entity list --area 'Example Hall' --limit 9` to see all 9
  Run `hass-axi entity update <entity_id> --name "<name>" --area <id|name>` to preview a change, and add --write to send it
```

```
$ hass-axi entity list --search 'Example Bridge' --limit 2 --fields entity_id,name,original_name
count: 2 of 9 matched (23 total)
entities[2]{entity_id,name,original_name}:
  sensor.example_bridge_dawn,Example Bridge Next dawn,Next dawn
  binary_sensor.example_bridge_rising,Example Bridge Solar rising,Solar rising
help[3]:
  Run `hass-axi entity get <entity_id>` for one entry in full
  Run `hass-axi entity list --search 'Example Bridge' --limit 9` to see all 9
  Run `hass-axi entity update <entity_id> --name "<name>" --area <id|name>` to preview a change, and add --write to send it
```

`--search` matches the composed name, which is why searching for the name a user reads finds the
entity even though no registry row contains that string. `original_name` stays available as a field
when you want the entity's own half alone.

The same fallback keeps the arithmetic honest. `area list` counts an entity into the area it is
really in, and the per-area counts plus `unassigned_entities` sum to the size of the registry:

```
$ hass-axi area list
count: 3 areas
unassigned_entities: 12
areas[3]{area_id,name,entities,devices}:
  example_hall,Example Hall,9,1
  example_room,Example Room,2,0
  example_study,Example Study,0,0
help[3]:
  Run `hass-axi entity list --area <id|name>` to see what one area holds
  Run `hass-axi area update <id|name> --name '<name>'` to preview a rename, and add --write to send it
  Run `hass-axi entity list --area none` to find entities with no area
```

`entity_id` is **not stable identity** and nothing here encourages treating it as such: filter by
area, search by name, or list what one device supplies with `entity list --device <device_id>` —
the only route from an opaque device id to its entities, because that id is not searchable and
should not be. Areas accept a name or an id anywhere `<id|name>` appears, and an ambiguous name is
an error rather than a guess. A name is compared the way it is typed: case and a typographic
apostrophe (`’`, which a phone writes into its own name) do not matter, so `--search "example's"`
finds it. A name that matches nothing is answered with the nearest ones that exist — `did you mean`
— rather than with an offer to create what was mistyped. `entity get` and `area get` show one entry in full; `area get` also
reports the icon, floor and aliases that `area list` leaves out by default — `area list --fields
area_id,name,floor_id` adds the floor to the list.

The typed write surface is `entity update` — `--name`, `--area` and `--icon`, each with a matching
`--clear-*` that falls back to what the integration supplies, plus `--new-id` to rename the
`entity_id` itself — together with `area create` and `area update`, and `device get` and
`device update` on the registry behind them. Deleting an area is deliberately not given a typed
command; `hass-axi ws area.delete --write` is there if you mean it, and the same goes for disabling or
deleting a device.

**Every one of those writes is a preview until `--write`.** `entity update`, `area create`,
`area update` and `device update` read the registry, resolve what was named, and print the fields
they would change with the value stored now beside the value asked for — and send nothing. Adding
`--write` sends exactly that. A request for what is already stored answers `already matches` either
way, because there is nothing to send. What a write resolves, the preview resolves too: an area
that does not exist fails there, and so does a `--floor` no floor answers to, which Home Assistant
itself would store without complaint.

**The device is usually the level a name or an area is wrong at**, which is why `device update`
exists rather than a loop over entities: an entity with no area of its own inherits its device's,
and an entity with no name of its own is named after its device, so one correction here moves every
entity that device supplies and leaves nothing behind still saying the old thing. It takes a
`device_id` or the displayed name, and `--name`/`--area` with matching `--clear-*` flags, the same
shape `entity update` has. One asymmetry is Home Assistant's rather than this tool's: a device
carries two names, and only `name_by_user` is writable. `--name` sets it, `--clear-name` removes it
and falls back to the integration's own `name`, and `device get` reports both alongside a
`name_source` saying which one is showing.

## Service calls that are checked, not forwarded

Home Assistant will accept a service call that cannot possibly do anything and answer `200` with an
empty list. Three different outcomes arrive on the wire looking identical, and a client that
forwards the call and prints the reply reports all three as success.

**Nothing is sent until you say so.** `service call` without `--write` is a preview: it reads the
published service, resolves the target, runs the capability pre-check and prints the request it
would make, the entities it would reach and what the check found — and sends nothing. It fails, as
the call would, for a service that does not exist or a target that reaches nothing, so every
refusal below is the same answer with or without the flag. `--write` sends it. The same flag, with
the same meaning, gates the typed registry writes, a write-method `hass-axi api` request and a write
`hass-axi ws` command: nothing in this tool changes Home Assistant without it.

**An entity that cannot do the thing is dropped in silence** when it was reached through an area or
a device. `hass-axi` reads the capability the service publishes and says so before sending:

```
$ hass-axi service call cover.set_cover_position --target-area example_room --data position=50 --write
error: "cover.set_cover_position needs a supported_features bitmask containing any of 4, and no entity the target matched has one: cover.example_blind reports 3"
code: UNSUPPORTED_CAPABILITY
help[4]:
  Run `hass-axi state list --area example_room --domain cover` to see what is there
  Run `hass-axi area list` to see each area's id and how much it holds
  Run `hass-axi service get cover.set_cover_position` to see what this service targets
  Run the same command with --no-check to send it anyway
```

The published requirement is a **list of alternatives** and is read as one, because that is Home
Assistant's own rule: `media_player.volume_up` names both VOLUME_SET and VOLUME_STEP, since core
backs a player that cannot step with one that can set. A speaker with only the first is not gated,
because it works. The requirement is also only read for the service's own domain — `reolink.ptz_move`
targets `button` entities and names a `camera` feature, and checking a button against a camera's bits
would refuse every call. `--no-check` exists because a published requirement is an integration's
claim about itself, and a wrong claim must not become a wall.

**A target that matched nothing** exits 1 and says so, rather than reporting a successful no-op:

```
$ hass-axi service call light.turn_on --target-area example_study --write
error: "area example_study matched 0 entities light.turn_on can act on, so the call did nothing"
code: NO_ENTITIES_TARGETED
help[3]:
  Run `hass-axi state list --area example_study --domain light` to see what is there
  Run `hass-axi area list` to see each area's id and how much it holds
  Run `hass-axi service get light.turn_on` to see what this service targets
```

**A call that genuinely had nothing to do** is a success, and says which:

```
$ hass-axi service call light.turn_off --target-entity light.example_lamp --write
service: light.turn_off
changed: light.turn_off accepted with 0 states changed
target: entity light.example_lamp matched 1 entity; which reported no state change
help[2]:
  Run `hass-axi state get <entity_id>` to see an entity's current state
  Run `hass-axi service get light.turn_off` to see what this service targets
```

Home Assistant returns the states that actually changed, so `[]` means both "everything was already
as asked" and "nothing was reached at all" and it never says which. `hass-axi` resolves the target
when, and only when, the change set is empty and a target was given: reaching nothing exits 1,
reaching something exits 0 with the count and any entity that was `unavailable` and therefore
skipped. Which domains a service can reach is read from its published `target`, never guessed from
its name.

**A refusal carries no reason at all.** Home Assistant renders a rejected service call as an empty
`400` — the status line and no body — so an unknown service, an undeclared field and a missing
required one are indistinguishable on the wire. (Two refusals are worse still: a named entity
lacking a capability, and a `--response` call that matched nothing, arrive as a plain-text `500`
with a fixed apology.) `hass-axi` fetches the service model at that point and answers from it:

```
$ hass-axi service call light.turn_on --target-entity light.example_lamp --data brightnes=180 --write
error: light.turn_on does not accept field brightnes
code: UNKNOWN_SERVICE_FIELD
help[2]:
  fields for light.turn_on: transition, rgb_color, color_temp_kelvin, brightness_pct, brightness_step_pct, effect, rgbw_color, rgbww_color, color_name, hs_color, xy_color, brightness, brightness_step, white, profile, flash
  Run `hass-axi service get light.turn_on` for their types and which are required
```

A call that succeeds pays for none of that: the explanation is failure-path only. (A preview
names the same field before anything is sent, as a warning rather than a refusal, because a
published field list is an integration's claim about itself.) The model is
never cached — an integration added or removed rewrites it, and nothing signals when.

`service get` renders the same model on demand, from the installation itself, so it is never stale
and never describes integrations you do not have:

```
$ hass-axi service get cover.set_cover_position
service: cover.set_cover_position
name: set_cover_position
description: ""
response: none
target: entity domain cover; supported_features matching any of 4
fields[1]{field,required,type,description}:
  position,true,number,""
help[2]:
  Run `hass-axi service call cover.set_cover_position --target-entity <entity_id> --data position=<value>` to preview the call, and add --write to send it
  Run `hass-axi state get <entity_id>` and compare its supported_features attribute
```

**What `hass-axi` deliberately does not do is turn that model into commands.** Home Assistant
publishes enough metadata to generate a typed command per service, and generating them would mean
roughly 77 nouns and 327 subcommands wrapping the one command that already reaches all of them —
each firing at a device without asking whether it can do the thing. Consuming the model to
*validate, explain and recover* costs one shared dependency and none of that. `--data key=value`
reaches every field of every service, forever, with no metadata to go stale.

## Install

```sh
pip install hass-axi
# or run it without installing
uvx hass-axi entity list --area 'Example Room'
```

From a checkout:

```sh
scripts/dev-setup.sh       # creates .venv and installs this checkout into it
scripts/install-hooks.sh   # point core.hooksPath at .githooks
```

`hass-axi` is normally installed as an isolated user-level tool with its own launcher on `PATH`, and
an editable install into whatever interpreter happens to be ambient overwrites that launcher and
points it at the checkout — so deleting the checkout leaves the reader's own installation dead.
`dev-setup.sh` builds `.venv` instead, the environment `.github/workflows/ci.yml` already uses, and
every development command runs out of it: `.venv/bin/pytest`, `.venv/bin/ruff`, `.venv/bin/hass-axi`.

## Configure

Nothing about any particular installation is baked in: the base URL and token come from the
environment, and the entity table is fetched at runtime. Two variables, and nothing else — a third,
optional one makes the session [read-only](#read-only-sessions):

```sh
export HA_URL=https://homeassistant.example.com   # or HASS_SERVER
export HA_TOKEN=<long-lived access token>          # or HASS_TOKEN
```

Create the token in Home Assistant on your profile page, under **Security → Long-lived access
tokens**.

`HA_URL` may name several base URLs, comma-separated — typically the local address first and a
remote one second:

```sh
export HA_URL=https://homeassistant.example.com,https://remote.example.net
```

They are tried in order and the first that accepts a connection (and, for `https`, a TLS handshake)
is used by both transports for the whole run; `hass-axi ping`, `doctor` and the home view say when a
later one was used. Only a transport failure moves on to the next URL. An HTTP answer — a 401, a
404, a 500 — is the same installation answering, and trying it another way would only repeat the
refusal. With one URL nothing is probed and nothing changes.

**There is deliberately no `--token` flag and no credential file.** A token on a command line leaks
into shell history and the process table; a token in a file leaks into commits. The environment is
the only channel. Anything token-shaped is redacted before it reaches stdout or stderr, so a
credential cannot escape through an error message or a debug line either. Two more invariants hold
around the URL: a redirect that changes scheme or host is **refused** rather than followed, because
`urllib` would copy the `Authorization` header onto it, and any `user:password@` in `HA_URL` is
stripped and registered as a secret before the base URL is ever printed. A bare host defaults to
`https://`, never `http://`.

Check both transports at once:

```
$ hass-axi doctor
healthy: true
checks[4]{check,status,detail}:
  read_only,ok,"HASS_AXI_READ_ONLY is not set: writes are allowed"
  environment,ok,HA_URL and HA_TOKEN are set
  rest,ok,API running. (version 2026.8.3)
  websocket,ok,"authenticated, 23 registry entries in 3 areas"
version: 2026.8.3
```

`doctor` exits non-zero when any leg fails, so it works as a gate in a script or a hook.

## Readings, history and energy, summarised with their caveats

A question about the house is usually a question about a reading — how much power the kitchen is
drawing, how much energy went out overnight, how cold the hall got. Answering it from raw state
means joining two transports and summing buckets by hand, and getting either wrong yields a
plausible number that is not true.

```sh
hass-axi sensor list --device-class power --area 'Example Room'
hass-axi statistics get sensor.example_legacy_meter --start 7d
hass-axi history get binary_sensor.example_doorway --start 24h
hass-axi logbook get --entity light.example_lamp --start 2h
```

- **`sensor list` finds a reading by what it measures and where it is** — `--device-class`,
  `--unit`, `--area`, `--search` — and answers with its value and unit; `--fields` adds its area,
  the age of the reading and the rest. The area comes
  from the registry, inherited from the device unless the entity sets its own, so every run reads
  both transports. Diagnostic and configuration sensors (battery voltages, signal strength) are set
  aside by the registry's own `entity_category`, never by a name pattern; `--all` puts them back.
  A filter that matches nothing lists the device classes and units that would have matched.
- **`statistics get` reads the recorder's long-term statistics and answers in the shape the
  statistic has.** A meter (a *sum* statistic: energy, water, gas) reports its total over the
  window; a reading (a *mean* statistic: power, temperature) reports its average with its minimum
  and maximum; a bearing reports a circular mean. The kind comes from the recorder's own metadata,
  never from a device class or a name — asking for the wrong kind is not an error upstream, the
  buckets just come back empty.
- **The number covers the window that was asked for, whatever the period.** The recorder widens a
  daily, weekly or monthly request to whole periods before it reads — `--start 7d` in daily buckets
  is eight buckets, and in monthly ones the whole month — so summing the buckets counts time before
  `--start`. The total, mean, minimum and maximum are therefore read from the hourly rows that begin
  inside the window; `--period` decides only how the buckets are counted and where the caveats
  look. Past 400 days the buckets are summed as they come and a caveat says what they cover.
- **Data-quality problems are stated, not corrected.** When the buckets show something a reader
  should know before trusting the number, a `caveats` line says so beside it: buckets missing from
  the window, a meter that went backwards, a meter that resets about once a day (so its live state
  is a since-reset reading, not a running total), one bucket that holds more than half of
  everything and is twenty times the median — a sensor that briefly reported a lifetime figure as a
  daily one, or a single genuinely heavy day; the buckets cannot say which. The number is always what the recorder holds.
  Every check is a rule about the buckets' shape, nothing about any particular integration.
- **`history get` summarises a timeline** — the range of a reading, or how long anything else
  spent in each state — counting the state the entity was already in when the window opened.
- **`logbook get` says what caused each change** when Home Assistant recorded it, folding the
  half-dozen `context_*` keys into one `cause`: an automation by name, a service, an entity.

Every window takes `--start` and `--end` as an age (`30m`, `24h`, `7d`) or an ISO 8601 time; a time
with no offset is UTC, and the end defaults to now. The end is always sent, because Home
Assistant's history and logbook views end a window that has none a day after its start — so a
week-long request without it silently answers for one day.

The home view (`hass-axi` with no arguments) also lists what needs attention: entities not
reporting, batteries under 20%, and sensors whose integration has reported nothing for 24 hours.
`hass-axi ping` times one authenticated request, for a liveness gate cheaper than `doctor`.

## Read-only sessions

A third variable makes a session incapable of changing anything:

```sh
export HASS_AXI_READ_ONLY=1
```

Every write is then refused **before it is sent**, and the refusal does not care which route the
write took. A typed command:

```
$ hass-axi entity update light.example_lamp --name 'Something Else' --write
error: "`hass-axi entity update` is a write, and this session is read-only"
code: READ_ONLY
class: usage
help[3]:
  This session is read-only; the command was refused before anything changed
  Reads still work, e.g. `hass-axi state list`, `hass-axi entity list`, `hass-axi area list`
  Unset HASS_AXI_READ_ONLY (or HA_AXI_READ_ONLY, its deprecated spelling) to allow writes; it is a switch, so any non-empty value enables it
```

The raw WebSocket escape hatch, which is where the registry writes actually live:

```
$ hass-axi ws --raw config/area_registry/create --param name='Bypass Attempt' --write
error: "`hass-axi ws` is a write, and this session is read-only"
code: READ_ONLY
```

And the raw REST escape hatch:

```
$ hass-axi api POST /services/light/turn_on --field entity_id=light.example_lamp --write
error: "`hass-axi api` is a write, and this session is read-only"
code: READ_ONLY
```

Each of those last two prints the same three `help` lines as the first; only the head of the output
is reproduced here. All three exit `2`, and the area registry is unchanged afterwards: the refusal
is reached before either transport is opened, and before the token is even read. Without
`--write` all three are previews, which send nothing and are therefore still allowed: a
read-only session can see what a request would have been, and is told the write would be refused.

Four things about it are deliberate, and the first two are why it is worth having at all.

- **It is a variable, never a flag.** A flag is omitted by exactly the caller that most needs it.
  Setting it in the environment covers every command the session runs, including the ones an agent
  composes rather than a person types.
- **Every subcommand and every WebSocket command carries an explicit classification, and the
  default is a write.** Nothing is inferred from a command's name or from an HTTP verb: `service
  call` mutates through a surface that looks like any other POST, and the WebSocket command set
  does not follow REST conventions at all. A command nobody classified is refused, so the failure
  mode of forgetting is a refusal rather than an unguarded mutation — and a test enumerates both
  command tables and fails on the first declaration that has none.
- **It is a switch, not a boolean.** Any non-empty value enables it, `0` and `false` included.
  Parsing the value is how a guard comes to be off while an operator believes it is on; unsetting
  the variable is the only way to allow writes.
- **Refused commands stay visible.** They are still listed in `--help` and in the command table,
  because an agent that cannot see the command it needs cannot work out why its plan is impossible.

Enforcement sits at three points — the dispatcher, `RestClient.request` and `WsClient.send_command`
— and never inside a command body, so a new command is guarded whether or not its author knew there
was a gate. The two transports are the load-bearing pair: a classification is a claim a module makes
about itself, and a test pins that a module claiming to be a read and posting anyway is still
refused. A guard that held on one transport and not the other would be worse than none, because it
would reassure without protecting.

Reads are untouched, including `template render` — a POST that renders server-side and changes
nothing. `hass-axi doctor` reports the mode as its first check, and the no-argument view prints
`read_only: on` when it is set, so a session knows what it is before it plans anything.

**What it does not cover.** `hass-axi api` hands an opaque path straight to the installation, so there
the method is the only fact available and the rule errs closed: `GET`, `HEAD` and `OPTIONS` pass,
everything else is refused, including a `POST` that happens not to change anything. Any Home
Assistant endpoint that mutated on a `GET` would pass that check — none does, and the same
assumption is the one every read-only HTTP proxy makes, but it is an assumption rather than a
guarantee. `hass-axi ws --raw` is judged by the API type it names: one a declared command already
names as a read passes, and an undeclared type is refused.

## Everything else it reaches

These are table stakes for any Home Assistant client. They are here because the two sections above
need them and because an agent that has the tool should not have to leave it — not because they are
what `hass-axi` is for.

- **`state list` / `state get`** — the runtime view over REST: what an entity is doing right now and
  its attributes, with `--domain`, `--state`, `--search`, `--fields` and `--limit`, plus `--stale
  <age>` — entities of any domain not reported for at least that long, the generic form of the home
  view's stale-sensor count.
- **`service list` / `service get`** — discover what an installation can be asked to do. `service
  list --domain <name> --fields service,response,target` adds whether each service answers with a
  payload and whether it takes a target.
- **`template render`** — render a Jinja template server-side, from `--template`, `--template-file`
  or stdin. It sees every entity Home Assistant knows about.
- **`api`** — any authenticated REST path, with `--field`, `--body` and `--query`. The escape hatch
  for anything with no typed command. Anything but `GET` and `HEAD` is previewed until `--write`
  is passed. A long response is shortened — each list to its first 25 items, each string to a
  preview — with the full size reported, and `--full` prints all of it.
- **`ws`** — any WebSocket command. `ws --list` prints the declared names and whether each reads
  or writes, `ws <name> --param k=v` sends a read and previews a write until `--write` is passed,
  and `ws --raw <api/type>` reaches a type that has no declared name yet — which counts as a
  write. Results are shortened like `api`'s, with `--full` for the whole of one. Adding a
  declared command is one entry in `REGISTRY` in `src/hass_axi/ws.py`; the auth handshake, id
  correlation and error translation are shared.
- **`ping`** — one authenticated request, timed: a liveness gate cheaper than `doctor`.
- **`doctor`** — environment and connection checks over both transports.
- **`setup`** — install, check or remove the agent integrations on this machine (below).
- **`context`** — the ambient document a session hook prints. Reads the environment, the command
  table and the local session record only, so it reaches nothing and exits 0 with nothing
  configured (below).

The whole command surface, and the transport each half runs on:

| Command | Transport | What it does |
| --- | --- | --- |
| `hass-axi entity list\|get\|update` | WebSocket | The entity registry: names, areas, platforms, entity ids |
| `hass-axi area list\|get\|create\|update` | WebSocket | The area registry |
| `hass-axi device list\|get\|update` | WebSocket | The device registry: device names and areas, which entities inherit |
| `hass-axi service list\|get\|call` | REST | Discover services, read one's fields, preview a call and send it |
| `hass-axi state list\|get` | REST | Entity states and attributes as they are right now |
| `hass-axi sensor list` | both | Sensors by device class, unit, area or name, with value and unit |
| `hass-axi history get` | REST | State timelines, with each reading's range or time in each state |
| `hass-axi logbook get` | REST | Logbook entries and what caused them |
| `hass-axi statistics list\|get` | WebSocket | Recorder statistics: a meter's total, a reading's average, min and max |
| `hass-axi template render` | REST | Render a Jinja template server-side |
| `hass-axi ws` | WebSocket | Any WebSocket command, declared or raw |
| `hass-axi api` | REST | Any authenticated REST path |
| `hass-axi ping` | REST | One authenticated request, timed |
| `hass-axi doctor` | both | Environment and connection checks |
| `hass-axi setup` | — | Install, check or remove the agent integrations |
| `hass-axi context` | — | The ambient document a session hook prints |

`--help` on any command is the authoritative reference: it lists every flag per subcommand, with
defaults and two or three worked examples. Nothing here duplicates it, and it works with no
configuration present.

### `state` and `entity` are different views, and both are needed

`state` is the runtime view over REST — what an entity is doing, and the name it displays. `entity`
is the registry view over WebSocket — an entity's stable identity: the name a user set, the area it
belongs to, the integration that supplied it. All of those live only in the registry; states live
only over REST.

`--area` works the same on `state list`, `entity list` and `device list`. On `state list` it costs
one extra registry round-trip, because areas live over the WebSocket, and it is paid only when the
flag is passed:

```
$ hass-axi state list --area 'Example Room'
count: 2 of 2 matched (23 total)
states[2]{entity_id,name,state}:
  light.example_lamp,Reading Lamp,off
  cover.example_blind,Example Blind,closed
help[2]:
  Run `hass-axi state get <entity_id>` for one entity's full attributes
  Run `hass-axi state list --domain light` to narrow by domain
```

That flag exists because an agent that learns `--area` on `entity list` will reach for it on
`state list`. A filter is not the same as importing registry columns into the runtime view, which
is why `area` is still not a `state list --fields` choice.

## Library: `hass_axi.toolkit`

The rules this tool applies are importable by a program that is not the CLI. `hass_axi.toolkit` is
the supported library surface: plain values in, plain values out, and a failed lookup or an answer
of the wrong shape reported as data rather than raised as a CLI error. Nothing in it imports the
command modules, the argument parser, the output boundary or a transport, it depends on the
standard library alone, and no text it produces names a command to run. It is new, and it may grow.

```python
from hass_axi.toolkit import names, recorder, shapes

found = names.resolve(
    "example's den", areas, ident=lambda a: a["area_id"], name=lambda a: a["name"]
)
found.match  # the one area that was named, or None
found.ties  # every area sharing the folded name, when more than one does
found.near  # the nearest areas, when nothing matched

shapes.health_fault(answer)  # None, or why this is not Home Assistant's API root
recorder.summarize(meta, buckets, start, "day", end=end, hourly=hourly_rows)
```

- **`names`** — `fold` compares names the way people type them: case, typographic quotation marks,
  dashes, the ellipsis, accents and spacing do not matter. `resolve` finds an entry by identifier and
  then by folded name, and **reports a tie instead of breaking it**: two names that fold alike come
  back as candidates, never as a pick. `nearest` returns near misses.
- **`shapes`** — whether a decoded answer is what Home Assistant's API gives (`health_fault`,
  `shape_fault`), whether a body is text (`is_text`), whether a value can be an entity id.
- **`recorder`** — a statistic's kind from its metadata, the total, mean, minimum and maximum inside
  a window (`summarize`), and the caveats its buckets support: missing buckets, a meter that went
  backwards or reset, one bucket that dwarfs the rest, buckets that reach outside the window.

The CLI's own commands are built on it, so the two cannot disagree.

## The model: what Home Assistant answers, declared once

What a Home Assistant sends, and the rows this tool prints from it, are declared in `metaobjects/`
as [MetaObjects](https://metaobjects.dev) metadata. Every name there is Home Assistant's own, taken
from a names-only capture of a real server (`tests/fixtures/ha-shape/capture.json`), and an object
declares the keys this tool reads or its test doubles send rather than everything the server
publishes: 31 objects, 153 keys. A row column either extends the one key it is read from, so a
renamed or retyped key fails at load, or names the keys it is computed from.

Four things are generated from it and committed:

- **`src/hass_axi/model/rows.py`**, which ships in the wheel: the `--fields` vocabulary and default
  set of all ten rows the list commands print, and the keys each column reads — of the row's own
  object and of any other it is joined to. No command module keeps a column list of its own.
- **`src/hass_axi/model/readers.py`**, which ships in the wheel too: one frozen reader per declared
  object, importing the standard library alone. `read(raw)` gives each declared key as an attribute,
  `sent(name)` tells a key sent as null from one not sent, and `raw` is the answer untouched, which
  is what a command prints when it prints an open bag such as a state's attributes. The commands
  read an answer through these, so a key a command reads is a key the capture check holds to a
  real server, and a misspelt one is an error rather than a blank.
- **`tests/hamodel/elements.py`**: one builder per declared object. The test doubles make their
  answers through these, and a builder refuses a key the model does not declare.
- **`tests/hamodel/capture_contract.py`** and its tests: the check that every declared key is one
  the captured server sent. It fails in one direction only. A declared key in no captured answer
  fails unless the model gives the reason it could not be observed; a key the server sends and the
  model does not declare is reported by `scripts/ci-local.sh --only model` and never failed,
  because a server publishes far more than a tool reads.

Change the metadata, never a generated file:

```sh
uvx --python 3.12 --from "$METAOBJECTS" --with "$AXI_TOOLKIT" metaobjects gen   # regenerate, then commit
scripts/ci-local.sh --only model                               # fails on a hand edit or a stale file
```

`METAOBJECTS` and `AXI_TOOLKIT` are the pinned toolchain versions; `scripts/ci-local.sh` is the one
place that pin is written, so read it there rather than here.

The generators are [`axi-toolkit`](https://github.com/dmealing/axi-toolkit)'s, shared with the
sibling AXI CLI, and run under `uvx` because the toolchain needs Python 3.11 or newer. Nothing
generated imports it: the package still supports Python 3.9 and gains no dependency.

**Two places still read an answer by key**, and the scan in `tests/test_shape_contract.py` holds
exactly those to the capture. `service` reads the published service model by key, because it hands
the same objects on to the hand-written reader in `axi_toolkit.ha.services`, and one name there is
accepted as a list or as a single string, which a generated reader of a list gives as nothing. And
the WebSocket client reads a frame by key, because a frame is one of five objects until its `type`
has been read. Everywhere else that scan finds only the tool's own rows, and it fails if an answer
is read by key again.

## Output format

Structured [TOON](https://toonformat.dev/) on stdout by default, which is roughly 40% cheaper in
tokens than the equivalent JSON:

```
$ hass-axi state list --domain light --domain cover
count: 2 of 2 matched (23 total)
states[2]{entity_id,name,state}:
  light.example_lamp,Reading Lamp,off
  cover.example_blind,Example Blind,closed
help[1]:
  Run `hass-axi state get <entity_id>` for one entity's full attributes
```

- `--human` renders aligned tables for a person.
- `--json` emits raw JSON.
- `-v`, `-V` and `--version` each print the bare version — the number and nothing else — and exit 0, in
  every output mode. The bare flag is answered before the rest of the tool is loaded, so probing it
  costs about what starting Python costs.
- **Errors go to stdout too**, in the same structured shape, and carry the command that fixes
  them. stderr carries only diagnostics (`--debug`), which agents do not read.
- Exit codes: `0` success — including idempotent no-ops — `1` error, `2` usage error. A read-only
  refusal is a `2`: the verdict is reached without touching the installation, and no argument to
  the same command changes it. A `401` is a `1`, because only the server could have said it.
- Unknown flags and extra arguments are **rejected by name** rather than ignored, with the
  subcommand's valid flags listed inline so the correction takes one turn, not two:

```
$ hass-axi state list --domian light
error: unknown flag --domian for `state list`
code: UNKNOWN_FLAG
help[2]:
  valid flags for `state list`: --area, --domain, --state, --search, --stale, --limit, --fields (--help always allowed)
  Run `hass-axi state --help` for the full reference
```

One documented deviation: `help[N]:` blocks render one suggestion per line rather than as a
delimiter-joined TOON array. Suggestions are command lines that routinely contain commas, and this
is the shape the AXI standard and the sibling AXI CLIs use. Every **data** structure is strict TOON,
and "strict" is a test result rather than a claim: all 179 of the specification's own conformance
fixtures are vendored into the suite and every one of them has to pass.

## Error codes

Every failure carries a `code` naming the one thing that went wrong, and a `class` naming the kind
of thing it is. The class is what to switch on: it is the difference between retrying, changing the
arguments, and fetching a different token.

```sh
$ hass-axi state list
error: "could not reach Home Assistant: [Errno 111] Connection refused"
code: UNREACHABLE
class: transport
help[2]:
  Check HA_URL points at a reachable Home Assistant instance
  Run `hass-axi doctor` to test the connection
```

<!-- error-codes:start -->

| class | what happened | what to do next |
| --- | --- | --- |
| `usage` | the invocation is wrong; nothing was sent | change the command line |
| `config` | this machine is not set up to talk to Home Assistant | change the environment |
| `transport` | Home Assistant was not reached, or is not serving | change nothing and retry |
| `auth` | the credential was rejected | mint a new long-lived access token |
| `permission` | the credential was accepted; the caller is not permitted | use a different account, or lift the block on the instance |
| `not_found` | the subject named does not resolve to one thing that exists here | look it up and ask again |
| `refused` | the subject exists and this request was refused | change the arguments |
| `internal` | a bug in hass-axi | report it |

Three of those are the ones that used to be indistinguishable, and they demand opposite responses.
An agent that cannot tell a rejected token from a command this version does not have retries the
token forever; one that cannot tell either from an unreachable host reports that Home Assistant is
down when it is not. `permission` is a fourth: Home Assistant answers "your credential is fine, you
are not allowed" on both transports — a banned address over REST, an account that is not an
administrator over the WebSocket — and a new token fixes neither.

The whole vocabulary, which is closed:

- `usage` — `UNKNOWN_COMMAND`, `UNKNOWN_SUBCOMMAND`, `MISSING_SUBCOMMAND`, `UNKNOWN_FLAG`,
  `MISSING_VALUE`, `MISSING_ARGUMENT`, `UNEXPECTED_ARGUMENT`, `CONFLICTING_FLAGS`, `UNKNOWN_FIELD`,
  `BAD_LIMIT`, `BAD_TIMEOUT`, `BAD_TIME`, `BAD_WINDOW`, `BAD_PERIOD`, `BAD_KIND`, `BAD_JSON`,
  `BAD_ICON`,
  `BAD_PAIR`, `BAD_SERVICE`, `MISSING_PATH`, `MISSING_NAME`, `MISSING_TEMPLATE`, `MISSING_COMMAND`, `MISSING_PARAM`, `NO_CHANGES`, `NO_SUCH_COMMAND`,
  `UNREADABLE`, `UNREADABLE_FILE`, `UNWRITABLE`, `READ_ONLY`
- `config` — `NOT_CONFIGURED`, `BAD_URL`, `BAD_TOKEN`, `MISSING_DEPENDENCY`, `REDIRECT_REFUSED`,
  `NOT_HOME_ASSISTANT`
- `transport` — `UNREACHABLE`, `TIMEOUT`, `TLS_ERROR`, `CONNECTION_DROPPED`, `UNAVAILABLE`,
  `WS_HANDSHAKE`, `WS_CLOSED`, `WS_PROTOCOL`
- `auth` — `UNAUTHORIZED`
- `permission` — `FORBIDDEN`
- `not_found` — `NOT_FOUND`, `NO_SUCH_ENTITY`, `NO_SUCH_AREA`, `AMBIGUOUS_AREA`, `NO_SUCH_FLOOR`,
  `AMBIGUOUS_FLOOR`, `NO_SUCH_DEVICE`,
  `AMBIGUOUS_DEVICE`, `NO_SUCH_DOMAIN`, `NO_SUCH_SERVICE`, `NO_SUCH_STATISTIC`,
  `NO_ENTITIES_TARGETED`, `NO_SUCH_WS_COMMAND`, `NO_WEBSOCKET_API`
- `refused` — `BAD_REQUEST`, `METHOD_NOT_ALLOWED`, `SERVER_ERROR`, `API_ERROR`, `INVALID_FORMAT`,
  `NOT_ALLOWED`, `NOT_SUPPORTED`, `HOME_ASSISTANT_ERROR`, `SERVICE_VALIDATION_ERROR`,
  `TEMPLATE_ERROR`, `UNKNOWN_SERVICE_FIELD`, `MISSING_SERVICE_FIELD`, `MISSING_TARGET`,
  `UNSUPPORTED_CAPABILITY`,
  `RESPONSE_REQUIRED`, `RESPONSE_NOT_SUPPORTED`
- `internal` — `INTERNAL_ERROR`, `ID_REUSE`

<!-- error-codes:end -->

Two properties hold across the whole table, and both are enforced by the suite rather than promised
here. **The vocabulary is closed**: a code is always written out at the point it is raised, never
built from a status number or from a string a server sent, so the set above is the whole set and a
caller can switch over it exhaustively. **Classification happens at the transport boundary** —
`RestClient.request` and `WsClient.send_command` — and never in a command body, so a command added
later is classified whether or not its author knew the taxonomy existed. `tests/test_error_codes.py`
runs every subcommand against every fault on both transports and fails on the first one that cannot
say which class it met.

Coverage is bounded, and worth saying plainly: the class is as good as what Home Assistant puts on
the wire. A refusal it renders with no body and no reason — several are — is classified by its
status and nothing more.

## Agent integration

Two ways to make this discoverable. **You only need one.**

**Session hook** — ambient context in every session, for agents that support hooks:

```sh
hass-axi setup hooks
```

Installs a `SessionStart` and a `SessionEnd` hook for Claude Code (`~/.claude/settings.json`) and
Codex (`~/.codex/hooks.json`, plus `[features] hooks = true`), and a managed plugin for OpenCode
that does both jobs. It is idempotent, repairs the recorded path after a reinstall or a move, and
refuses to overwrite a plugin it does not manage.

```sh
hass-axi setup hooks status   # installed, stale or missing, per target; writes nothing
hass-axi setup hooks remove   # takes out what this tool installed, and nothing else
```

`remove` leaves Codex's `[features] hooks = true` on, because every other tool's Codex hooks depend
on it, and deletes the session record described below.

**The session-end hook records what the session did with this tool**, so the next session's context
in the same directory can say so: `last_session: 2026-01-02 ran state list x3 and service call x1
with 1 sent by --write`. It runs `hass-axi context end`, which reads the transcript the agent names
and counts the `hass-axi` commands issued through its tools. The record is command names and counts
and never an argument — arguments are where entity ids, area names and a token typed on a command
line would be — and it is kept in one local file, `sessions.json` under `$XDG_STATE_HOME/hass-axi/`
(`~/.local/state/hass-axi/` when that is unset). It
opens no connection and cannot fail a session's close: anything it cannot read is recorded as
nothing. OpenCode has no session-end event, so its plugin records when a session goes idle.

It owns exactly its own entries, and knows which by a `managed_by` key it writes into each — not
by the command naming this tool. A `SessionStart` hook you wrote yourself is left alone however it
reaches this tool: an environment prefix, another interpreter, a shell wrapper. An entry written by
a release before that key existed is adopted once, in the one shape those releases could produce —
the executable and nothing else — so upgrading repairs the hook you already have rather than adding
a second beside it.

What the hook puts in front of a session is `hass-axi context`, which reads the environment, the
command table and that local record and nothing else — no connection, no token, no address, and
exit 0 whether or not this machine has ever been pointed at Home Assistant:

```
$ hass-axi context
bin: ~/.local/bin/hass-axi
description: Agent CLI for Home Assistant. Reads and writes the registries REST cannot reach and explains a service call Home Assistant refuses. Prefer this over raw curl for Home Assistant operations.
config: HA_URL and HA_TOKEN are set
registries: names and areas live in the registry which only the WebSocket API serves -- `entity list` and `area list` read it; `state list` reads REST and cannot see either
entity_ids: an entity_id is not stable identity and its words mean nothing -- reach an entity with `entity list --search '<the name a user sees>'` or `--area <id|name>` rather than guess one
services: prefer `service call` over `api POST /services/...` -- it explains a refusal Home Assistant returns with no body at all and tells reaching nothing apart from changing nothing
readings: find a reading by what it measures with `sensor list --device-class <class>` and get its total or average over a window with `statistics get <entity_id>`
commands[16]: state,sensor,history,logbook,statistics,service,template,entity,area,device,ws,api,ping,doctor,setup,context
help[4]:
  Run `hass-axi` for this installation at a glance: entity counts by domain and what needs attention
  Run `hass-axi entity list --area <id|name>` to read the registry, which REST cannot reach
  Run `hass-axi service call <domain>.<service> --target-entity <entity_id>` to preview an action and add --write to send it
  Run `hass-axi <command> --help` for its flags, or `hass-axi --help` for all of them
```

`config` reports *which* variables are set and never what they hold. On a machine that has never
been configured the same document comes back with the two exports in its `help` block instead — and
still exits 0, which is the point: a hook runs before anybody has decided to use the tool, so the
one reader who most needs telling that this tool exists is the one with nothing set up yet.

The no-argument view is still where live state lives, and it is worth running once configuration is
in place:

```
$ hass-axi
bin: ~/.local/bin/hass-axi
description: Agent CLI for Home Assistant. Reads and writes the registries REST cannot reach and explains a service call Home Assistant refuses. Prefer this over raw curl for Home Assistant operations.
url: https://homeassistant.example.com
entities: 21 in 10 domains
unavailable: 0
unknown: 7
domains[8]{domain,entities}:
  sensor,12
  conversation,1
  event,1
  input_boolean,1
  light,1
  person,1
  sun,1
  todo,1
not_reporting[4]{entity_id,name,state,for}:
  sensor.example_next_backup,Example Next Backup,unknown,21s
  sensor.example_last_backup,Example Last Backup,unknown,21s
  sensor.example_backup_attempt,Example Backup Attempt,unknown,21s
  person.example_person,Example Person,unknown,21s
low_battery[1]{entity_id,name,battery}:
  sensor.example_remote_battery,Example Remote Battery,14%
help[6]:
  Run `hass-axi state list --domain <domain>` to list entity states
  Run `hass-axi state list` for all 21 entities across 10 domains
  Run `hass-axi entity list --area <id|name>` to read the registry, which REST cannot reach
  Run `hass-axi area list` to see the areas defined here
  Run `hass-axi sensor list --device-class <class>` to find a reading by what it measures
  Run `hass-axi service call <domain>.<service> --target-entity <entity_id>` to preview an action and add --write to send it
```

It opens a connection and prints the installation's address, which is why it is not what a hook
runs: as ambient context it would pay a round-trip at every session start and put an address into
an agent's context. With nothing configured, or with an installation that does not answer, it
still exits 0: it prints `live_state: not available`, the `code` and `class` of what stood in the
way, the command names and the setup lines. `hass-axi ping` and `hass-axi doctor` are the commands
whose exit code reports whether the installation is reachable. The
`url` line reads as the documentation placeholder here because every example in this file was run
against a throwaway Home Assistant, and that is the one line whose value is the reader's own. This
block was re-run for the rename against a fresh one, so its counts are its own rather than the
installation the registry examples above describe. The attention lists — `not_reporting`,
`low_battery`, and stale sensors when there are any — appear only when they have something in them.

`unavailable` and `unknown` are counted apart because they are different facts: `unknown` means
reachable and not yet reporting, which is much the commoner of the two, and summing them under the
name of one would contradict `state list --state unavailable` outright.

**Agent Skill** — loads on demand, no per-session token cost, works in any agent that supports
skills:

```sh
npx skills add dmealing/hass-axi --skill hass-axi
```

`skills/hass-axi/SKILL.md` is generated from the CLI's own command table by `hass-axi setup skill`, and
`scripts/ci-local.sh` runs `hass-axi setup skill --check` so it can never drift from the commands it documents.

## This repository is public, and stays generic

This tool talks to home automation installations, so the failure that matters is not a bug — it is
a commit that describes, or grants access to, someone's house. That is enforced by a scanner, not
by a convention:

```sh
scripts/leakcheck.py            # scan every tracked file
scripts/leakcheck.py --staged   # scan what a commit would actually record
scripts/leakcheck.py --commit-msg <path>   # scan a commit message
scripts/leakcheck.py --pull-request <n>    # scan a pull request's title and body
scripts/leakcheck.py --rules    # list the rules and what each one catches
scripts/leakcheck.py --demo     # self-test: prove every rule still fires
```

**What it catches.** Run `--rules` for the live list; today it is JWTs (what a Home Assistant token
looks like), RFC1918 and CGNAT addresses, IPv4/IPv6 link-local and unique-local addresses, `.local`
/`.lan`/`.localdomain` hostnames, `*.ui.nabu.casa` remote-access hostnames, geographic coordinates
(`/api/config` returns the house's), MAC and Zigbee IEEE addresses, absolute home directories,
emails outside the reserved documentation domains, and literal bearer credentials.

Each file is scanned twice: once **per line**, and once **condensed**, with whitespace, quotes,
backslashes and `+` removed from the whole file. A credential split across lines or assembled by
concatenation is invisible to a line pass, and splitting a token across fragments is exactly how one
hides — deliberately or not. The condensed pass runs only the token rules, because joining arbitrary
lines can fuse unrelated digits into a plausible address, and a guard that cries wolf gets bypassed.

**What it does not catch,** stated plainly so the coverage is not mistaken for more than it is: a
generic public hostname or IP that happens to be someone's instance, a secret that is neither
JWT-shaped nor bearer-prefixed, anything inside a binary or an image, and any shape no rule
describes. The scanner narrows the ways a leak can happen; it does not make review unnecessary.

A line that must legitimately keep one of these shapes carries `leakcheck: allow=<rule>`. The
exemption is **per rule on purpose** — a blanket marker would switch off every rule on that line,
including one nobody was thinking about, which is how a live credential hides behind a suppressed
lint.

It runs in four places:

```sh
scripts/install-hooks.sh   # points core.hooksPath at .githooks
```

- **`.githooks/pre-commit`** blocks the commit locally, including the very first commit in a
  repository, which has no `HEAD` to diff against.
- **`.githooks/commit-msg`** scans the message, which is a separate channel from file content and
  just as public.
- **CI** runs `--demo` first — proving the scanner still detects what it claims — and then scans
  the whole tree. The demo's own output is published too, so it reports findings without the
  values. Bypassing the local hooks only delays the failure.
- **The pull request itself**, on every open, push *and edit*. A title and a body are published the
  moment they are written, are in no checkout and pass under no hook, so nothing above reaches them
  — and tooling routinely pastes captured output into a body, where a `pytest` header carries a
  `rootdir:` line holding an absolute path. It fails the check when it cannot read the pull request
  rather than reporting a clean it cannot support, and it reports the field, line and rule of a
  match — plus the offset when the finding's pass read the text as written — without printing the
  match: a CI log is more public than the page it came from. For the same
  reason a pull request cannot carry an `allow=` marker — in a file that marker is committed and
  reviewed, and in a body it is an off-switch anyone can add after every check has run.

A commit message is checked as well as scanned. release-please builds the changelog and the version
bump from commit messages, and when its parser cannot read one it says so at debug level, drops the
commit and **exits 0** — a merged fix that is never published, with a green release run over it. So
`.githooks/commit-msg` also runs `scripts/commitcheck.py`, and the release workflow re-checks every
commit since the last tag. Rich commit bodies are the point of this history and nothing here
restricts them; the one shape to know is that a body line must not *begin* with a word run straight
into an unclosed or nested parenthesis — `` `Decimal(repr(v))` `` at a line start is refused, and the
same phrase one word further along the line is fine. This repository has never lost a release to it,
and `tests/fixtures/commit-messages/` records how narrowly. Run `scripts/commitcheck.py --rules` for
the grammar rule and its citation.

Fixtures, tests, docs and examples use invented identifiers throughout:
`light.example_lamp`, area `Example Room`, `https://homeassistant.example.com`. Test fixtures build
credential shapes at run time rather than embedding literals, so the suite that proves the scanner
works does not itself trip it.

## Testing

```sh
scripts/dev-setup.sh                    # creates .venv; see Install for why it is not a bare install
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

**No live installation and no live token are needed, which is the point.** The suite runs against
real local servers on loopback: an `http.server` for REST, and a real `websockets` server that
performs the same `auth_required` / `auth` / `auth_ok` handshake Home Assistant does. A second
suite does talk to a real Home Assistant, and is opt-in: see "The live suite" below.

Covered by tests:

- the TOON encoder **against the specification's own encode fixtures**, every one of them,
  published by [`toon-format/spec`](https://github.com/toon-format/spec) and run on every
  `pytest`. The encoder is the shared `axi-toolkit` library's, which also carries the fixtures
  and the encoder's own rule-by-rule suite — tabular, keyed tabular, list and inline forms,
  quoting, escaping, delimiters, root forms. The case
  count is asserted too, so a fixture that stops being collected fails the suite instead of
  quietly lowering the score;
- every command's output shape, filters, field selection, limits and empty states;
- the read-only gate, by enumerating both command tables and failing on the first subcommand or
  WebSocket command that carries no classification — so the guard is whole because of the sweep,
  not because somebody remembered every command;
- the WebSocket protocol beyond the happy path: the handshake, an unexpected greeting, a message
  arriving mid-authentication, auth rejection, event and pong frames interleaved with results, id
  correlation, a non-JSON frame, a socket closed mid-command, both directions of the error
  boundary, implicit connect, and a registry larger than the library's default frame size;
- **credential containment**: a cross-origin redirect refused rather than followed, a token that
  cannot be a header rejected before it reaches one, URL userinfo stripped and redacted, an
  unexpected exception rendered as a structured error on stdout instead of a traceback, and
  **stderr asserted clean and redacted** — the gap that let two escapes ship;
- flag validation, renamed-flag hints, exit codes, `--help` in every position (and never stolen
  from a flag value), and `--help` for every command without any configuration present;
- **the exit-code contract as a table** (`tests/test_exit_codes.py`): exit 2 is a usage error and is
  decided without the installation, so every static fault is run reachable, unreachable and
  unconfigured and has to be the same error in all three — swept over every value-taking flag of
  every subcommand from the dispatch table, so a flag added later is held to it;
- **the encoder, read back by a decoder this project did not write** (`tests/test_toon_roundtrip.py`):
  the specification's fixtures, and every command's TOON against its own `--json`, through
  `toon-format`. Nothing there compares the tool with itself;
- **properties, with generated input** (`tests/test_properties.py`): any JSON value round-trips, and
  any argument vector ends in exit 0, 1 or 2 with a parseable document whose class is `usage`
  exactly when the exit is 2;
- **golden snapshots of the text an agent reads** (`tests/test_snapshots.py`): `--help` for every
  command, one of each kind of error document, and the `context` document. Text this tool writes
  from its own declarations only — never live data;
- **destructive registry cases, offline** (`tests/test_registry_writes.py`): an area, a floor, an
  entity and a device created, renamed, collided, moved and deleted against a double that refuses
  the way Home Assistant's own handlers do — duplicate names, an id of another domain, a delete of
  what is not there — and every typed write as a preview that sends nothing;
- the leak scanner adversarially: every rule against the shape it claims, every rule against
  content that must not trip it, the split/concatenated/percent-encoded evasions, the scoped allow
  marker, and both git hooks end to end through a real `git commit`;
- hook installation: idempotency, path repair, atomic writes, leaving other tools' *and the
  user's own* hooks alone, collapsing a duplicate managed entry wherever it sits, and every value
  the Codex features flag can already hold — including the ones that used to make the tool append a
  duplicate TOML key its parser refuses.

**What only a live installation can confirm** — that a real Home Assistant accepts the exact request
bodies built here, `return_response` behaviour, how a very large registry behaves in practice, and
whether a published `supported_features` requirement agrees with the one enforced — is what the
live suite below is for. The doubles implement the documented protocol and enforce the
parts of it that are known: the REST double rejects a nested service-call `target` the way Home
Assistant does, refuses an unknown service with the same empty `400` and no body, and drops an
`unavailable` or incapable entity in the same silence. So they verify this client against the
specification rather than against a particular server build.

What the doubles do *not* get to invent is the shape of ordinary data. Their fixtures carry the
distribution a real installation has — entries named entirely by their device, entries that name
only their own half, a `has_entity_name` of each setting, a disabled entry with no state at all, an
`unknown` state as well as an `unavailable` one, services and fields that publish no prose — and
`tests/test_double_fidelity.py` asserts each of those shapes is still present. Every one of them was
absent once, and each absence cost a defect that a green suite could not see.

The names themselves are held to a real server. `tests/fixtures/ha-shape/capture.json` is what the
pinned lab container sent, reduced by `scripts/shapecapture.py` to the keys each object carries and
the JSON type of each: a state, the registry entries, a service definition, the recorder's
statistics, history and logbook rows, and the WebSocket frames. It holds no value and nobody's
house. `tests/test_shape_contract.py` reads the doubles with the same script and scans the source
for every key still read off an answer by name, and fails on a name the capture does not have
unless the name is listed with the reason the lab could not show it. A key read through a generated
reader is held to the same capture by the generated check.

```sh
.venv/bin/python scripts/shapecapture.py --check           # has the lab drifted from the capture
.venv/bin/python scripts/shapecapture.py --write           # refresh the capture; needs docker
.venv/bin/python scripts/shapecapture.py --check --house   # how an installation differs, read-only
```

`--write` starts the lab itself and accepts no other source. `--check --house` reads `HA_URL` and
`HA_TOKEN` (or `--env-file <path>`), sends reads only, and prints counts and key names: never the
address, the token or a value. Neither runs from `pytest`, `scripts/ci-local.sh` or the gate.

### The live suite

`tests/live/` runs this build against a real Home Assistant and compares every answer with
something the tool did not produce: the raw REST and WebSocket APIs read by the harness itself, an
independent TOON decoder, and the recorder's own rows. It is **opt-in twice** — `pyproject.toml`
deselects the `live` marker, so `pytest`, `scripts/ci-local.sh` and the gate never collect it, and
every test in it skips without `HASS_AXI_LIVE=1` — and it is run by hand:

```sh
scripts/live-test.sh --house           # reads, previews and loopback faults
scripts/live-test.sh --house-writes    # the same, plus writes that undo themselves
scripts/live-test.sh --lab             # rejected credentials and destructive writes, in a container
scripts/live-test.sh --house --env-file <path>   # HA_URL and HA_TOKEN from a file, never printed
```

| tier | what runs | where |
| --- | --- | --- |
| A | reads, swept over every domain and area and checked against the raw API | the installation in `HA_URL` |
| B | a preview of every write shape, with a fingerprint of the registries before and after | the same |
| C | writes that reverse themselves and are read back: a notification created and dismissed, a stored value set to itself, one scratch area created and deleted | the same, only with `--house-writes` |
| D | faults — a closed port, a bad certificate, a redirect, a 200 from something that is not Home Assistant — from loopback stubs with a synthetic token | this machine |
| E | rejected credentials, and an entity, area, floor and device really renamed, moved and deleted | a disposable container, removed afterwards |

Three rules keep it safe to point at a house. **Targets are chosen by shape at run time** — "a light
that is on or off", "an entity whose area comes from its device" — so the suite holds no entity id,
area name or address, and a shape an installation lacks skips rather than fails. **Nothing in tiers
A to D** sends an invalid credential to the installation, calls a service on a real device, or
renames or moves anything real; a rejected credential raises a notification and can ban an address
on a real server, so those cases run only in the container. And **the token is never kept**: it is
scrubbed from every result, an invocation that prints it or any segment of it fails, and the log
records commands, exits and timings but not output, because the output of a real installation is
that installation's data.

The lab tier needs `docker`. It creates a container from a pinned image
(`HASS_AXI_LAB_IMAGE` selects another release), onboards it, mints a credential that exists only in
that process, and removes the container whether the tests passed or not. Nothing in the suite makes
an LLM call or uses a paid API, and nothing is scheduled: there is no cron entry, timer or workflow
that runs it, by design.

## Continuous integration

GitHub Actions is disabled on this repository, so none of these workflows runs today. The checks in
`ci.yml` run locally through `scripts/ci-local.sh`, which the no-mistakes gate runs on every change
(`.no-mistakes.yaml`); `scripts/ci-local.sh --matrix` adds `pytest` on 3.9–3.12.

| Workflow | Runner | Triggers | What runs |
| --- | --- | --- | --- |
| `ci.yml` | self-hosted | push to `main`, nightly, manual | each section of `scripts/ci-local.sh`: leak scan, commit audit, lint, `pytest` on 3.9–3.12, generated-skill check, model drift check |
| `hygiene.yml` | `ubuntu-latest` | `pull_request`, including `edited` | the leak scan of the tree, and the two checks that read the pull request's own title and body |
| `release.yml` | `ubuntu-latest` | push to `main`, manual | release-please, and an OIDC publish when a release PR merges; not the route in use, see [Releasing](#releasing) |

With Actions enabled, a pull request shows **one** hosted check, and that is deliberate. The leak scan is the
gate that has to run before a human reads a diff; everything heavier runs on the maintainer's own
machine, where the full matrix is free and does not queue behind anyone. `edited` is in that
trigger list because a pull request's title and body are published the moment they are written,
exist in no checkout, pass under no hook, and can be rewritten after every other check has run. `ci.yml` never
triggers on `pull_request` and must not start to — this repository is public and that runner is a
personal workstation, so a pull-request trigger would give any contributor code execution on it.

## Releasing

Version bumps and the changelog are driven from conventional commits by
[release-please](https://github.com/googleapis/release-please), which prepares a release PR when
`main` carries user-facing commits.

**Releases are cut and uploaded locally while GitHub Actions is disabled.** `release.yml` does not
run, so nothing on GitHub opens the release PR or publishes one: release-please is run from the
maintainer's workstation, and every release since 0.8.0 has been built, smoke-tested and uploaded
to PyPI from there, authenticated with a long-lived PyPI API token. That token is held on that
workstation and is in no file in this repository. A release uploaded this way carries no
attestations: nothing published beside the files says which commit or which build produced them.

`release.yml` describes the other route, which is not in use: with Actions enabled, merging the
release PR builds the distribution, smoke-tests the built wheel, and publishes through
[trusted publishing](https://docs.pypi.org/trusted-publishers/) — an OIDC exchange that needs no
stored token and attests what it uploads. No release of `hass-axi` has been published that way.
It requires a one-time configuration on PyPI by the repository owner (project
`hass-axi`, owner `dmealing`, workflow `release.yml`, environment `pypi`); the trusted publisher
registered for `ha-axi` does not carry over. The transitional `ha-axi` package under
`legacy/ha-axi/` is not built by the workflow — see [Moving from `ha-axi`](#moving-from-ha-axi).

## Moving from `ha-axi`

This tool was published as `ha-axi` up to 0.7.1, and renamed to `hass-axi` because an unrelated
Home Assistant CLI is published under the old name and installs the same binary.

| before | now |
| --- | --- |
| `pip install ha-axi` | `pip install hass-axi` |
| `ha-axi …` | `hass-axi …` |
| `import ha_axi` | `import hass_axi` |
| `HA_AXI_READ_ONLY` | `HASS_AXI_READ_ONLY` |
| `HA_AXI_DEBUG` | `HASS_AXI_DEBUG` |
| `skills/ha-axi/SKILL.md` | `skills/hass-axi/SKILL.md` |

`HA_URL`, `HA_TOKEN` and their `HASS_SERVER`/`HASS_TOKEN` aliases are unchanged. The old
`HA_AXI_*` names still work and print a one-line deprecation notice on stderr; a session that sets
`HA_AXI_READ_ONLY` is still read-only, because an upgrade must never be what switched the guard
off. Run `hass-axi setup hooks` once after upgrading: it rewrites the hook entry and removes the
OpenCode plugin the old name installed. It recognises them by the exact command this tool wrote, so
a hook belonging to the other `ha-axi` is left alone.

**The final `ha-axi` release is a transitional package.** `legacy/ha-axi/` in this repository builds
`ha-axi` 0.8.0, which contains no code of its own: it depends on `hass-axi`, so `pip install -U
ha-axi` brings the renamed tool in, and its `ha-axi` command forwards to `hass-axi` after a
deprecation notice on stderr, so existing scripts and hooks keep working until they are moved. It is
published once, by hand, after `hass-axi` itself is on PyPI, and nothing is published under the old
name after it. Uninstalling it (`pip uninstall ha-axi`) removes the forwarding command and frees the
name for the other tool.

## License

MIT.

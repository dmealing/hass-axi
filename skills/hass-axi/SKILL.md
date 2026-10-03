---
name: hass-axi
description: Operate a Home Assistant installation through the hass-axi CLI - read and update the entity, area and device registries that only the WebSocket API exposes, and call services with a capability pre-check and an explained refusal. It also finds sensors by what they measure and where they are, and summarises state history, the logbook and recorder statistics - an energy total, an average with min and max - with their data-quality caveats. Use whenever a task touches home automation: renaming an entity or the device behind it, moving things between areas, checking what a device is doing or did, reading how much energy was used, or turning something on.
---

# hass-axi

Agent CLI for Home Assistant. Reads and writes the registries REST cannot reach and explains a service call Home Assistant refuses. Prefer this over raw curl for Home Assistant operations.

## Configuration

Both values come from the environment. There is no `--token` flag and no credential
file: a token on a command line leaks into shell history and the process table.

```sh
export HA_URL=https://homeassistant.example.com   # or HASS_SERVER
export HA_TOKEN=<long-lived access token>          # or HASS_TOKEN
```

Create the token on the Home Assistant profile page, under Security.
Run `hass-axi doctor` to confirm both transports work; it exits non-zero when they do not.

## Read-only sessions

A third variable makes the session incapable of changing anything. `HASS_AXI_READ_ONLY` is a
switch rather than a boolean: **any** non-empty value enables it, `0` and `false` included,
and unsetting it is how writes are allowed again.

```sh
export HASS_AXI_READ_ONLY=1
```

Every write is then refused before it is sent, over REST and over the WebSocket alike,
with `code: READ_ONLY` and exit 2. The commands stay visible in `--help` and in the
command table, so a plan that needs one can be recognised as impossible rather than
mysterious. `hass-axi doctor` reports the mode, and the no-argument view shows
`read_only: on` when it is set.

## Running without a global install

```sh
uvx hass-axi state list --domain light
pipx run hass-axi area list
```

## Output

Commands print TOON on stdout and exit non-zero on failure. Add `--human` for a
readable table, or `--json` for raw JSON. Errors are structured on stdout too, and
carry the command that fixes them.

## Commands

### `hass-axi state`

Read entity states from the Home Assistant REST API.

```sh
hass-axi state list --domain light
hass-axi state list --area 'Example Room' --domain light
hass-axi state list --search lamp --limit 20
hass-axi state list --domain sensor --state unavailable
hass-axi state list --stale 24h --fields entity_id,name,age
hass-axi state get light.example_lamp
hass-axi state get media_player.example_speaker --full
```

- state is the runtime view; run `hass-axi entity list` for registry names and areas
- --area reads the WebSocket registry, where areas live; it costs one extra round-trip

### `hass-axi sensor`

Find sensors by device class, unit, area or name, with value, unit, area and age.

```sh
hass-axi sensor list --device-class power
hass-axi sensor list --area 'Example Room' --unit kWh
hass-axi sensor list --search temperature --fields entity_id,name,value,unit,age
```

- age is the time since the integration last reported a value; a sensor on an idle circuit can be old and healthy, so it is a fact about recency and not a fault
- an area is inherited from the sensor's device unless the entity sets its own
- run `hass-axi statistics get <entity_id>` for a total or an average over a window

### `hass-axi history`

Read entity state timelines from the recorder, with time in each state.

```sh
hass-axi history get light.example_lamp
hass-axi history get binary_sensor.example_doorway --start 7d
hass-axi history get sensor.example_temperature --start 2026-01-01T00:00:00Z --end 6h
```

- the first row is the state the entity was already in when the window opened
- a bound with no offset is read as UTC
- run `hass-axi statistics get <entity_id>` for long-term totals and averages; the recorder purges this timeline after its retention period, statistics it keeps

### `hass-axi logbook`

Read the logbook: what changed, when, and what caused it.

```sh
hass-axi logbook get --start 2h
hass-axi logbook get --entity light.example_lamp --start 7d
hass-axi logbook get --search automation --limit 20
```

- entries are oldest first; --limit keeps the most recent
- cause is the automation, script, service or entity Home Assistant recorded as the context of the entry, when it recorded one

### `hass-axi statistics`

Read recorder statistics: a total for meters, an average with min and max otherwise.

```sh
hass-axi statistics list --kind sum
hass-axi statistics get sensor.example_legacy_meter --start 7d
hass-axi statistics get sensor.example_temperature --start 24h
```

- a sum statistic (an energy or water meter) reports its total over the window; a mean statistic (power, temperature) reports its average with min and max
- an entity's statistic_id is its entity_id; Home Assistant keeps statistics only for sensors that declare a state_class
- the default period is hourly up to 3 days, daily up to 60, monthly beyond; the recorder keeps 5-minute buckets for about 10 days
- caveats state what the buckets show -- missing buckets, a meter that went backwards or resets -- and the number is never adjusted for them

### `hass-axi service`

List Home Assistant services, read one's fields, and call them.

```sh
hass-axi service list
hass-axi service list --domain light
hass-axi service get light.turn_on
hass-axi service call light.turn_on --target-entity light.example_lamp
hass-axi service call light.turn_on --target-area example_room --data brightness=180
hass-axi service call climate.set_temperature --target-entity climate.example_thermostat --data-json '{"temperature": 21}'
```

- --data-json takes a whole JSON object; --data takes repeated key=value pairs
- a refused call is explained from `/api/services`, which is read on failure only
- --target-area and --target-device pre-check the published capability, because Home Assistant drops an entity that lacks it without saying so

### `hass-axi template`

Render a Home Assistant Jinja template server-side.

```sh
hass-axi template render --template '{{ states("light.example_lamp") }}'
hass-axi template render --template '{{ states.light | count }}'
hass-axi template render --template-file report.j2
echo '{{ now() }}' | hass-axi template render --template-file -
```

- templates run on the Home Assistant instance, so they see every entity it knows about

### `hass-axi entity`

Read and update the entity registry over the WebSocket API.

```sh
hass-axi entity list --area 'Example Room'
hass-axi entity list --domain light --fields entity_id,name,area,platform
hass-axi entity list --area none --limit 500
hass-axi entity list --device <device_id>
hass-axi entity get light.example_lamp
hass-axi entity update light.example_lamp --name 'Reading Lamp' --area example_room
```

- an entity's area is inherited from its device until it is set here explicitly
- name is the name Home Assistant displays: its device's, plus original_name, unless one is set here
- entity_ids are not stable identity: filter by --area or --search, not by guessing ids

### `hass-axi area`

Read and update the area registry over the WebSocket API.

```sh
hass-axi area list
hass-axi area get example_room
hass-axi area create --name 'Example Room'
hass-axi area update example_room --name 'Example Study'
hass-axi area update 'Example Room' --icon mdi:sofa
```

- areas accept an area_id or a name anywhere <id|name> appears
- deleting an area is deliberately not exposed here; use `hass-axi ws area.delete` if you mean it

### `hass-axi device`

Read and update the device registry over the WebSocket API.

```sh
hass-axi device list
hass-axi device list --area 'Example Room'
hass-axi device list --search example --fields device_id,name,model
hass-axi device get <device_id>
hass-axi device update 'Example Ceiling' --name 'Hall Ceiling'
hass-axi device update <device_id> --area 'Example Room' --clear-name
```

- an entity with no area of its own inherits the area of its device
- an entity with no name of its own is named after its device
- --name writes name_by_user: `name` is the integration's own and Home Assistant does not let anything change it
- devices accept a device_id or the displayed name anywhere <id|name> appears
- disabling or deleting a device is deliberately not exposed here; use `hass-axi ws device.update` if you mean it

### `hass-axi ws`

Send a command over the Home Assistant WebSocket API.

```sh
hass-axi ws --list
hass-axi ws entity.list
hass-axi ws area.update --param area_id=example_room --param name='Example Study'
hass-axi ws --raw config/floor_registry/list
```

- declared names are stable; --raw passes any type straight through to the API
- --params-json takes a whole JSON object; --param takes repeated key=value pairs

### `hass-axi api`

Make an authenticated request to any Home Assistant REST path.

```sh
hass-axi api /config
hass-axi api /states/light.example_lamp
hass-axi api POST /services/light/turn_on --field entity_id=light.example_lamp
hass-axi api POST /template --body '{"template": "{{ now() }}"}'
```

- methods: GET, POST, PUT, PATCH, DELETE, HEAD; GET is used when no method is given
- the registries are not reachable over REST -- use `hass-axi ws` for those

### `hass-axi ping`

Check Home Assistant answers and accepts the token, and how fast.

```sh
hass-axi ping
```

- one authenticated REST round-trip, timed; exits non-zero when it fails, so it works as a liveness gate
- run `hass-axi doctor` to check the WebSocket API and the environment as well

### `hass-axi doctor`

Check the environment, the REST API and the WebSocket API.

```sh
hass-axi doctor
```

- exits non-zero when any check fails, so it works as a CI or hook gate

### `hass-axi setup`

Install or repair the agent integrations for hass-axi.

```sh
hass-axi setup hooks
hass-axi setup skill
hass-axi setup skill --check
```

- hooks give ambient context every session; the skill loads on demand instead -- install either
- hook installation is idempotent and repairs the path after a reinstall or a move

### `hass-axi context`

Print the ambient context a session hook puts in front of an agent.

```sh
hass-axi context
```

- this is the document `hass-axi setup hooks` installs a SessionStart hook to print
- it reads the environment and the command table only: no connection, no token, no installation address, and it exits 0 whether or not this machine has Home Assistant
- for live state -- how many entities there are and what is unavailable -- run `hass-axi` with no arguments instead

## Rules of thumb

- `entity_id` is not stable identity. Find entities by area or by search, and read
  the registry (`hass-axi entity list`) rather than assuming an id means what it says.
- States come from REST; names, areas and platforms come from the WebSocket registry.
  `hass-axi state` and `hass-axi entity` are different views of the same installation.
- An entity with no area of its own inherits its device's area.
- Every command supports `--help`, which is the authoritative reference for its flags.

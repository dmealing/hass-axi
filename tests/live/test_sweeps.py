"""P3: the tool's answers, compared with the raw API computed independently."""

from __future__ import annotations

import pytest

BIG = ["--limit", "1000000"]


def state_ids(target) -> set:
    return {state["entity_id"] for state in target.rest("/states")}


def within(mine, before: set, after: set, what: str) -> None:
    """``mine`` holds everything present throughout and nothing absent throughout.

    A live installation gains and loses entities while the suite runs, so the
    raw API is read on both sides of the tool and the answer has to lie between
    the two: nothing that was there the whole time may be missing, and nothing
    may appear that was never there.
    """
    mine = set(mine)
    missing, extra = (before & after) - mine, mine - (before | after)
    assert not missing and not extra, f"{what}: {len(missing)} missing, {len(extra)} unexpected"


def test_every_domain_lists_the_states_the_raw_api_holds(house, snapshot):
    before = state_ids(house)
    listed = {}
    for domain in snapshot.domains():
        document = house.ok("state", "list", "--domain", domain, *BIG, "--fields", "entity_id")
        listed[domain] = [row["entity_id"] for row in document["states"]]
    after = state_ids(house)
    for domain, mine in listed.items():
        prefix = domain + "."
        assert all(entity_id.startswith(prefix) for entity_id in mine)
        within(
            mine,
            {i for i in before if i.startswith(prefix)},
            {i for i in after if i.startswith(prefix)},
            f"domain {domain}",
        )


def test_state_and_device_listings_follow_the_same_areas(house, snapshot):
    def area_of(entity_id: str) -> str:
        entry = snapshot.entity.get(entity_id)
        return snapshot.effective_area(entry) if entry else ""

    devices = 0
    for area_id in [*snapshot.area, "none"]:
        wanted = "" if area_id == "none" else area_id
        before = state_ids(house)
        document = house.ok("state", "list", "--area", area_id, *BIG, "--fields", "entity_id")
        after = state_ids(house)
        within(
            [row["entity_id"] for row in document["states"]],
            {i for i in before if area_of(i) == wanted},
            {i for i in after if area_of(i) == wanted},
            f"states in area {area_id}",
        )
        listing = house.ok("device", "list", "--area", area_id, *BIG, "--fields", "device_id")
        raw_devices = sorted(
            d["id"]
            for d in snapshot.devices
            if ((d.get("area_id") if d.get("area_id") in snapshot.area else "") or "") == wanted
        )
        assert sorted(row["device_id"] for row in listing["devices"]) == raw_devices
        devices += len(raw_devices)
    assert devices == len(snapshot.devices)


def test_sensor_listings_agree_with_the_raw_states(house, snapshot):
    fields = ["--fields", "entity_id,device_class"]
    before = house.rest("/states")
    everything = house.ok("sensor", "list", "--all", *BIG, *fields)
    after = house.rest("/states")

    def sensors(states, device_class=None) -> set:
        return {
            s["entity_id"]
            for s in states
            if s["entity_id"].startswith("sensor.")
            and (device_class is None or s["attributes"].get("device_class") == device_class)
        }

    within(
        [r["entity_id"] for r in everything["sensors"]], sensors(before), sensors(after), "sensors"
    )
    classes = {s["attributes"].get("device_class") for s in before} - {None}
    for device_class in sorted(c for c in classes if sensors(before, c)):
        document = house.ok(
            "sensor", "list", "--all", "--device-class", device_class, *BIG, *fields
        )
        within(
            [r["entity_id"] for r in document["sensors"]],
            sensors(before, device_class) & sensors(after, device_class),
            sensors(before, device_class)
            | sensors(after, device_class)
            | sensors(house.rest("/states"), device_class),
            f"device class {device_class}",
        )


def test_a_state_is_reported_as_the_raw_api_holds_it(house, snapshot):
    sample = sorted(snapshot.state)[:: max(1, len(snapshot.state) // 60)]
    compared = 0
    for entity_id in sample:
        result, document = house.doc("state", "get", entity_id, "--full")
        if result.code != 0:
            # Gone while the suite ran is the only acceptable reason.
            assert entity_id not in state_ids(house), f"state get failed: {document.get('error')}"
            continue
        raw = house.rest(f"/states/{entity_id}")
        if raw["last_updated"] != document["state"]["last_updated"]:
            continue  # it changed between the two reads
        assert document["state"]["state"] == raw["state"]
        assert set(document["attributes"]) == set(raw["attributes"])
        compared += 1
    assert compared


def test_the_areas_and_none_partition_the_registry(house, snapshot):
    """Every entity is in exactly one area or in none, device-inherited areas included."""
    seen: list = []
    for area_id in [*snapshot.area, "none"]:
        document = house.ok("entity", "list", "--area", area_id, *BIG, "--fields", "entity_id")
        mine = sorted(row["entity_id"] for row in document["entities"])
        wanted = "" if area_id == "none" else area_id
        raw = sorted(
            e["entity_id"] for e in snapshot.entities if snapshot.effective_area(e) == wanted
        )
        assert mine == raw, f"area {area_id!r} differs from the registry"
        seen.extend(mine)
    assert sorted(seen) == sorted(snapshot.entity)


def test_an_area_by_name_is_the_same_area_by_id(house, snapshot):
    for area_id, area in snapshot.area.items():
        names = [a["name"] for a in snapshot.areas]
        if names.count(area["name"]) > 1:
            continue
        by_id = house.ok("entity", "list", "--area", area_id, *BIG, "--fields", "entity_id")
        by_name = house.ok("entity", "list", "--area", area["name"], *BIG, "--fields", "entity_id")
        assert by_id["entities"] == by_name["entities"]


def test_area_list_counts_sum_to_the_registry(house, snapshot):
    document = house.ok("area", "list")
    if "areas" not in document or isinstance(document["areas"], str):
        pytest.skip("this installation has no areas")
    placed = sum(row["entities"] for row in document["areas"])
    assert placed + document["unassigned_entities"] == len(snapshot.entities)


def test_the_displayed_name_is_the_name_home_assistant_shows(house, snapshot):
    """The composed registry name equals the state's `friendly_name`, for every entity with both."""
    document = house.ok("entity", "list", *BIG, "--fields", "entity_id,name")
    mine = {row["entity_id"]: row["name"] for row in document["entities"]}
    wrong = [
        entity_id
        for entity_id, state in snapshot.state.items()
        if entity_id in mine
        and state["attributes"].get("friendly_name") is not None
        and mine[entity_id] != state["attributes"]["friendly_name"]
    ]
    assert not wrong, f"{len(wrong)} names differ from friendly_name"
    assert all(mine[i] == snapshot.displayed_name(snapshot.entity[i]) for i in mine)


def test_every_device_row_agrees_with_the_registry(house, snapshot):
    document = house.ok("device", "list", *BIG, "--fields", "device_id,name,area_id,entities")
    per_device: dict = {}
    for entry in snapshot.entities:
        if entry.get("device_id"):
            per_device[entry["device_id"]] = per_device.get(entry["device_id"], 0) + 1
    assert len(document["devices"]) == len(snapshot.devices)
    for row in document["devices"]:
        device = snapshot.device[row["device_id"]]
        assert row["name"] == snapshot.device_name(device)
        assert row["area_id"] == (device.get("area_id") or "")
        assert row["entities"] == per_device.get(row["device_id"], 0)


def test_every_service_reports_the_fields_it_publishes(house, snapshot):
    checked = 0
    for entry in snapshot.services:
        for name, spec in entry["services"].items():
            if checked >= 400:
                break
            document = house.ok("service", "get", f"{entry['domain']}.{name}", "--fields", "field")
            published = set()
            for key, field in (spec.get("fields") or {}).items():
                nested = field.get("fields") if isinstance(field, dict) else None
                published.update(nested if isinstance(nested, dict) else [key])
            listed = {row["field"] for row in document["fields"]}
            assert listed == published, f"{entry['domain']}.{name} lists other fields"
            checked += 1
    assert checked

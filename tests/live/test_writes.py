"""P7: previews that send nothing, read-only sessions, and writes that undo themselves.

Tier B (previews) and the read-only cases run against the installation and are
proved harmless by a fingerprint taken before and after. Tier C is opt-in and
is limited to writes that reverse themselves and are read back through the raw
API: a persistent notification created and dismissed, a registry update that
sets a stored value to itself, and one scratch area created, changed and
deleted inside the same test. Nothing real is renamed, moved or switched.
"""

from __future__ import annotations

import hashlib
import json
import secrets

from .harness import contract

WRITE = "--write"
PREVIEW = "nothing was sent to Home Assistant"


def notifications(target) -> list:
    return sorted(n["notification_id"] for n in target.ws("persistent_notification/get"))


def fingerprint(target, entity_id: str) -> str:
    """Everything a stray write could have changed, reduced to one hash."""
    frames = target.ws_many(
        [
            {"type": "config/entity_registry/list"},
            {"type": "config/device_registry/list"},
            {"type": "config/area_registry/list"},
            {"type": "config/floor_registry/list"},
            {"type": "persistent_notification/get"},
        ]
    )
    state = target.rest(f"/states/{entity_id}")
    parts = [f["result"] for f in frames[:4]]
    parts.append(sorted(n["notification_id"] for n in frames[4]["result"]))
    parts.append([state["state"], state["last_updated"]])
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def previews(snapshot) -> list:
    light = snapshot.light()
    area = snapshot.biggest_area()
    device = snapshot.devices[0]["id"]
    scratch = "hass-axi live preview only"
    return [
        ["service", "call", "light.turn_on", "--target-entity", light],
        ["service", "call", "light.turn_off", "--target-entity", light],
        ["service", "call", "light.turn_on", "--target-entity", light, "--data", "brightness=120"],
        [
            "service",
            "call",
            "light.turn_on",
            "--target-entity",
            light,
            "--data-json",
            '{"brightness": 120}',
        ],
        ["service", "call", "light.turn_on", "--target-area", area],
        ["service", "call", "light.turn_on", "--target-area", snapshot.area[area]["name"]],
        ["service", "call", "light.turn_on", "--target-device", device],
        ["service", "call", "light.turn_on"],
        ["service", "call", "persistent_notification.create", "--data", "message=preview only"],
        ["api", "POST", "/services/light/turn_on", "--field", f"entity_id={light}"],
        ["api", "POST", "/states/sensor.hass_axi_preview_only", "--body", '{"state": "1"}'],
        ["api", "DELETE", "/states/sensor.hass_axi_preview_only"],
        ["api", "put", "/config/zz"],
        ["ws", "entity.update", "--param", f"entity_id={light}", "--param", "name=Preview Only"],
        ["ws", "area.create", "--param", f"name={scratch}"],
        ["ws", "area.update", "--param", f"area_id={area}", "--param", "name=Preview Only"],
        ["ws", "area.delete", "--param", f"area_id={area}"],
        [
            "ws",
            "device.update",
            "--param",
            f"device_id={device}",
            "--param",
            "name_by_user=Preview Only",
        ],
        ["ws", "--raw", "config/entity_registry/remove", "--param", f"entity_id={light}"],
        [
            "ws",
            "--raw",
            "call_service",
            "--params-json",
            json.dumps({"domain": "light", "service": "turn_off", "target": {"entity_id": light}}),
        ],
        ["entity", "update", light, "--name", "Preview Only"],
        ["entity", "update", light, "--clear-area"],
        ["entity", "update", light, "--new-id", "light.hass_axi_preview_only"],
        ["area", "create", "--name", scratch],
        ["area", "update", area, "--name", "Preview Only"],
        ["area", "update", area, "--icon", "mdi:test-tube"],
        ["device", "update", device, "--name", "Preview Only"],
        ["device", "update", device, "--clear-area"],
    ]


def test_every_write_is_a_preview_until_it_is_told_otherwise(house, snapshot):
    """Tier B: 28 write shapes in three modes, and the installation is byte-identical after."""
    light = snapshot.light()
    before = fingerprint(house, light)
    for argv in previews(snapshot):
        for mode in ([], ["--json"], ["--human"]):
            result = house.run(*argv, *mode)
            assert result.code in (0, 1), f"{result.cmd}: exit {result.code}"
            if result.code == 0:
                # A request for what is already stored has nothing to preview.
                assert PREVIEW in result.out or "already matches" in result.out, (
                    f"{result.cmd} did not say it was a preview"
                )
    assert fingerprint(house, light) == before, "a preview changed the installation"


def test_a_read_only_session_refuses_every_write_before_any_transport(house, snapshot):
    light = snapshot.light()
    before = fingerprint(house, light)
    on = {"HASS_AXI_READ_ONLY": "1"}
    writes = [[*argv, WRITE] for argv in previews(snapshot)]
    for argv in writes:
        result = house.run(*argv, "--json", env=on)
        contract(result, "json", code="READ_ONLY", exit_code=2)
        # The same refusal with nothing to reach: it never got as far as a transport.
        dead = house.run(*argv, "--json", env={**on, "HA_URL": "http://127.0.0.1:9"})
        contract(dead, "json", code="READ_ONLY", exit_code=2)
    for value in ("0", "false", "off"):
        result = house.run(*writes[0], "--json", env={"HASS_AXI_READ_ONLY": value})
        contract(result, "json", code="READ_ONLY", exit_code=2)
    legacy = house.run(*writes[0], "--json", env={"HA_AXI_READ_ONLY": "1"})
    assert legacy.code == 2 and '"READ_ONLY"' in legacy.out
    assert fingerprint(house, light) == before


def test_a_read_only_session_still_reads_and_previews(house, snapshot):
    on = {"HASS_AXI_READ_ONLY": "1"}
    light = snapshot.light()
    for argv in (
        ["state", "list", "--limit", "3"],
        ["entity", "get", light],
        ["area", "list"],
        ["doctor"],
        ["template", "render", "--template", "{{ 1 }}"],
    ):
        assert house.run(*argv, env=on).code == 0, argv
    preview = house.run("entity", "update", light, "--name", "Preview Only", env=on)
    assert preview.code == 0 and "read-only" in preview.out
    assert "update" in house.run("entity", "--help", env=on).out


# ------------------------------------------------------------------- tier C


def test_a_notification_is_created_and_dismissed(house, writes):
    before = notifications(house)
    notification_id = f"hass_axi_live_{secrets.token_hex(4)}"
    message = 'live suite: café, "quoted", [brackets], a: colon'
    try:
        document = house.ok(
            "service",
            "call",
            "persistent_notification.create",
            "--data-json",
            json.dumps({"message": message, "notification_id": notification_id}),
            WRITE,
        )
        assert "preview" not in document
        stored = {n["notification_id"]: n for n in house.ws("persistent_notification/get")}
        assert stored[notification_id]["message"] == message
    finally:
        house.run(
            "service",
            "call",
            "persistent_notification.dismiss",
            "--data",
            f"notification_id={notification_id}",
            WRITE,
        )
    assert notifications(house) == before


def test_asking_for_what_is_stored_sends_nothing(house, snapshot, writes):
    """An update that sets a stored value to itself is a no-op, with or without the flag."""
    entity_id = snapshot.named_entity()
    entry = snapshot.entity[entity_id]
    before = house.ws("config/entity_registry/get", entity_id=entity_id)
    for extra in ([], [WRITE]):
        document = house.ok("entity", "update", entity_id, "--name", entry["name"], *extra)
        assert "already matches" in document["updated"]
        document = house.ok("entity", "update", entity_id, "--area", entry["area_id"], *extra)
        assert "already matches" in document["updated"]
    after = house.ws("config/entity_registry/get", entity_id=entity_id)
    assert after == before, "a no-op update changed the registry entry"

    device = snapshot.named_device()
    for extra in ([], [WRITE]):
        document = house.ok(
            "device", "update", device["id"], "--name", device["name_by_user"], *extra
        )
        assert "already matches" in document["updated"]
    now = next(d for d in house.ws("config/device_registry/list") if d["id"] == device["id"])
    assert now == device


def test_a_scratch_area_lives_and_dies_inside_one_test(house, writes):
    before = house.ws("config/area_registry/list")
    name = f"hass-axi live scratch {secrets.token_hex(3)}"
    area_id = None

    def stored():
        return next(
            (a for a in house.ws("config/area_registry/list") if a["area_id"] == area_id), None
        )

    try:
        preview = house.ok("area", "create", "--name", name)
        assert preview["preview"] == PREVIEW
        assert len(house.ws("config/area_registry/list")) == len(before)

        created = house.ok("area", "create", "--name", name, WRITE)
        area_id = created["area"]["area_id"]
        assert stored()["name"] == name
        again = house.ok("area", "create", "--name", name.upper(), WRITE)
        assert "already exists" in again["created"]

        house.ok("area", "update", area_id, "--icon", "mdi:test-tube", WRITE)
        assert stored()["icon"] == "mdi:test-tube"
        repeat = house.ok("area", "update", area_id, "--icon", "mdi:test-tube", WRITE)
        assert "already matches" in repeat["updated"]

        # A floor that does not exist is refused here; Home Assistant would store it.
        result, document = house.doc(
            "area", "update", area_id, "--floor", "zz_no_such_floor_zz", WRITE
        )
        assert (result.code, document["code"]) == (1, "NO_SUCH_FLOOR")
        assert not stored().get("floor_id")
        result, document = house.doc("area", "update", area_id, "--icon", "not-an-icon", WRITE)
        assert (result.code, document["code"]) == (2, "BAD_ICON")
        assert stored()["icon"] == "mdi:test-tube"

        special = f'{name}, "x": [y] ’s'
        house.ok("area", "update", area_id, "--name", special, "--clear-icon", WRITE)
        assert (stored()["name"], stored()["icon"]) == (special, None)
        listed = house.ok("area", "list")
        assert any(row["name"] == special for row in listed["areas"])
        assert house.ok("area", "get", special.replace("’", "'"))["area"]["area_id"] == area_id

        house.ok("ws", "area.delete", "--param", f"area_id={area_id}")
        assert stored() is not None, "a delete preview deleted"
    finally:
        if area_id is not None:
            house.run("ws", "area.delete", "--param", f"area_id={area_id}", WRITE)
    assert house.ws("config/area_registry/list") == before
    result, document = house.doc("area", "get", area_id)
    assert (result.code, document["code"]) == (1, "NO_SUCH_AREA")

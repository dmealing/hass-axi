"""P11, tier E: what only a server that may be damaged can answer.

Rejected credentials -- which raise a notification, and can ban an address, on
a real installation -- and writes that really create, rename, move, switch and
delete. All of it runs against the disposable container in `lab/`, which mints
its own credential and is removed when the session ends. Nothing here is ever
pointed at a real house.
"""

from __future__ import annotations

import base64
import secrets

import pytest

from .harness import contract

WRITE = "--write"


def forged(token: str) -> str:
    header, payload, _ = token.split(".")
    signature = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    return f"{header}.{payload}.{signature}"


def test_the_lab_is_a_home_assistant(lab):
    document = lab.ok("ping")
    assert document["ok"] is True and document["version"]
    assert lab.ok("doctor")["healthy"] is True


@pytest.mark.parametrize("kind", ["random", "forged"])
def test_a_rejected_credential_is_an_auth_fault_on_both_transports(lab, kind):
    token = secrets.token_urlsafe(40) if kind == "random" else forged(lab.token)
    for argv in (["state", "list"], ["entity", "list"], ["ping"], ["area", "list"]):
        for mode, extra in (("toon", []), ("json", ["--json"])):
            result = lab.run(*argv, *extra, env={"HA_TOKEN": token})
            document = contract(result, mode, code="UNAUTHORIZED", exit_code=1)
            assert document["class"] == "auth"
            assert token not in result.out + result.err
    debug = lab.run("state", "list", "--debug", env={"HA_TOKEN": token})
    assert token not in debug.out + debug.err and token.split(".")[-1] not in debug.out + debug.err


def test_a_revoked_token_is_an_auth_fault(lab):
    minted = lab.ws(
        "auth/long_lived_access_token", client_name=f"revoke-{secrets.token_hex(3)}", lifespan=1
    )
    assert lab.run("ping", env={"HA_TOKEN": minted}).code == 0
    tokens = lab.ws("auth/refresh_tokens")
    mine = next(t for t in tokens if (t.get("client_name") or "").startswith("revoke-"))
    lab.ws("auth/delete_refresh_token", refresh_token_id=mine["id"])
    result = lab.run("ping", "--json", env={"HA_TOKEN": minted})
    contract(result, "json", code="UNAUTHORIZED", exit_code=1)


def test_a_service_call_changes_exactly_what_the_preview_said(lab, lab_snapshot):
    light = lab_snapshot.light()
    was = lab.rest(f"/states/{light}")["state"]
    service = "light.turn_off" if was == "on" else "light.turn_on"
    try:
        preview = lab.ok("service", "call", service, "--target-entity", light)
        assert [row["entity_id"] for row in preview["would_reach"]] == [light]
        assert lab.rest(f"/states/{light}")["state"] == was, "a preview switched a light"

        sent = lab.ok("service", "call", service, "--target-entity", light, WRITE)
        assert [row["entity_id"] for row in sent["changed"]] == [light]
        assert lab.rest(f"/states/{light}")["state"] != was

        again = lab.ok("service", "call", service, "--target-entity", light, WRITE)
        assert "0 states changed" in again["changed"]
    finally:
        back = "light.turn_on" if was == "on" else "light.turn_off"
        lab.run("service", "call", back, "--target-entity", light, WRITE)
    assert lab.rest(f"/states/{light}")["state"] == was


def test_a_call_without_a_target_is_explained_before_and_after(lab):
    preview = lab.ok("service", "call", "light.turn_on")
    assert "refuses it without one" in preview["target"]
    result, document = lab.doc("service", "call", "light.turn_on", WRITE)
    assert (result.code, document["code"]) == (1, "MISSING_TARGET")
    assert any("--target-entity" in line for line in document["help"])


def test_a_capability_the_entity_lacks_is_refused_with_the_reason(lab, lab_snapshot):
    """A template cover that can only open and close, asked to move to a position."""
    blind = lab_snapshot.first(
        lambda i: (
            i.startswith("cover.")
            and not (lab_snapshot.state[i]["attributes"].get("supported_features", 0) & 4)
        ),
        lab_snapshot.state,
        "cover that cannot be positioned",
    )
    argv = [
        "service",
        "call",
        "cover.set_cover_position",
        "--target-entity",
        blind,
        "--data",
        "position=50",
    ]
    result, document = lab.doc(*argv)
    assert (result.code, document["code"]) == (1, "UNSUPPORTED_CAPABILITY")
    result, document = lab.doc(*argv, WRITE)
    assert (result.code, document["code"]) == (1, "UNSUPPORTED_CAPABILITY")


def test_a_response_call_that_matches_nothing_is_explained(lab, lab_snapshot):
    empty = lab_snapshot.first(
        lambda a: not any(lab_snapshot.effective_area(e) == a for e in lab_snapshot.entities),
        lab_snapshot.area,
        "area with no entities",
    )
    result, document = lab.doc(
        "service",
        "call",
        "weather.get_forecasts",
        "--target-area",
        empty,
        "--data",
        "type=daily",
        "--response",
        WRITE,
    )
    assert (result.code, document["code"]) == (1, "NO_ENTITIES_TARGETED")


def test_an_area_lives_and_dies_against_a_real_registry(lab):
    before = lab.ws("config/area_registry/list")
    name = f"Scratch {secrets.token_hex(3)}"
    assert lab.ok("area", "create", "--name", name)["preview"]
    assert lab.ws("config/area_registry/list") == before

    area_id = lab.ok("area", "create", "--name", name, WRITE)["area"]["area_id"]
    assert any(
        a["area_id"] == area_id and a["name"] == name for a in lab.ws("config/area_registry/list")
    )
    assert "already exists" in lab.ok("area", "create", "--name", name.lower(), WRITE)["created"]

    # A floor that does not exist is refused here; the real registry would store it.
    result, document = lab.doc("area", "update", area_id, "--floor", "no_such_floor", WRITE)
    assert (result.code, document["code"]) == (1, "NO_SUCH_FLOOR")
    stored = next(a for a in lab.ws("config/area_registry/list") if a["area_id"] == area_id)
    assert stored["floor_id"] is None

    floor = lab.ws("config/floor_registry/create", name=f"Floor {secrets.token_hex(3)}", level=1)
    try:
        updated = lab.ok(
            "area", "update", area_id, "--floor", floor["name"], "--icon", "mdi:sofa", WRITE
        )
        assert updated["area"]["floor_id"] == floor["floor_id"]
        other = lab.ws("config/area_registry/list")[0]
        result, document = lab.doc(
            "area", "update", area_id, "--name", other["name"].upper(), WRITE
        )
        assert result.code == 1 and "already in use" in document["error"]
        assert document["help"]
    finally:
        lab.ws("config/floor_registry/delete", floor_id=floor["floor_id"])
    stored = next(a for a in lab.ws("config/area_registry/list") if a["area_id"] == area_id)
    assert stored["floor_id"] is None, "a deleted floor stayed on the area"

    assert lab.ok("ws", "area.delete", "--param", f"area_id={area_id}", WRITE)
    result, document = lab.doc("ws", "area.delete", "--param", f"area_id={area_id}", WRITE)
    assert result.code == 1 and document["help"]
    assert [a["area_id"] for a in lab.ws("config/area_registry/list")] == [
        a["area_id"] for a in before
    ]


def test_an_entity_is_renamed_moved_and_put_back(lab, lab_snapshot):
    light = lab_snapshot.light()
    before = lab.ws("config/entity_registry/get", entity_id=light)
    area = lab.ws("config/area_registry/list")[0]
    new_id = f"light.scratch_{secrets.token_hex(3)}"
    name = 'Scratch’s Lamp, the "second"'
    try:
        preview = lab.ok(
            "entity", "update", light, "--name", name, "--area", area["name"], "--new-id", new_id
        )
        assert {row["field"] for row in preview["would_change"]} == {
            "name",
            "area_id",
            "new_entity_id",
        }
        assert lab.ws("config/entity_registry/get", entity_id=light)["name"] == before["name"]

        sent = lab.ok(
            "entity",
            "update",
            light,
            "--name",
            name,
            "--area",
            area["name"],
            "--new-id",
            new_id,
            WRITE,
        )
        assert (sent["entity"], sent["name"], sent["area_id"]) == (new_id, name, area["area_id"])
        stored = lab.ws("config/entity_registry/get", entity_id=new_id)
        assert (stored["name"], stored["area_id"]) == (name, area["area_id"])
        found = lab.ok("entity", "list", "--search", "scratch's lamp")
        assert [row["entity_id"] for row in found["entities"]] == [new_id]

        other = next(
            e["entity_id"]
            for e in lab_snapshot.entities
            if e["entity_id"].startswith("light.") and e["entity_id"] != light
        )
        result, document = lab.doc("entity", "update", new_id, "--new-id", other, WRITE)
        assert result.code == 1 and document["help"]
    finally:
        current = (
            new_id
            if any(e["entity_id"] == new_id for e in lab.ws("config/entity_registry/list"))
            else light
        )
        lab.ws(
            "config/entity_registry/update",
            entity_id=current,
            new_entity_id=light,
            name=before["name"],
            area_id=before["area_id"],
        )
    after = lab.ws("config/entity_registry/get", entity_id=light)
    assert (after["name"], after["area_id"]) == (before["name"], before["area_id"])


def test_a_device_is_renamed_moved_and_put_back(lab, lab_snapshot):
    device = lab_snapshot.devices[0]
    area = lab.ws("config/area_registry/list")[0]
    try:
        preview = lab.ok(
            "device", "update", device["id"], "--name", "Scratch Device", "--area", area["area_id"]
        )
        assert preview["preview"]
        sent = lab.ok(
            "device",
            "update",
            device["id"],
            "--name",
            "Scratch Device",
            "--area",
            area["area_id"],
            WRITE,
        )
        assert (sent["name"], sent["area_id"]) == ("Scratch Device", area["area_id"])
        assert lab.ok("device", "get", "scratch device")["device"]["device_id"] == device["id"]
    finally:
        lab.ws(
            "config/device_registry/update",
            device_id=device["id"],
            name_by_user=device.get("name_by_user"),
            area_id=device.get("area_id"),
        )
    now = next(d for d in lab.ws("config/device_registry/list") if d["id"] == device["id"])
    assert (now["name_by_user"], now["area_id"]) == (
        device.get("name_by_user"),
        device.get("area_id"),
    )


def test_the_double_refuses_what_the_real_server_refuses(lab):
    """The shapes the offline double transcribes, read off the server it transcribes."""
    frames = lab.ws_many(
        [
            {"type": "config/area_registry/delete", "area_id": "zz_no_such_area"},
            {"type": "config/area_registry/update", "area_id": "zz_no_such_area", "name": "x"},
            {"type": "config/floor_registry/delete", "floor_id": "zz_no_such_floor"},
            {"type": "config/entity_registry/remove", "entity_id": "light.zz_no_such_entity"},
            {
                "type": "config/device_registry/update",
                "device_id": "zz_no_such_device",
                "name_by_user": "x",
            },
            {"type": "config/area_registry/create", "name": 7},
        ]
    )
    codes = [frame["error"]["code"] for frame in frames]
    assert codes == [
        "invalid_info",
        "unknown_error",
        "invalid_info",
        "not_found",
        "unknown_error",
        "invalid_format",
    ]
    assert frames[0]["error"]["message"] == "Area ID doesn't exist"
    assert frames[2]["error"]["message"] == "Floor ID doesn't exist"
    assert frames[3]["error"]["message"] == "Entity not found"


def test_statistics_buckets_widen_the_window_as_the_double_says(lab):
    """Read off the real recorder: a daily bucket begins before the start that was asked for."""
    from datetime import datetime, timedelta, timezone

    meta = lab.ws("recorder/list_statistic_ids")
    sums = [m["statistic_id"] for m in meta if m.get("has_sum")]
    start = datetime.now(timezone.utc) - timedelta(minutes=30)
    rows = lab.ws(
        "recorder/statistics_during_period",
        start_time=start.isoformat(),
        statistic_ids=sums,
        period="day",
        types=["change"],
    )
    if not any(rows.values()):
        pytest.skip("the lab has compiled no statistics yet; the recorder does it hourly")
    first = min(bucket[0]["start"] for bucket in rows.values() if bucket)
    assert first < start.timestamp() * 1000, "the daily bucket no longer starts before the window"
    document = lab.ok("statistics", "get", sums[0], "--start", "30m", "--period", "day")
    assert not any(
        "more than the window" in c for c in document["statistics"][0].get("caveats", [])
    )

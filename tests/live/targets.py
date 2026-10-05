"""Targets chosen by shape, at run time, from a snapshot of the installation.

No entity id, area name or device name is written in this suite. "A light that
is on or off", "an entity whose area comes from its device", "an area whose
name is not ASCII": each is found in whatever installation the suite is pointed
at, and a test whose shape is not present there skips and says which.
"""

from __future__ import annotations

import collections

import pytest


class Snapshot:
    """The raw states and registries, read once, with nothing from the tool."""

    def __init__(self, target) -> None:
        self.states = target.rest("/states")
        frames = target.ws_many(
            [
                {"type": "config/entity_registry/list"},
                {"type": "config/device_registry/list"},
                {"type": "config/area_registry/list"},
                {"type": "config/floor_registry/list"},
            ]
        )
        self.entities, self.devices, self.areas, self.floors = (f["result"] for f in frames)
        self.services = target.rest("/services")
        self.state = {s["entity_id"]: s for s in self.states}
        self.entity = {e["entity_id"]: e for e in self.entities}
        self.device = {d["id"]: d for d in self.devices}
        self.area = {a["area_id"]: a for a in self.areas}

    # ---------------------------------------- rules written independently

    def effective_area(self, entry: dict) -> str:
        own = entry.get("area_id")
        inherited = (self.device.get(entry.get("device_id") or "") or {}).get("area_id")
        area_id = own or inherited or ""
        return area_id if area_id in self.area else ""

    def device_name(self, device: dict) -> str:
        return device.get("name_by_user") or device.get("name") or ""

    def displayed_name(self, entry: dict) -> str:
        if entry.get("name"):
            return entry["name"]
        device = self.device.get(entry.get("device_id") or "")
        parts = [self.device_name(device) if device else "", entry.get("original_name") or ""]
        return " ".join(part for part in parts if part)

    # ------------------------------------------------------------ shapes

    def first(self, predicate, items, what: str):
        found = next((item for item in items if predicate(item)), None)
        if found is None:
            pytest.skip(f"this installation has no {what}")
        return found

    def domains(self) -> list:
        return sorted({entity_id.split(".", 1)[0] for entity_id in self.state})

    def light(self) -> str:
        return self.first(
            lambda i: (
                i.startswith("light.")
                and self.state[i]["state"] in ("on", "off")
                and i in self.entity
            ),
            self.state,
            "registered light that is on or off",
        )

    def unavailable(self) -> str:
        return self.first(
            lambda i: self.state[i]["state"] == "unavailable", self.state, "unavailable entity"
        )

    def unregistered(self) -> str:
        return self.first(
            lambda i: i not in self.entity, self.state, "state with no registry entry"
        )

    def disabled(self) -> str:
        return self.first(
            lambda i: self.entity[i].get("disabled_by") and i not in self.state,
            self.entity,
            "disabled registry entry with no state",
        )

    def inherited(self) -> str:
        return self.first(
            lambda i: not self.entity[i].get("area_id") and self.effective_area(self.entity[i]),
            self.entity,
            "entity whose area comes from its device",
        )

    def named_entity(self) -> str:
        """An entity with a stored name and its own area: a safe no-op subject."""
        return self.first(
            lambda i: self.entity[i].get("name") and self.entity[i].get("area_id") in self.area,
            self.entity,
            "entity with a stored name and an area of its own",
        )

    def named_device(self) -> dict:
        return self.first(
            lambda d: d.get("name_by_user") and d.get("area_id") in self.area,
            self.devices,
            "device with a user name and an area",
        )

    def meter(self) -> str:
        return self.first(
            lambda i: (
                i.startswith("sensor.")
                and self.state[i]["attributes"].get("state_class") in ("total_increasing", "total")
                and self.state[i]["state"] not in ("unknown", "unavailable")
            ),
            self.state,
            "meter with a state class",
        )

    def reading(self) -> str:
        return self.first(
            lambda i: (
                i.startswith("sensor.")
                and self.state[i]["attributes"].get("state_class") == "measurement"
                and self.state[i]["state"] not in ("unknown", "unavailable")
            ),
            self.state,
            "measurement sensor",
        )

    def typographic(self) -> str:
        """A friendly name holding a typographic apostrophe or quotation mark."""
        marks = "‘’ʼ“”"
        return self.first(
            lambda i: any(
                m in (self.state[i]["attributes"].get("friendly_name") or "") for m in marks
            ),
            self.state,
            "name with a typographic quotation mark",
        )

    def multi_word_name(self) -> str:
        return self.first(
            lambda i: " " in (self.state[i]["attributes"].get("friendly_name") or ""),
            self.state,
            "entity with a multi-word name",
        )

    def biggest_area(self) -> str:
        if not self.areas:
            pytest.skip("this installation has no areas")
        sizes = collections.Counter(self.effective_area(e) for e in self.entities)
        return max(self.area, key=lambda area_id: sizes.get(area_id, 0))

    def empty_area(self) -> str:
        used = {self.effective_area(e) for e in self.entities}
        return self.first(lambda a: a not in used, self.area, "area with no entities")

    def duplicate_device_name(self) -> str:
        names = collections.Counter(self.device_name(d) for d in self.devices)
        return self.first(lambda n: n and names[n] > 1, names, "device name two devices share")

    def camera(self) -> str:
        return self.first(lambda i: i.startswith("camera."), self.state, "camera")

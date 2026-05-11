"""Device list / show / rename / remove / interview / configure / set / get."""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient


def list_devices(client: BridgeClient, *, timeout: float = 5.0) -> list[dict]:
    raw = client.collect_retained(
        f"{client.base_topic}/bridge/devices", timeout=timeout
    )
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def show(client: BridgeClient, ident: str) -> Optional[dict]:
    """Find a device by IEEE address or friendly_name."""
    ident_l = ident.lower()
    for d in list_devices(client):
        if d.get("ieee_address", "").lower() == ident_l:
            return d
        if d.get("friendly_name", "").lower() == ident_l:
            return d
    return None


def summarize(devices: list[dict]) -> list[dict]:
    """Flatten device records for table display."""
    out: list[dict] = []
    for d in devices:
        defn = d.get("definition") or {}
        out.append({
            "friendly_name": d.get("friendly_name"),
            "ieee_address": d.get("ieee_address"),
            "model": defn.get("model"),
            "vendor": defn.get("vendor"),
            "type": d.get("type"),
            "manufacturer": d.get("manufacturer"),
            "power_source": d.get("power_source"),
            "interview_completed": d.get("interview_completed"),
            "supported": d.get("supported"),
            "disabled": d.get("disabled", False),
            "description": d.get("description"),
        })
    return out


# ── mutation primitives ─────────────────────────────────────────────────

def rename(client: BridgeClient, *, from_: str, to: str,
           homeassistant_rename: bool = True,
           timeout: float = 15.0) -> dict:
    """Rename a device. `homeassistant_rename` also renames the HA entities so
    the unique_id is preserved (default true — almost always what you want)."""
    return client.request("device/rename", payload={
        "from": from_, "to": to, "homeassistant_rename": homeassistant_rename,
    }, timeout=timeout)


def remove(client: BridgeClient, id_: str, *,
           force: bool = False, block: bool = False,
           timeout: float = 30.0) -> dict:
    """Remove a device from the network (and from z2m's database).

    force=True skips network-level removal (useful when the device is already
    physically gone). block=True adds it to the block-list so it can't rejoin.
    """
    return client.request("device/remove", payload={
        "id": id_, "force": force, "block": block,
    }, timeout=timeout)


def configure(client: BridgeClient, id_: str, *, timeout: float = 30.0) -> dict:
    """Re-run device configuration (re-bindings, reports). Use after a device
    starts reporting wrong values or never set up reports correctly."""
    return client.request("device/configure", payload={"id": id_},
                           timeout=timeout)


def interview(client: BridgeClient, id_: str, *, timeout: float = 60.0) -> dict:
    """Force a fresh device interview (re-read endpoints, clusters, model).
    Slow — typically 30+ seconds while the device wakes up."""
    return client.request("device/interview", payload={"id": id_},
                           timeout=timeout)


def options(client: BridgeClient, id_: str, options_payload: dict,
            *, timeout: float = 10.0) -> dict:
    """Set per-device options (z2m's `device_options` block)."""
    return client.request("device/options", payload={
        "id": id_, "options": options_payload,
    }, timeout=timeout)


def set_value(client: BridgeClient, friendly_name: str, fields: dict) -> int:
    """Publish to `<base>/<friendly_name>/set` to write device state."""
    topic = f"{client.base_topic}/{friendly_name}/set"
    return client.publish(topic, fields)


def get_value(client: BridgeClient, friendly_name: str, keys: list[str]) -> int:
    """Publish to `<base>/<friendly_name>/get` to ask the device to publish state."""
    topic = f"{client.base_topic}/{friendly_name}/get"
    payload = {k: "" for k in keys}
    return client.publish(topic, payload)


def watch_device(client: BridgeClient, friendly_name: str, *,
                  duration: Optional[float] = None) -> list[dict]:
    """Tail the device's state topic for N seconds."""
    topic = f"{client.base_topic}/{friendly_name}"
    collected: list[dict] = []

    def _cb(_t, p):
        try:
            collected.append(json.loads(p))
        except json.JSONDecodeError:
            collected.append({"raw": p})
    client.subscribe(topic, _cb)
    end = time.time() + duration if duration else None
    try:
        while end is None or time.time() < end:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    return collected

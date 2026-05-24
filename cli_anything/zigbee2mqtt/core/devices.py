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


# ── one-shot retained state read ────────────────────────────────────────

def read_state(client: BridgeClient, friendly_name: str, *,
                timeout: float = 3.0) -> dict:
    """Return the current retained state payload for a device.

    z2m publishes the latest state on ``<base>/<friendly_name>`` with
    ``retain=true``, so a fresh subscribe receives the last-known message
    instantly. Unlike :func:`watch_device` this never blocks waiting for a
    new update — it just grabs whatever is retained.
    """
    if not friendly_name:
        raise ValueError("friendly_name is required")
    topic = f"{client.base_topic}/{friendly_name}"
    raw = client.collect_retained(topic, timeout=timeout)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


# ── staleness sweep (local filter on bridge/devices.last_seen) ──────────

def find_stale(client: BridgeClient, *,
                threshold_minutes: int = 60,
                include_routers: bool = True,
                include_end_devices: bool = True,
                timeout: float = 5.0) -> list[dict]:
    """Rank devices by how long they've been silent.

    z2m records ``last_seen`` (ISO-8601 string when the last message
    arrived from the device) inside the retained ``bridge/devices``
    payload. This helper subtracts that from now and keeps anything
    older than *threshold_minutes*. No MQTT round-trip beyond the
    initial bridge/devices subscribe.

    Returns rows sorted oldest-first::

        {
          "friendly_name", "ieee_address", "type", "model",
          "last_seen", "minutes_since_seen", "power_source",
        }
    """
    import datetime as _dt
    devices = list_devices(client, timeout=timeout)
    now = _dt.datetime.now(_dt.timezone.utc)
    out: list[dict] = []
    for d in devices:
        kind = d.get("type")
        if kind == "Coordinator":
            continue
        if kind == "Router" and not include_routers:
            continue
        if kind in ("EndDevice", "GreenPower") and not include_end_devices:
            continue
        last = d.get("last_seen")
        if not last:
            mins = None
        else:
            try:
                # last_seen is ISO-8601 with Z or +00:00
                last_dt = _dt.datetime.fromisoformat(
                    last.replace("Z", "+00:00"),
                )
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=_dt.timezone.utc)
                mins = (now - last_dt).total_seconds() / 60.0
            except (ValueError, AttributeError):
                mins = None
        if mins is None or mins < threshold_minutes:
            continue
        defn = d.get("definition") or {}
        out.append({
            "friendly_name": d.get("friendly_name"),
            "ieee_address": d.get("ieee_address"),
            "type": kind,
            "model": defn.get("model"),
            "last_seen": last,
            "minutes_since_seen": round(mins, 1),
            "power_source": d.get("power_source"),
        })
    out.sort(key=lambda r: r["minutes_since_seen"] or 0, reverse=True)
    return out


# ── definition generator (for unsupported devices) ──────────────────────

def generate_external_definition(client: BridgeClient, id_: str, *,
                                  timeout: float = 30.0) -> dict:
    """Ask z2m to generate a starter external-converter `.js` for a device.

    z2m walks the device's interview data (endpoints / clusters /
    attributes) and emits a converter file you can drop into
    ``external_converters/``. Useful when a device is recognised at the
    transport layer but missing from zigbee-herdsman-converters.

    Returns ``{source, ...}`` where ``source`` is the generated JS.
    """
    if not id_:
        raise ValueError("id_ is required (friendly_name or ieee_address)")
    return client.request("device/generate_external_definition",
                            payload={"id": id_}, timeout=timeout)


# ── manual reporting configuration ──────────────────────────────────────

def configure_reporting(client: BridgeClient, *,
                          id_: str,
                          cluster: str,
                          attribute: str,
                          minimum_report_interval: int,
                          maximum_report_interval: int,
                          reportable_change: Optional[float] = None,
                          endpoint: Optional[int] = None,
                          timeout: float = 15.0) -> dict:
    """Set up a single attribute report on a device endpoint.

    z2m's default reporting setup (run during interview / configure)
    sometimes misses the attribute you actually want, or sets a window
    that's too aggressive on a battery device. This bypasses defaults
    and writes a single report binding directly.

    *cluster* is a herdsman cluster name (``"genOnOff"``,
    ``"genLevelCtrl"``, ``"msTemperatureMeasurement"`` …). *attribute*
    is the herdsman attribute id within that cluster. Intervals are in
    seconds; *reportable_change* is in the attribute's native units
    (omit for boolean attributes).
    """
    if not id_:
        raise ValueError("id_ is required")
    if not cluster or not attribute:
        raise ValueError("cluster and attribute are required")
    if minimum_report_interval < 0 or maximum_report_interval < 0:
        raise ValueError("intervals must be non-negative")
    payload: dict = {
        "id": id_,
        "cluster": cluster,
        "attribute": attribute,
        "minimum_report_interval": int(minimum_report_interval),
        "maximum_report_interval": int(maximum_report_interval),
    }
    if reportable_change is not None:
        payload["reportable_change"] = reportable_change
    if endpoint is not None:
        payload["endpoint"] = int(endpoint)
    return client.request("device/configure_reporting", payload=payload,
                            timeout=timeout)

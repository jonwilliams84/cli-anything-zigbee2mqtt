"""Permit-join + touchlink + network-map — the network-admin surface."""

from __future__ import annotations

import json
import time
from typing import Optional

from cli_anything.zigbee2mqtt.core.devices import (
    classify_battery,
    list_devices,
    parse_availability,
)
from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient, MqttError


def permit_join(
    client: BridgeClient,
    *,
    value: bool,
    time_secs: int = 254,
    device: str | None = None,
    timeout: float = 10.0,
) -> dict:
    """Open / close the network for joining new devices.

    `time_secs` is how many seconds permit_join stays open (max 254). Pass
    `device=<router-friendly-name>` to open via a specific router instead of
    the coordinator — useful for ranges the coordinator can't reach.
    """
    payload: dict = {"value": bool(value), "time": int(time_secs)}
    if device:
        payload["device"] = device
    return client.request("permit_join", payload=payload, timeout=timeout)


def network_map(
    client: BridgeClient, *, type_: str = "raw", routes: bool = True, timeout: float = 60.0
) -> dict:
    """Generate a fresh network map.

    `type_`: 'raw' (JSON), 'graphviz' (DOT), 'plantuml' (PlantUML).
    Generation takes 30-60s while z2m polls every router.
    """
    if type_ not in ("raw", "graphviz", "plantuml"):
        raise ValueError("type_ must be raw | graphviz | plantuml")
    return client.request(
        "networkmap",
        payload={
            "type": type_,
            "routes": routes,
        },
        timeout=timeout,
    )


def touchlink_scan(client: BridgeClient, *, timeout: float = 30.0) -> dict:
    return client.request("touchlink/scan", payload={}, timeout=timeout)


def touchlink_identify(
    client: BridgeClient, ieee: str, channel: int, *, timeout: float = 15.0
) -> dict:
    return client.request(
        "touchlink/identify",
        payload={
            "ieee_address": ieee,
            "channel": channel,
        },
        timeout=timeout,
    )


def touchlink_factory_reset(
    client: BridgeClient,
    *,
    ieee: str | None = None,
    channel: int | None = None,
    timeout: float = 30.0,
) -> dict:
    payload: dict = {}
    if ieee is not None:
        payload["ieee_address"] = ieee
    if channel is not None:
        payload["channel"] = channel
    return client.request("touchlink/factory_reset", payload=payload, timeout=timeout)


def coordinator_check(client: BridgeClient, *, timeout: float = 10.0) -> dict:
    """Ask z2m to verify the coordinator is responsive."""
    return client.request("coordinator_check", payload={}, timeout=timeout)


def backup(client: BridgeClient, *, timeout: float = 30.0) -> dict:
    """Trigger a z2m coordinator backup (writes to z2m's data dir)."""
    return client.request("backup", payload={}, timeout=timeout)


# ── network stats (one-shot health summary over the retained surface) ────────


def bridge_summary(info: dict) -> dict:
    """Extract the headline fields from a retained ``bridge/info`` payload.

    Client-free so it stays unit-testable without a transport. ``permit_join``
    is read from the top level when present, falling back to the nested
    ``config`` block (z2m moved it there in recent releases).
    """
    info = info if isinstance(info, dict) else {}
    coord = info.get("coordinator") if isinstance(info.get("coordinator"), dict) else {}
    meta = coord.get("meta") if isinstance(coord.get("meta"), dict) else {}
    cfg = info.get("config") if isinstance(info.get("config"), dict) else {}
    network = info.get("network") if isinstance(info.get("network"), dict) else {}
    permit_join = info.get("permit_join")
    if permit_join is None:
        permit_join = cfg.get("permit_join")
    return {
        "version": info.get("version"),
        "coordinator_type": coord.get("type"),
        "coordinator_firmware": meta.get("version"),
        "permit_join": permit_join,
        "network": {key: network.get(key) for key in ("pan_id", "extended_pan_id", "channel")},
    }


def summarize_inventory(devices: list[dict]) -> dict:
    """Aggregate the ``bridge/devices`` inventory into headline counts.

    Client-free. Tolerates non-dict entries (z2m never sends them today, but
    the inventory join helpers elsewhere in this harness do not assume it
    either).
    """
    by_type: dict[str, int] = {}
    unsupported = 0
    disabled = 0
    total = 0
    for d in devices:
        if not isinstance(d, dict):
            continue
        total += 1
        dtype = str(d.get("type") or "unknown")
        by_type[dtype] = by_type.get(dtype, 0) + 1
        if d.get("supported") is False:
            unsupported += 1
        if d.get("disabled"):
            disabled += 1
    return {
        "total": total,
        "by_type": dict(sorted(by_type.items())),
        "unsupported": unsupported,
        "disabled": disabled,
    }


def availability_overview(
    devices: list[dict], availability_by_name: dict[str, Optional[str]]
) -> dict:
    """Count online / offline / unknown from an availability name→state join.

    ``availability_by_name`` maps a device's ``friendly_name`` (or IEEE
    address) to ``"online"`` / ``"offline"`` / ``None``. Devices absent from
    the map (availability feature disabled, or nothing retained yet) count as
    ``unknown``. Coordinators are skipped — they are never "offline".
    """
    online = offline = unknown = 0
    for d in devices:
        if not isinstance(d, dict) or d.get("type") == "Coordinator":
            continue
        name = d.get("friendly_name")
        state = availability_by_name.get(name) if name else None
        if state is None and d.get("ieee_address"):
            state = availability_by_name.get(d["ieee_address"])
        if state == "online":
            online += 1
        elif state == "offline":
            offline += 1
        else:
            unknown += 1
    return {"online": online, "offline": offline, "unknown": unknown}


def battery_overview(
    devices: list[dict], states_by_name: dict[str, dict], *, below: float = 20.0
) -> dict:
    """Battery health counts plus the five weakest batteries on the network.

    ``states_by_name`` maps friendly_name (or IEEE address) to the parsed
    retained state payload. Classification reuses ``devices.classify_battery``
    so ``network stats`` agrees with ``device battery``. ``mains`` devices are
    counted (the block totals the network) but never ranked. The ``lowest``
    list only contains devices that reported a usable percent — battery
    devices that never woke up are already visible as ``unknown``.
    Coordinators are skipped — they have no battery and no meaningful power
    source of their own.
    """
    counts = {"ok": 0, "low": 0, "unknown": 0, "mains": 0}
    ranked: list[tuple[float, str]] = []
    for d in devices:
        if not isinstance(d, dict) or d.get("type") == "Coordinator":
            continue
        name = d.get("friendly_name")
        state = None
        if name and name in states_by_name:
            state = states_by_name[name]
        elif d.get("ieee_address") and d["ieee_address"] in states_by_name:
            state = states_by_name[d["ieee_address"]]
        battery = state.get("battery") if isinstance(state, dict) else None
        battery_low = state.get("battery_low") if isinstance(state, dict) else None
        status = classify_battery(
            battery, battery_low, power_source=d.get("power_source"), below=below
        )
        counts[status] = counts.get(status, 0) + 1
        pct = None
        if battery is not None:
            try:
                pct = float(battery)
            except (TypeError, ValueError):
                pct = None
        if pct is not None:
            ranked.append((pct, str(name)))
    ranked.sort()
    return {
        "ok": counts["ok"],
        "low": counts["low"],
        "unknown": counts["unknown"],
        "mains": counts["mains"],
        "lowest": [
            {"friendly_name": name, "battery": pct} for pct, name in ranked[:5] if pct < below
        ],
    }


def linkquality_overview(states_by_name: dict[str, dict]) -> dict:
    """min / max / avg linkquality across all retained state payloads.

    Client-free. Only numeric ``linkquality`` values count; routers report LQI
    eagerly, sleeping end devices only when they last woke.
    """
    values: list[float] = []
    for state in states_by_name.values():
        if not isinstance(state, dict):
            continue
        try:
            values.append(float(state.get("linkquality")))
        except (TypeError, ValueError):
            continue
    if not values:
        return {"reported": 0, "min": None, "max": None, "avg": None}
    return {
        "reported": len(values),
        "min": min(values),
        "max": max(values),
        "avg": round(sum(values) / len(values), 1),
    }


def network_stats(
    client: BridgeClient,
    *,
    duration: float = 2.0,
    below: float = 20.0,
    timeout: float = 5.0,
) -> dict:
    """One-shot network health summary — the z2m frontend dashboard in JSON.

    Joins three sources the z2m process already publishes:
      - ``bridge/info`` (retained): version, coordinator, permit_join, network.
      - ``bridge/devices`` (retained): inventory counts by type.
      - one ``<base>/#`` subscription for `duration` seconds: retained
        ``<name>/availability`` and ``<name>`` state payloads give availability,
        battery and linkquality totals with a single wildcard subscription
        instead of one round trip per device (same technique as
        ``devices.availability_sweep``).

    Raises ``MqttError`` when no retained ``bridge/info`` arrives — that means
    z2m is not reachable, and a stats report of all zeros would lie.
    """
    raw_info = client.collect_retained(f"{client.base_topic}/bridge/info", timeout=timeout)
    info: dict = {}
    if raw_info:
        try:
            decoded = json.loads(raw_info)
            if isinstance(decoded, dict):
                info = decoded
        except json.JSONDecodeError:
            info = {}
    if not info:
        raise MqttError("no retained bridge/info — is z2m running?")

    devices = list_devices(client, timeout=timeout)

    availability: dict[str, Optional[str]] = {}
    states: dict[str, dict] = {}
    prefix = f"{client.base_topic}/"

    def _cb(topic: str, payload: str) -> None:
        if not topic.startswith(prefix):
            return
        rest = topic[len(prefix) :]
        if rest.endswith("/availability"):
            availability[rest[: -len("/availability")]] = parse_availability(payload)
            return
        if not rest or "/" in rest:
            return  # bridge/*, set/get, group topics are not device state
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return
        if isinstance(data, dict):
            states[rest] = data

    client.subscribe(f"{client.base_topic}/#", _cb)
    end = time.time() + max(duration, 0.0)
    try:
        while time.time() < end:
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass

    return {
        "bridge": bridge_summary(info),
        "devices": summarize_inventory(devices),
        "availability": availability_overview(devices, availability),
        "battery": battery_overview(devices, states, below=below),
        "linkquality": linkquality_overview(states),
        "window_secs": duration,
    }

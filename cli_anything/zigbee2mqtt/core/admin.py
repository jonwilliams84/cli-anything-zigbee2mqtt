"""Permit-join + touchlink + network-map — the network-admin surface."""

from __future__ import annotations

from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient


def permit_join(client: BridgeClient, *, value: bool, time_secs: int = 254,
                 device: str | None = None, timeout: float = 10.0) -> dict:
    """Open / close the network for joining new devices.

    `time_secs` is how many seconds permit_join stays open (max 254). Pass
    `device=<router-friendly-name>` to open via a specific router instead of
    the coordinator — useful for ranges the coordinator can't reach.
    """
    payload: dict = {"value": bool(value), "time": int(time_secs)}
    if device:
        payload["device"] = device
    return client.request("permit_join", payload=payload, timeout=timeout)


def network_map(client: BridgeClient, *, type_: str = "raw",
                routes: bool = True, timeout: float = 60.0) -> dict:
    """Generate a fresh network map.

    `type_`: 'raw' (JSON), 'graphviz' (DOT), 'plantuml' (PlantUML).
    Generation takes 30-60s while z2m polls every router.
    """
    if type_ not in ("raw", "graphviz", "plantuml"):
        raise ValueError("type_ must be raw | graphviz | plantuml")
    return client.request("networkmap", payload={
        "type": type_, "routes": routes,
    }, timeout=timeout)


def touchlink_scan(client: BridgeClient, *, timeout: float = 30.0) -> dict:
    return client.request("touchlink/scan", payload={}, timeout=timeout)


def touchlink_identify(client: BridgeClient, ieee: str, channel: int,
                        *, timeout: float = 15.0) -> dict:
    return client.request("touchlink/identify", payload={
        "ieee_address": ieee, "channel": channel,
    }, timeout=timeout)


def touchlink_factory_reset(client: BridgeClient, *,
                              ieee: str | None = None, channel: int | None = None,
                              timeout: float = 30.0) -> dict:
    payload: dict = {}
    if ieee is not None:
        payload["ieee_address"] = ieee
    if channel is not None:
        payload["channel"] = channel
    return client.request("touchlink/factory_reset", payload=payload,
                            timeout=timeout)


def coordinator_check(client: BridgeClient, *, timeout: float = 10.0) -> dict:
    """Ask z2m to verify the coordinator is responsive."""
    return client.request("coordinator_check", payload={}, timeout=timeout)


def backup(client: BridgeClient, *, timeout: float = 30.0) -> dict:
    """Trigger a z2m coordinator backup (writes to z2m's data dir)."""
    return client.request("backup", payload={}, timeout=timeout)

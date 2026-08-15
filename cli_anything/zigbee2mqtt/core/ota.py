"""OTA firmware update commands."""

from __future__ import annotations

from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient


def check(client: BridgeClient, id_: str, *, timeout: float = 30.0) -> dict:
    """Ask z2m to check if a device has a firmware update available.

    Returns the response payload (includes available/current versions).
    """
    return client.request("device/ota_update/check", payload={"id": id_}, timeout=timeout)


def update(client: BridgeClient, id_: str, *, timeout: float = 600.0) -> dict:
    """Trigger an OTA update for a device.

    Updates can take many minutes — default timeout is 10 minutes. Use
    `bridge watch-events` in another shell to see progress.
    """
    return client.request("device/ota_update/update", payload={"id": id_}, timeout=timeout)


def schedule(client: BridgeClient, id_: str, *, timeout: float = 10.0) -> dict:
    """Schedule an OTA update (z2m runs it during the next idle window)."""
    return client.request("device/ota_update/schedule", payload={"id": id_}, timeout=timeout)


def unschedule(client: BridgeClient, id_: str, *, timeout: float = 10.0) -> dict:
    """Cancel an OTA update that was queued with :func:`schedule`.

    z2m keeps a scheduled update pending until the device next checks in, which
    for a battery device can be hours. This removes it from the queue — the
    counterpart to ``schedule`` and the safe way to back out of a firmware roll
    you no longer want.
    """
    if not id_:
        raise ValueError("id_ is required (friendly_name or ieee_address)")
    return client.request("device/ota_update/unschedule", payload={"id": id_}, timeout=timeout)

"""OTA firmware update commands."""

from __future__ import annotations

from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient


def check(client: BridgeClient, id_: str, *, timeout: float = 30.0) -> dict:
    """Ask z2m to check if a device has a firmware update available.

    Returns the response payload (includes available/current versions).
    """
    return client.request("device/ota_update/check", payload={"id": id_},
                           timeout=timeout)


def update(client: BridgeClient, id_: str, *, timeout: float = 600.0) -> dict:
    """Trigger an OTA update for a device.

    Updates can take many minutes — default timeout is 10 minutes. Use
    `bridge watch-events` in another shell to see progress.
    """
    return client.request("device/ota_update/update", payload={"id": id_},
                           timeout=timeout)


def schedule(client: BridgeClient, id_: str, *, timeout: float = 10.0) -> dict:
    """Schedule an OTA update (z2m runs it during the next idle window)."""
    return client.request("device/ota_update/schedule", payload={"id": id_},
                           timeout=timeout)

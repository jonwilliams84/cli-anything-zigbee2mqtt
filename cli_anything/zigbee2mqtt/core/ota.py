"""OTA firmware update commands."""

from __future__ import annotations

from typing import Optional

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


# ───────────────────────────────────────────────── network firmware sweep


_STATUS_ORDER = {
    "update_available": 0,
    "up_to_date": 1,
    "not_supported": 2,
    "unknown": 3,
    "error": 4,
}


def _classify(response: dict) -> tuple[str, str]:
    """Turn a `device/ota_update/check` response into (status, detail)."""
    status = response.get("status")
    if status == "ok":
        data = response.get("data")
        if not isinstance(data, dict):
            return "unknown", "" if data is None else str(data)
        if "update_available" in data:
            return ("update_available" if data["update_available"] else "up_to_date"), ""
        return "unknown", ""
    detail = str(response.get("error") or response.get("message") or "")
    low = detail.lower()
    if "not support" in low or "unsupported" in low:
        return "not_supported", detail
    return "error", detail


def check_all(
    client: BridgeClient,
    *,
    timeout: float = 30.0,
    include_disabled: bool = False,
) -> list[dict]:
    """Ask every device in the network if a firmware update is available.

    One `device/ota_update/check` request per device (the coordinator and
    disabled devices are skipped unless *include_disabled* is set). A device
    that never answers or errors out is recorded as an ``error`` row instead
    of aborting the sweep, so one unreachable battery sensor cannot hide the
    rest of the network's firmware state.

    Returns rows sorted update-available-first, then by name::

        {
          "friendly_name", "ieee_address",
          "status",  # update_available | up_to_date | not_supported | unknown | error
          "update_available",  # bool, or None when unknown
          "detail",
        }
    """
    from cli_anything.zigbee2mqtt.core import devices as devices_core

    rows: list[dict] = []
    for d in devices_core.list_devices(client):
        if d.get("type") == "Coordinator":
            continue
        if d.get("disabled") and not include_disabled:
            continue
        name = d.get("friendly_name") or d.get("ieee_address") or ""
        id_ = d.get("ieee_address") or name
        update_available: Optional[bool] = None
        try:
            response = client.request(
                "device/ota_update/check", payload={"id": id_}, timeout=timeout
            )
            status, detail = _classify(response)
            if status == "update_available":
                update_available = True
            elif status == "up_to_date":
                update_available = False
        except Exception as exc:  # noqa: BLE001 - sweep continues past dead devices
            status, detail = "error", str(exc)
        rows.append(
            {
                "friendly_name": name,
                "ieee_address": d.get("ieee_address"),
                "status": status,
                "update_available": update_available,
                "detail": detail,
            }
        )
    rows.sort(key=lambda r: (_STATUS_ORDER.get(r["status"], 99), str(r["friendly_name"]).lower()))
    return rows


def summarize_check(rows: list[dict]) -> dict:
    """Counts by status for a :func:`check_all` result."""
    counts = {k: 0 for k in _STATUS_ORDER}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    out = {"total": len(rows)}
    out.update(counts)
    return out

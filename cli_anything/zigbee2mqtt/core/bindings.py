"""Direct zigbee bindings — `device/bind`, `device/unbind`, list bindings.

A zigbee *binding* glues two endpoints together at the network layer so one
device can directly drive another (e.g. a wall switch operating a bulb)
without the coordinator forwarding every message. Bindings survive z2m
being offline, so latency-sensitive control (lights, sirens) is best
implemented as a binding plus a fallback automation.

The wire format on z2m's MQTT API::

    bridge/request/device/bind     {from, to, clusters?}
    bridge/request/device/unbind   {from, to, clusters?}

* ``from`` is ``<friendly_name>`` or ``<ieee_address>``, optionally
  suffixed with ``/<endpoint>`` (defaults to 1).
* ``to`` accepts the same shape AND a group friendly_name (z2m resolves it).
* ``clusters`` is an optional list of cluster names (``["genOnOff",
  "genLevelCtrl"]``). When omitted, z2m binds the union of cluster names
  defined on the source endpoint that match a sink endpoint cluster.

Bindings are not a registry — there is no ``bridge/request/device/bindings``
list endpoint. Instead, the retained ``bridge/devices`` payload already
carries every endpoint's outgoing bindings under
``endpoints["1"].bindings[]``. :func:`list_bindings` synthesises a flat
view from that payload — no extra round trip needed.
"""

from __future__ import annotations

from typing import Optional

from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient
from cli_anything.zigbee2mqtt.core import devices as devices_core


# ──────────────────────────────────────────────────────────── mutators

def bind(
    client: BridgeClient, *,
    from_: str,
    to: str,
    clusters: Optional[list[str]] = None,
    timeout: float = 15.0,
) -> dict:
    """Bind two endpoints. `clusters=None` lets z2m pick the union.

    Returns the z2m response, which includes a ``clusters`` array of every
    cluster that was actually bound — confirms the intersection logic.
    """
    if not from_ or not to:
        raise ValueError("from_ and to are required")
    payload: dict = {"from": from_, "to": to}
    if clusters is not None:
        payload["clusters"] = list(clusters)
    return client.request("device/bind", payload=payload, timeout=timeout)


def unbind(
    client: BridgeClient, *,
    from_: str,
    to: str,
    clusters: Optional[list[str]] = None,
    timeout: float = 15.0,
) -> dict:
    """Remove a binding. `clusters=None` removes every common cluster."""
    if not from_ or not to:
        raise ValueError("from_ and to are required")
    payload: dict = {"from": from_, "to": to}
    if clusters is not None:
        payload["clusters"] = list(clusters)
    return client.request("device/unbind", payload=payload, timeout=timeout)


# ──────────────────────────────────────────────────────── introspection

def list_bindings(
    client: BridgeClient, *,
    device_ident: Optional[str] = None,
    timeout: float = 5.0,
) -> list[dict]:
    """Flatten every outgoing binding declared on `bridge/devices`.

    When *device_ident* is given, return only bindings for that device
    (matched against friendly_name or ieee_address). Otherwise return
    every binding in the network — useful for "which devices are bound
    to this group?" sweeps.

    Output rows::

        {
          "from_device": "<friendly_name>",
          "from_ieee": "0x...",
          "from_endpoint": 1,
          "cluster": "genOnOff",
          "to_type": "endpoint" | "group",
          "to_device": "<friendly_name>" | None,
          "to_ieee": "0x..." | None,
          "to_endpoint": 1 | None,
          "to_group": 42 | None,
        }
    """
    devices = devices_core.list_devices(client, timeout=timeout)
    target_l: Optional[str] = device_ident.lower() if device_ident else None

    out: list[dict] = []
    for d in devices:
        fname = d.get("friendly_name") or ""
        ieee = d.get("ieee_address") or ""
        if target_l is not None:
            if fname.lower() != target_l and ieee.lower() != target_l:
                continue

        endpoints = d.get("endpoints") or {}
        if not isinstance(endpoints, dict):
            continue
        for ep_id, ep in endpoints.items():
            if not isinstance(ep, dict):
                continue
            try:
                ep_num = int(ep_id)
            except (TypeError, ValueError):
                ep_num = ep_id  # type: ignore[assignment]
            for b in (ep.get("bindings") or []):
                if not isinstance(b, dict):
                    continue
                cluster = b.get("cluster")
                tgt = b.get("target") or {}
                ttype = tgt.get("type")
                row: dict = {
                    "from_device": fname,
                    "from_ieee": ieee,
                    "from_endpoint": ep_num,
                    "cluster": cluster,
                    "to_type": ttype,
                    "to_device": None,
                    "to_ieee": None,
                    "to_endpoint": None,
                    "to_group": None,
                }
                if ttype == "group":
                    row["to_group"] = tgt.get("id")
                else:
                    row["to_ieee"] = tgt.get("ieee_address")
                    row["to_endpoint"] = tgt.get("endpoint")
                    # Best-effort friendly_name lookup by ieee
                    if row["to_ieee"]:
                        for cand in devices:
                            if (cand.get("ieee_address") or "").lower() \
                                    == row["to_ieee"].lower():
                                row["to_device"] = cand.get("friendly_name")
                                break
                out.append(row)
    return out

"""Zigbee group management — add / remove / membership."""

from __future__ import annotations

import json
from typing import Optional

from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient


def list_groups(client: BridgeClient, *, timeout: float = 5.0) -> list[dict]:
    raw = client.collect_retained(f"{client.base_topic}/bridge/groups", timeout=timeout)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def add(
    client: BridgeClient, friendly_name: str, *, id_: Optional[int] = None, timeout: float = 10.0
) -> dict:
    payload: dict = {"friendly_name": friendly_name}
    if id_ is not None:
        payload["id"] = id_
    return client.request("group/add", payload=payload, timeout=timeout)


def remove(
    client: BridgeClient, id_or_name: str, *, force: bool = False, timeout: float = 10.0
) -> dict:
    return client.request(
        "group/remove",
        payload={
            "id": id_or_name,
            "force": force,
        },
        timeout=timeout,
    )


def rename(client: BridgeClient, from_: str, to: str, *, timeout: float = 10.0) -> dict:
    return client.request(
        "group/rename",
        payload={
            "from": from_,
            "to": to,
        },
        timeout=timeout,
    )


def add_member(client: BridgeClient, group: str, device: str, *, timeout: float = 10.0) -> dict:
    return client.request(
        "group/members/add",
        payload={
            "group": group,
            "device": device,
        },
        timeout=timeout,
    )


def remove_member(
    client: BridgeClient,
    group: str,
    device: str,
    *,
    skip_disable_reporting: bool = False,
    timeout: float = 10.0,
) -> dict:
    return client.request(
        "group/members/remove",
        payload={
            "group": group,
            "device": device,
            "skip_disable_reporting": skip_disable_reporting,
        },
        timeout=timeout,
    )


def remove_all_members(client: BridgeClient, group: str, *, timeout: float = 15.0) -> dict:
    return client.request(
        "group/members/remove_all",
        payload={
            "group": group,
        },
        timeout=timeout,
    )


def options(
    client: BridgeClient, id_: str, options_payload: dict, *, timeout: float = 10.0
) -> dict:
    """Set group-level options (transition, retain, etc.).

    Mirrors :func:`devices.options` for groups. Common keys:

    * ``transition`` — default transition (seconds) for state changes
    * ``off_state`` — ``"last_state"`` | ``"all_state"``
    * ``retain`` — retain published state messages

    *id_* is the group's friendly_name or numeric id.
    """
    if not id_:
        raise ValueError("id_ is required (group friendly_name or numeric id)")
    if not isinstance(options_payload, dict):
        raise ValueError("options_payload must be a dict")
    return client.request(
        "group/options",
        payload={
            "id": id_,
            "options": options_payload,
        },
        timeout=timeout,
    )


# ── group membership query ──────────────────────────────────────────────


def list_members(client: BridgeClient, group: str, *, timeout: float = 5.0) -> list[dict]:
    """Return the members of a group by friendly_name or numeric id.

    Reads the retained ``bridge/groups`` topic, finds the matching group,
    and returns its ``members`` array. Each member dict typically has
    ``ieee_address`` and ``endpoint`` keys. Returns ``[]`` when the
    group is not found or has no members.
    """
    if not group:
        raise ValueError("group is required (friendly_name or numeric id)")
    groups = list_groups(client, timeout=timeout)
    group_l = str(group).lower()
    for g in groups:
        gid = str(g.get("id", "")).lower()
        gname = str(g.get("friendly_name", "")).lower()
        if group_l == gid or group_l == gname:
            members = g.get("members") or []
            return members if isinstance(members, list) else []
    return []


# ── group state control (same command topics as devices) ────────────────
#
# A group is addressable exactly like a device: z2m subscribes
# `<base>/<group>/set` and `<base>/<group>/get`, and republishes the group's
# aggregate state on the retained `<base>/<group>` topic. Writing to the group
# emits a single Zigbee groupcast instead of N unicasts, so it is both faster
# and visually atomic (all bulbs change together) — always prefer it over
# looping `device set` across members.


def set_state(client: BridgeClient, group: str, fields: dict) -> int:
    """Publish to ``<base>/<group>/set`` to command every member at once.

    *fields* is the usual z2m command payload, e.g.
    ``{"state": "ON", "brightness": 180, "transition": 2}``.
    """
    if not group:
        raise ValueError("group is required (friendly_name or numeric id)")
    if not isinstance(fields, dict) or not fields:
        raise ValueError("fields must be a non-empty dict")
    return client.publish(f"{client.base_topic}/{group}/set", fields)


def get_state(client: BridgeClient, group: str, keys: list[str]) -> int:
    """Publish to ``<base>/<group>/get`` to ask the group to republish state."""
    if not group:
        raise ValueError("group is required (friendly_name or numeric id)")
    if not keys:
        raise ValueError("at least one key is required (e.g. state, brightness)")
    return client.publish(f"{client.base_topic}/{group}/get", {k: "" for k in keys})


def read_state(client: BridgeClient, group: str, *, timeout: float = 3.0) -> dict:
    """Return the group's last retained state payload (one-shot, never blocks).

    Mirrors :func:`devices.read_state`. A group that has never been commanded
    has no retained message, in which case ``{}`` comes back.
    """
    if not group:
        raise ValueError("group is required (friendly_name or numeric id)")
    raw = client.collect_retained(f"{client.base_topic}/{group}", timeout=timeout)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return data if isinstance(data, dict) else {"raw": raw}

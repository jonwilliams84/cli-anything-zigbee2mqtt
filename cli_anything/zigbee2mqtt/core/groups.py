"""Zigbee group management — add / remove / membership."""

from __future__ import annotations

import json
from typing import Optional

from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient


def list_groups(client: BridgeClient, *, timeout: float = 5.0) -> list[dict]:
    raw = client.collect_retained(
        f"{client.base_topic}/bridge/groups", timeout=timeout
    )
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def add(client: BridgeClient, friendly_name: str, *,
        id_: Optional[int] = None, timeout: float = 10.0) -> dict:
    payload: dict = {"friendly_name": friendly_name}
    if id_ is not None:
        payload["id"] = id_
    return client.request("group/add", payload=payload, timeout=timeout)


def remove(client: BridgeClient, id_or_name: str, *,
           force: bool = False, timeout: float = 10.0) -> dict:
    return client.request("group/remove", payload={
        "id": id_or_name, "force": force,
    }, timeout=timeout)


def rename(client: BridgeClient, from_: str, to: str, *,
           timeout: float = 10.0) -> dict:
    return client.request("group/rename", payload={
        "from": from_, "to": to,
    }, timeout=timeout)


def add_member(client: BridgeClient, group: str, device: str, *,
                timeout: float = 10.0) -> dict:
    return client.request("group/members/add", payload={
        "group": group, "device": device,
    }, timeout=timeout)


def remove_member(client: BridgeClient, group: str, device: str, *,
                   skip_disable_reporting: bool = False,
                   timeout: float = 10.0) -> dict:
    return client.request("group/members/remove", payload={
        "group": group, "device": device,
        "skip_disable_reporting": skip_disable_reporting,
    }, timeout=timeout)


def remove_all_members(client: BridgeClient, group: str, *,
                        timeout: float = 15.0) -> dict:
    return client.request("group/members/remove_all", payload={
        "group": group,
    }, timeout=timeout)

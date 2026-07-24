"""Bridge-level operations: info, state, restart, health, options, log tail."""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional

from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient


def info(client: BridgeClient, *, timeout: float = 5.0) -> dict:
    """Retained `bridge/info` — z2m version, coordinator, network params."""
    raw = client.collect_retained(
        f"{client.base_topic}/bridge/info", timeout=timeout
    )
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def state(client: BridgeClient, *, timeout: float = 3.0) -> str:
    """Retained `bridge/state` — typically 'online' / 'offline'."""
    raw = client.collect_retained(
        f"{client.base_topic}/bridge/state", timeout=timeout
    ) or ""
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            return data.get("state", raw)
        except json.JSONDecodeError:
            pass
    return raw


def restart(client: BridgeClient, *, timeout: float = 30.0) -> dict:
    """Ask z2m to restart itself (in-process; no kubectl required)."""
    return client.request("restart", payload={}, timeout=timeout)


def health_check(client: BridgeClient, *, timeout: float = 10.0) -> dict:
    return client.request("health_check", payload={}, timeout=timeout)


def options_get(client: BridgeClient, *, timeout: float = 5.0) -> dict:
    """Read z2m runtime options (subset of configuration.yaml)."""
    return client.request("options", payload={}, timeout=timeout)


def options_set(client: BridgeClient, options: dict, *,
                 timeout: float = 10.0) -> dict:
    return client.request("options", payload={"options": options},
                            timeout=timeout)


def watch_logging(client: BridgeClient, *,
                    duration: Optional[float] = None,
                    callback: Optional[Callable[[dict], None]] = None) -> list[dict]:
    """Tail `bridge/logging` for N seconds (None = until interrupted)."""
    collected: list[dict] = []

    def _cb(_topic, payload):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            data = {"raw": payload}
        collected.append(data)
        if callback:
            try:
                callback(data)
            except Exception as exc:  # noqa: BLE001 - user callback errors must not break tail
                data["_callback_error"] = str(exc)
    client.subscribe(f"{client.base_topic}/bridge/logging", _cb)
    end = time.time() + duration if duration else None
    try:
        while end is None or time.time() < end:
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    return collected


def watch_events(client: BridgeClient, *,
                  duration: Optional[float] = None,
                  callback: Optional[Callable[[dict], None]] = None) -> list[dict]:
    """Tail `bridge/event` — device joined/removed, OTA progress, etc."""
    collected: list[dict] = []

    def _cb(_topic, payload):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            data = {"raw": payload}
        collected.append(data)
        if callback:
            try:
                callback(data)
            except Exception as exc:  # noqa: BLE001 - user callback errors must not break tail
                data["_callback_error"] = str(exc)
    client.subscribe(f"{client.base_topic}/bridge/event", _cb)
    end = time.time() + duration if duration else None
    try:
        while end is None or time.time() < end:
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    return collected

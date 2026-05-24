"""External z2m extensions — list / show / save / remove.

z2m extensions are JS files that hook deeper into z2m than converters
do: they can subscribe to MQTT topics, inject services, schedule jobs,
or intercept messages. The bridge exposes them at::

    bridge/request/extension/save     {name, code}
    bridge/request/extension/remove   {name}

The full inventory is published on the retained ``bridge/extensions``
topic as a JSON array of ``{name, code}`` pairs. This module mirrors the
shape of :mod:`cli_anything.zigbee2mqtt.core.converters` so the CLI's
``extension`` group has the same ergonomics as ``converter``.

Unlike converters (which we manage via kubectl exec because they sit in a
data directory inside the z2m pod), extensions are managed entirely over
MQTT — z2m persists them through the bridge protocol. That means no
``k8s_backend`` dependency here.
"""

from __future__ import annotations

import json
from typing import Optional

from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient


def list_extensions(client: BridgeClient, *,
                     timeout: float = 5.0) -> list[dict]:
    """Return every extension z2m currently has loaded.

    Output is the retained ``bridge/extensions`` payload — a list of
    ``{name, code}`` dicts. Returns ``[]`` when the topic has no retained
    message (no extensions configured).
    """
    raw = client.collect_retained(
        f"{client.base_topic}/bridge/extensions", timeout=timeout,
    )
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def show(client: BridgeClient, name: str, *,
         timeout: float = 5.0) -> Optional[dict]:
    """Return one extension by name, or None if unknown."""
    if not name:
        raise ValueError("name is required")
    for ext in list_extensions(client, timeout=timeout):
        if isinstance(ext, dict) and ext.get("name") == name:
            return ext
    return None


def save(client: BridgeClient, *,
         name: str, code: str,
         timeout: float = 15.0) -> dict:
    """Upload (or overwrite) an extension by name.

    *name* must end with ``.js`` (z2m enforces this in some versions —
    we hint to help). *code* is the full JS source.
    """
    if not name:
        raise ValueError("name is required")
    if not name.endswith(".js"):
        raise ValueError("name should end with .js")
    if not isinstance(code, str) or not code:
        raise ValueError("code must be a non-empty string")
    return client.request("extension/save", payload={
        "name": name, "code": code,
    }, timeout=timeout)


def save_from_file(client: BridgeClient, *,
                    name: str, local_path: str,
                    timeout: float = 15.0) -> dict:
    """Convenience wrapper — read a local .js file and `save()` it."""
    with open(local_path, "r", encoding="utf-8") as fh:
        code = fh.read()
    return save(client, name=name, code=code, timeout=timeout)


def remove(client: BridgeClient, name: str, *,
            timeout: float = 10.0) -> dict:
    """Remove an extension by name."""
    if not name:
        raise ValueError("name is required")
    return client.request("extension/remove", payload={"name": name},
                            timeout=timeout)

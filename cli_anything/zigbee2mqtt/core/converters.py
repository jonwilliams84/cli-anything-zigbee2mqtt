"""External-converter management — MQTT bridge path and kubectl file path.

z2m 2.x exposes external converters over **two** surfaces:

1. **MQTT bridge requests** (works on any z2m reachable over MQTT, no
   kubectl):
   ::

       bridge/request/converter/save    {name, code}
       bridge/request/converter/remove  {name}

   The full inventory is published on the retained ``bridge/converters``
   topic as a JSON array of ``{name, code}`` pairs — the same pattern as
   ``bridge/extensions`` (see :mod:`cli_anything.zigbee2mqtt.core.extensions`).
   Saving through the bridge also **hot-loads** the converter: z2m writes the
   file, imports the module, and registers the definitions immediately — no
   restart. ``converter/remove`` hot-unloads likewise.

2. **Filesystem management** (kubectl exec into the z2m container): any
   ``.js`` file in ``<data>/external_converters/`` is auto-loaded at start.
   This module wraps the kubectl primitives so callers don't have to
   remember the exact exec/cp/sed dance.

MQTT-side functions (``*_mqtt``) mirror the shape of
:mod:`cli_anything.zigbee2mqtt.core.extensions`.
"""

from __future__ import annotations

import json

from cli_anything.zigbee2mqtt.core import k8s_backend
from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

#: file suffixes z2m accepts for external converter (and extension) files.
SUPPORTED_SUFFIXES = (".js", ".mjs", ".cjs")


# ── MQTT bridge path (no kubectl) ───────────────────────────────────────


def list_mqtt(client: BridgeClient, *, timeout: float = 5.0) -> list[dict]:
    """Return every converter z2m currently has, from retained ``bridge/converters``.

    Output is a list of ``{name, code}`` dicts; each row gains a
    ``bytes`` count of its source for quick sizing. Returns ``[]`` when the
    topic has no retained message (no external converters configured).
    """
    raw = client.collect_retained(
        f"{client.base_topic}/bridge/converters",
        timeout=timeout,
    )
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    rows: list[dict] = []
    for item in data:
        if not isinstance(item, dict) or "name" not in item:
            continue
        code = item.get("code") or ""
        rows.append(
            {
                "name": item["name"],
                "code": item.get("code", ""),
                "bytes": len(code.encode("utf-8")),
            }
        )
    return rows


def show_mqtt(client: BridgeClient, name: str, *, timeout: float = 5.0) -> str:
    """Return one converter's source code from the retained inventory.

    Raises ``KeyError`` when no converter with that name exists.
    """
    if not name:
        raise ValueError("name is required")
    for row in list_mqtt(client, timeout=timeout):
        if row.get("name") == name:
            return row.get("code") or ""
    raise KeyError(f"no converter named {name!r}")


def save_mqtt(client: BridgeClient, *, name: str, code: str, timeout: float = 15.0) -> dict:
    """Save (and hot-load) a converter through the bridge request API.

    *name* must end with ``.js`` / ``.mjs`` / ``.cjs`` (z2m rejects anything
    else). *code* is the full JS source. Returns the bridge response.
    """
    if not name:
        raise ValueError("name is required")
    if not name.endswith(SUPPORTED_SUFFIXES):
        raise ValueError("name should end with .js, .mjs or .cjs")
    if not isinstance(code, str) or not code:
        raise ValueError("code must be a non-empty string")
    return client.request(
        "converter/save",
        payload={
            "name": name,
            "code": code,
        },
        timeout=timeout,
    )


def save_mqtt_from_file(
    client: BridgeClient, *, name: str, local_path: str, timeout: float = 15.0
) -> dict:
    """Convenience wrapper — read a local .js file and ``save_mqtt()`` it."""
    with open(local_path, "r", encoding="utf-8") as fh:
        code = fh.read()
    return save_mqtt(client, name=name, code=code, timeout=timeout)


def remove_mqtt(client: BridgeClient, name: str, *, timeout: float = 10.0) -> dict:
    """Remove an external converter through the bridge request API.

    Hot-unloads the converter's definitions; no backup is taken (the file
    inside the z2m container is deleted by z2m itself).
    """
    if not name:
        raise ValueError("name is required")
    return client.request("converter/remove", payload={"name": name}, timeout=timeout)


# ── kubectl file path (existing commands) ───────────────────────────────


def list_converters(target: k8s_backend.K8sTarget) -> list[dict]:
    """Return one row per .js file in the converters dir, with size + mtime."""
    proc = k8s_backend.exec_(
        target,
        ["sh", "-c", f"ls -la {target.data_path}/external_converters 2>/dev/null | tail -n +2"],
        check=False,
    )
    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    rows: list[dict] = []
    for line in out.splitlines():
        parts = line.split()
        # mode links user group size month day time/year name
        if len(parts) < 9 or parts[-1] in (".", ".."):
            continue
        name = parts[-1]
        if not name.endswith(".js") and not name.endswith(".bak") and "." in name:
            # still record .bak/.removed.bak so the user can see them
            pass
        rows.append(
            {
                "name": name,
                "size_bytes": parts[4] if len(parts) > 4 else None,
                "modified": " ".join(parts[5:8]) if len(parts) > 7 else None,
            }
        )
    return rows


def show(target: k8s_backend.K8sTarget, name: str) -> str:
    return k8s_backend.read_external_converter(target, name)


def add(target: k8s_backend.K8sTarget, *, name: str, content: str, backup: bool = True) -> dict:
    k8s_backend.write_external_converter(target, name, content, backup=backup)
    return {"name": name, "bytes": len(content.encode("utf-8")), "backup": backup}


def add_from_file(
    target: k8s_backend.K8sTarget, *, name: str, local_path: str, backup: bool = True
) -> dict:
    with open(local_path, "r", encoding="utf-8") as f:
        content = f.read()
    return add(target, name=name, content=content, backup=backup)


def remove(target: k8s_backend.K8sTarget, name: str, *, backup: bool = True) -> dict:
    k8s_backend.remove_external_converter(target, name, backup=backup)
    return {"name": name, "removed": True, "backup": backup}

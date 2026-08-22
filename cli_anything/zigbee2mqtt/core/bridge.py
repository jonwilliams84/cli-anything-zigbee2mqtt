"""Bridge-level operations: info, state, restart, health, options, log tail."""

from __future__ import annotations

import json
import logging
import time
from typing import Callable, Optional

from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

logger = logging.getLogger(__name__)


def info(client: BridgeClient, *, timeout: float = 5.0) -> dict:
    """Retained `bridge/info` — z2m version, coordinator, network params."""
    raw = client.collect_retained(f"{client.base_topic}/bridge/info", timeout=timeout)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def state(client: BridgeClient, *, timeout: float = 3.0) -> str:
    """Retained `bridge/state` — typically 'online' / 'offline'."""
    raw = client.collect_retained(f"{client.base_topic}/bridge/state", timeout=timeout) or ""
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            return data.get("state", raw)
        except json.JSONDecodeError:
            logger.warning("Failed to parse bridge/state payload as JSON: %s", raw)
    return raw


def restart(client: BridgeClient, *, timeout: float = 30.0) -> dict:
    """Ask z2m to restart itself (in-process; no kubectl required)."""
    return client.request("restart", payload={}, timeout=timeout)


def health_check(client: BridgeClient, *, timeout: float = 10.0) -> dict:
    return client.request("health_check", payload={}, timeout=timeout)


def options_get(client: BridgeClient, *, timeout: float = 5.0) -> dict:
    """Read z2m runtime options (subset of configuration.yaml)."""
    return client.request("options", payload={}, timeout=timeout)


def options_set(client: BridgeClient, options: dict, *, timeout: float = 10.0) -> dict:
    return client.request("options", payload={"options": options}, timeout=timeout)


def watch_logging(
    client: BridgeClient,
    *,
    duration: Optional[float] = None,
    callback: Optional[Callable[[dict], None]] = None,
) -> list[dict]:
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
            except Exception as exc:
                # user callback errors must not break the tail loop; log and record
                logger.warning("watch_logging callback raised: %s", exc, exc_info=True)
                data["_callback_error"] = str(exc)

    client.subscribe(f"{client.base_topic}/bridge/logging", _cb)
    end = time.time() + duration if duration else None
    try:
        while end is None or time.time() < end:
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    return collected


def watch_events(
    client: BridgeClient,
    *,
    duration: Optional[float] = None,
    callback: Optional[Callable[[dict], None]] = None,
) -> list[dict]:
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
            except Exception as exc:
                # user callback errors must not break the tail loop; log and record
                logger.warning("watch_events callback raised: %s", exc, exc_info=True)
                data["_callback_error"] = str(exc)

    client.subscribe(f"{client.base_topic}/bridge/event", _cb)
    end = time.time() + duration if duration else None
    try:
        while end is None or time.time() < end:
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    return collected


def status(client: BridgeClient, *, timeout: float = 5.0) -> dict:
    """Combined view of bridge info and current state."""
    return {
        "info": info(client, timeout=timeout),
        "state": state(client, timeout=timeout),
    }


# ── log level get / set ──────────────────────────────────────────────────


def get_log_level(client: BridgeClient, *, timeout: float = 5.0) -> dict:
    """Return the current z2m log level from bridge/info.

    z2m publishes ``advanced.log_level`` in the retained ``bridge/info``
    payload. This is a convenience wrapper that extracts just that field.
    """
    info_data = info(client, timeout=timeout)
    advanced = info_data.get("advanced") or {}
    return {"log_level": advanced.get("log_level")}


def set_log_level(client: BridgeClient, level: str, *, timeout: float = 10.0) -> dict:
    """Set the z2m log level at runtime (no restart needed).

    Valid levels: ``debug``, ``info``, ``warn``, ``error``, ``silent``.
    Uses the ``bridge/options`` request endpoint with
    ``{"advanced": {"log_level": "<level>"}}``.
    """
    valid = {"debug", "info", "warn", "error", "silent"}
    if level not in valid:
        raise ValueError(f"level must be one of {sorted(valid)}, got {level!r}")
    return options_set(
        client,
        {"advanced": {"log_level": level}},
        timeout=timeout,
    )


# ── cluster dictionary (retained bridge/definitions) ────────────────────
#
# z2m publishes the zigbee-herdsman cluster dictionary it is running with on
# the retained ``bridge/definitions`` topic::
#
#     {"clusters": {"genOnOff": {"ID": 6, "attributes": {...},
#                                "commands": {...}, "commandsResponse": {...}}},
#      "custom_clusters": {"0x00124b00...": {"myCluster": {...}}}}
#
# This is the authoritative list of names ``device read`` / ``device write`` /
# ``device configure-reporting`` will accept, including any manufacturer
# clusters contributed by external converters. It is a retained read, so no
# round trip and no device wake-up.


def definitions(client: BridgeClient, *, timeout: float = 5.0) -> dict:
    """Return the parsed retained ``bridge/definitions`` payload (``{}`` if absent)."""
    raw = client.collect_retained(f"{client.base_topic}/bridge/definitions", timeout=timeout)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return data if isinstance(data, dict) else {}


def _cluster_map(defs: dict) -> dict:
    clusters = defs.get("clusters") if isinstance(defs, dict) else None
    return clusters if isinstance(clusters, dict) else {}


def summarize_clusters(defs: dict) -> list[dict]:
    """One row per cluster: name, numeric id, and how much it defines.

    Rows are ``{"cluster", "id", "attributes", "commands",
    "commands_response"}``, sorted by numeric id then name so the table reads
    like the ZCL spec.
    """
    rows: list[dict] = []
    for name, body in _cluster_map(defs).items():
        body = body if isinstance(body, dict) else {}
        rows.append(
            {
                "cluster": name,
                "id": body.get("ID", body.get("id")),
                "attributes": len(body.get("attributes") or {}),
                "commands": len(body.get("commands") or {}),
                "commands_response": len(body.get("commandsResponse") or {}),
            }
        )
    rows.sort(key=lambda r: (r["id"] is None, r["id"] if r["id"] is not None else 0, r["cluster"]))
    return rows


def find_cluster(defs: dict, cluster: str) -> Optional[dict]:
    """Look a cluster up by name (case-insensitive) or numeric id.

    Accepts ``"genOnOff"``, ``"genonoff"``, ``"6"`` or ``"0x0006"``. Returns
    ``{"cluster", "id", "definition"}`` or ``None`` when unknown.
    """
    if cluster is None or not str(cluster).strip():
        raise ValueError("cluster is required (name or numeric id)")
    text = str(cluster).strip()
    wanted_id: Optional[int]
    try:
        wanted_id = int(text, 0)
    except ValueError:
        wanted_id = None
    text_l = text.lower()
    for name, body in _cluster_map(defs).items():
        body = body if isinstance(body, dict) else {}
        cid = body.get("ID", body.get("id"))
        if name.lower() == text_l or (wanted_id is not None and cid == wanted_id):
            return {"cluster": name, "id": cid, "definition": body}
    return None


def cluster_attributes(defs: dict, cluster: str) -> list[dict]:
    """Attribute rows for one cluster — the names ``device read`` accepts.

    Each row is ``{"cluster", "attribute", "id", "type",
    "manufacturer_code"}``, sorted by attribute id.
    """
    found = find_cluster(defs, cluster)
    if not found:
        return []
    attrs = found["definition"].get("attributes") or {}
    rows: list[dict] = []
    if isinstance(attrs, dict):
        for attr_name, attr in attrs.items():
            attr = attr if isinstance(attr, dict) else {}
            rows.append(
                {
                    "cluster": found["cluster"],
                    "attribute": attr_name,
                    "id": attr.get("ID", attr.get("id")),
                    "type": attr.get("type"),
                    "manufacturer_code": attr.get("manufacturerCode"),
                }
            )
    rows.sort(
        key=lambda r: (r["id"] is None, r["id"] if r["id"] is not None else 0, r["attribute"])
    )
    return rows


def cluster_commands(defs: dict, cluster: str) -> list[dict]:
    """Command rows for one cluster (requests and responses in one table).

    Each row is ``{"cluster", "command", "id", "direction", "parameters"}``
    where *direction* is ``"request"`` or ``"response"``.
    """
    found = find_cluster(defs, cluster)
    if not found:
        return []
    rows: list[dict] = []
    for key, direction in (("commands", "request"), ("commandsResponse", "response")):
        block = found["definition"].get(key) or {}
        if not isinstance(block, dict):
            continue
        for cmd_name, cmd in block.items():
            cmd = cmd if isinstance(cmd, dict) else {}
            params = cmd.get("parameters") or []
            rows.append(
                {
                    "cluster": found["cluster"],
                    "command": cmd_name,
                    "id": cmd.get("ID", cmd.get("id")),
                    "direction": direction,
                    "parameters": [
                        p.get("name") for p in params if isinstance(p, dict) and p.get("name")
                    ],
                }
            )
    rows.sort(key=lambda r: (r["direction"] != "request", r["id"] is None, r["id"] or 0))
    return rows


def custom_clusters(defs: dict) -> list[dict]:
    """Flatten ``custom_clusters`` — manufacturer clusters added by converters.

    z2m keys this block by device ieee_address, each holding that device's
    extra cluster definitions. Rows are ``{"ieee_address", "cluster", "id",
    "attributes", "commands"}``.
    """
    block = defs.get("custom_clusters") if isinstance(defs, dict) else None
    rows: list[dict] = []
    if not isinstance(block, dict):
        return rows
    for ieee, clusters in block.items():
        if not isinstance(clusters, dict):
            continue
        for name, body in clusters.items():
            body = body if isinstance(body, dict) else {}
            rows.append(
                {
                    "ieee_address": ieee,
                    "cluster": name,
                    "id": body.get("ID", body.get("id")),
                    "attributes": len(body.get("attributes") or {}),
                    "commands": len(body.get("commands") or {}),
                }
            )
    rows.sort(key=lambda r: (str(r["ieee_address"]), str(r["cluster"])))
    return rows

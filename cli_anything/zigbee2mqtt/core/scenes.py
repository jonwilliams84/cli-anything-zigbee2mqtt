"""Zigbee scene management — store / recall / add / remove / rename / list.

Scenes are a Zigbee-cluster feature (``genScenes``), not a bridge feature, so
z2m exposes them through the *device/group command* topic rather than through
``bridge/request/…``. Every operation is a publish to::

    <base>/<friendly_name>/set          # or <base>/<friendly_name>/<endpoint>/set

with one of these payload keys:

============================  ==========================================
``{"scene_store": {...}}``    capture the target's **current** state as a
                              scene on the device(s)
``{"scene_recall": <id>}``    apply a stored scene
``{"scene_add": {...}}``      write a scene explicitly (state / brightness /
                              color / transition given up-front, no need to
                              set the lights first)
``{"scene_remove": <id>}``    delete one scene
``{"scene_remove_all": ""}``  delete every scene on the target
``{"scene_rename": {...}}``   rename a stored scene
============================  ==========================================

*target* is a device **or** a group friendly_name — publishing to a group makes
every member store/recall the scene under the same id, which is the normal way
scenes are used (one id per room). Scene ids are per-endpoint and 8-bit, so the
valid range is 0-255.

Because these are fire-and-forget publishes (the Zigbee scene commands have no
``bridge/response`` counterpart), the helpers here return the payload that was
published plus the publish rc — enough for an agent to confirm intent — and
:func:`list_scenes` reads the target's retained state to verify the result.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

#: Zigbee scene ids are a single byte.
MIN_SCENE_ID = 0
MAX_SCENE_ID = 255


def _check_target(target: str) -> str:
    if not target or not str(target).strip():
        raise ValueError("target is required (device or group friendly_name)")
    return str(target).strip()


def _check_scene_id(scene_id: int) -> int:
    try:
        sid = int(scene_id)
    except (TypeError, ValueError):
        raise ValueError(f"scene_id must be an integer, got {scene_id!r}") from None
    if sid < MIN_SCENE_ID or sid > MAX_SCENE_ID:
        raise ValueError(f"scene_id must be {MIN_SCENE_ID}-{MAX_SCENE_ID}, got {sid}")
    return sid


def _check_name(name: str) -> str:
    if not name or not str(name).strip():
        raise ValueError("name is required")
    return str(name).strip()


def set_topic(client: BridgeClient, target: str, *, endpoint: Optional[int | str] = None) -> str:
    """Return the ``/set`` topic for *target*, optionally endpoint-scoped.

    Scene ids live per endpoint, so a multi-gang device needs
    ``<base>/<name>/<endpoint>/set`` to address the right one.
    """
    target = _check_target(target)
    if endpoint is None or endpoint == "":
        return f"{client.base_topic}/{target}/set"
    return f"{client.base_topic}/{target}/{endpoint}/set"


def _publish(
    client: BridgeClient,
    target: str,
    payload: dict,
    *,
    endpoint: Optional[int | str] = None,
) -> dict:
    topic = set_topic(client, target, endpoint=endpoint)
    rc = client.publish(topic, payload)
    return {"target": _check_target(target), "topic": topic, "published": payload, "rc": rc}


# ── mutations ───────────────────────────────────────────────────────────


def store(
    client: BridgeClient,
    target: str,
    scene_id: int,
    *,
    name: Optional[str] = None,
    endpoint: Optional[int | str] = None,
) -> dict:
    """Capture the target's current state as scene *scene_id*.

    Set the lights how you want them first (``device set`` / ``group set``),
    then ``store`` snapshots that state into the device's scene table.
    *name* is optional metadata z2m keeps so ``list_scenes`` can label it.
    """
    sid = _check_scene_id(scene_id)
    body: dict[str, Any] = {"ID": sid}
    if name is not None:
        body["name"] = _check_name(name)
    return _publish(client, target, {"scene_store": body}, endpoint=endpoint)


def recall(
    client: BridgeClient,
    target: str,
    scene_id: int,
    *,
    endpoint: Optional[int | str] = None,
) -> dict:
    """Apply stored scene *scene_id* on the target."""
    sid = _check_scene_id(scene_id)
    return _publish(client, target, {"scene_recall": sid}, endpoint=endpoint)


def add(
    client: BridgeClient,
    target: str,
    scene_id: int,
    *,
    name: Optional[str] = None,
    transition: Optional[float] = None,
    state: Optional[str] = None,
    brightness: Optional[int] = None,
    color_temp: Optional[int] = None,
    color: Optional[dict] = None,
    extra: Optional[dict] = None,
    endpoint: Optional[int | str] = None,
) -> dict:
    """Write a scene explicitly, without first setting the lights.

    Unlike :func:`store` (which snapshots live state) this hands z2m the
    values to bake in. Any attribute the target exposes can be supplied via
    *extra* (e.g. ``{"color": {"x": 0.4, "y": 0.4}}``); the named kwargs are
    just the common ones. ``transition`` is the fade time in seconds used
    when the scene is later recalled.
    """
    sid = _check_scene_id(scene_id)
    body: dict[str, Any] = {"ID": sid}
    if name is not None:
        body["name"] = _check_name(name)
    if transition is not None:
        if float(transition) < 0:
            raise ValueError("transition must be >= 0")
        body["transition"] = float(transition)
    if state is not None:
        body["state"] = str(state).upper()
    if brightness is not None:
        b = int(brightness)
        if b < 0 or b > 254:
            raise ValueError("brightness must be 0-254")
        body["brightness"] = b
    if color_temp is not None:
        body["color_temp"] = int(color_temp)
    if color is not None:
        if not isinstance(color, dict):
            raise ValueError('color must be a JSON object, e.g. {"x":0.4,"y":0.4}')
        body["color"] = color
    if extra:
        if not isinstance(extra, dict):
            raise ValueError("extra must be a dict of attribute -> value")
        body.update(extra)
    return _publish(client, target, {"scene_add": body}, endpoint=endpoint)


def remove(
    client: BridgeClient,
    target: str,
    scene_id: int,
    *,
    endpoint: Optional[int | str] = None,
) -> dict:
    """Delete a single scene from the target's scene table."""
    sid = _check_scene_id(scene_id)
    return _publish(client, target, {"scene_remove": sid}, endpoint=endpoint)


def remove_all(
    client: BridgeClient,
    target: str,
    *,
    endpoint: Optional[int | str] = None,
) -> dict:
    """Delete every scene stored on the target.

    z2m expects an empty value for this command, hence ``{"scene_remove_all": ""}``.
    """
    return _publish(client, target, {"scene_remove_all": ""}, endpoint=endpoint)


def rename(
    client: BridgeClient,
    target: str,
    scene_id: int,
    name: str,
    *,
    endpoint: Optional[int | str] = None,
) -> dict:
    """Rename a stored scene (metadata only — the light values are untouched)."""
    sid = _check_scene_id(scene_id)
    return _publish(
        client,
        target,
        {"scene_rename": {"ID": sid, "name": _check_name(name)}},
        endpoint=endpoint,
    )


# ── read side ───────────────────────────────────────────────────────────


def _normalize(rows: Any) -> list[dict]:
    """Coerce z2m's ``scenes`` array into ``[{"id": int, "name": str}]``."""
    out: list[dict] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if isinstance(row, dict):
            sid = row.get("id", row.get("ID"))
            out.append({"id": sid, "name": row.get("name")})
        elif isinstance(row, int):
            out.append({"id": row, "name": None})
    out.sort(key=lambda r: (r["id"] is None, r["id"]))
    return out


def list_scenes(client: BridgeClient, target: str, *, timeout: float = 3.0) -> list[dict]:
    """Return the scenes z2m believes are stored on a device or group.

    z2m publishes a ``scenes`` array inside the retained state payload of
    every scene-capable device and group, so this is a single retained read
    on ``<base>/<target>``. Groups also carry ``scenes`` in the retained
    ``bridge/groups`` inventory — used as a fallback when the group has
    never published state (e.g. right after creation).

    Returns ``[]`` when the target has no scenes or is unknown.
    """
    target = _check_target(target)
    raw = client.collect_retained(f"{client.base_topic}/{target}", timeout=timeout)
    if raw:
        try:
            state = json.loads(raw)
        except json.JSONDecodeError:
            state = None
        if isinstance(state, dict) and state.get("scenes") is not None:
            return _normalize(state.get("scenes"))
    # fallback: the retained group inventory carries scenes too
    raw_groups = client.collect_retained(
        f"{client.base_topic}/bridge/groups",
        timeout=timeout,
    )
    if not raw_groups:
        return []
    try:
        groups = json.loads(raw_groups)
    except json.JSONDecodeError:
        return []
    if not isinstance(groups, list):
        return []
    target_l = target.lower()
    for g in groups:
        if not isinstance(g, dict):
            continue
        if target_l in (str(g.get("friendly_name", "")).lower(), str(g.get("id", "")).lower()):
            return _normalize(g.get("scenes"))
    return []

"""Device list / show / rename / remove / interview / configure / set / get."""

from __future__ import annotations

import json
import time
from typing import Optional

from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient


def list_devices(client: BridgeClient, *, timeout: float = 5.0) -> list[dict]:
    raw = client.collect_retained(f"{client.base_topic}/bridge/devices", timeout=timeout)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def show(client: BridgeClient, ident: str) -> Optional[dict]:
    """Find a device by IEEE address or friendly_name."""
    ident_l = ident.lower()
    for d in list_devices(client):
        if d.get("ieee_address", "").lower() == ident_l:
            return d
        if d.get("friendly_name", "").lower() == ident_l:
            return d
    return None


def summarize(devices: list[dict]) -> list[dict]:
    """Flatten device records for table display."""
    out: list[dict] = []
    for d in devices:
        defn = d.get("definition") or {}
        out.append(
            {
                "friendly_name": d.get("friendly_name"),
                "ieee_address": d.get("ieee_address"),
                "model": defn.get("model"),
                "vendor": defn.get("vendor"),
                "type": d.get("type"),
                "manufacturer": d.get("manufacturer"),
                "power_source": d.get("power_source"),
                "interview_completed": d.get("interview_completed"),
                "supported": d.get("supported"),
                "disabled": d.get("disabled", False),
                "description": d.get("description"),
            }
        )
    return out


# ── mutation primitives ─────────────────────────────────────────────────


def rename(
    client: BridgeClient,
    *,
    from_: str,
    to: str,
    homeassistant_rename: bool = True,
    timeout: float = 15.0,
) -> dict:
    """Rename a device. `homeassistant_rename` also renames the HA entities so
    the unique_id is preserved (default true — almost always what you want)."""
    return client.request(
        "device/rename",
        payload={
            "from": from_,
            "to": to,
            "homeassistant_rename": homeassistant_rename,
        },
        timeout=timeout,
    )


def remove(
    client: BridgeClient,
    id_: str,
    *,
    force: bool = False,
    block: bool = False,
    timeout: float = 30.0,
) -> dict:
    """Remove a device from the network (and from z2m's database).

    force=True skips network-level removal (useful when the device is already
    physically gone). block=True adds it to the block-list so it can't rejoin.
    """
    return client.request(
        "device/remove",
        payload={
            "id": id_,
            "force": force,
            "block": block,
        },
        timeout=timeout,
    )


def configure(client: BridgeClient, id_: str, *, timeout: float = 30.0) -> dict:
    """Re-run device configuration (re-bindings, reports). Use after a device
    starts reporting wrong values or never set up reports correctly."""
    return client.request("device/configure", payload={"id": id_}, timeout=timeout)


def interview(client: BridgeClient, id_: str, *, timeout: float = 60.0) -> dict:
    """Force a fresh device interview (re-read endpoints, clusters, model).
    Slow — typically 30+ seconds while the device wakes up."""
    return client.request("device/interview", payload={"id": id_}, timeout=timeout)


def options(
    client: BridgeClient, id_: str, options_payload: dict, *, timeout: float = 10.0
) -> dict:
    """Set per-device options (z2m's `device_options` block)."""
    return client.request(
        "device/options",
        payload={
            "id": id_,
            "options": options_payload,
        },
        timeout=timeout,
    )


def set_value(client: BridgeClient, friendly_name: str, fields: dict) -> int:
    """Publish to `<base>/<friendly_name>/set` to write device state."""
    topic = f"{client.base_topic}/{friendly_name}/set"
    return client.publish(topic, fields)


def get_value(client: BridgeClient, friendly_name: str, keys: list[str]) -> int:
    """Publish to `<base>/<friendly_name>/get` to ask the device to publish state."""
    topic = f"{client.base_topic}/{friendly_name}/get"
    payload = {k: "" for k in keys}
    return client.publish(topic, payload)


def watch_device(
    client: BridgeClient, friendly_name: str, *, duration: Optional[float] = None
) -> list[dict]:
    """Tail the device's state topic for N seconds."""
    topic = f"{client.base_topic}/{friendly_name}"
    collected: list[dict] = []

    def _cb(_t, p):
        try:
            collected.append(json.loads(p))
        except json.JSONDecodeError:
            collected.append({"raw": p})

    client.subscribe(topic, _cb)
    end = time.time() + duration if duration else None
    try:
        while end is None or time.time() < end:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    return collected


# ── one-shot retained state read ────────────────────────────────────────


def read_state(client: BridgeClient, friendly_name: str, *, timeout: float = 3.0) -> dict:
    """Return the current retained state payload for a device.

    z2m publishes the latest state on ``<base>/<friendly_name>`` with
    ``retain=true``, so a fresh subscribe receives the last-known message
    instantly. Unlike :func:`watch_device` this never blocks waiting for a
    new update — it just grabs whatever is retained.
    """
    if not friendly_name:
        raise ValueError("friendly_name is required")
    topic = f"{client.base_topic}/{friendly_name}"
    raw = client.collect_retained(topic, timeout=timeout)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


# ── staleness sweep (local filter on bridge/devices.last_seen) ──────────


def find_stale(
    client: BridgeClient,
    *,
    threshold_minutes: int = 60,
    include_routers: bool = True,
    include_end_devices: bool = True,
    timeout: float = 5.0,
) -> list[dict]:
    """Rank devices by how long they've been silent.

    z2m records ``last_seen`` (ISO-8601 string when the last message
    arrived from the device) inside the retained ``bridge/devices``
    payload. This helper subtracts that from now and keeps anything
    older than *threshold_minutes*. No MQTT round-trip beyond the
    initial bridge/devices subscribe.

    Returns rows sorted oldest-first::

        {
          "friendly_name", "ieee_address", "type", "model",
          "last_seen", "minutes_since_seen", "power_source",
        }
    """
    import datetime as _dt

    devices = list_devices(client, timeout=timeout)
    now = _dt.datetime.now(_dt.timezone.utc)
    out: list[dict] = []
    for d in devices:
        kind = d.get("type")
        if kind == "Coordinator":
            continue
        if kind == "Router" and not include_routers:
            continue
        if kind in ("EndDevice", "GreenPower") and not include_end_devices:
            continue
        last = d.get("last_seen")
        if not last:
            mins = None
        else:
            try:
                # last_seen is ISO-8601 with Z or +00:00
                last_dt = _dt.datetime.fromisoformat(
                    last.replace("Z", "+00:00"),
                )
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=_dt.timezone.utc)
                mins = (now - last_dt).total_seconds() / 60.0
            except (ValueError, AttributeError):
                mins = None
        if mins is None or mins < threshold_minutes:
            continue
        defn = d.get("definition") or {}
        out.append(
            {
                "friendly_name": d.get("friendly_name"),
                "ieee_address": d.get("ieee_address"),
                "type": kind,
                "model": defn.get("model"),
                "last_seen": last,
                "minutes_since_seen": round(mins, 1),
                "power_source": d.get("power_source"),
            }
        )
    out.sort(key=lambda r: r["minutes_since_seen"] or 0, reverse=True)
    return out


# ── definition generator (for unsupported devices) ──────────────────────


def generate_external_definition(client: BridgeClient, id_: str, *, timeout: float = 30.0) -> dict:
    """Ask z2m to generate a starter external-converter `.js` for a device.

    z2m walks the device's interview data (endpoints / clusters /
    attributes) and emits a converter file you can drop into
    ``external_converters/``. Useful when a device is recognised at the
    transport layer but missing from zigbee-herdsman-converters.

    Returns ``{source, ...}`` where ``source`` is the generated JS.
    """
    if not id_:
        raise ValueError("id_ is required (friendly_name or ieee_address)")
    return client.request(
        "device/generate_external_definition", payload={"id": id_}, timeout=timeout
    )


# ── manual reporting configuration ──────────────────────────────────────


def configure_reporting(
    client: BridgeClient,
    *,
    id_: str,
    cluster: str,
    attribute: str,
    minimum_report_interval: int,
    maximum_report_interval: int,
    reportable_change: Optional[float] = None,
    endpoint: Optional[int] = None,
    timeout: float = 15.0,
) -> dict:
    """Set up a single attribute report on a device endpoint.

    z2m's default reporting setup (run during interview / configure)
    sometimes misses the attribute you actually want, or sets a window
    that's too aggressive on a battery device. This bypasses defaults
    and writes a single report binding directly.

    *cluster* is a herdsman cluster name (``"genOnOff"``,
    ``"genLevelCtrl"``, ``"msTemperatureMeasurement"`` …). *attribute*
    is the herdsman attribute id within that cluster. Intervals are in
    seconds; *reportable_change* is in the attribute's native units
    (omit for boolean attributes).
    """
    if not id_:
        raise ValueError("id_ is required")
    if not cluster or not attribute:
        raise ValueError("cluster and attribute are required")
    if minimum_report_interval < 0 or maximum_report_interval < 0:
        raise ValueError("intervals must be non-negative")
    payload: dict = {
        "id": id_,
        "cluster": cluster,
        "attribute": attribute,
        "minimum_report_interval": int(minimum_report_interval),
        "maximum_report_interval": int(maximum_report_interval),
    }
    if reportable_change is not None:
        payload["reportable_change"] = reportable_change
    if endpoint is not None:
        payload["endpoint"] = int(endpoint)
    return client.request("device/configure_reporting", payload=payload, timeout=timeout)


# ── enable / disable ───────────────────────────────────────────────────


def disable(client: BridgeClient, id_: str, *, timeout: float = 10.0) -> dict:
    """Disable a device so z2m stops polling and publishing its state.

    Uses z2m's ``device/options`` endpoint with ``{"disabled": true}``.
    The device stays in the database but is effectively dormant — useful
    for battery devices that are offline for long periods or for
    troubleshooting a misbehaving sensor without removing it.
    """
    if not id_:
        raise ValueError("id_ is required")
    return client.request(
        "device/options",
        payload={"id": id_, "options": {"disabled": True}},
        timeout=timeout,
    )


def enable(client: BridgeClient, id_: str, *, timeout: float = 10.0) -> dict:
    """Re-enable a previously disabled device.

    Uses z2m's ``device/options`` endpoint with ``{"disabled": false}``.
    """
    if not id_:
        raise ValueError("id_ is required")
    return client.request(
        "device/options",
        payload={"id": id_, "options": {"disabled": False}},
        timeout=timeout,
    )


# ── last-seen summary ────────────────────────────────────────────────────


def last_seen(client: BridgeClient, id_: str, *, timeout: float = 5.0) -> dict:
    """Return a compact last-seen summary for a single device.

    Looks up the device by IEEE address or friendly_name, then extracts
    the ``last_seen`` timestamp and computes minutes since. Returns
    ``{}`` when the device is not found.
    """
    dev = show(client, id_)
    if not dev:
        return {}
    import datetime as _dt

    last = dev.get("last_seen")
    result: dict = {
        "friendly_name": dev.get("friendly_name"),
        "ieee_address": dev.get("ieee_address"),
        "last_seen": last,
        "type": dev.get("type"),
    }
    if not last:
        result["minutes_since_seen"] = None
        return result
    try:
        last_dt = _dt.datetime.fromisoformat(last.replace("Z", "+00:00"))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=_dt.timezone.utc)
        now = _dt.datetime.now(_dt.timezone.utc)
        result["minutes_since_seen"] = round((now - last_dt).total_seconds() / 60.0, 1)
    except (ValueError, AttributeError):
        result["minutes_since_seen"] = None
    return result


# ── exposes introspection (local, from the retained device record) ──────

#: z2m's ``access`` bitmask (see the exposes docs).
ACCESS_PUBLISHED = 0b001  # value is published in the state topic
ACCESS_SET = 0b010  # value can be written with `<name>/set`
ACCESS_GET = 0b100  # value can be requested with `<name>/get`


def decode_access(access: Optional[int]) -> str:
    """Render z2m's numeric ``access`` bitmask as a short ``pubs/set/get`` string.

    ``5`` → ``"published,get"``. Returns ``""`` when *access* is missing so
    table output stays aligned.
    """
    if access is None:
        return ""
    try:
        bits = int(access)
    except (TypeError, ValueError):
        return ""
    parts = []
    if bits & ACCESS_PUBLISHED:
        parts.append("published")
    if bits & ACCESS_SET:
        parts.append("set")
    if bits & ACCESS_GET:
        parts.append("get")
    return ",".join(parts)


def flatten_exposes(exposes: Optional[list], *, _prefix: str = "") -> list[dict]:
    """Flatten a device definition's ``exposes`` tree into one row per property.

    z2m nests exposes: a ``light`` expose carries ``features`` (state,
    brightness, color_temp …), and a ``composite`` expose namespaces its
    features under its own property. This walks the tree depth-first and
    returns flat rows suitable for a table::

        {"property", "name", "type", "access", "access_flags", "unit",
         "values", "value_min", "value_max", "endpoint", "description"}

    Rows keep the order z2m declared them in.
    """
    rows: list[dict] = []
    for exp in exposes or []:
        if not isinstance(exp, dict):
            continue
        features = exp.get("features")
        prop = exp.get("property")
        if isinstance(features, list) and features:
            # A composite namespaces its children under its own property;
            # light/switch/cover/… expose their features at the top level.
            child_prefix = _prefix
            if exp.get("type") == "composite" and prop:
                child_prefix = f"{_prefix}{prop}."
            rows.extend(flatten_exposes(features, _prefix=child_prefix))
            continue
        if not prop:
            continue
        access = exp.get("access")
        rows.append(
            {
                "property": f"{_prefix}{prop}",
                "name": exp.get("name"),
                "type": exp.get("type"),
                "access": access,
                "access_flags": decode_access(access),
                "unit": exp.get("unit"),
                "values": exp.get("values"),
                "value_min": exp.get("value_min"),
                "value_max": exp.get("value_max"),
                "endpoint": exp.get("endpoint"),
                "description": exp.get("description"),
            }
        )
    return rows


def exposes(
    client: BridgeClient,
    ident: str,
    *,
    settable_only: bool = False,
    timeout: float = 5.0,
) -> list[dict]:
    """Return the flattened exposes table for one device.

    Purely local — it reads the retained ``bridge/devices`` inventory that
    :func:`list_devices` already uses, so there is no extra round trip and no
    need to wake the device. This is the fastest way to find out which
    properties ``device set`` / ``device get`` will actually accept.

    ``settable_only=True`` keeps only writable properties (access bit 2).
    Returns ``[]`` when the device is unknown or has no definition.
    """
    if not ident:
        raise ValueError("ident is required (friendly_name or ieee_address)")
    dev = show(client, ident)
    if not dev:
        return []
    defn = dev.get("definition") or {}
    rows = flatten_exposes(defn.get("exposes"))
    if settable_only:
        rows = [r for r in rows if (r.get("access") or 0) & ACCESS_SET]
    return rows


# ── availability (retained <base>/<name>/availability) ──────────────────

AVAILABILITY_SUFFIX = "/availability"


def parse_availability(raw: Optional[str]) -> Optional[str]:
    """Normalise an availability payload to ``"online"`` / ``"offline"``.

    z2m publishes either the bare string ``online`` / ``offline`` (legacy) or
    ``{"state": "online"}`` (default since 1.34). Returns ``None`` when the
    payload is missing or unrecognisable.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        state = data.get("state") if isinstance(data, dict) else None
        return str(state).lower() if state else None
    return text.lower()


def read_availability(client: BridgeClient, friendly_name: str, *, timeout: float = 3.0) -> dict:
    """Read one device's retained availability state (one-shot, never blocks long).

    Availability is only published when z2m's ``availability`` feature is
    enabled; otherwise ``availability`` comes back ``None``.
    """
    if not friendly_name:
        raise ValueError("friendly_name is required")
    topic = f"{client.base_topic}/{friendly_name}{AVAILABILITY_SUFFIX}"
    raw = client.collect_retained(topic, timeout=timeout)
    state = parse_availability(raw)
    return {
        "friendly_name": friendly_name,
        "availability": state,
        "online": None if state is None else state == "online",
    }


def availability_sweep(
    client: BridgeClient,
    *,
    duration: float = 2.0,
    offline_only: bool = False,
    timeout: float = 5.0,
) -> list[dict]:
    """Availability for every known device, in one pass.

    Subscribes to ``<base>/#`` once and collects the retained
    ``…/availability`` messages that arrive, instead of doing one blocking
    read per device (which would cost ``N × timeout`` on a big network).
    Results are joined onto the ``bridge/devices`` inventory so devices that
    never published availability still appear, with ``availability: null``.

    Rows are sorted offline-first, then by friendly_name. ``offline_only=True``
    drops everything that is currently online or unknown.
    """
    devices = list_devices(client, timeout=timeout)
    seen: dict[str, Optional[str]] = {}
    prefix = f"{client.base_topic}/"

    def _cb(topic: str, payload: str) -> None:
        if not topic.startswith(prefix) or not topic.endswith(AVAILABILITY_SUFFIX):
            return
        name = topic[len(prefix) : -len(AVAILABILITY_SUFFIX)]
        seen[name] = parse_availability(payload)

    client.subscribe(f"{client.base_topic}/#", _cb)
    end = time.time() + max(duration, 0.0)
    try:
        while time.time() < end:
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass

    rows: list[dict] = []
    for d in devices:
        if d.get("type") == "Coordinator":
            continue
        name = d.get("friendly_name")
        state = seen.get(name) if name else None
        if state is None and d.get("ieee_address"):
            state = seen.get(d["ieee_address"])
        if offline_only and state != "offline":
            continue
        defn = d.get("definition") or {}
        rows.append(
            {
                "friendly_name": name,
                "ieee_address": d.get("ieee_address"),
                "availability": state,
                "online": None if state is None else state == "online",
                "type": d.get("type"),
                "model": defn.get("model"),
                "last_seen": d.get("last_seen"),
            }
        )
    rows.sort(key=lambda r: (r["availability"] != "offline", str(r["friendly_name"] or "")))
    return rows


# ── battery audit (local join of bridge/devices × retained state topics) ─


def _coerce_num(v):
    """Percent / voltage arrive as int, float or numeric string — else None."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def classify_battery(
    battery,
    battery_low,
    *,
    power_source: Optional[str] = None,
    below: float = 20.0,
) -> str:
    """Classify one device's battery health.

    ``battery`` is the percent value from the device state (may be a numeric
    string, ``None``, or missing); ``battery_low`` is the optional boolean
    low-battery flag. A device that is not battery powered classifies as
    ``"mains"``; one that is battery powered but has published nothing usable
    classifies as ``"unknown"`` (typically a sleeping sensor that must be
    woken to report). Otherwise ``"low"`` when below *below* percent, else
    ``"ok"``.
    """
    if battery is not None:
        pct = _coerce_num(battery)
        if pct is not None:
            return "low" if pct < below else "ok"
    if battery_low is True:
        return "low"
    if battery_low is False:
        return "ok"
    if power_source and "batter" in str(power_source).lower():
        return "unknown"
    return "mains"


def battery_sweep(
    client: BridgeClient,
    *,
    duration: float = 2.0,
    below: float = 20.0,
    low_only: bool = False,
    timeout: float = 5.0,
) -> list[dict]:
    """Battery audit across the whole network, in one pass.

    Battery sensors publish ``battery`` (percent), ``battery_low`` and
    ``voltage`` (mV) inside their retained state payload — but only when they
    last woke up, so the audit joins the ``bridge/devices`` inventory onto the
    retained state topics collected in a single ``<base>/#`` subscription
    (same technique as :func:`availability_sweep`).

    Devices with no battery at all (mains-powered) are dropped. Battery
    devices that never published state get ``status: "unknown"`` — tap them
    to wake them and re-run.

    Rows are sorted worst-first (``low`` → ``unknown`` → ``ok``, then by
    battery percent ascending), so the top row is the device needing a fresh
    cell. ``low_only=True`` drops everything that is ``"ok"``.

    Status ranking keys used for sorting: low=0, unknown=1, ok=2.
    """
    devices = list_devices(client, timeout=timeout)
    states: dict[str, dict] = {}
    prefix = f"{client.base_topic}/"

    def _cb(topic: str, payload: str) -> None:
        if not topic.startswith(prefix) or topic.endswith(AVAILABILITY_SUFFIX):
            return
        name = topic[len(prefix) :]
        # state topics are `<base>/<friendly_name>` — anything with a second
        # level (`bridge/...`, `.../set`, `.../get`, `.../availability`) is
        # not a device state payload.
        if not name or "/" in name:
            return
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return
        if isinstance(data, dict):
            states[name] = data

    client.subscribe(f"{client.base_topic}/#", _cb)
    end = time.time() + max(duration, 0.0)
    try:
        while time.time() < end:
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass

    rows: list[dict] = []
    for d in devices:
        if d.get("type") == "Coordinator":
            continue
        name = d.get("friendly_name")
        state = states.get(name) if name else None
        if state is None and d.get("ieee_address"):
            state = states.get(d["ieee_address"])

        battery = state.get("battery") if isinstance(state, dict) else None
        battery_low = state.get("battery_low") if isinstance(state, dict) else None
        voltage = state.get("voltage") if isinstance(state, dict) else None
        try:
            voltage = float(voltage) if voltage is not None else None
        except (TypeError, ValueError):
            voltage = None

        power_source = d.get("power_source")
        status = classify_battery(battery, battery_low, power_source=power_source, below=below)
        if status == "mains":
            continue
        if low_only and status == "ok":
            continue

        defn = d.get("definition") or {}
        rows.append(
            {
                "friendly_name": name,
                "ieee_address": d.get("ieee_address"),
                "battery": _coerce_num(battery),
                "battery_low": bool(battery_low) if battery_low is not None else None,
                "voltage": voltage,
                "status": status,
                "type": d.get("type"),
                "model": defn.get("model"),
                "power_source": power_source,
            }
        )
    rank = {"low": 0, "unknown": 1, "ok": 2}
    rows.sort(
        key=lambda r: (
            rank.get(r["status"], 3),
            r["battery"] if r["battery"] is not None else float("inf"),
            str(r["friendly_name"] or ""),
        )
    )
    return rows


# ── endpoint / cluster introspection (local, from bridge/devices) ───────
#
# The retained ``bridge/devices`` inventory carries, per endpoint:
#
#     "endpoints": {"1": {"clusters": {"input": [...], "output": [...]},
#                         "bindings": [...], "configured_reportings": [...],
#                         "scenes": [...]}}
#
# That is exactly the information ``device read`` / ``device write`` /
# ``device bind --cluster`` / ``device configure-reporting`` need, so the
# helpers below expose it without any round trip — they work fine against a
# sleeping battery device.

#: Accepted values for the ``direction`` filter on :func:`clusters`.
CLUSTER_DIRECTIONS = ("input", "output", "all")


def _endpoint_key(ep_id) -> int | str:
    """Endpoint ids arrive as JSON object keys (strings) — prefer ints."""
    try:
        return int(ep_id)
    except (TypeError, ValueError):
        return ep_id


def _iter_endpoints(device: dict):
    """Yield ``(endpoint_id, endpoint_dict)`` pairs, lowest endpoint first."""
    endpoints = device.get("endpoints") or {}
    if not isinstance(endpoints, dict):
        return
    for ep_id, ep in endpoints.items():
        if isinstance(ep, dict):
            yield _endpoint_key(ep_id), ep


def _match_endpoint(ep_num, wanted) -> bool:
    if wanted is None or wanted == "":
        return True
    return str(ep_num) == str(wanted)


def flatten_endpoint_clusters(
    device: dict,
    *,
    direction: str = "all",
    endpoint: Optional[int | str] = None,
) -> list[dict]:
    """Flatten one device record into one row per (endpoint, cluster).

    Pure function over a ``bridge/devices`` entry — no MQTT. Each row::

        {"endpoint", "cluster", "direction", "bound", "reported"}

    ``direction`` is ``"input"`` (clusters the device *implements*, i.e. what
    you can read/write), ``"output"`` (clusters it *sends*, i.e. what you can
    bind away to a target) or ``"all"``. ``bound`` says an outgoing binding
    already exists for that cluster on that endpoint; ``reported`` says an
    attribute report is configured for it. Rows are ordered by endpoint, then
    input-before-output, then cluster name.
    """
    if direction not in CLUSTER_DIRECTIONS:
        raise ValueError(f"direction must be one of {list(CLUSTER_DIRECTIONS)}, got {direction!r}")
    rows: list[dict] = []
    for ep_num, ep in _iter_endpoints(device):
        if not _match_endpoint(ep_num, endpoint):
            continue
        clusters = ep.get("clusters") or {}
        if not isinstance(clusters, dict):
            clusters = {}
        bound = {
            b.get("cluster")
            for b in (ep.get("bindings") or [])
            if isinstance(b, dict) and b.get("cluster")
        }
        reported = {
            r.get("cluster")
            for r in (ep.get("configured_reportings") or [])
            if isinstance(r, dict) and r.get("cluster")
        }
        for kind in ("input", "output"):
            if direction not in (kind, "all"):
                continue
            names = clusters.get(kind) or []
            if not isinstance(names, list):
                continue
            for name in names:
                rows.append(
                    {
                        "endpoint": ep_num,
                        "cluster": name,
                        "direction": kind,
                        "bound": name in bound,
                        "reported": name in reported,
                    }
                )
    rows.sort(key=lambda r: (str(r["endpoint"]), r["direction"] != "input", str(r["cluster"])))
    return rows


def clusters(
    client: BridgeClient,
    ident: str,
    *,
    direction: str = "all",
    endpoint: Optional[int | str] = None,
    timeout: float = 5.0,
) -> list[dict]:
    """Return the endpoint/cluster table for one device.

    Local read of the retained inventory (same source as :func:`exposes`), so
    it costs nothing and needs no device wake-up. Use it to find the exact
    cluster names ``device read`` / ``device write`` /
    ``device configure-reporting`` accept, and to see which output clusters
    are still unbound before calling ``device bind``.

    Returns ``[]`` when the device is unknown or was never interviewed.
    """
    if not ident:
        raise ValueError("ident is required (friendly_name or ieee_address)")
    if direction not in CLUSTER_DIRECTIONS:
        raise ValueError(f"direction must be one of {list(CLUSTER_DIRECTIONS)}, got {direction!r}")
    dev = show(client, ident)
    if not dev:
        return []
    return flatten_endpoint_clusters(dev, direction=direction, endpoint=endpoint)


def flatten_reportings(device: dict, *, endpoint: Optional[int | str] = None) -> list[dict]:
    """Flatten a device record's ``configured_reportings`` into table rows.

    Pure function over a ``bridge/devices`` entry. Each row::

        {"friendly_name", "ieee_address", "endpoint", "cluster", "attribute",
         "minimum_report_interval", "maximum_report_interval",
         "reportable_change"}

    z2m stores the attribute either as a plain name or as
    ``{"ID": 0, "type": 33}`` for manufacturer-specific ids — both are
    normalised to a string here.
    """
    fname = device.get("friendly_name")
    ieee = device.get("ieee_address")
    rows: list[dict] = []
    for ep_num, ep in _iter_endpoints(device):
        if not _match_endpoint(ep_num, endpoint):
            continue
        for rep in ep.get("configured_reportings") or []:
            if not isinstance(rep, dict):
                continue
            attr = rep.get("attribute")
            if isinstance(attr, dict):
                attr = attr.get("ID", attr.get("id"))
            rows.append(
                {
                    "friendly_name": fname,
                    "ieee_address": ieee,
                    "endpoint": ep_num,
                    "cluster": rep.get("cluster"),
                    "attribute": None if attr is None else str(attr),
                    "minimum_report_interval": rep.get("minimum_report_interval"),
                    "maximum_report_interval": rep.get("maximum_report_interval"),
                    "reportable_change": rep.get("reportable_change"),
                }
            )
    return rows


def reportings(
    client: BridgeClient,
    *,
    device_ident: Optional[str] = None,
    endpoint: Optional[int | str] = None,
    timeout: float = 5.0,
) -> list[dict]:
    """List configured attribute reports — for one device or the whole network.

    This is the read-back for :func:`configure_reporting`: after adding a
    report, run this to confirm z2m recorded it (the bridge response only says
    the request was accepted). With *device_ident* omitted it sweeps every
    device, which is the quick way to find sensors that never got reports set
    up during their interview.

    Rows are sorted by device, endpoint, cluster, attribute.
    """
    target_l = device_ident.lower() if device_ident else None
    out: list[dict] = []
    for d in list_devices(client, timeout=timeout):
        if target_l is not None:
            fname = (d.get("friendly_name") or "").lower()
            ieee = (d.get("ieee_address") or "").lower()
            if target_l not in (fname, ieee):
                continue
        out.extend(flatten_reportings(d, endpoint=endpoint))
    out.sort(
        key=lambda r: (
            str(r["friendly_name"] or ""),
            str(r["endpoint"]),
            str(r["cluster"] or ""),
            str(r["attribute"] or ""),
        )
    )
    return out


def endpoint_summary(client: BridgeClient, ident: str, *, timeout: float = 5.0) -> list[dict]:
    """One row per endpoint: how many clusters, bindings, reports and scenes.

    The orientation view for a multi-gang switch or multi-endpoint plug —
    it tells you which ``--endpoint`` to pass to ``device read`` /
    ``scene store`` before drilling into :func:`clusters`.

    Each row::

        {"endpoint", "input_clusters", "output_clusters", "bindings",
         "configured_reportings", "scenes", "scene_ids"}
    """
    if not ident:
        raise ValueError("ident is required (friendly_name or ieee_address)")
    dev = show(client, ident)
    if not dev:
        return []
    rows: list[dict] = []
    for ep_num, ep in _iter_endpoints(dev):
        cl = ep.get("clusters") or {}
        if not isinstance(cl, dict):
            cl = {}
        scenes = [s for s in (ep.get("scenes") or []) if isinstance(s, dict)]
        rows.append(
            {
                "endpoint": ep_num,
                "input_clusters": len(cl.get("input") or []),
                "output_clusters": len(cl.get("output") or []),
                "bindings": len(ep.get("bindings") or []),
                "configured_reportings": len(ep.get("configured_reportings") or []),
                "scenes": len(scenes),
                "scene_ids": [s.get("id", s.get("ID")) for s in scenes],
            }
        )
    rows.sort(key=lambda r: str(r["endpoint"]))
    return rows

"""Raw MQTT passthrough — direct access to z2m's message plane.

z2m's control surface *is* MQTT: bridge request/response topics, per-device
``<name>/set`` / ``<name>/get`` writes, and retained state dumps
(``bridge/info``, ``bridge/devices``, ``bridge/groups``, each device's state
topic). The typed command groups wrap the common paths; these helpers reach
*everything else* — documented or not, current or future bridge API — without
leaving the CLI.

Topics are always resolved against the bridge base topic: ``lamp/set`` means
``<base>/lamp/set``. A topic that already carries the base topic prefix is
used verbatim.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable, Optional

from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

logger = logging.getLogger(__name__)

# ── pure helpers (client-free, unit-testable) ────────────────────────────


def normalize_topic(topic: str, base_topic: str) -> str:
    """Resolve *topic* against the bridge base topic.

    Raises ``ValueError`` on an empty/blank topic — that would target the
    broker root and almost always means a scripting mistake.
    """
    if topic is None or not str(topic).strip():
        raise ValueError("topic is required")
    base = base_topic.rstrip("/")
    if str(topic).startswith(f"{base}/"):
        return topic
    return f"{base}/{topic}"


def coerce_payload(raw: Optional[str]):
    """Best-effort decode of an MQTT payload for display / re-send.

    JSON text parses (object, list, number, bool, null); everything else stays
    the raw UTF-8-decoded string, which is what a non-JSON topic carries.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def check_qos(qos: int) -> int:
    if qos not in (0, 1, 2):
        raise ValueError("qos must be 0, 1 or 2")
    return qos


# ── transport-backed operations ──────────────────────────────────────────


def publish_raw(
    client: BridgeClient,
    topic: str,
    payload=None,
    *,
    retain: bool = False,
    qos: int = 0,
) -> dict:
    """Publish *payload* to ``<base>/<topic>`` and report what went on the wire.

    A *payload* string that parses as JSON is sent as JSON (so
    ``mqtt publish lamp/set '{"state":"ON"}'`` is indistinguishable from
    ``device set``); any other text is published verbatim, and an omitted
    payload publishes an empty message. Returns
    ``{"topic", "payload", "published", "rc", "retain", "qos"}`` — use
    ``published`` / ``rc`` to gate scripts (same shape as ``device identify``).
    """
    check_qos(qos)
    full = normalize_topic(topic, client.base_topic)
    sent = coerce_payload(payload) if isinstance(payload, str) else payload
    rc = client.publish(full, sent, retain=retain, qos=qos)
    return {
        "topic": full,
        "payload": sent,
        "published": int(rc) == 0,
        "rc": int(rc),
        "retain": retain,
        "qos": qos,
    }


def read_raw(client: BridgeClient, topic: str, *, timeout: float = 5.0) -> dict:
    """One-shot read of a topic's retained message (never blocks longer than *timeout*).

    Works for every retained z2m dump — ``bridge/info``, ``bridge/devices``,
    ``bridge/groups``, ``bridge/definitions`` — and for any device state
    topic. A topic with no retained message reports ``payload: null`` rather
    than an error, because "nothing ever published here" is a valid answer.
    """
    full = normalize_topic(topic, client.base_topic)
    raw = client.collect_retained(full, timeout=timeout)
    payload = coerce_payload(raw) if raw is not None else None
    return {"topic": full, "payload": payload}


def watch_topic(
    client: BridgeClient,
    filter_: str,
    *,
    duration: Optional[float] = 15.0,
    callback: Optional[Callable[[dict], None]] = None,
) -> list[dict]:
    """Collect every message matching *filter_* for *duration* seconds.

    *filter_* is an MQTT wildcard subscription resolved against the base
    topic, e.g. ``sensors/#`` or ``bridge/logging``. Each entry is
    ``{"topic", "payload"}`` with the payload JSON-decoded when possible.
    ``duration=None`` tails until interrupted (Ctrl-C). Errors in *callback*
    are logged, never raised — a misbehaving consumer must not kill the tail
    loop (same contract as ``bridge.watch_events``).
    """
    if not filter_ or not str(filter_).strip():
        raise ValueError("filter_ is required")
    filt = normalize_topic(filter_, client.base_topic)
    collected: list[dict] = []

    def _cb(topic, payload):
        data = {"topic": topic, "payload": coerce_payload(payload)}
        collected.append(data)
        if callback:
            try:
                callback(data)
            except Exception as exc:  # noqa: BLE001 — see security-scan notes
                logger.warning("watch_topic callback raised: %s", exc, exc_info=True)
                data["_callback_error"] = str(exc)

    client.subscribe(filt, _cb)
    end = (time.time() + duration) if duration is not None else None
    try:
        while end is None or time.time() < end:
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    return collected


def list_topics(
    client: BridgeClient,
    *,
    duration: float = 2.0,
    prefix: Optional[str] = None,
    preview_chars: int = 120,
) -> list[dict]:
    """Snapshot the topics under ``<base>/#`` that published during the window.

    Subscribing pulls every retained dump immediately, so a short window is
    enough to enumerate the bridge's topic tree — device state topics,
    ``bridge/*``, group topics — without knowing names up front. Live
    messages published during the window are captured too (last write wins
    per topic). Rows sort by topic and carry the payload size in bytes plus a
    truncated preview, so the list stays readable even when
    ``bridge/devices`` dumps tens of kilobytes.
    """
    filt = f"{client.base_topic}/#"
    if prefix:
        filt = f"{normalize_topic(prefix, client.base_topic)}/#"
    rows: dict[str, dict] = {}
    lock = threading.Lock()

    def _cb(topic, payload):
        with lock:
            rows[topic] = {
                "topic": topic,
                "bytes": len(payload or ""),
                "preview": (payload or "")[:preview_chars],
            }

    client.subscribe(filt, _cb)
    end = time.time() + duration
    try:
        while time.time() < end:
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    return [rows[t] for t in sorted(rows)]

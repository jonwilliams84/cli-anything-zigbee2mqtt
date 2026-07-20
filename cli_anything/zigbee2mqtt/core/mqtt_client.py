"""MQTT helper for talking to a running Zigbee2MQTT bridge.

z2m's control surface is request/response over MQTT:
  - request:  zigbee2mqtt/bridge/request/<path>
  - response: zigbee2mqtt/bridge/response/<path>
Each request can carry a `transaction` id; z2m echoes it back so we can correlate
when several requests are in flight. We always set one — that lets `bridge_request`
return the matching response synchronously.

The same client also supports raw publish / subscribe for inspecting device-state
topics (`zigbee2mqtt/<friendly_name>`), bridge events / logging, and per-device
`/get` and `/set` writes.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Callable, Optional

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    mqtt = None  # type: ignore


class MqttError(RuntimeError):
    pass


class BridgeClient:
    """Thin MQTT client tailored for z2m's bridge protocol.

    Use as a context manager — `with BridgeClient(...) as c: c.request(...)` —
    or call `connect()` / `disconnect()` explicitly.
    """

    def __init__(self, host: str, port: int = 1883, *,
                 username: Optional[str] = None,
                 password: Optional[str] = None,
                 base_topic: str = "zigbee2mqtt",
                 client_id: Optional[str] = None,
                 keepalive: int = 30) -> None:
        if mqtt is None:
            raise MqttError(
                "paho-mqtt not installed — pip install paho-mqtt "
                "or reinstall the harness."
            )
        self.host = host
        self.port = port
        self.base_topic = base_topic.rstrip("/")
        self._username = username
        self._password = password
        self.client_id = client_id or f"cli-anything-z2m-{uuid.uuid4().hex[:8]}"
        self.keepalive = keepalive
        self.client = mqtt.Client(client_id=self.client_id)
        if username:
            self.client.username_pw_set(username, password or None)
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}
        self._subscribers: list[tuple[str, Callable[[str, str], None]]] = []
        self.client.on_message = self._on_message
        self._connected = False

    # ── context manager + lifecycle ─────────────────────────────────────

    def __enter__(self) -> "BridgeClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.disconnect()

    def connect(self) -> None:
        if self._connected:
            return
        self.client.connect(self.host, self.port, keepalive=self.keepalive)
        # subscribe to the bridge response surface up front
        self.client.subscribe(f"{self.base_topic}/bridge/response/#", qos=0)
        self.client.loop_start()
        self._connected = True

    def disconnect(self) -> None:
        if not self._connected:
            return
        try:
            self.client.loop_stop()
            self.client.disconnect()
        finally:
            self._connected = False

    # ── bridge request/response ─────────────────────────────────────────

    def request(self, path: str, payload: Any = None, *,
                 timeout: float = 15.0) -> dict:
        """Send a bridge request and wait for the matching response.

        `path` is e.g. "device/rename" — the leading `bridge/request/` is added.
        `payload` is JSON-encoded; if it's a dict we add a `transaction` id
        to correlate the response. Plain string/scalar payloads are passed verbatim.

        Returns the response dict on success or raises MqttError on timeout / z2m
        error status.
        """
        if not self._connected:
            self.connect()
        path = path.lstrip("/")
        topic = f"{self.base_topic}/bridge/request/{path}"
        txn = uuid.uuid4().hex[:12]
        if isinstance(payload, dict):
            payload = dict(payload)
            payload.setdefault("transaction", txn)
        elif payload is None:
            payload = {"transaction": txn}
        else:
            # scalar payload (rare) — wrap into the standard shape
            payload = {"value": payload, "transaction": txn}
        body = json.dumps(payload)

        event = threading.Event()
        slot: dict = {}
        with self._lock:
            self._pending[txn] = {"event": event, "slot": slot, "path": path}

        try:
            info = self.client.publish(topic, body, qos=0)
            info.wait_for_publish(timeout=5)
            if not event.wait(timeout=timeout):
                raise MqttError(f"timed out waiting for response to {path}")
        finally:
            with self._lock:
                self._pending.pop(txn, None)

        resp = slot.get("response", {})
        status = resp.get("status")
        if status == "error":
            raise MqttError(f"{path}: {resp.get('error') or resp}")
        return resp

    def _on_message(self, _client, _ud, msg):
        topic = msg.topic
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload = ""
        # bridge response handling
        prefix = f"{self.base_topic}/bridge/response/"
        if topic.startswith(prefix):
            try:
                data = json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                data = {"raw": payload}
            txn = data.get("transaction")
            if txn:
                with self._lock:
                    pending = self._pending.get(txn)
                    if pending:
                        pending["slot"]["response"] = data
                        pending["event"].set()
                        return
        # general subscriber dispatch
        for filt, cb in list(self._subscribers):
            if mqtt.topic_matches_sub(filt, topic):
                try:
                    cb(topic, payload)
                except Exception:
                    pass

    # ── generic publish / subscribe ─────────────────────────────────────

    def publish(self, topic: str, payload: Any, *, retain: bool = False,
                 qos: int = 0) -> int:
        if not self._connected:
            self.connect()
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload)
        elif isinstance(payload, bool):
            body = "true" if payload else "false"
        elif isinstance(payload, (int, float)):
            body = str(payload)
        else:
            body = "" if payload is None else str(payload)
        info = self.client.publish(topic, body, qos=qos, retain=retain)
        info.wait_for_publish(timeout=5)
        return info.rc

    def subscribe(self, filter_: str, callback: Callable[[str, str], None]) -> None:
        if not self._connected:
            self.connect()
        self.client.subscribe(filter_, qos=0)
        self._subscribers.append((filter_, callback))

    def collect_retained(self, topic: str, *, timeout: float = 5.0) -> Optional[str]:
        """One-shot: subscribe, wait for the retained message on `topic`, return payload.

        Useful for reading `zigbee2mqtt/bridge/devices` or `bridge/info` which are
        published with retain=true and arrive immediately after subscription.
        """
        slot: dict = {"payload": None}
        event = threading.Event()

        def _cb(_t, p):
            slot["payload"] = p
            event.set()
        self.subscribe(topic, _cb)
        event.wait(timeout=timeout)
        return slot.get("payload")

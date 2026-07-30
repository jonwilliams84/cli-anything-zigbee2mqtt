"""Tests for uncovered branches in mqtt_client, devices, and ota modules.

Targets error paths, edge cases, and branches that the existing suite
never exercises — not trivial wiring or constants.
"""

from __future__ import annotations

import json
import threading
import pytest

from cli_anything.zigbee2mqtt.core import devices as devices_core
from cli_anything.zigbee2mqtt.core import ota as ota_core


# ════════════════════════════════════════════════════════════════════════
# FakeMqttClient — same pattern as test_core.py
# ════════════════════════════════════════════════════════════════════════


class FakeMqttClient:
    """Minimum surface to satisfy paho.mqtt.Client usage in BridgeClient."""

    def __init__(self, client_id):
        self.client_id = client_id
        self.on_message = None
        self.subscriptions: list[str] = []
        self.published: list[tuple[str, str, int, bool]] = []
        self.username = None
        self.password = None
        self._connected = False
        self._stop = threading.Event()

    def username_pw_set(self, u, p=None):
        self.username, self.password = u, p

    def connect(self, host, port, keepalive=30):
        self.host, self.port, self.keepalive = host, port, keepalive
        self._connected = True

    def disconnect(self):
        self._connected = False

    def loop_start(self):
        self._stop.clear()

    def loop_stop(self):
        self._stop.set()

    def subscribe(self, topic, qos=0):
        self.subscriptions.append(topic)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))
        if "/bridge/request/" in topic:
            req_path = topic.split("/bridge/request/", 1)[1]
            try:
                data = json.loads(payload)
            except Exception:
                data = {}
            txn = data.get("transaction")
            resp_topic = topic.replace("/request/", "/response/")
            resp = {"status": "ok", "data": {"echo": req_path}, "transaction": txn}

            class FakeMsg:
                def __init__(self, t, p):
                    self.topic = t
                    self.payload = json.dumps(p).encode()

            if self.on_message:
                self.on_message(self, None, FakeMsg(resp_topic, resp))

        class Info:
            rc = 0

            def wait_for_publish(self, timeout=None):
                return None

        return Info()


@pytest.fixture
def fake_paho(monkeypatch):
    """Swap paho.mqtt.client.Client for FakeMqttClient inside mqtt_client.py."""
    from cli_anything.zigbee2mqtt.core import mqtt_client as mc

    real_mqtt = mc.mqtt

    class FakeMqttModule:
        Client = FakeMqttClient

        @staticmethod
        def topic_matches_sub(filt, topic):
            if filt == topic:
                return True
            if filt.endswith("/#") and topic.startswith(filt[:-1]):
                return True
            if "+" in filt:
                fparts = filt.split("/")
                tparts = topic.split("/")
                if len(fparts) != len(tparts):
                    return False
                return all(f == "+" or f == t for f, t in zip(fparts, tparts, strict=False))
            return False

    monkeypatch.setattr(mc, "mqtt", FakeMqttModule)
    yield
    monkeypatch.setattr(mc, "mqtt", real_mqtt)


# ════════════════════════════════════════════════════════════════════════
# mqtt_client — uncovered branches
# ════════════════════════════════════════════════════════════════════════


class TestMqttClientRequestPayloads:
    """Cover the scalar-payload and None-payload branches in request()."""

    def test_request_scalar_payload_wraps_in_value(self, fake_paho):
        """A scalar (non-dict, non-None) payload should be wrapped as {'value': payload}."""
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        with c as client:
            resp = client.request("device/rename", payload="my-scalar")
        # The published body should contain {"value": "my-scalar", "transaction": ...}
        last_topic, last_body, _, _ = client.client.published[-1]
        body = json.loads(last_body)
        assert body["value"] == "my-scalar"
        assert "transaction" in body
        assert resp["status"] == "ok"

    def test_request_none_payload_creates_transaction_only(self, fake_paho):
        """A None payload should produce a body with only 'transaction'."""
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        with c as client:
            client.request("bridge/info", payload=None)
        last_topic, last_body, _, _ = client.client.published[-1]
        body = json.loads(last_body)
        assert set(body.keys()) == {"transaction"}

    def test_request_timeout_raises_mqtt_error(self, fake_paho, monkeypatch):
        """When no response arrives within timeout, MqttError must be raised."""
        from cli_anything.zigbee2mqtt.core import mqtt_client as mc

        # Patch publish to NOT auto-respond, so the event never fires
        def silent_publish(self, topic, payload, qos=0, retain=False):
            self.published.append((topic, payload, qos, retain))

            class Info:
                rc = 0

                def wait_for_publish(self, timeout=None):
                    return None

            return Info()

        monkeypatch.setattr(FakeMqttClient, "publish", silent_publish, raising=True)

        c = mc.BridgeClient("fake-host", base_topic="zigbee2mqtt")
        with c as client:
            with pytest.raises(mc.MqttError, match="timed out"):
                client.request("device/rename", payload={"from": "A", "to": "B"}, timeout=0.05)


class TestMqttClientPublishTypes:
    """Cover the type-dispatch branches in publish()."""

    def test_publish_bool_true(self, fake_paho):
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        c.connect()
        c.publish("test/topic", True)
        _, body, _, _ = c.client.published[-1]
        assert body == "true"

    def test_publish_bool_false(self, fake_paho):
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        c.connect()
        c.publish("test/topic", False)
        _, body, _, _ = c.client.published[-1]
        assert body == "false"

    def test_publish_int(self, fake_paho):
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        c.connect()
        c.publish("test/topic", 42)
        _, body, _, _ = c.client.published[-1]
        assert body == "42"

    def test_publish_float(self, fake_paho):
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        c.connect()
        c.publish("test/topic", 3.14)
        _, body, _, _ = c.client.published[-1]
        assert body == "3.14"

    def test_publish_none(self, fake_paho):
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        c.connect()
        c.publish("test/topic", None)
        _, body, _, _ = c.client.published[-1]
        assert body == ""

    def test_publish_string(self, fake_paho):
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        c.connect()
        c.publish("test/topic", "hello")
        _, body, _, _ = c.client.published[-1]
        assert body == "hello"


class TestMqttClientOnMessageEdgeCases:
    """Cover _on_message edge cases: empty payload, malformed JSON, no txn."""

    def test_on_message_empty_payload_on_response_topic(self, fake_paho):
        """Empty payload on a bridge/response topic should not crash."""
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        c.connect()

        class FakeMsg:
            topic = "zigbee2mqtt/bridge/response/device/rename"
            payload = b""

        # Should not raise
        c._on_message(None, None, FakeMsg())

    def test_on_message_malformed_json_on_response_topic(self, fake_paho):
        """Malformed JSON on a response topic falls through to subscriber dispatch
        because it has no 'transaction' key to correlate."""
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        c.connect()

        received: list[str] = []

        def cb(topic, payload):
            received.append(payload)

        c.subscribe("zigbee2mqtt/bridge/response/#", cb)

        class FakeMsg:
            topic = "zigbee2mqtt/bridge/response/device/rename"
            payload = b"not-json-at-all"

        c._on_message(None, None, FakeMsg())

        # The malformed payload should be dispatched to subscribers as-is
        assert received == ["not-json-at-all"]

    def test_on_message_response_with_unknown_txn(self, fake_paho):
        """A response with a transaction not in _pending should be dispatched to subscribers."""
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        c.connect()

        received: list[tuple[str, str]] = []

        def cb(topic, payload):
            received.append((topic, payload))

        c.subscribe("zigbee2mqtt/bridge/response/#", cb)

        class FakeMsg:
            topic = "zigbee2mqtt/bridge/response/device/rename"
            payload = json.dumps({"transaction": "unknown-txn", "status": "ok"}).encode()

        c._on_message(None, None, FakeMsg())

        assert len(received) == 1
        assert "unknown-txn" in received[0][1]

    def test_on_message_decode_exception_falls_back_to_empty(self, fake_paho):
        """If msg.payload.decode raises, payload should become empty string."""
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        c.connect()

        received: list[str] = []

        def cb(topic, payload):
            received.append(payload)

        c.subscribe("zigbee2mqtt/some/topic", cb)

        class BadPayload:
            def decode(self, *args, **kwargs):
                raise UnicodeDecodeError("utf-8", b"", 0, 1, "bad")

        class FakeMsg:
            topic = "zigbee2mqtt/some/topic"
            payload = BadPayload()

        c._on_message(None, None, FakeMsg())

        assert received == [""]


class TestMqttClientLifecycle:
    """Cover connect/disconnect idempotency and context manager."""

    def test_double_connect_is_noop(self, fake_paho):
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        c.connect()
        c.connect()  # should not re-subscribe
        subs_before = len(c.client.subscriptions)
        c.connect()
        assert len(c.client.subscriptions) == subs_before

    def test_disconnect_when_not_connected_is_noop(self, fake_paho):
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        # Should not raise
        c.disconnect()

    def test_context_manager_connects_and_disconnects(self, fake_paho):
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        with c as client:
            assert client._connected is True
        assert c._connected is False

    def test_username_password_set_on_init(self, fake_paho):
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", username="user", password="pass")
        assert c.client.username == "user"
        assert c.client.password == "pass"

    def test_request_auto_connects_if_not_connected(self, fake_paho):
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        # Don't call connect() first — request() should auto-connect
        resp = c.request("device/rename", payload={"from": "A", "to": "B"})
        assert resp["status"] == "ok"
        assert c._connected is True


class TestMqttClientCollectRetained:
    """Cover collect_retained()."""

    def test_collect_retained_returns_payload(self, fake_paho):
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        c.connect()

        # Simulate a retained message arriving by calling the subscriber callback
        # that collect_retained registered
        # collect_retained subscribes and waits; we need to fire the callback
        # from a separate thread to avoid deadlock

        def fire_retained():
            import time

            time.sleep(0.05)
            # Find the callback registered for our topic
            for filt, cb in c._subscribers:
                if filt == "zigbee2mqtt/bridge/info":
                    cb("zigbee2mqtt/bridge/info", '{"version": "1.0"}')

        t = threading.Thread(target=fire_retained)
        t.start()
        result = c.collect_retained("zigbee2mqtt/bridge/info", timeout=2.0)
        t.join()
        assert result == '{"version": "1.0"}'

    def test_collect_retained_returns_none_on_timeout(self, fake_paho):
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        c.connect()
        result = c.collect_retained("zigbee2mqtt/bridge/info", timeout=0.05)
        assert result is None


# ════════════════════════════════════════════════════════════════════════
# devices — uncovered branches
# ════════════════════════════════════════════════════════════════════════


class FakeClient:
    """Fake BridgeClient for devices/ota tests (no real MQTT)."""

    base_topic = "zigbee2mqtt"

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self._request_responses: dict[str, dict] = {}
        self._retained: dict[str, str] = {}
        self.published: list[tuple[str, object]] = []

    def set_request(self, path: str, response: dict) -> None:
        self._request_responses[path] = response

    def set_retained(self, topic: str, payload) -> None:
        if not isinstance(payload, str):
            payload = json.dumps(payload)
        self._retained[topic] = payload

    def request(self, path: str, payload=None, *, timeout: float = 0):
        self.requests.append({"path": path, "payload": payload, "timeout": timeout})
        return self._request_responses.get(path, {"status": "ok", "data": {}})

    def collect_retained(self, topic: str, *, timeout: float = 5.0):
        return self._retained.get(topic)

    def publish(self, topic: str, payload, *, retain: bool = False, qos: int = 0) -> int:
        self.published.append((topic, payload))
        return 0

    def subscribe(self, filter_: str, callback) -> None:
        pass


class TestDevicesListShow:
    """Cover list_devices and show edge cases."""

    def test_list_devices_empty_retained(self):
        fc = FakeClient()
        result = devices_core.list_devices(fc)
        assert result == []

    def test_list_devices_malformed_json(self):
        fc = FakeClient()
        fc.set_retained("zigbee2mqtt/bridge/devices", "not-json")
        result = devices_core.list_devices(fc)
        assert result == []

    def test_list_devices_non_list_retained(self):
        """If retained payload is a dict (not a list), should return []."""
        fc = FakeClient()
        fc.set_retained("zigbee2mqtt/bridge/devices", '{"not": "a list"}')
        result = devices_core.list_devices(fc)
        assert result == []

    def test_list_devices_valid_list(self):
        fc = FakeClient()
        fc.set_retained(
            "zigbee2mqtt/bridge/devices",
            [{"friendly_name": "lamp", "ieee_address": "0x1234"}],
        )
        result = devices_core.list_devices(fc)
        assert len(result) == 1
        assert result[0]["friendly_name"] == "lamp"

    def test_show_by_ieee_address(self):
        fc = FakeClient()
        fc.set_retained(
            "zigbee2mqtt/bridge/devices",
            [{"friendly_name": "lamp", "ieee_address": "0x1234"}],
        )
        result = devices_core.show(fc, "0x1234")
        assert result is not None
        assert result["friendly_name"] == "lamp"

    def test_show_by_friendly_name_case_insensitive(self):
        fc = FakeClient()
        fc.set_retained(
            "zigbee2mqtt/bridge/devices",
            [{"friendly_name": "Lounge Lamp", "ieee_address": "0x1234"}],
        )
        result = devices_core.show(fc, "lounge lamp")
        assert result is not None
        assert result["friendly_name"] == "Lounge Lamp"

    def test_show_not_found_returns_none(self):
        fc = FakeClient()
        fc.set_retained(
            "zigbee2mqtt/bridge/devices",
            [{"friendly_name": "lamp", "ieee_address": "0x1234"}],
        )
        result = devices_core.show(fc, "nonexistent")
        assert result is None


class TestDevicesMutations:
    """Cover rename, remove, configure, interview, options, set_value, get_value."""

    def test_rename_sends_correct_payload(self):
        fc = FakeClient()
        devices_core.rename(fc, from_="old_name", to="new_name")
        call = fc.requests[0]
        assert call["path"] == "device/rename"
        assert call["payload"]["from"] == "old_name"
        assert call["payload"]["to"] == "new_name"
        assert call["payload"]["homeassistant_rename"] is True

    def test_rename_homeassistant_rename_false(self):
        fc = FakeClient()
        devices_core.rename(fc, from_="old", to="new", homeassistant_rename=False)
        assert fc.requests[0]["payload"]["homeassistant_rename"] is False

    def test_remove_with_force_and_block(self):
        fc = FakeClient()
        devices_core.remove(fc, "0x1234", force=True, block=True)
        call = fc.requests[0]
        assert call["path"] == "device/remove"
        assert call["payload"]["id"] == "0x1234"
        assert call["payload"]["force"] is True
        assert call["payload"]["block"] is True

    def test_remove_defaults(self):
        fc = FakeClient()
        devices_core.remove(fc, "0x1234")
        call = fc.requests[0]
        assert call["payload"]["force"] is False
        assert call["payload"]["block"] is False

    def test_configure_sends_id(self):
        fc = FakeClient()
        devices_core.configure(fc, "0x1234")
        call = fc.requests[0]
        assert call["path"] == "device/configure"
        assert call["payload"]["id"] == "0x1234"

    def test_interview_sends_id(self):
        fc = FakeClient()
        devices_core.interview(fc, "0x1234")
        call = fc.requests[0]
        assert call["path"] == "device/interview"
        assert call["payload"]["id"] == "0x1234"

    def test_options_sends_options_payload(self):
        fc = FakeClient()
        devices_core.options(fc, "0x1234", {"debounce": 100})
        call = fc.requests[0]
        assert call["path"] == "device/options"
        assert call["payload"]["id"] == "0x1234"
        assert call["payload"]["options"] == {"debounce": 100}

    def test_set_value_publishes_to_set_topic(self):
        fc = FakeClient()
        devices_core.set_value(fc, "lamp", {"state": "ON"})
        topic, payload = fc.published[0]
        assert topic == "zigbee2mqtt/lamp/set"
        assert payload == {"state": "ON"}

    def test_get_value_publishes_to_get_topic(self):
        fc = FakeClient()
        devices_core.get_value(fc, "lamp", ["state", "brightness"])
        topic, payload = fc.published[0]
        assert topic == "zigbee2mqtt/lamp/get"
        assert payload == {"state": "", "brightness": ""}


class TestDevicesReadState:
    """Cover read_state edge cases."""

    def test_read_state_empty_friendly_name_raises(self):
        fc = FakeClient()
        with pytest.raises(ValueError, match="friendly_name is required"):
            devices_core.read_state(fc, "")

    def test_read_state_no_retained_returns_empty(self):
        fc = FakeClient()
        result = devices_core.read_state(fc, "lamp")
        assert result == {}

    def test_read_state_malformed_json_returns_raw(self):
        fc = FakeClient()
        fc.set_retained("zigbee2mqtt/lamp", "not-json")
        result = devices_core.read_state(fc, "lamp")
        assert result == {"raw": "not-json"}

    def test_read_state_valid_json(self):
        fc = FakeClient()
        fc.set_retained("zigbee2mqtt/lamp", '{"state": "ON", "brightness": 100}')
        result = devices_core.read_state(fc, "lamp")
        assert result["state"] == "ON"
        assert result["brightness"] == 100


class TestDevicesFindStale:
    """Cover find_stale edge cases: filtering, timezone handling, malformed last_seen."""

    SAMPLE_DEVICES = [
        {
            "friendly_name": "coordinator",
            "ieee_address": "0x0000",
            "type": "Coordinator",
            "last_seen": "2025-01-01T00:00:00Z",
        },
        {
            "friendly_name": "router1",
            "ieee_address": "0x1111",
            "type": "Router",
            "last_seen": "2025-01-01T00:00:00Z",
        },
        {
            "friendly_name": "enddevice1",
            "ieee_address": "0x2222",
            "type": "EndDevice",
            "last_seen": "2025-01-01T00:00:00Z",
        },
        {
            "friendly_name": "greenpower1",
            "ieee_address": "0x3333",
            "type": "GreenPower",
            "last_seen": "2025-01-01T00:00:00Z",
        },
        {
            "friendly_name": "unknown_type",
            "ieee_address": "0x4444",
            "type": "Unknown",
            "last_seen": "2025-01-01T00:00:00Z",
        },
    ]

    def test_find_stale_skips_coordinator(self):
        fc = FakeClient()
        fc.set_retained("zigbee2mqtt/bridge/devices", json.dumps(self.SAMPLE_DEVICES))
        result = devices_core.find_stale(fc, threshold_minutes=-999999)
        friendly_names = [r["friendly_name"] for r in result]
        assert "coordinator" not in friendly_names

    def test_find_stale_excludes_routers_when_not_included(self):
        fc = FakeClient()
        fc.set_retained("zigbee2mqtt/bridge/devices", json.dumps(self.SAMPLE_DEVICES))
        result = devices_core.find_stale(fc, threshold_minutes=-999999, include_routers=False)
        friendly_names = [r["friendly_name"] for r in result]
        assert "router1" not in friendly_names

    def test_find_stale_includes_routers_when_requested(self):
        fc = FakeClient()
        fc.set_retained("zigbee2mqtt/bridge/devices", json.dumps(self.SAMPLE_DEVICES))
        result = devices_core.find_stale(fc, threshold_minutes=-999999, include_routers=True)
        friendly_names = [r["friendly_name"] for r in result]
        assert "router1" in friendly_names

    def test_find_stale_excludes_end_devices_when_not_included(self):
        fc = FakeClient()
        fc.set_retained("zigbee2mqtt/bridge/devices", json.dumps(self.SAMPLE_DEVICES))
        result = devices_core.find_stale(fc, threshold_minutes=-999999, include_end_devices=False)
        friendly_names = [r["friendly_name"] for r in result]
        assert "enddevice1" not in friendly_names
        assert "greenpower1" not in friendly_names

    def test_find_stale_includes_end_devices_when_requested(self):
        fc = FakeClient()
        fc.set_retained("zigbee2mqtt/bridge/devices", json.dumps(self.SAMPLE_DEVICES))
        result = devices_core.find_stale(fc, threshold_minutes=-999999, include_end_devices=True)
        friendly_names = [r["friendly_name"] for r in result]
        assert "enddevice1" in friendly_names
        assert "greenpower1" in friendly_names

    def test_find_stale_device_without_last_seen_is_skipped(self):
        """A device with no last_seen should have mins=None and be skipped."""
        fc = FakeClient()
        devices = [{"friendly_name": "no_seen", "ieee_address": "0x5555", "type": "Router"}]
        fc.set_retained("zigbee2mqtt/bridge/devices", json.dumps(devices))
        result = devices_core.find_stale(fc, threshold_minutes=-999999, include_routers=True)
        friendly_names = [r["friendly_name"] for r in result]
        assert "no_seen" not in friendly_names

    def test_find_stale_malformed_last_seen_treated_as_none(self):
        """A device with a malformed last_seen should be skipped (mins=None)."""
        fc = FakeClient()
        devices = [
            {
                "friendly_name": "bad_time",
                "ieee_address": "0x6666",
                "type": "Router",
                "last_seen": "not-a-date",
            }
        ]
        fc.set_retained("zigbee2mqtt/bridge/devices", json.dumps(devices))
        result = devices_core.find_stale(fc, threshold_minutes=-999999, include_routers=True)
        friendly_names = [r["friendly_name"] for r in result]
        assert "bad_time" not in friendly_names

    def test_find_stale_naive_last_seen_treated_as_utc(self):
        """A last_seen without timezone info should be treated as UTC."""
        fc = FakeClient()
        # Use a very old date so it's definitely stale
        devices = [
            {
                "friendly_name": "naive_time",
                "ieee_address": "0x7777",
                "type": "Router",
                "last_seen": "2020-01-01T00:00:00",  # no timezone suffix
            }
        ]
        fc.set_retained("zigbee2mqtt/bridge/devices", json.dumps(devices))
        result = devices_core.find_stale(fc, threshold_minutes=0, include_routers=True)
        friendly_names = [r["friendly_name"] for r in result]
        assert "naive_time" in friendly_names

    def test_find_stale_sorts_oldest_first(self):
        """Results should be sorted by minutes_since_seen descending."""
        fc = FakeClient()
        devices = [
            {
                "friendly_name": "newer",
                "ieee_address": "0x8888",
                "type": "Router",
                "last_seen": "2025-01-01T00:00:00Z",
            },
            {
                "friendly_name": "older",
                "ieee_address": "0x9999",
                "type": "Router",
                "last_seen": "2020-01-01T00:00:00Z",
            },
        ]
        fc.set_retained("zigbee2mqtt/bridge/devices", json.dumps(devices))
        result = devices_core.find_stale(fc, threshold_minutes=0, include_routers=True)
        # older device should come first (higher minutes_since_seen)
        assert result[0]["friendly_name"] == "older"
        assert result[1]["friendly_name"] == "newer"
        assert result[0]["minutes_since_seen"] > result[1]["minutes_since_seen"]


class TestDevicesGenerateExternalDefinition:
    """Cover generate_external_definition validation."""

    def test_empty_id_raises(self):
        fc = FakeClient()
        with pytest.raises(ValueError, match="id_ is required"):
            devices_core.generate_external_definition(fc, "")

    def test_valid_id_sends_request(self):
        fc = FakeClient()
        fc.set_request("device/generate_external_definition", {"status": "ok", "source": "code"})
        result = devices_core.generate_external_definition(fc, "0x1234")
        assert result["status"] == "ok"
        assert fc.requests[0]["path"] == "device/generate_external_definition"
        assert fc.requests[0]["payload"]["id"] == "0x1234"


class TestDevicesConfigureReporting:
    """Cover configure_reporting validation and payload construction."""

    def test_empty_id_raises(self):
        fc = FakeClient()
        with pytest.raises(ValueError, match="id_ is required"):
            devices_core.configure_reporting(
                fc,
                id_="",
                cluster="genOnOff",
                attribute="onOff",
                minimum_report_interval=1,
                maximum_report_interval=100,
            )

    def test_empty_cluster_raises(self):
        fc = FakeClient()
        with pytest.raises(ValueError, match="cluster and attribute are required"):
            devices_core.configure_reporting(
                fc,
                id_="0x1234",
                cluster="",
                attribute="onOff",
                minimum_report_interval=1,
                maximum_report_interval=100,
            )

    def test_empty_attribute_raises(self):
        fc = FakeClient()
        with pytest.raises(ValueError, match="cluster and attribute are required"):
            devices_core.configure_reporting(
                fc,
                id_="0x1234",
                cluster="genOnOff",
                attribute="",
                minimum_report_interval=1,
                maximum_report_interval=100,
            )

    def test_negative_interval_raises(self):
        fc = FakeClient()
        with pytest.raises(ValueError, match="intervals must be non-negative"):
            devices_core.configure_reporting(
                fc,
                id_="0x1234",
                cluster="genOnOff",
                attribute="onOff",
                minimum_report_interval=-1,
                maximum_report_interval=100,
            )

    def test_basic_payload(self):
        fc = FakeClient()
        devices_core.configure_reporting(
            fc,
            id_="0x1234",
            cluster="genOnOff",
            attribute="onOff",
            minimum_report_interval=1,
            maximum_report_interval=100,
        )
        call = fc.requests[0]
        assert call["path"] == "device/configure_reporting"
        assert call["payload"]["id"] == "0x1234"
        assert call["payload"]["cluster"] == "genOnOff"
        assert call["payload"]["attribute"] == "onOff"
        assert call["payload"]["minimum_report_interval"] == 1
        assert call["payload"]["maximum_report_interval"] == 100
        assert "reportable_change" not in call["payload"]
        assert "endpoint" not in call["payload"]

    def test_payload_with_reportable_change(self):
        fc = FakeClient()
        devices_core.configure_reporting(
            fc,
            id_="0x1234",
            cluster="msTemperatureMeasurement",
            attribute="measuredValue",
            minimum_report_interval=10,
            maximum_report_interval=300,
            reportable_change=0.5,
        )
        assert fc.requests[0]["payload"]["reportable_change"] == 0.5

    def test_payload_with_endpoint(self):
        fc = FakeClient()
        devices_core.configure_reporting(
            fc,
            id_="0x1234",
            cluster="genOnOff",
            attribute="onOff",
            minimum_report_interval=1,
            maximum_report_interval=100,
            endpoint=2,
        )
        assert fc.requests[0]["payload"]["endpoint"] == 2


# ════════════════════════════════════════════════════════════════════════
# ota — all three functions are uncovered
# ════════════════════════════════════════════════════════════════════════


class TestOta:
    """Cover ota.check, ota.update, ota.schedule."""

    def test_check_sends_correct_request(self):
        fc = FakeClient()
        fc.set_request("device/ota_update/check", {"status": "ok", "data": {"available": False}})
        result = ota_core.check(fc, "0x1234")
        assert result["status"] == "ok"
        call = fc.requests[0]
        assert call["path"] == "device/ota_update/check"
        assert call["payload"]["id"] == "0x1234"

    def test_update_sends_correct_request(self):
        fc = FakeClient()
        fc.set_request("device/ota_update/update", {"status": "ok"})
        result = ota_core.update(fc, "0x1234")
        assert result["status"] == "ok"
        call = fc.requests[0]
        assert call["path"] == "device/ota_update/update"
        assert call["payload"]["id"] == "0x1234"

    def test_schedule_sends_correct_request(self):
        fc = FakeClient()
        fc.set_request("device/ota_update/schedule", {"status": "ok"})
        result = ota_core.schedule(fc, "0x1234")
        assert result["status"] == "ok"
        call = fc.requests[0]
        assert call["path"] == "device/ota_update/schedule"
        assert call["payload"]["id"] == "0x1234"

    def test_check_custom_timeout(self):
        fc = FakeClient()
        ota_core.check(fc, "0x1234", timeout=5.0)
        assert fc.requests[0]["timeout"] == 5.0

    def test_update_custom_timeout(self):
        fc = FakeClient()
        ota_core.update(fc, "0x1234", timeout=120.0)
        assert fc.requests[0]["timeout"] == 120.0

    def test_schedule_custom_timeout(self):
        fc = FakeClient()
        ota_core.schedule(fc, "0x1234", timeout=3.0)
        assert fc.requests[0]["timeout"] == 3.0

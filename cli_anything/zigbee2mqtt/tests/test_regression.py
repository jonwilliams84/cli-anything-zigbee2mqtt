"""Regression tests for the subscriber-leak fix in ``collect_retained``.

``collect_retained`` registers a one-shot subscriber via ``self.subscribe``
and must remove it from ``self._subscribers`` once ``event.wait`` returns —
whether the message arrived (success) or the wait timed out.  Before the fix
the callback was left in ``self._subscribers`` forever, causing duplicate
dispatch on every subsequent message and an unbounded memory leak.
"""

from __future__ import annotations

import json
import threading

import pytest

from cli_anything.zigbee2mqtt.core import mqtt_client as mc


# ── fake paho transport (reused shape from test_core) ────────────────────────

class FakeMqttClient:
    def __init__(self, client_id):
        self.client_id = client_id
        self.on_message = None
        self.subscriptions: list[str] = []
        self.published: list[tuple[str, str, int, bool]] = []

    def username_pw_set(self, u, p=None):
        pass

    def connect(self, host, port, keepalive=30):
        pass

    def disconnect(self):
        pass

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def subscribe(self, topic, qos=0):
        self.subscriptions.append(topic)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))

        class Info:
            rc = 0
            def wait_for_publish(self, timeout=None):
                return None
        return Info()


@pytest.fixture
def fake_paho(monkeypatch):
    real_mqtt = mc.mqtt

    class FakeMqttModule:
        Client = FakeMqttClient

        @staticmethod
        def topic_matches_sub(filt, topic):
            if filt == topic:
                return True
            if filt.endswith("/#") and topic.startswith(filt[:-1]):
                return True
            return False

    monkeypatch.setattr(mc, "mqtt", FakeMqttModule)
    yield
    monkeypatch.setattr(mc, "mqtt", real_mqtt)


# ── tests ───────────────────────────────────────────────────────────────────

class TestCollectRetainedSubscriberCleanup:
    def test_subscriber_removed_after_timeout(self, fake_paho):
        """On timeout the one-shot subscriber must be removed from _subscribers."""
        client = mc.BridgeClient("fake-host", base_topic="zigbee2mqtt")
        client._connected = True  # bypass real connect

        # No message will arrive → event.wait times out immediately.
        result = client.collect_retained("zigbee2mqtt/bridge/info", timeout=0.01)

        assert result is None
        assert client._subscribers == [], (
            "subscriber leaked: collect_retained must remove its one-shot "
            "callback from _subscribers after the wait times out"
        )

    def test_subscriber_removed_after_message(self, fake_paho):
        """When the message arrives, the subscriber must still be cleaned up."""
        client = mc.BridgeClient("fake-host", base_topic="zigbee2mqtt")
        client._connected = True

        topic = "zigbee2mqtt/bridge/info"
        payload = json.dumps({"version": "1.0"})

        # Drive the on_message callback from a background thread so that
        # collect_retained's event.wait can return; then assert cleanup.
        def deliver():
            # small delay so collect_retained is already waiting
            threading.Event().wait(0.02)

            class FakeMsg:
                def __init__(self, t, p):
                    self.topic = t
                    self.payload = p.encode()
            client._on_message(None, None, FakeMsg(topic, payload))

        t = threading.Thread(target=deliver)
        t.start()
        result = client.collect_retained(topic, timeout=1.0)
        t.join()

        assert result == payload
        assert client._subscribers == [], (
            "subscriber leaked: collect_retained must remove its one-shot "
            "callback from _subscribers after the message arrives"
        )

    def test_no_duplicate_dispatch_after_collect_retained(self, fake_paho):
        """A leftover subscriber would re-fire on later messages — verify it doesn't."""
        client = mc.BridgeClient("fake-host", base_topic="zigbee2mqtt")
        client._connected = True

        topic = "zigbee2mqtt/bridge/info"
        client.collect_retained(topic, timeout=0.01)

        # If the subscriber leaked, a subsequent message would try to invoke
        # the (closed-over) callback.  Simulate a later message and make sure
        # no stale callback is present to receive it.
        assert client._subscribers == []

        class FakeMsg:
            def __init__(self, t, p):
                self.topic = t
                self.payload = p.encode()

        # Should be a no-op — no subscribers registered.
        client._on_message(None, None, FakeMsg(topic, "later"))
        assert client._subscribers == []

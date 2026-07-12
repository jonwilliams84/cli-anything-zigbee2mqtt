"""Regression tests for confirmed findings in mqtt_client.py."""

from __future__ import annotations

import json
import pytest

from cli_anything.zigbee2mqtt.core import mqtt_client as mc


class FakeMqttClient:
    def __init__(self, client_id):
        self.client_id = client_id
        self.on_message = None
        self.subscriptions = []
        self.published = []

    def username_pw_set(self, u, p=None): pass
    def connect(self, host, port, keepalive=30): pass
    def disconnect(self): pass
    def loop_start(self): pass
    def loop_stop(self): pass
    def subscribe(self, topic, qos=0): self.subscriptions.append(topic)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))
        if "/bridge/request/" in topic:
            try:
                data = json.loads(payload)
                txn = data.get("transaction")
            except Exception:
                txn = None
            resp_topic = topic.replace("/request/", "/response/")
            resp = {"status": "ok", "data": {"echo": "x"}, "transaction": txn}

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
            return all(f == "+" or f == t for f, t in zip(fparts, tparts))
        return False


@pytest.fixture
def fake_paho(monkeypatch):
    monkeypatch.setattr(mc, "mqtt", FakeMqttModule)


class BadBytes(bytes):
    """bytes subclass whose decode raises a non-UnicodeDecodeError."""
    def decode(self, encoding="utf-8", errors="strict"):
        raise RuntimeError("unexpected decode failure")


class TestMqttClientRegression:
    def test_subscriber_exception_propagates(self, fake_paho):
        """Finding 2: callback exceptions must not be swallowed."""
        client = mc.BridgeClient("localhost", 1883)
        client.connect()

        class Boom(Exception):
            pass

        def bad_cb(topic, payload):
            raise Boom("callback failure")

        client.subscribe("zigbee2mqtt/#", bad_cb)

        class Msg:
            topic = "zigbee2mqtt/sensor"
            payload = b"hello"

        with pytest.raises(Boom):
            client._on_message(None, None, Msg())

    def test_non_unicode_decode_error_propagates(self, fake_paho):
        """Finding 1: only UnicodeDecodeError should be caught during payload decode."""
        client = mc.BridgeClient("localhost", 1883)
        client.connect()

        class Msg:
            topic = "zigbee2mqtt/bridge/response/test"
            payload = BadBytes(b"ok")

        with pytest.raises(RuntimeError):
            client._on_message(None, None, Msg())

    def test_credentials_not_stored_on_instance(self, fake_paho):
        """Finding 3: username/password must not be kept as instance attributes."""
        client = mc.BridgeClient("localhost", 1883, username="u", password="p")
        assert not hasattr(client, "_username")
        assert not hasattr(client, "_password")

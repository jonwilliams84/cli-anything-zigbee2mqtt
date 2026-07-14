import pytest
from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

class FakeMqttClient:
    def __init__(self, client_id):
        self.client_id = client_id
        self.on_message = None
        self.subscriptions = []
    def username_pw_set(self, u, p=None): pass
    def connect(self, host, port, keepalive=30): pass
    def disconnect(self): pass
    def loop_start(self): pass
    def loop_stop(self): pass
    def subscribe(self, topic, qos=0): self.subscriptions.append(topic)
    def publish(self, topic, payload, qos=0, retain=False):
        class Info:
            rc = 0
            def wait_for_publish(self, timeout=None): return None
        return Info()

class Msg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload

def test_collect_retained_removes_callback(monkeypatch):
    import paho.mqtt.client as mqtt
    monkeypatch.setattr(mqtt, "Client", FakeMqttClient)
    monkeypatch.setattr(mqtt, "topic_matches_sub", lambda f, t: f == t)

    client = BridgeClient("localhost")
    client._connected = True
    topic = "test/topic"
    
    # Call collect_retained, it will timeout but add a callback
    client.collect_retained(topic, timeout=0.1)
    assert len(client._subscribers) == 1
    
    # Simulate receiving a message
    msg = Msg(topic, b"hello")
    client._on_message(None, None, msg)
    
    # The callback should have removed itself
    assert len(client._subscribers) == 0

def test_collect_retained_multiple_calls(monkeypatch):
    import paho.mqtt.client as mqtt
    monkeypatch.setattr(mqtt, "Client", FakeMqttClient)
    monkeypatch.setattr(mqtt, "topic_matches_sub", lambda f, t: f == t)

    client = BridgeClient("localhost")
    client._connected = True
    topic = "test/topic"
    
    for _ in range(5):
        client.collect_retained(topic, timeout=0.1)
    
    assert len(client._subscribers) == 5
    
    msg = Msg(topic, b"hello")
    client._on_message(None, None, msg)
    
    assert len(client._subscribers) == 0

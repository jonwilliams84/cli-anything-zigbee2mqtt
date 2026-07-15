import pytest
from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

def test_collect_retained_callback_leak(monkeypatch):
    from cli_anything.zigbee2mqtt.tests.test_core import FakeMqttClient, fake_paho
    
    import paho.mqtt.client as mqtt
    monkeypatch.setattr(mqtt, "Client", FakeMqttClient)
    monkeypatch.setattr(mqtt, "topic_matches_sub", lambda f, t: f == t or (f.endswith("/#") and t.startswith(f[:-1])))

    with BridgeClient(host="localhost") as client:
        topic = "test/topic"
        
        for _ in range(5):
            client.collect_retained(topic, timeout=0.1)
            
        # After 5 calls, the one-shot callbacks should have been removed.
        assert len(client._subscribers) == 0

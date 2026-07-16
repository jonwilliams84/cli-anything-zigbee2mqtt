import pytest
import json
from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

def test_bridge_client_subscriber_exception_handling(monkeypatch):
    """
    Verify that an exception in a subscriber callback does not crash
    the MQTT client's message loop.
    """
    # We need to mock paho.mqtt.client.Client because BridgeClient.__init__ creates one
    import paho.mqtt.client as mqtt
    
    class FakeMqttClient:
        def __init__(self, *args, **kwargs):
            self.on_message = None
            self.subscriptions = []
        def connect(self, *args, **kwargs): pass
        def disconnect(self): pass
        def loop_start(self): pass
        def loop_stop(self): pass
        def subscribe(self, topic, qos=0):
            self.subscriptions.append(topic)
        def publish(self, *args, **kwargs):
            class Info:
                rc = 0
                def wait_for_publish(self, timeout=None): return None
            return Info()

    monkeypatch.setattr(mqtt, "Client", FakeMqttClient)
    
    c = BridgeClient("fake-host", base_topic="z2m")
    c.connect()
    
    # This callback will raise an exception
    def crashing_callback(topic, payload):
        raise RuntimeError("I crashed!")
    
    # This callback should still be called
    received = []
    def healthy_callback(topic, payload):
        received.append(payload)
    
    c.subscribe("z2m/test/crash", crashing_callback)
    c.subscribe("z2m/test/healthy", healthy_callback)
    
    # Simulate messages arriving via the fake client
    class FakeMsg:
        def __init__(self, t, p):
            self.topic = t
            self.payload = p.encode()
            
    # Trigger crashing callback
    c._on_message(None, None, FakeMsg("z2m/test/crash", "payload1"))
    # Trigger healthy callback
    c._on_message(None, None, FakeMsg("z2m/test/healthy", "payload2"))
    
    assert "payload2" in received
    c.disconnect()

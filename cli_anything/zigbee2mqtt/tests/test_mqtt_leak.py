import pytest
from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

def test_collect_retained_leaks_subscribers(monkeypatch):
    import paho.mqtt.client as mqtt
    class FakeMqttClient:
        def __init__(self, *args, **kwargs): self.on_message = None
        def connect(self, *args, **kwargs): pass
        def disconnect(self): pass
        def loop_start(self): pass
        def loop_stop(self): pass
        def subscribe(self, topic, qos=0): pass
        def publish(self, *args, **kwargs):
            class Info:
                rc = 0
                def wait_for_publish(self, timeout=None): return None
            return Info()
    monkeypatch.setattr(mqtt, "Client", FakeMqttClient)
    
    c = BridgeClient("fake-host")
    c.connect()
    
    initial_count = len(c._subscribers)
    for i in range(10):
        c.collect_retained(f"topic/{i}")
    
    final_count = len(c._subscribers)
    print(f"Initial: {initial_count}, Final: {final_count}")
    # The fix should ensure that subscribers are cleaned up after collect_retained
    assert final_count == initial_count, "Subscribers leaked in collect_retained"
    c.disconnect()

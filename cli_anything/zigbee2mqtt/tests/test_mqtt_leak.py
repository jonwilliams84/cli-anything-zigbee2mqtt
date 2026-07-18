"""Regression test: collect_retained must unsubscribe after collecting."""

import threading

from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient
from cli_anything.zigbee2mqtt.tests.test_core import fake_paho


def test_collect_retained_unsubscribes(fake_paho):
    """collect_retained must call client.unsubscribe(topic) to avoid a leak."""
    c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
    c.connect()
    topic = "zigbee2mqtt/test/retained"

    def simulate_message():
        import time
        time.sleep(0.1)

        class FakeMsg:
            def __init__(self, t, p):
                self.topic = t
                self.payload = p.encode()

        c.client.on_message(c.client, None, FakeMsg(topic, "retained-value"))

    threading.Thread(target=simulate_message).start()
    val = c.collect_retained(topic)
    assert val == "retained-value"
    assert topic in c.client.unsubscriptions
    assert topic not in c.client.subscriptions
    assert not any(cb is not None for _, cb in c._subscribers)

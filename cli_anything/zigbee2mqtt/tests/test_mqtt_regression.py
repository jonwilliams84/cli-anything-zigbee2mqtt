import pytest
from unittest.mock import MagicMock, patch
from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient, MqttError

def test_subscriber_exception_does_not_crash_client():
    """Verify that a callback raising an exception is caught and does not crash the client."""
    # Mock paho-mqtt Client
    with patch('paho.mqtt.client.Client') as mock_client_cls:
        mock_mqtt_inst = mock_client_cls.return_value
        
        client = BridgeClient(host="localhost")
        client.connect()
        
        # Create a callback that fails
        def failing_callback(topic, payload):
            raise RuntimeError("Callback failed!")
            
        client.subscribe("test/topic", failing_callback)
        
        # Simulate an incoming message
        mock_msg = MagicMock()
        mock_msg.topic = "test/topic"
        mock_msg.payload = b"hello"
        
        # This should not raise an exception because of the try-except pass in _on_message
        client._on_message(None, None, mock_msg)

def test_subscriber_continues_after_failure():
    """Verify that other subscribers still receive messages if one fails."""
    with patch('paho.mqtt.client.Client') as mock_client_cls:
        mock_mqtt_inst = mock_client_cls.return_value
        
        client = BridgeClient(host="localhost")
        client.connect()
        
        received = []
        def failing_callback(topic, payload):
            raise RuntimeError("Fail")
        def success_callback(topic, payload):
            received.append(payload)
            
        client.subscribe("test/topic", failing_callback)
        client.subscribe("test/topic", success_callback)
        
        mock_msg = MagicMock()
        mock_msg.topic = "test/topic"
        mock_msg.payload = b"hello"
        
        client._on_message(None, None, mock_msg)
        
        assert "hello" in received

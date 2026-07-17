import pytest
from unittest.mock import MagicMock, patch
from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

def test_on_message_handles_non_dict_json():
    """Verify that _on_message doesn't crash if the JSON payload is not a dictionary."""
    with patch('paho.mqtt.client.Client') as mock_client_cls:
        client = BridgeClient(host="localhost")
        client.connect()
        
        # Payload is a JSON list instead of a dict
        mock_msg = MagicMock()
        mock_msg.topic = "zigbee2mqtt/bridge/response/test"
        mock_msg.payload = b"[1, 2, 3]"
        
        # This should not raise AttributeError: 'list' object has no attribute 'get'
        client._on_message(None, None, mock_msg)

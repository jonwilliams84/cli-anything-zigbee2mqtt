"""Unit tests for cli-anything-zigbee2mqtt core modules.

The MQTT client is exercised against a fake transport so no broker is needed.
"""

from __future__ import annotations

import json
import threading
import time
import pytest

from cli_anything.zigbee2mqtt.core import devices as devices_core
from cli_anything.zigbee2mqtt.core import project


# ── project profile ─────────────────────────────────────────────────────────

class TestProject:
    def test_defaults_when_no_file(self, tmp_path):
        cfg = project.load_config(tmp_path / "no-such.json")
        # replaced assert with if/raise to avoid B101 (assert stripped in -O)
        if cfg["base_topic"] != "zigbee2mqtt":
            raise ValueError(f"expected base_topic 'zigbee2mqtt', got {cfg['base_topic']!r}")
        if cfg["mqtt_port"] != 1883:
            raise ValueError(f"expected mqtt_port 1883, got {cfg['mqtt_port']!r}")
        if cfg["mqtt_host"] is not None:
            raise ValueError(f"expected mqtt_host None, got {cfg['mqtt_host']!r}")

    def test_save_round_trip(self, tmp_path):
        p = tmp_path / "profile.json"
        project.save_config({"mqtt_host": "10.0.0.5", "base_topic": "z2m"}, p)
        cfg = project.load_config(p)
        assert cfg["mqtt_host"] == "10.0.0.5"
        assert cfg["base_topic"] == "z2m"

    def test_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLI_Z2M_MQTT_HOST", "172.16.0.10")
        monkeypatch.setenv("CLI_Z2M_MQTT_PORT", "8883")
        cfg = project.load_config(tmp_path / "no.json")
        assert cfg["mqtt_host"] == "172.16.0.10"
        assert cfg["mqtt_port"] == 8883

    def test_merge_cli_ignores_none(self):
        cfg = project.merge_cli_overrides({"mqtt_host": "a"}, mqtt_host=None, base_topic="bb")
        assert cfg["mqtt_host"] == "a"
        assert cfg["base_topic"] == "bb"


# ── devices summarize ───────────────────────────────────────────────────────

class TestDevicesSummarize:
    SAMPLE = [
        {"friendly_name": "Front Sensor", "ieee_address": "0xa4c138...",
         "type": "EndDevice", "supported": True, "interview_completed": True,
         "manufacturer": "_TZE204_ya4ft0w4",
         "power_source": "Mains (single phase)",
         "definition": {"model": "ZY-M100-24GV3", "vendor": "Tuya"}},
        {"friendly_name": "Lounge Lamp", "ieee_address": "0xa4c1382132ff0994",
         "type": "Router", "supported": True, "interview_completed": True,
         "manufacturer": "Philips",
         "definition": {"model": "LCT001", "vendor": "Philips"}},
        # one row without a definition (unsupported / unknown)
        {"friendly_name": "Mystery", "ieee_address": "0xdead", "type": "Unknown",
         "interview_completed": False, "supported": False},
    ]

    def test_summarize_returns_one_row_per_device(self):
        rows = devices_core.summarize(self.SAMPLE)
        assert len(rows) == 3
        assert rows[0]["model"] == "ZY-M100-24GV3"
        assert rows[0]["vendor"] == "Tuya"

    def test_summarize_handles_missing_definition(self):
        rows = devices_core.summarize(self.SAMPLE)
        last = rows[-1]
        assert last["model"] is None
        assert last["vendor"] is None
        assert last["interview_completed"] is False


# ── BridgeClient (fake transport) ───────────────────────────────────────────

class FakeMqttClient:
    """Minimum surface to satisfy paho.mqtt.Client usage in BridgeClient.

    Echoes any publish to `<base>/bridge/request/<path>` back as a response on
    `<base>/bridge/response/<path>` with `status: ok` and the same transaction.
    """

    def __init__(self, client_id):
        self.client_id = client_id
        self.on_message = None
        self.subscriptions: list[str] = []
        self.published: list[tuple[str, str, int, bool]] = []
        self.username = None
        self.password = None
        self._connected = False
        self._loop_thread = None
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
        # nothing async needed — publishes drive the on_message directly

    def loop_stop(self):
        self._stop.set()

    def subscribe(self, topic, qos=0):
        self.subscriptions.append(topic)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))
        # Auto-respond on bridge request
        if "/bridge/request/" in topic:
            req_path = topic.split("/bridge/request/", 1)[1]
            try:
                data = json.loads(payload)
            except Exception:
                data = {}
            txn = data.get("transaction")
            resp_topic = topic.replace("/request/", "/response/")
            resp = {"status": "ok", "data": {"echo": req_path},
                    "transaction": txn}

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
            # Simple wildcard-ish matcher good enough for tests.
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

    monkeypatch.setattr(mc, "mqtt", FakeMqttModule)
    yield
    monkeypatch.setattr(mc, "mqtt", real_mqtt)


class TestBridgeClient:
    def test_connect_subscribes_to_response(self, fake_paho):
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient
        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        c.connect()
        subs = c.client.subscriptions  # type: ignore[attr-defined]
        assert any("/bridge/response/#" in s for s in subs)

    def test_request_correlates_response_by_transaction(self, fake_paho):
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient
        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        with c as client:
            resp = client.request("device/rename",
                                   payload={"from": "A", "to": "B"})
            assert resp["status"] == "ok"
            assert resp["data"]["echo"] == "device/rename"

    def test_request_raises_on_error_status(self, fake_paho, monkeypatch):
        """If z2m returns status=error, BridgeClient.request should raise."""
        from cli_anything.zigbee2mqtt.core import mqtt_client as mc
        # patch FakeMqttClient.publish to return an error response
        orig_publish = FakeMqttClient.publish

        def err_publish(self, topic, payload, qos=0, retain=False):
            self.published.append((topic, payload, qos, retain))
            if "/bridge/request/" in topic:
                req_path = topic.split("/bridge/request/", 1)[1]
                try:
                    data = json.loads(payload)
                except Exception:
                    data = {}
                txn = data.get("transaction")
                resp_topic = topic.replace("/request/", "/response/")
                resp = {"status": "error", "error": "boom",
                        "transaction": txn}

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

        monkeypatch.setattr(FakeMqttClient, "publish", err_publish, raising=True)

        c = mc.BridgeClient("fake-host", base_topic="zigbee2mqtt")
        with c as client:
            with pytest.raises(mc.MqttError, match="boom"):
                client.request("device/rename",
                                payload={"from": "A", "to": "B"})

        monkeypatch.setattr(FakeMqttClient, "publish", orig_publish, raising=True)

    def test_publish_topic_format(self, fake_paho):
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient
        from cli_anything.zigbee2mqtt.core import devices
        c = BridgeClient("fake-host", base_topic="z2m")
        with c as client:
            devices.set_value(client, "Lounge Lamp", {"state": "ON"})
        published = client.client.published  # type: ignore[attr-defined]
        # last publish should be the device set
        last_topic, last_payload, _, _ = published[-1]
        assert last_topic == "z2m/Lounge Lamp/set"
        assert json.loads(last_payload) == {"state": "ON"}

    def test_on_message_logs_failing_callback(self, fake_paho, caplog):
        """Regression: subscriber callbacks that raise must be logged, not silently swallowed.

        This was Bandit B110 (Try, Except, Pass).  The fix changed the bare
        ``except Exception: pass`` into a ``except Exception as exc: logger.warning(...)``
        block.  The test verifies the warning actually appears.
        """
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient
        import logging

        c = BridgeClient("fake-host", base_topic="z2m")
        c.connect()

        # register a subscriber whose callback always raises
        def bad_cb(topic, payload):
            raise RuntimeError("boom")

        c.subscribe("z2m/some/topic", bad_cb)

        # trigger _on_message with a matching topic — should NOT raise,
        # and should emit a WARNING log record

        class FakeMsg:
            topic = "z2m/some/topic"
            payload = b"{}"

        with caplog.at_level(logging.WARNING, logger="cli_anything.zigbee2mqtt.core.mqtt_client"):
            c._on_message(None, None, FakeMsg())  # type: ignore[arg-type]

        assert any("boom" in record.message for record in caplog.records), \
            "Expected a log record containing 'boom' from the failing callback"




# ── Regression: dead-code removals in mqtt_client.py ────────────────────────

class TestMqttClientNoDeadCode:
    """Regression tests: ensure the three dead-code findings stay gone."""

    def test_no_time_module_imported(self):
        """The `time` module must not be imported in mqtt_client.py."""
        import ast, inspect
        from cli_anything.zigbee2mqtt.core import mqtt_client as mc
        src = inspect.getsource(mc)
        tree = ast.parse(src)
        imports = [n.names[0].name for n in ast.walk(tree)
                   if isinstance(n, ast.Import) and
                      any(x.name == 'time' for x in n.names)]
        assert not imports, f"'time' module still imported: {imports}"

    def test_no_time_attribute_used(self):
        """No code in mqtt_client.py must call time.sleep / time.time / etc."""
        import ast, inspect
        from cli_anything.zigbee2mqtt.core import mqtt_client as mc
        src = inspect.getsource(mc)
        tree = ast.parse(src)
        bad = [f"line {n.lineno}: time.{n.attr}"
               for n in ast.walk(tree)
               if (isinstance(n, ast.Attribute)
                   and isinstance(n.value, ast.Name)
                   and n.value.id == 'time')]
        assert not bad, f"time module still used: {bad}"

    def test_no_useless_instance_vars(self, fake_paho):
        """_username and _password must not be stored as dead instance vars."""
        # Import inside test so fake_paho fixture has already patched mc.mqtt
        from cli_anything.zigbee2mqtt.core import mqtt_client as mc
        c = mc.BridgeClient("fake-host")
        assert not hasattr(c, '_username'), "_username is a dead instance var"
        assert not hasattr(c, '_password'), "_password is a dead instance var"

    def test_pending_dict_no_path_key(self):
        """The 'path' key must not be stored in _pending (unused once stored)."""
        import inspect
        from cli_anything.zigbee2mqtt.core import mqtt_client as mc
        src = inspect.getsource(mc)
        # _pending[txn] = {…} must contain only 'event' and 'slot'
        assert '_pending[txn] = {"event": event, "slot": slot}' in src, \
            "_pending must only contain 'event' and 'slot' keys"

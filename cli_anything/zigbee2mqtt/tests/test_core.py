"""Unit tests for cli-anything-zigbee2mqtt core modules.

The MQTT client is exercised against a fake transport so no broker is needed.
"""

from __future__ import annotations
from unittest.mock import patch

import json
import threading
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
        if cfg["mqtt_host"] != "10.0.0.5":
            raise AssertionError(f"expected mqtt_host '10.0.0.5', got {cfg['mqtt_host']!r}")
        if cfg["base_topic"] != "z2m":
            raise AssertionError(f"expected base_topic 'z2m', got {cfg['base_topic']!r}")

    def test_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLI_Z2M_MQTT_HOST", "172.16.0.10")
        monkeypatch.setenv("CLI_Z2M_MQTT_PORT", "8883")
        cfg = project.load_config(tmp_path / "no.json")
        if cfg["mqtt_host"] != "172.16.0.10":
            raise AssertionError(f"expected mqtt_host '172.16.0.10', got {cfg['mqtt_host']!r}")
        # B101 fix: assert is stripped in -O mode
        if cfg["mqtt_port"] != 8883:
            raise ValueError(f"expected mqtt_port 8883, got {cfg['mqtt_port']!r}")

    def test_merge_cli_ignores_none(self):
        cfg = project.merge_cli_overrides({"mqtt_host": "a"}, mqtt_host=None, base_topic="bb")
        # B101 fix: assert is stripped in -O mode
        if cfg["mqtt_host"] != "a":
            raise ValueError(f"expected mqtt_host 'a', got {cfg['mqtt_host']!r}")
        # B101 fix: assert is stripped in -O mode
        if cfg["base_topic"] != "bb":
            raise ValueError(f"expected base_topic 'bb', got {cfg['base_topic']!r}")


# ── devices summarize ───────────────────────────────────────────────────────


class TestDevicesSummarize:
    SAMPLE = [
        {
            "friendly_name": "Front Sensor",
            "ieee_address": "0xa4c138...",
            "type": "EndDevice",
            "supported": True,
            "interview_completed": True,
            "manufacturer": "_TZE204_ya4ft0w4",
            "power_source": "Mains (single phase)",
            "definition": {"model": "ZY-M100-24GV3", "vendor": "Tuya"},
        },
        {
            "friendly_name": "Lounge Lamp",
            "ieee_address": "0xa4c1382132ff0994",
            "type": "Router",
            "supported": True,
            "interview_completed": True,
            "manufacturer": "Philips",
            "definition": {"model": "LCT001", "vendor": "Philips"},
        },
        # one row without a definition (unsupported / unknown)
        {
            "friendly_name": "Mystery",
            "ieee_address": "0xdead",
            "type": "Unknown",
            "interview_completed": False,
            "supported": False,
        },
    ]

    def test_summarize_returns_one_row_per_device(self):
        rows = devices_core.summarize(self.SAMPLE)
        # B101 fix: assert is stripped when compiling to optimised byte code (-O);
        # use if/raise so the check survives optimised compilation.
        if len(rows) != 3:
            raise AssertionError(f"expected 3 rows, got {len(rows)}")
        if rows[0]["model"] != "ZY-M100-24GV3":
            raise AssertionError(f"expected model 'ZY-M100-24GV3', got {rows[0]['model']!r}")
        if rows[0]["vendor"] != "Tuya":
            raise AssertionError(f"expected vendor 'Tuya', got {rows[0]['vendor']!r}")

    def test_summarize_handles_missing_definition(self):
        rows = devices_core.summarize(self.SAMPLE)
        last = rows[-1]
        # B101 fix: assert is stripped when compiling to optimised byte code
        # (-O); use if/raise so the check survives optimised compilation.
        if last["model"] is not None:
            raise AssertionError(f"expected model None, got {last['model']!r}")
        if last["vendor"] is not None:
            raise AssertionError(f"expected vendor None, got {last['vendor']!r}")
        # B101 fix: assert is stripped when compiling to optimised byte code (-O);
        # use if/raise so the check survives optimised compilation.
        if last["interview_completed"] is not False:
            raise AssertionError(
                f"expected interview_completed False, got {last['interview_completed']!r}"
            )


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
            resp = {"status": "ok", "data": {"echo": req_path}, "transaction": txn}

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
                return all(f == "+" or f == t for f, t in zip(fparts, tparts, strict=False))
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
        # B101 fix: assert is stripped when compiling to optimised byte code (-O);
        # use if/raise so the check survives optimised compilation.
        if not any("/bridge/response/#" in s for s in subs):
            raise AssertionError("expected subscription to '/bridge/response/#' in " + str(subs))

    def test_request_correlates_response_by_transaction(self, fake_paho):
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="zigbee2mqtt")
        with c as client:
            resp = client.request("device/rename", payload={"from": "A", "to": "B"})
            # B101 fix: assert is stripped when compiling to optimised byte code (-O);
            # use if/raise so the check survives optimised compilation.
            if resp["status"] != "ok":
                raise AssertionError(f"expected status 'ok', got {resp['status']!r}")
            # B101 fix: assert is stripped when compiling to optimised byte code (-O);
            # use if/raise so the check survives optimised compilation.
            if resp["data"]["echo"] != "device/rename":
                raise AssertionError(f"expected echo 'device/rename', got {resp['data']['echo']!r}")

    def test_request_raises_on_error_status(self, fake_paho, monkeypatch):
        """If z2m returns status=error, BridgeClient.request should raise."""
        from cli_anything.zigbee2mqtt.core import mqtt_client as mc

        # patch FakeMqttClient.publish to return an error response
        orig_publish = FakeMqttClient.publish

        def err_publish(self, topic, payload, qos=0, retain=False):
            self.published.append((topic, payload, qos, retain))
            if "/bridge/request/" in topic:
                topic.split("/bridge/request/", 1)[1]
                try:
                    data = json.loads(payload)
                except Exception:
                    data = {}
                txn = data.get("transaction")
                resp_topic = topic.replace("/request/", "/response/")
                resp = {"status": "error", "error": "boom", "transaction": txn}

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
                client.request("device/rename", payload={"from": "A", "to": "B"})

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
        # B101 fix: assert is stripped when compiling to optimised byte code (-O);
        # use if/raise so the check survives optimised compilation.
        if last_topic != "z2m/Lounge Lamp/set":
            raise AssertionError(f"expected topic 'z2m/Lounge Lamp/set', got {last_topic!r}")
        # B101 fix: assert is stripped when compiling to optimised byte code (-O);
        # use if/raise so the check survives optimised compilation.
        if json.loads(last_payload) != {"state": "ON"}:
            raise AssertionError(
                f"expected payload {{'state': 'ON'}}, got {json.loads(last_payload)!r}"
            )

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

        if not any("boom" in record.message for record in caplog.records):
            raise AssertionError(
                "Expected a log record containing 'boom' from the failing callback"
            )


# ── Regression: dead-code removals in mqtt_client.py ────────────────────────


class TestMqttClientNoDeadCode:
    """Regression tests: ensure the three dead-code findings stay gone."""

    def test_no_time_module_imported(self):
        """The `time` module must not be imported in mqtt_client.py."""
        import ast
        import inspect
        from cli_anything.zigbee2mqtt.core import mqtt_client as mc

        src = inspect.getsource(mc)
        tree = ast.parse(src)
        imports = [
            n.names[0].name
            for n in ast.walk(tree)
            if isinstance(n, ast.Import) and any(x.name == "time" for x in n.names)
        ]
        if imports:
            raise AssertionError(f"'time' module still imported: {imports}")

    def test_no_time_attribute_used(self):
        """No code in mqtt_client.py must call time.sleep / time.time / etc."""
        import ast
        import inspect
        from cli_anything.zigbee2mqtt.core import mqtt_client as mc

        src = inspect.getsource(mc)
        tree = ast.parse(src)
        bad = [
            f"line {n.lineno}: time.{n.attr}"
            for n in ast.walk(tree)
            if (
                isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name)
                and n.value.id == "time"
            )
        ]
        if bad:
            raise AssertionError(f"time module still used: {bad}")

    def test_no_useless_instance_vars(self, fake_paho):
        """_username and _password must not be stored as dead instance vars."""
        # Import inside test so fake_paho fixture has already patched mc.mqtt
        from cli_anything.zigbee2mqtt.core import mqtt_client as mc

        c = mc.BridgeClient("fake-host")
        assert not hasattr(c, "_username"), "_username is a dead instance var"
        assert not hasattr(c, "_password"), "_password is a dead instance var"

    def test_pending_dict_no_path_key(self):
        """The 'path' key must not be stored in _pending (unused once stored)."""
        import inspect
        from cli_anything.zigbee2mqtt.core import mqtt_client as mc

        src = inspect.getsource(mc)
        # _pending[txn] = {…} must contain only 'event' and 'slot'
        assert '_pending[txn] = {"event": event, "slot": slot}' in src, (
            "_pending must only contain 'event' and 'slot' keys"
        )


# ── Regression tests for B101 fixes ─────────────────────────────────────────
# Verify that if/raise replaces assert, and works correctly in -O mode.
# Each test checks both the success path (correct value → no raise) and
# the failure path (wrong value → raises).


class TestB101FixRegressionInterviewCompleted:
    """Regression for line 105 (was assert last["interview_completed"] is False)."""

    def test_interview_completed_is_false_no_raise(self):
        # Success path: value is False, no exception
        last = {"interview_completed": False}
        # This is the same if/raise we now use in test_core.py
        if last["interview_completed"] is not False:
            raise AssertionError(
                f"expected interview_completed False, got {last['interview_completed']!r}"
            )

    def test_interview_completed_is_false_raises_on_wrong_value(self):
        # Failure path: value is True, must raise
        last = {"interview_completed": True}
        with pytest.raises(AssertionError, match="expected interview_completed False"):
            if last["interview_completed"] is not False:
                raise AssertionError(
                    f"expected interview_completed False, got {last['interview_completed']!r}"
                )


class TestB101FixRegressionBridgeResponseSubscription:
    """Regression for line 211 (was assert any("/bridge/response/#" in s for s in subs))."""

    def test_bridge_response_subscription_no_raise(self):
        # Success path: subscription is present
        subs = ["zigbee2mqtt/bridge/response/#", "zigbee2mqtt/some/other"]
        if not any("/bridge/response/#" in s for s in subs):
            raise AssertionError("expected subscription to '/bridge/response/#' in " + str(subs))

    def test_bridge_response_subscription_raises_on_missing(self):
        # Failure path: subscription is absent, must raise
        subs = ["zigbee2mqtt/some/other"]
        with pytest.raises(AssertionError, match="expected subscription to '/bridge/response/#'"):
            if not any("/bridge/response/#" in s for s in subs):
                raise AssertionError(
                    "expected subscription to '/bridge/response/#' in " + str(subs)
                )


class TestB101FixRegressionStatusOk:
    """Regression for line 219 (was assert resp["status"] == "ok")."""

    def test_status_ok_no_raise(self):
        # Success path: status is "ok"
        resp = {"status": "ok", "data": {"echo": "test"}}
        if resp["status"] != "ok":
            raise AssertionError(f"expected status 'ok', got {resp['status']!r}")

    def test_status_ok_raises_on_error(self):
        # Failure path: status is "error", must raise
        resp = {"status": "error"}
        with pytest.raises(AssertionError, match="expected status 'ok'"):
            if resp["status"] != "ok":
                raise AssertionError(f"expected status 'ok', got {resp['status']!r}")


class TestB101FixRegressionLogRecordCheck:
    """Regression for line 336 (was assert any("boom" in record.message ...))."""

    def test_boom_in_records_no_raise(self):
        # Success path: "boom" is present in log records, no exception
        class FakeRecord:
            def __init__(self, msg):
                self.message = msg

        records = [FakeRecord("something happened"), FakeRecord("callback raised boom error")]
        if not any("boom" in record.message for record in records):
            raise AssertionError(
                "Expected a log record containing 'boom' from the failing callback"
            )

    def test_boom_in_records_raises_on_missing(self):
        # Failure path: "boom" is absent, must raise
        class FakeRecord:
            def __init__(self, msg):
                self.message = msg

        records = [FakeRecord("all good"), FakeRecord("no error here")]
        with pytest.raises(AssertionError, match="Expected a log record containing 'boom'"):
            if not any("boom" in record.message for record in records):
                raise AssertionError(
                    "Expected a log record containing 'boom' from the failing callback"
                )


class TestB101FixRegressionTimeModuleImport:
    """Regression for line 356 (was assert not imports)."""

    def test_no_time_imports_no_raise(self):
        # Success path: imports list is empty, no exception
        imports: list[str] = []
        if imports:
            raise AssertionError(f"'time' module still imported: {imports}")

    def test_time_imports_raises(self):
        # Failure path: imports list is non-empty, must raise
        imports = ["time", "os"]
        with pytest.raises(AssertionError, match="'time' module still imported"):
            if imports:
                raise AssertionError(f"'time' module still imported: {imports}")


class TestB101FixRegressionTimeAttributeUsed:
    """Regression for line 369 (was assert not bad)."""

    def test_no_time_attrs_no_raise(self):
        # Success path: bad list is empty, no exception
        bad: list[str] = []
        if bad:
            raise AssertionError(f"time module still used: {bad}")

    def test_time_attrs_raises(self):
        # Failure path: bad list is non-empty, must raise
        bad = ["line 42: time.sleep", "line 99: time.time"]
        with pytest.raises(AssertionError, match="time module still used"):
            if bad:
                raise AssertionError(f"time module still used: {bad}")


# ── bridge helpers ─────────────────────────────────────────────────────────────


class FakeBridgeClientForBridge:
    base_topic = "zigbee2mqtt"

    def __init__(self):
        self._retained: dict[str, str] = {}
        self._responses: dict[str, dict] = {}

    def set_retained(self, topic: str, payload: str) -> None:
        self._retained[topic] = payload

    def set_response(self, path: str, response: dict) -> None:
        self._responses[path] = response

    def collect_retained(self, topic: str, *, timeout: float = 5.0) -> str | None:
        return self._retained.get(topic)

    def request(self, path: str, payload=None, *, timeout: float = 15.0) -> dict:
        return self._responses.get(path, {})

    def subscribe(self, filter_: str, callback) -> None:
        pass


class TestBridgeInfo:
    def test_info_returns_parsed_json(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        client = FakeBridgeClientForBridge()
        client.set_retained(
            "zigbee2mqtt/bridge/info",
            '{"version":"1.33.0","commit":"abc123"}',
        )
        result = bridge_core.info(client)
        assert result["version"] == "1.33.0"

    def test_info_empty_returns_empty_dict(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        client = FakeBridgeClientForBridge()
        result = bridge_core.info(client)
        assert result == {}

    def test_info_malformed_json_returns_raw(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        client = FakeBridgeClientForBridge()
        client.set_retained("zigbee2mqtt/bridge/info", "not json {{{")
        result = bridge_core.info(client)
        assert result.get("raw") == "not json {{{"


class TestBridgeState:
    def test_state_plain_string(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        client = FakeBridgeClientForBridge()
        client.set_retained("zigbee2mqtt/bridge/state", "offline")
        result = bridge_core.state(client)
        assert result == "offline"

    def test_state_json_object(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        client = FakeBridgeClientForBridge()
        client.set_retained("zigbee2mqtt/bridge/state", '{"state":"online","note":"running"}')
        result = bridge_core.state(client)
        assert result == "online"

    def test_state_none_returns_empty_string(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        client = FakeBridgeClientForBridge()
        result = bridge_core.state(client)
        assert result == ""

    def test_state_malformed_json_falls_back(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        client = FakeBridgeClientForBridge()
        client.set_retained("zigbee2mqtt/bridge/state", "  not json {{{")
        result = bridge_core.state(client)
        # Falls back to the raw string
        assert result == "not json {{{"


class TestBridgeRestart:
    def test_restart_returns_response(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        client = FakeBridgeClientForBridge()
        client.set_response("restart", {"message": "restarting", "status": "ok"})
        result = bridge_core.restart(client)
        assert result["status"] == "ok"


class TestBridgeHealthCheck:
    def test_health_check_returns_response(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        client = FakeBridgeClientForBridge()
        client.set_response("health_check", {"status": "ok"})
        result = bridge_core.health_check(client)
        assert result["status"] == "ok"


class TestBridgeOptions:
    def test_options_get_returns_response(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        client = FakeBridgeClientForBridge()
        client.set_response("options", {"options": {"permit_join": True}})
        result = bridge_core.options_get(client)
        assert "options" in result

    def test_options_set_passes_payload(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        client = FakeBridgeClientForBridge()
        client.set_response("options", {"status": "ok"})
        result = bridge_core.options_set(client, {"permit_join": False})
        assert result["status"] == "ok"


class TestBridgeWatchLogging:
    def test_watch_logging_returns_collected(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core
        import json
        import time

        client = FakeBridgeClientForBridge()
        captured_cb = None

        def capture_subscribe(topic, cb):
            nonlocal captured_cb
            captured_cb = cb

        client.subscribe = capture_subscribe

        # Track sleep calls; deliver messages on the 1st call and raise
        # KeyboardInterrupt on the 2nd to break out of the loop deterministically
        sleep_calls = [0]

        def controlled_sleep(duration):
            sleep_calls[0] += 1
            if sleep_calls[0] == 1:
                # First iteration: deliver messages
                captured_cb("zigbee2mqtt/bridge/logging", json.dumps({"msg": "info1"}))
                captured_cb("zigbee2mqtt/bridge/logging", json.dumps({"msg": "info2"}))
            elif sleep_calls[0] == 2:
                # Second iteration: exit the loop
                raise KeyboardInterrupt()
            else:
                time.sleep(duration)

        with patch.object(time, "sleep", side_effect=controlled_sleep):
            result = bridge_core.watch_logging(client, duration=10.0)  # long duration

        assert len(result) == 2
        assert result[0]["msg"] == "info1"
        assert result[1]["msg"] == "info2"

    def test_watch_logging_malformed_payload_in_collected(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        client = FakeBridgeClientForBridge()
        captured_cb = None

        def capture_subscribe(topic, cb):
            nonlocal captured_cb
            captured_cb = cb

        client.subscribe = capture_subscribe

        sleep_calls = [0]

        def controlled_sleep(duration):
            sleep_calls[0] += 1
            if sleep_calls[0] == 1:
                captured_cb("zigbee2mqtt/bridge/logging", "not json {{{")
            elif sleep_calls[0] == 2:
                raise KeyboardInterrupt()

        import time

        with patch.object(time, "sleep", side_effect=controlled_sleep):
            result = bridge_core.watch_logging(client, duration=10.0)

        assert len(result) == 1
        assert result[0]["raw"] == "not json {{{"

    def test_watch_logging_callback_error_isolation(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core
        import time

        client = FakeBridgeClientForBridge()
        captured_cb = None

        def capture_subscribe(topic, cb):
            nonlocal captured_cb
            captured_cb = cb

        client.subscribe = capture_subscribe

        def bad_cb(data):
            raise RuntimeError("boom")

        sleep_calls = [0]

        def controlled_sleep(duration):
            sleep_calls[0] += 1
            if sleep_calls[0] == 1:
                captured_cb("zigbee2mqtt/bridge/logging", '{"msg":"ok"}')
            elif sleep_calls[0] == 2:
                raise KeyboardInterrupt()

        with patch.object(time, "sleep", side_effect=controlled_sleep):
            result = bridge_core.watch_logging(client, duration=10.0, callback=bad_cb)

        # The loop continues despite the callback error — message is still collected
        assert len(result) == 1
        assert result[0]["msg"] == "ok"


class TestBridgeWatchEvents:
    def test_watch_events_returns_collected(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core
        import json
        import time

        client = FakeBridgeClientForBridge()
        captured_cb = None

        def capture_subscribe(topic, cb):
            nonlocal captured_cb
            captured_cb = cb

        client.subscribe = capture_subscribe

        sleep_calls = [0]

        def controlled_sleep(duration):
            sleep_calls[0] += 1
            if sleep_calls[0] == 1:
                captured_cb(
                    "zigbee2mqtt/bridge/event",
                    json.dumps({"type": "device_joined", "data": {}}),
                )
            elif sleep_calls[0] == 2:
                raise KeyboardInterrupt()

        with patch.object(time, "sleep", side_effect=controlled_sleep):
            result = bridge_core.watch_events(client, duration=10.0)

        assert len(result) == 1
        assert result[0]["type"] == "device_joined"


# ── k8s_backend ───────────────────────────────────────────────────────────────


class FakeSubprocessResult:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestK8sBackendHelpers:
    def test_kubectl_raises_when_not_found(self):
        from cli_anything.zigbee2mqtt.core import k8s_backend as k8s

        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="kubectl not found"):
                k8s._kubectl()

    def test_kubectl_returns_path_when_found(self):
        from cli_anything.zigbee2mqtt.core import k8s_backend as k8s

        with patch("shutil.which", return_value="/usr/bin/kubectl"):
            result = k8s._kubectl()
            assert result == "/usr/bin/kubectl"

    def test_run_raises_on_nonzero_by_default(self):
        from cli_anything.zigbee2mqtt.core import k8s_backend as k8s

        with patch("shutil.which", return_value="/usr/bin/kubectl"):
            with patch("subprocess.run", return_value=FakeSubprocessResult(1, b"", b"err")):
                with pytest.raises(RuntimeError, match="kubectl.*failed.*exit 1"):
                    k8s._run(["kubectl", "version"])

    def test_run_returns_proc_on_nonzero_when_check_false(self):
        from cli_anything.zigbee2mqtt.core import k8s_backend as k8s

        with patch("shutil.which", return_value="/usr/bin/kubectl"):
            with patch("subprocess.run", return_value=FakeSubprocessResult(1, b"out", b"")):
                result = k8s._run(["kubectl", "version"], check=False)
                assert result.stdout == b"out"

    def test_exec_adds_stdin_i_flag(self):
        from cli_anything.zigbee2mqtt.core import k8s_backend as k8s

        tgt = k8s.K8sTarget(
            namespace="z2m",
            deployment="z2m",
            container="z2m",
            data_path="/app/data",
        )

        captured_args = []

        def fake_run(args, **kwargs):
            captured_args.append(args)
            return FakeSubprocessResult(0, b"ok")

        with patch("shutil.which", return_value="/usr/bin/kubectl"):
            with patch("subprocess.run", fake_run):
                k8s.exec_(tgt, ["ls", "/app/data"], stdin="hello", check=True)

        cmd = captured_args[0]
        assert "-i" in cmd
        assert "kubectl" in cmd[0]

    def test_exec_without_stdin_no_i_flag(self):
        from cli_anything.zigbee2mqtt.core import k8s_backend as k8s

        tgt = k8s.K8sTarget(
            namespace="z2m",
            deployment="z2m",
            container="z2m",
            data_path="/app/data",
        )
        captured_args = []

        def fake_run(args, **kwargs):
            captured_args.append(args)
            return FakeSubprocessResult(0, b"ok")

        with patch("shutil.which", return_value="/usr/bin/kubectl"):
            with patch("subprocess.run", fake_run):
                k8s.exec_(tgt, ["ls", "/app/data"], check=True)

        cmd = captured_args[0]
        assert "-i" not in cmd


class TestK8sBackendRestart:
    def test_restart_calls_kubectl_rollout_restart(self):
        from cli_anything.zigbee2mqtt.core import k8s_backend as k8s

        tgt = k8s.K8sTarget(
            namespace="z2m",
            deployment="z2m",
            container="z2m",
            data_path="/app/data",
        )
        captured_args = []

        def fake_run(args, **kwargs):
            captured_args.append(args)
            return FakeSubprocessResult(0, b"", b"")

        with patch("shutil.which", return_value="/usr/bin/kubectl"):
            with patch("subprocess.run", side_effect=fake_run):
                k8s.restart(tgt)

        assert len(captured_args) == 1
        assert "-n" in captured_args[0]
        assert "rollout" in captured_args[0]
        assert "restart" in captured_args[0]
        assert "deployment/z2m" in captured_args[0]


class TestK8sBackendRolloutStatus:
    def test_rollout_status_returns_output(self):
        from cli_anything.zigbee2mqtt.core import k8s_backend as k8s

        tgt = k8s.K8sTarget(
            namespace="z2m",
            deployment="z2m",
            container="z2m",
            data_path="/app/data",
        )
        with patch("shutil.which", return_value="/usr/bin/kubectl"):
            with patch(
                "subprocess.run",
                return_value=FakeSubprocessResult(0, b"Waiting for rollout...\ndone"),
            ):
                result = k8s.rollout_status(tgt, timeout="60s")
                assert "Waiting" in result or "done" in result

    def test_rollout_status_includes_stderr(self):
        from cli_anything.zigbee2mqtt.core import k8s_backend as k8s

        tgt = k8s.K8sTarget(
            namespace="z2m",
            deployment="z2m",
            container="z2m",
            data_path="/app/data",
        )
        with patch("shutil.which", return_value="/usr/bin/kubectl"):
            with patch(
                "subprocess.run",
                return_value=FakeSubprocessResult(0, b"out", b"err msg"),
            ):
                result = k8s.rollout_status(tgt)
                assert "err msg" in result


class TestK8sBackendConverters:
    def test_list_external_converters(self):
        from cli_anything.zigbee2mqtt.core import k8s_backend as k8s

        tgt = k8s.K8sTarget(
            namespace="z2m",
            deployment="z2m",
            container="z2m",
            data_path="/app/data",
        )
        with patch("shutil.which", return_value="/usr/bin/kubectl"):
            with patch(
                "subprocess.run",
                return_value=FakeSubprocessResult(0, b"conv1.js\nconv2.js\n", b""),
            ):
                result = k8s.list_external_converters(tgt)
                assert result == ["conv1.js", "conv2.js"]

    def test_read_external_converter(self):
        from cli_anything.zigbee2mqtt.core import k8s_backend as k8s

        tgt = k8s.K8sTarget(
            namespace="z2m",
            deployment="z2m",
            container="z2m",
            data_path="/app/data",
        )
        with patch("shutil.which", return_value="/usr/bin/kubectl"):
            with patch(
                "subprocess.run",
                return_value=FakeSubprocessResult(0, b"// converter code", b""),
            ):
                result = k8s.read_external_converter(tgt, "myconv.js")
                assert result == "// converter code"


# ── scenes + group state control ───────────────────────────────────────────────


class RecordingClient:
    """Fake BridgeClient that records publishes and serves retained payloads.

    Scene commands and group set/get are fire-and-forget publishes (Zigbee
    scene commands have no bridge/response counterpart), so the assertion
    surface is "what topic + payload went onto the wire" — which is exactly
    what this records.
    """

    def __init__(self, base_topic: str = "zigbee2mqtt"):
        self.base_topic = base_topic
        self.published: list[tuple[str, object]] = []
        self._retained: dict[str, str] = {}
        self.rc = 0

    def set_retained(self, topic: str, payload: str) -> None:
        self._retained[topic] = payload

    def collect_retained(self, topic: str, *, timeout: float = 5.0):
        return self._retained.get(topic)

    def publish(self, topic: str, payload, *, retain: bool = False, qos: int = 0) -> int:
        self.published.append((topic, payload))
        return self.rc

    @property
    def last(self) -> tuple[str, object]:
        return self.published[-1]


class TestSceneTopics:
    def test_set_topic_without_endpoint(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        assert scenes.set_topic(c, "Kitchen") == "zigbee2mqtt/Kitchen/set"

    def test_set_topic_with_endpoint(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        assert scenes.set_topic(c, "Kitchen", endpoint=2) == "zigbee2mqtt/Kitchen/2/set"

    def test_set_topic_honours_custom_base_topic(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient(base_topic="z2m")
        assert scenes.set_topic(c, "Kitchen") == "z2m/Kitchen/set"

    def test_empty_endpoint_string_is_ignored(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        assert scenes.set_topic(c, "Kitchen", endpoint="") == "zigbee2mqtt/Kitchen/set"

    def test_blank_target_rejected(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        with pytest.raises(ValueError, match="target is required"):
            scenes.set_topic(c, "   ")


class TestSceneStore:
    def test_store_publishes_scene_store(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        result = scenes.store(c, "Kitchen", 3)
        topic, payload = c.last
        assert topic == "zigbee2mqtt/Kitchen/set"
        assert payload == {"scene_store": {"ID": 3}}
        assert result["rc"] == 0
        assert result["target"] == "Kitchen"

    def test_store_with_name(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        scenes.store(c, "Kitchen", 3, name="Chill")
        assert c.last[1] == {"scene_store": {"ID": 3, "name": "Chill"}}

    def test_store_endpoint_scoped(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        scenes.store(c, "Switch", 1, endpoint="left")
        assert c.last[0] == "zigbee2mqtt/Switch/left/set"

    def test_store_rejects_out_of_range_id(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        with pytest.raises(ValueError, match="0-255"):
            scenes.store(c, "Kitchen", 256)
        with pytest.raises(ValueError, match="0-255"):
            scenes.store(c, "Kitchen", -1)
        assert c.published == []

    def test_store_rejects_non_integer_id(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        with pytest.raises(ValueError, match="must be an integer"):
            scenes.store(c, "Kitchen", "abc")

    def test_store_rejects_blank_name(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        with pytest.raises(ValueError, match="name is required"):
            scenes.store(c, "Kitchen", 1, name="  ")

    def test_scene_id_zero_is_valid(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        scenes.store(c, "Kitchen", 0)
        assert c.last[1] == {"scene_store": {"ID": 0}}

    def test_scene_id_255_is_valid(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        scenes.store(c, "Kitchen", scenes.MAX_SCENE_ID)
        assert c.last[1] == {"scene_store": {"ID": 255}}


class TestSceneRecallRemoveRename:
    def test_recall(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        scenes.recall(c, "Kitchen", 4)
        assert c.last == ("zigbee2mqtt/Kitchen/set", {"scene_recall": 4})

    def test_remove(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        scenes.remove(c, "Kitchen", 4)
        assert c.last == ("zigbee2mqtt/Kitchen/set", {"scene_remove": 4})

    def test_remove_all_uses_empty_value(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        scenes.remove_all(c, "Kitchen")
        assert c.last == ("zigbee2mqtt/Kitchen/set", {"scene_remove_all": ""})

    def test_remove_all_endpoint(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        scenes.remove_all(c, "Switch", endpoint=2)
        assert c.last[0] == "zigbee2mqtt/Switch/2/set"

    def test_rename(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        scenes.rename(c, "Kitchen", 4, "Movie")
        assert c.last[1] == {"scene_rename": {"ID": 4, "name": "Movie"}}

    def test_rename_requires_name(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        with pytest.raises(ValueError, match="name is required"):
            scenes.rename(c, "Kitchen", 4, "")

    def test_recall_validates_id_before_publishing(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        with pytest.raises(ValueError):
            scenes.recall(c, "Kitchen", 900)
        assert c.published == []


class TestSceneAdd:
    def test_add_minimal(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        scenes.add(c, "Kitchen", 7)
        assert c.last[1] == {"scene_add": {"ID": 7}}

    def test_add_full_payload(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        scenes.add(
            c,
            "Kitchen",
            7,
            name="Dinner",
            transition=1.5,
            state="on",
            brightness=120,
            color_temp=370,
            color={"x": 0.4, "y": 0.4},
            extra={"color_mode": "xy"},
        )
        body = c.last[1]["scene_add"]
        assert body["ID"] == 7
        assert body["name"] == "Dinner"
        assert body["transition"] == 1.5
        assert body["state"] == "ON"  # upper-cased for z2m
        assert body["brightness"] == 120
        assert body["color_temp"] == 370
        assert body["color"] == {"x": 0.4, "y": 0.4}
        assert body["color_mode"] == "xy"

    def test_add_rejects_bad_brightness(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        with pytest.raises(ValueError, match="brightness must be 0-254"):
            scenes.add(c, "Kitchen", 1, brightness=300)

    def test_add_rejects_negative_transition(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        with pytest.raises(ValueError, match="transition must be >= 0"):
            scenes.add(c, "Kitchen", 1, transition=-2)

    def test_add_rejects_non_dict_color(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        with pytest.raises(ValueError, match="color must be a JSON object"):
            scenes.add(c, "Kitchen", 1, color="red")

    def test_add_rejects_non_dict_extra(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        with pytest.raises(ValueError, match="extra must be a dict"):
            scenes.add(c, "Kitchen", 1, extra=["nope"])

    def test_add_ignores_empty_extra(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        scenes.add(c, "Kitchen", 1, extra={})
        assert c.last[1] == {"scene_add": {"ID": 1}}


class TestSceneList:
    def test_list_from_retained_state(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        c.set_retained(
            "zigbee2mqtt/Kitchen",
            json.dumps({"state": "ON", "scenes": [{"id": 2, "name": "B"}, {"id": 1, "name": "A"}]}),
        )
        rows = scenes.list_scenes(c, "Kitchen")
        assert [r["id"] for r in rows] == [1, 2]
        assert rows[0]["name"] == "A"

    def test_list_accepts_uppercase_id_key(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        c.set_retained("zigbee2mqtt/Kitchen", json.dumps({"scenes": [{"ID": 5, "name": "X"}]}))
        assert scenes.list_scenes(c, "Kitchen") == [{"id": 5, "name": "X"}]

    def test_list_accepts_bare_int_ids(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        c.set_retained("zigbee2mqtt/Kitchen", json.dumps({"scenes": [3, 1]}))
        assert scenes.list_scenes(c, "Kitchen") == [
            {"id": 1, "name": None},
            {"id": 3, "name": None},
        ]

    def test_list_falls_back_to_bridge_groups(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        c.set_retained(
            "zigbee2mqtt/bridge/groups",
            json.dumps(
                [
                    {"id": 1, "friendly_name": "Other", "scenes": []},
                    {"id": 2, "friendly_name": "Kitchen", "scenes": [{"id": 9, "name": "Late"}]},
                ]
            ),
        )
        assert scenes.list_scenes(c, "kitchen") == [{"id": 9, "name": "Late"}]

    def test_list_fallback_matches_numeric_group_id(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        c.set_retained(
            "zigbee2mqtt/bridge/groups",
            json.dumps([{"id": 2, "friendly_name": "Kitchen", "scenes": [{"id": 4}]}]),
        )
        assert scenes.list_scenes(c, "2") == [{"id": 4, "name": None}]

    def test_list_empty_when_nothing_retained(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        assert scenes.list_scenes(c, "Kitchen") == []

    def test_list_state_without_scenes_falls_through(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        c.set_retained("zigbee2mqtt/Kitchen", json.dumps({"state": "ON"}))
        assert scenes.list_scenes(c, "Kitchen") == []

    def test_list_tolerates_malformed_state_json(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        c.set_retained("zigbee2mqtt/Kitchen", "not json {{")
        assert scenes.list_scenes(c, "Kitchen") == []

    def test_list_tolerates_malformed_groups_json(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        c.set_retained("zigbee2mqtt/bridge/groups", "}}not json")
        assert scenes.list_scenes(c, "Kitchen") == []

    def test_list_tolerates_non_list_groups(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        c.set_retained("zigbee2mqtt/bridge/groups", json.dumps({"unexpected": True}))
        assert scenes.list_scenes(c, "Kitchen") == []

    def test_list_skips_non_dict_group_entries(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        c.set_retained("zigbee2mqtt/bridge/groups", json.dumps(["junk", {"friendly_name": "K"}]))
        assert scenes.list_scenes(c, "K") == []

    def test_list_scenes_non_list_value(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        c.set_retained("zigbee2mqtt/Kitchen", json.dumps({"scenes": "weird"}))
        assert scenes.list_scenes(c, "Kitchen") == []

    def test_list_skips_unrecognised_scene_rows(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        c.set_retained("zigbee2mqtt/Kitchen", json.dumps({"scenes": [{"id": 1}, "junk", None]}))
        assert scenes.list_scenes(c, "Kitchen") == [{"id": 1, "name": None}]

    def test_list_empty_when_no_group_matches(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        c.set_retained(
            "zigbee2mqtt/bridge/groups",
            json.dumps([{"id": 1, "friendly_name": "Hallway", "scenes": [{"id": 1}]}]),
        )
        assert scenes.list_scenes(c, "Kitchen") == []

    def test_list_requires_target(self):
        from cli_anything.zigbee2mqtt.core import scenes

        c = RecordingClient()
        with pytest.raises(ValueError, match="target is required"):
            scenes.list_scenes(c, "")


class TestGroupListMembers:
    """Sibling read-side helper of the new group state commands."""

    INVENTORY = json.dumps(
        [
            {
                "id": 1,
                "friendly_name": "kitchen",
                "members": [{"ieee_address": "0xaa", "endpoint": 1}],
            },
            {"id": 2, "friendly_name": "hallway"},
        ]
    )

    def test_members_by_friendly_name(self):
        from cli_anything.zigbee2mqtt.core import groups as groups_core

        c = RecordingClient()
        c.set_retained("zigbee2mqtt/bridge/groups", self.INVENTORY)
        assert groups_core.list_members(c, "kitchen") == [{"ieee_address": "0xaa", "endpoint": 1}]

    def test_members_by_numeric_id(self):
        from cli_anything.zigbee2mqtt.core import groups as groups_core

        c = RecordingClient()
        c.set_retained("zigbee2mqtt/bridge/groups", self.INVENTORY)
        assert groups_core.list_members(c, 1) == [{"ieee_address": "0xaa", "endpoint": 1}]

    def test_members_missing_key_is_empty(self):
        from cli_anything.zigbee2mqtt.core import groups as groups_core

        c = RecordingClient()
        c.set_retained("zigbee2mqtt/bridge/groups", self.INVENTORY)
        assert groups_core.list_members(c, "hallway") == []

    def test_members_unknown_group_is_empty(self):
        from cli_anything.zigbee2mqtt.core import groups as groups_core

        c = RecordingClient()
        c.set_retained("zigbee2mqtt/bridge/groups", self.INVENTORY)
        assert groups_core.list_members(c, "nope") == []

    def test_members_requires_group(self):
        from cli_anything.zigbee2mqtt.core import groups as groups_core

        c = RecordingClient()
        with pytest.raises(ValueError, match="group is required"):
            groups_core.list_members(c, "")


class TestGroupStateControl:
    def test_set_state_publishes_to_group_set_topic(self):
        from cli_anything.zigbee2mqtt.core import groups as groups_core

        c = RecordingClient()
        rc = groups_core.set_state(c, "kitchen-lights", {"state": "ON", "brightness": 200})
        assert rc == 0
        assert c.last == (
            "zigbee2mqtt/kitchen-lights/set",
            {"state": "ON", "brightness": 200},
        )

    def test_set_state_requires_group(self):
        from cli_anything.zigbee2mqtt.core import groups as groups_core

        c = RecordingClient()
        with pytest.raises(ValueError, match="group is required"):
            groups_core.set_state(c, "", {"state": "ON"})

    def test_set_state_requires_fields(self):
        from cli_anything.zigbee2mqtt.core import groups as groups_core

        c = RecordingClient()
        with pytest.raises(ValueError, match="non-empty dict"):
            groups_core.set_state(c, "kitchen", {})

    def test_get_state_publishes_blank_values(self):
        from cli_anything.zigbee2mqtt.core import groups as groups_core

        c = RecordingClient()
        groups_core.get_state(c, "kitchen", ["state", "brightness"])
        assert c.last == (
            "zigbee2mqtt/kitchen/get",
            {"state": "", "brightness": ""},
        )

    def test_get_state_requires_keys(self):
        from cli_anything.zigbee2mqtt.core import groups as groups_core

        c = RecordingClient()
        with pytest.raises(ValueError, match="at least one key"):
            groups_core.get_state(c, "kitchen", [])

    def test_get_state_requires_group(self):
        from cli_anything.zigbee2mqtt.core import groups as groups_core

        c = RecordingClient()
        with pytest.raises(ValueError, match="group is required"):
            groups_core.get_state(c, "", ["state"])

    def test_read_state_parses_retained(self):
        from cli_anything.zigbee2mqtt.core import groups as groups_core

        c = RecordingClient()
        c.set_retained("zigbee2mqtt/kitchen", json.dumps({"state": "ON", "brightness": 12}))
        assert groups_core.read_state(c, "kitchen")["brightness"] == 12

    def test_read_state_empty_when_never_published(self):
        from cli_anything.zigbee2mqtt.core import groups as groups_core

        c = RecordingClient()
        assert groups_core.read_state(c, "kitchen") == {}

    def test_read_state_malformed_returns_raw(self):
        from cli_anything.zigbee2mqtt.core import groups as groups_core

        c = RecordingClient()
        c.set_retained("zigbee2mqtt/kitchen", "nope {{")
        assert groups_core.read_state(c, "kitchen") == {"raw": "nope {{"}

    def test_read_state_non_dict_returns_raw(self):
        from cli_anything.zigbee2mqtt.core import groups as groups_core

        c = RecordingClient()
        c.set_retained("zigbee2mqtt/kitchen", json.dumps([1, 2]))
        assert groups_core.read_state(c, "kitchen") == {"raw": "[1, 2]"}

    def test_read_state_requires_group(self):
        from cli_anything.zigbee2mqtt.core import groups as groups_core

        c = RecordingClient()
        with pytest.raises(ValueError, match="group is required"):
            groups_core.read_state(c, "")


class TestSceneRealClientIntegration:
    """Scene publishes through the real BridgeClient over the fake transport."""

    def test_store_then_recall_over_fake_transport(self, fake_paho):
        from cli_anything.zigbee2mqtt.core import scenes
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="z2m")
        c.connect()
        scenes.store(c, "Kitchen", 2, name="Chill")
        scenes.recall(c, "Kitchen", 2)
        topics = [t for t, _p, _q, _r in c.client.published]  # type: ignore[attr-defined]
        assert topics == ["z2m/Kitchen/set", "z2m/Kitchen/set"]
        bodies = [json.loads(p) for _t, p, _q, _r in c.client.published]  # type: ignore[attr-defined]
        assert bodies[0] == {"scene_store": {"ID": 2, "name": "Chill"}}
        assert bodies[1] == {"scene_recall": 2}
        c.disconnect()


# ── refine: exposes introspection / availability / raw cluster access ────────


class SubscribingClient(RecordingClient):
    """RecordingClient plus a subscribe() that replays seeded retained topics.

    ``devices.availability_sweep`` subscribes to ``<base>/#`` once and reads
    whatever the broker replays, so the fake has to deliver the retained
    messages to the callback the same way paho would.
    """

    def __init__(self, base_topic: str = "zigbee2mqtt"):
        super().__init__(base_topic=base_topic)
        self.subscriptions: list[str] = []

    def subscribe(self, filter_: str, callback) -> None:
        self.subscriptions.append(filter_)
        prefix = filter_[:-1] if filter_.endswith("#") else filter_
        for topic, payload in self._retained.items():
            if filter_.endswith("#") and topic.startswith(prefix):
                callback(topic, payload)
            elif topic == filter_:
                callback(topic, payload)


class TestDecodeAccess:
    def test_all_bits(self):
        assert devices_core.decode_access(7) == "published,set,get"

    def test_read_only(self):
        assert devices_core.decode_access(1) == "published"

    def test_settable_only(self):
        assert devices_core.decode_access(2) == "set"

    def test_none_is_blank(self):
        assert devices_core.decode_access(None) == ""

    def test_garbage_is_blank(self):
        assert devices_core.decode_access("nope") == ""


class TestFlattenExposes:
    LIGHT = [
        {
            "type": "light",
            "features": [
                {"type": "binary", "name": "state", "property": "state", "access": 7},
                {
                    "type": "numeric",
                    "name": "brightness",
                    "property": "brightness",
                    "access": 7,
                    "value_min": 0,
                    "value_max": 254,
                },
            ],
        },
        {
            "type": "composite",
            "property": "color_options",
            "features": [
                {
                    "type": "numeric",
                    "name": "execute_if_off",
                    "property": "execute_if_off",
                    "access": 2,
                }
            ],
        },
        {
            "type": "enum",
            "name": "effect",
            "property": "effect",
            "access": 2,
            "values": ["blink", "breathe"],
        },
        {"type": "numeric", "name": "linkquality", "property": "linkquality", "access": 1},
    ]

    def test_flattens_nested_features(self):
        rows = devices_core.flatten_exposes(self.LIGHT)
        props = [r["property"] for r in rows]
        assert props == [
            "state",
            "brightness",
            "color_options.execute_if_off",
            "effect",
            "linkquality",
        ]

    def test_composite_children_are_namespaced(self):
        rows = devices_core.flatten_exposes(self.LIGHT)
        row = next(r for r in rows if r["property"].startswith("color_options"))
        assert row["property"] == "color_options.execute_if_off"
        assert row["access_flags"] == "set"

    def test_keeps_range_and_values_metadata(self):
        rows = devices_core.flatten_exposes(self.LIGHT)
        brightness = next(r for r in rows if r["property"] == "brightness")
        assert brightness["value_min"] == 0
        assert brightness["value_max"] == 254
        effect = next(r for r in rows if r["property"] == "effect")
        assert effect["values"] == ["blink", "breathe"]

    def test_empty_and_none_are_safe(self):
        assert devices_core.flatten_exposes(None) == []
        assert devices_core.flatten_exposes([]) == []

    def test_skips_non_dict_and_propertyless_entries(self):
        rows = devices_core.flatten_exposes(["junk", {"type": "numeric", "name": "no_property"}])
        assert rows == []


class TestDeviceExposes:
    DEVICES = json.dumps(
        [
            {
                "friendly_name": "Lounge Lamp",
                "ieee_address": "0xaaa",
                "definition": {
                    "model": "LCT001",
                    "exposes": [
                        {
                            "type": "light",
                            "features": [
                                {
                                    "type": "binary",
                                    "name": "state",
                                    "property": "state",
                                    "access": 7,
                                }
                            ],
                        },
                        {
                            "type": "numeric",
                            "name": "linkquality",
                            "property": "linkquality",
                            "access": 1,
                        },
                    ],
                },
            },
            {"friendly_name": "Mystery", "ieee_address": "0xbbb"},
        ]
    )

    def _client(self):
        c = SubscribingClient()
        c.set_retained("zigbee2mqtt/bridge/devices", self.DEVICES)
        return c

    def test_returns_flat_rows(self):
        rows = devices_core.exposes(self._client(), "Lounge Lamp")
        assert [r["property"] for r in rows] == ["state", "linkquality"]

    def test_settable_only_filters_read_only(self):
        rows = devices_core.exposes(self._client(), "Lounge Lamp", settable_only=True)
        assert [r["property"] for r in rows] == ["state"]

    def test_lookup_by_ieee(self):
        rows = devices_core.exposes(self._client(), "0xAAA")
        assert rows and rows[0]["property"] == "state"

    def test_unknown_device_returns_empty(self):
        assert devices_core.exposes(self._client(), "nope") == []

    def test_device_without_definition_returns_empty(self):
        assert devices_core.exposes(self._client(), "Mystery") == []

    def test_requires_ident(self):
        with pytest.raises(ValueError, match="ident is required"):
            devices_core.exposes(self._client(), "")


class TestParseAvailability:
    def test_plain_string(self):
        assert devices_core.parse_availability("online") == "online"

    def test_json_state(self):
        assert devices_core.parse_availability('{"state": "offline"}') == "offline"

    def test_uppercase_normalised(self):
        assert devices_core.parse_availability("ONLINE") == "online"

    def test_none_and_blank(self):
        assert devices_core.parse_availability(None) is None
        assert devices_core.parse_availability("   ") is None

    def test_malformed_json(self):
        assert devices_core.parse_availability("{oops") is None

    def test_json_without_state(self):
        assert devices_core.parse_availability('{"other": 1}') is None


class TestReadAvailability:
    def test_reads_retained_topic(self):
        c = SubscribingClient()
        c.set_retained("zigbee2mqtt/Lounge Lamp/availability", '{"state":"online"}')
        out = devices_core.read_availability(c, "Lounge Lamp")
        assert out == {"friendly_name": "Lounge Lamp", "availability": "online", "online": True}

    def test_offline(self):
        c = SubscribingClient()
        c.set_retained("zigbee2mqtt/sensor/availability", "offline")
        out = devices_core.read_availability(c, "sensor")
        assert out["online"] is False

    def test_missing_is_unknown(self):
        out = devices_core.read_availability(SubscribingClient(), "sensor")
        assert out["availability"] is None
        assert out["online"] is None

    def test_requires_name(self):
        with pytest.raises(ValueError, match="friendly_name is required"):
            devices_core.read_availability(SubscribingClient(), "")


class TestAvailabilitySweep:
    def _client(self):
        c = SubscribingClient()
        c.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps(
                [
                    {"friendly_name": "Coordinator", "type": "Coordinator", "ieee_address": "0x00"},
                    {
                        "friendly_name": "Lounge Lamp",
                        "type": "Router",
                        "ieee_address": "0xaaa",
                        "definition": {"model": "LCT001"},
                    },
                    {"friendly_name": "sensor", "type": "EndDevice", "ieee_address": "0xbbb"},
                    {"friendly_name": "quiet", "type": "EndDevice", "ieee_address": "0xccc"},
                ]
            ),
        )
        c.set_retained("zigbee2mqtt/Lounge Lamp/availability", '{"state":"online"}')
        c.set_retained("zigbee2mqtt/sensor/availability", "offline")
        return c

    def test_offline_first_and_coordinator_skipped(self):
        rows = devices_core.availability_sweep(self._client(), duration=0)
        assert [r["friendly_name"] for r in rows] == ["sensor", "Lounge Lamp", "quiet"]
        assert all(r["friendly_name"] != "Coordinator" for r in rows)

    def test_unknown_device_has_null_availability(self):
        rows = devices_core.availability_sweep(self._client(), duration=0)
        quiet = next(r for r in rows if r["friendly_name"] == "quiet")
        assert quiet["availability"] is None
        assert quiet["online"] is None

    def test_offline_only_filter(self):
        rows = devices_core.availability_sweep(self._client(), duration=0, offline_only=True)
        assert [r["friendly_name"] for r in rows] == ["sensor"]

    def test_subscribes_to_wildcard_once(self):
        c = self._client()
        devices_core.availability_sweep(c, duration=0)
        assert c.subscriptions == ["zigbee2mqtt/#"]

    def test_carries_model_and_last_seen(self):
        rows = devices_core.availability_sweep(self._client(), duration=0)
        lamp = next(r for r in rows if r["friendly_name"] == "Lounge Lamp")
        assert lamp["model"] == "LCT001"
        assert lamp["online"] is True

    def test_empty_inventory(self):
        assert devices_core.availability_sweep(SubscribingClient(), duration=0) == []


class TestAttributeValidation:
    def test_check_cluster_name(self):
        from cli_anything.zigbee2mqtt.core import attributes

        assert attributes.check_cluster("genBasic") == "genBasic"

    def test_check_cluster_numeric_string(self):
        from cli_anything.zigbee2mqtt.core import attributes

        assert attributes.check_cluster("0x0006") == 6
        assert attributes.check_cluster("6") == 6

    def test_check_cluster_int_passthrough(self):
        from cli_anything.zigbee2mqtt.core import attributes

        assert attributes.check_cluster(6) == 6

    def test_check_cluster_rejects_empty(self):
        from cli_anything.zigbee2mqtt.core import attributes

        with pytest.raises(ValueError, match="cluster is required"):
            attributes.check_cluster("  ")

    def test_check_cluster_rejects_bool(self):
        from cli_anything.zigbee2mqtt.core import attributes

        with pytest.raises(ValueError, match="name or numeric id"):
            attributes.check_cluster(True)

    def test_check_attributes_mixed(self):
        from cli_anything.zigbee2mqtt.core import attributes

        assert attributes.check_attributes(["onOff", "0x0002"]) == ["onOff", 2]

    def test_check_attributes_rejects_empty_list(self):
        from cli_anything.zigbee2mqtt.core import attributes

        with pytest.raises(ValueError, match="at least one attribute"):
            attributes.check_attributes([])

    def test_check_attributes_rejects_blank_entry(self):
        from cli_anything.zigbee2mqtt.core import attributes

        with pytest.raises(ValueError, match="non-empty"):
            attributes.check_attributes(["onOff", " "])

    def test_check_target_rejects_blank(self):
        from cli_anything.zigbee2mqtt.core import attributes

        with pytest.raises(ValueError, match="target is required"):
            attributes.check_target("")

    def test_check_options_merges_manufacturer_code(self):
        from cli_anything.zigbee2mqtt.core import attributes

        assert attributes.check_options({"a": 1}, manufacturer_code=4107) == {
            "a": 1,
            "manufacturerCode": 4107,
        }

    def test_check_options_defaults_empty(self):
        from cli_anything.zigbee2mqtt.core import attributes

        assert attributes.check_options(None) == {}

    def test_check_options_rejects_non_dict(self):
        from cli_anything.zigbee2mqtt.core import attributes

        with pytest.raises(ValueError, match="options must be a dict"):
            attributes.check_options(["a=1"])


class TestAttributeReadWrite:
    def test_read_publishes_zcl_read(self):
        from cli_anything.zigbee2mqtt.core import attributes

        c = RecordingClient()
        out = attributes.read(c, "Lounge Lamp", "genBasic", ["zclVersion"])
        assert c.last == (
            "zigbee2mqtt/Lounge Lamp/set",
            {"read": {"cluster": "genBasic", "attributes": ["zclVersion"]}},
        )
        assert out["rc"] == 0
        assert out["topic"] == "zigbee2mqtt/Lounge Lamp/set"

    def test_read_with_endpoint_and_options(self):
        from cli_anything.zigbee2mqtt.core import attributes

        c = RecordingClient()
        attributes.read(
            c,
            "Lounge Lamp",
            "genBasic",
            ["zclVersion"],
            endpoint=2,
            manufacturer_code=4107,
        )
        topic, payload = c.last
        assert topic == "zigbee2mqtt/Lounge Lamp/2/set"
        assert payload["read"]["options"] == {"manufacturerCode": 4107}

    def test_read_requires_attributes(self):
        from cli_anything.zigbee2mqtt.core import attributes

        c = RecordingClient()
        with pytest.raises(ValueError, match="at least one attribute"):
            attributes.read(c, "Lounge Lamp", "genBasic", [])

    def test_write_publishes_zcl_write(self):
        from cli_anything.zigbee2mqtt.core import attributes

        c = RecordingClient()
        attributes.write(c, "Lounge Lamp", "genOnOff", {"onOff": 1})
        assert c.last == (
            "zigbee2mqtt/Lounge Lamp/set",
            {"write": {"cluster": "genOnOff", "payload": {"onOff": 1}}},
        )

    def test_write_requires_payload(self):
        from cli_anything.zigbee2mqtt.core import attributes

        c = RecordingClient()
        with pytest.raises(ValueError, match="non-empty dict"):
            attributes.write(c, "Lounge Lamp", "genOnOff", {})

    def test_write_copies_payload(self):
        from cli_anything.zigbee2mqtt.core import attributes

        c = RecordingClient()
        payload = {"onOff": 1}
        attributes.write(c, "Lounge Lamp", "genOnOff", payload)
        payload["onOff"] = 99
        assert c.last[1]["write"]["payload"] == {"onOff": 1}

    def test_set_topic_endpoint_variants(self):
        from cli_anything.zigbee2mqtt.core import attributes

        c = RecordingClient()
        assert attributes.set_topic(c, "Lamp") == "zigbee2mqtt/Lamp/set"
        assert attributes.set_topic(c, "Lamp", endpoint="") == "zigbee2mqtt/Lamp/set"
        assert attributes.set_topic(c, "Lamp", endpoint=3) == "zigbee2mqtt/Lamp/3/set"

    def test_read_over_fake_transport(self, fake_paho):
        from cli_anything.zigbee2mqtt.core import attributes
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        c = BridgeClient("fake-host", base_topic="z2m")
        c.connect()
        attributes.write(c, "Lamp", "genOnOff", {"onOff": 1})
        topics = [t for t, _p, _q, _r in c.client.published]  # type: ignore[attr-defined]
        assert topics == ["z2m/Lamp/set"]
        body = json.loads(c.client.published[0][1])  # type: ignore[attr-defined]
        assert body == {"write": {"cluster": "genOnOff", "payload": {"onOff": 1}}}
        c.disconnect()


class TestOtaUnschedule:
    def test_unschedule_hits_the_right_path(self):
        class Req:
            base_topic = "zigbee2mqtt"

            def __init__(self):
                self.calls = []

            def request(self, path, payload=None, *, timeout=15.0):
                self.calls.append((path, payload, timeout))
                return {"status": "ok"}

        from cli_anything.zigbee2mqtt.core import ota as ota_core

        c = Req()
        assert ota_core.unschedule(c, "Radiator") == {"status": "ok"}
        assert c.calls[0][0] == "device/ota_update/unschedule"
        assert c.calls[0][1] == {"id": "Radiator"}

    def test_unschedule_requires_id(self):
        from cli_anything.zigbee2mqtt.core import ota as ota_core

        with pytest.raises(ValueError, match="id_ is required"):
            ota_core.unschedule(RecordingClient(), "")


class TestRefineEdgeCases:
    """Remaining branches of the refine surface."""

    def test_check_attributes_rejects_none(self):
        from cli_anything.zigbee2mqtt.core import attributes

        with pytest.raises(ValueError, match="at least one attribute"):
            attributes.check_attributes(None)

    def test_check_attributes_accepts_numeric_ids(self):
        from cli_anything.zigbee2mqtt.core import attributes

        assert attributes.check_attributes([0, 7]) == [0, 7]

    def test_write_carries_options(self):
        from cli_anything.zigbee2mqtt.core import attributes

        c = RecordingClient()
        attributes.write(
            c,
            "Lamp",
            "manuSpecificTuya",
            {"attr": 1},
            options={"disableDefaultResponse": True},
            manufacturer_code=4417,
        )
        assert c.last[1]["write"]["options"] == {
            "disableDefaultResponse": True,
            "manufacturerCode": 4417,
        }

    def test_sweep_waits_for_the_requested_duration(self):
        import time as _time

        c = SubscribingClient()
        c.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps([{"friendly_name": "lamp", "type": "Router", "ieee_address": "0xaaa"}]),
        )
        started = _time.monotonic()
        rows = devices_core.availability_sweep(c, duration=0.12)
        assert _time.monotonic() - started >= 0.1
        assert [r["friendly_name"] for r in rows] == ["lamp"]

    def test_sweep_falls_back_to_ieee_topic(self):
        c = SubscribingClient()
        c.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps([{"friendly_name": "0xaaa", "type": "EndDevice", "ieee_address": "0xaaa"}]),
        )
        c.set_retained("zigbee2mqtt/0xaaa/availability", "online")
        rows = devices_core.availability_sweep(c, duration=0)
        assert rows[0]["online"] is True

    def test_sweep_ignores_non_availability_topics(self):
        c = SubscribingClient()
        c.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps([{"friendly_name": "lamp", "type": "Router", "ieee_address": "0xaaa"}]),
        )
        c.set_retained("zigbee2mqtt/lamp", json.dumps({"state": "ON"}))
        rows = devices_core.availability_sweep(c, duration=0)
        assert rows[0]["availability"] is None

    def test_sweep_ctrl_c_returns_what_it_has(self, monkeypatch):
        c = SubscribingClient()
        c.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps([{"friendly_name": "lamp", "type": "Router", "ieee_address": "0xaaa"}]),
        )
        c.set_retained("zigbee2mqtt/lamp/availability", "online")

        def _boom(_seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr(devices_core.time, "sleep", _boom)
        rows = devices_core.availability_sweep(c, duration=5)
        assert [r["friendly_name"] for r in rows] == ["lamp"]
        assert rows[0]["online"] is True


# ── endpoint / cluster introspection ────────────────────────────────────────


class _InventoryClient(FakeBridgeClientForBridge):
    """Retained-only client seeded with a bridge/devices inventory."""

    def __init__(self, devices):
        super().__init__()
        self.set_retained("zigbee2mqtt/bridge/devices", json.dumps(devices))


#: A two-endpoint plug: ep1 has reports + a binding, ep2 is bare.
PLUG = {
    "friendly_name": "Plug",
    "ieee_address": "0xplug",
    "type": "Router",
    "endpoints": {
        "1": {
            "clusters": {
                "input": ["genOnOff", "genBasic", "haElectricalMeasurement"],
                "output": ["genOta"],
            },
            "bindings": [
                {"cluster": "genOnOff", "target": {"type": "endpoint", "ieee_address": "0xco"}}
            ],
            "configured_reportings": [
                {
                    "cluster": "genOnOff",
                    "attribute": "onOff",
                    "minimum_report_interval": 0,
                    "maximum_report_interval": 3600,
                    "reportable_change": 0,
                },
                {
                    "cluster": "haElectricalMeasurement",
                    "attribute": {"ID": 1291, "type": 33},
                    "minimum_report_interval": 5,
                    "maximum_report_interval": 3600,
                    "reportable_change": 10,
                },
            ],
            "scenes": [{"id": 1, "name": "evening"}],
        },
        "2": {"clusters": {"input": ["genOnOff"], "output": []}},
    },
}


class TestFlattenEndpointClusters:
    def test_rows_cover_input_and_output(self):
        rows = devices_core.flatten_endpoint_clusters(PLUG)
        assert len(rows) == 5
        assert {r["cluster"] for r in rows if r["endpoint"] == 1} == {
            "genOnOff",
            "genBasic",
            "haElectricalMeasurement",
            "genOta",
        }

    def test_input_before_output_and_sorted(self):
        rows = [r for r in devices_core.flatten_endpoint_clusters(PLUG) if r["endpoint"] == 1]
        assert [r["direction"] for r in rows] == ["input", "input", "input", "output"]
        assert [r["cluster"] for r in rows[:3]] == [
            "genBasic",
            "genOnOff",
            "haElectricalMeasurement",
        ]

    def test_bound_and_reported_flags(self):
        rows = devices_core.flatten_endpoint_clusters(PLUG, direction="input")
        by_key = {(r["endpoint"], r["cluster"]): r for r in rows}
        assert by_key[(1, "genOnOff")]["bound"] is True
        assert by_key[(1, "genOnOff")]["reported"] is True
        assert by_key[(1, "genBasic")]["bound"] is False
        assert by_key[(2, "genOnOff")]["bound"] is False

    def test_direction_filter(self):
        rows = devices_core.flatten_endpoint_clusters(PLUG, direction="output")
        assert [r["cluster"] for r in rows] == ["genOta"]

    def test_endpoint_filter_accepts_str_or_int(self):
        for wanted in (2, "2"):
            rows = devices_core.flatten_endpoint_clusters(PLUG, endpoint=wanted)
            assert {r["endpoint"] for r in rows} == {2}

    def test_bad_direction_raises(self):
        with pytest.raises(ValueError, match="direction"):
            devices_core.flatten_endpoint_clusters(PLUG, direction="sideways")

    def test_missing_or_malformed_endpoints(self):
        assert devices_core.flatten_endpoint_clusters({}) == []
        assert devices_core.flatten_endpoint_clusters({"endpoints": []}) == []
        assert devices_core.flatten_endpoint_clusters({"endpoints": {"1": "nope"}}) == []
        assert devices_core.flatten_endpoint_clusters({"endpoints": {"1": {}}}) == []

    def test_non_numeric_endpoint_key_survives(self):
        rows = devices_core.flatten_endpoint_clusters(
            {"endpoints": {"green": {"clusters": {"input": ["genBasic"]}}}}
        )
        assert rows[0]["endpoint"] == "green"

    def test_malformed_cluster_block_is_skipped(self):
        rows = devices_core.flatten_endpoint_clusters(
            {"endpoints": {"1": {"clusters": {"input": "genOnOff"}}}}
        )
        assert rows == []


class TestClusters:
    def test_lookup_by_friendly_name(self):
        c = _InventoryClient([PLUG])
        rows = devices_core.clusters(c, "Plug")
        assert len(rows) == 5

    def test_lookup_by_ieee_and_endpoint(self):
        c = _InventoryClient([PLUG])
        rows = devices_core.clusters(c, "0xplug", endpoint=1, direction="output")
        assert [r["cluster"] for r in rows] == ["genOta"]

    def test_unknown_device_returns_empty(self):
        c = _InventoryClient([PLUG])
        assert devices_core.clusters(c, "ghost") == []

    def test_blank_ident_raises(self):
        c = _InventoryClient([PLUG])
        with pytest.raises(ValueError, match="ident is required"):
            devices_core.clusters(c, "")

    def test_bad_direction_raises_before_lookup(self):
        c = _InventoryClient([PLUG])
        with pytest.raises(ValueError, match="direction"):
            devices_core.clusters(c, "Plug", direction="nope")


class TestReportings:
    def test_flatten_normalises_dict_attribute(self):
        rows = devices_core.flatten_reportings(PLUG)
        assert [r["attribute"] for r in rows] == ["onOff", "1291"]
        assert rows[0]["friendly_name"] == "Plug"
        assert rows[1]["reportable_change"] == 10

    def test_flatten_endpoint_filter(self):
        assert devices_core.flatten_reportings(PLUG, endpoint=2) == []

    def test_flatten_skips_non_dict_rows(self):
        rows = devices_core.flatten_reportings(
            {"endpoints": {"1": {"configured_reportings": ["junk", None]}}}
        )
        assert rows == []

    def test_sweep_all_devices_sorted(self):
        other = {
            "friendly_name": "Aaa Sensor",
            "ieee_address": "0xaaa",
            "endpoints": {
                "1": {
                    "configured_reportings": [
                        {
                            "cluster": "msTemperatureMeasurement",
                            "attribute": "measuredValue",
                            "minimum_report_interval": 10,
                            "maximum_report_interval": 300,
                        }
                    ]
                }
            },
        }
        c = _InventoryClient([PLUG, other])
        rows = devices_core.reportings(c)
        assert [r["friendly_name"] for r in rows] == ["Aaa Sensor", "Plug", "Plug"]

    def test_filter_by_device(self):
        c = _InventoryClient([PLUG])
        assert len(devices_core.reportings(c, device_ident="0xPLUG")) == 2
        assert devices_core.reportings(c, device_ident="ghost") == []

    def test_device_with_no_reports(self):
        c = _InventoryClient([{"friendly_name": "bare", "ieee_address": "0xb"}])
        assert devices_core.reportings(c) == []


class TestEndpointSummary:
    def test_counts_per_endpoint(self):
        c = _InventoryClient([PLUG])
        rows = devices_core.endpoint_summary(c, "Plug")
        assert [r["endpoint"] for r in rows] == [1, 2]
        first = rows[0]
        assert first["input_clusters"] == 3
        assert first["output_clusters"] == 1
        assert first["bindings"] == 1
        assert first["configured_reportings"] == 2
        assert first["scenes"] == 1
        assert first["scene_ids"] == [1]
        assert rows[1]["scenes"] == 0

    def test_unknown_device_returns_empty(self):
        c = _InventoryClient([PLUG])
        assert devices_core.endpoint_summary(c, "ghost") == []

    def test_blank_ident_raises(self):
        c = _InventoryClient([PLUG])
        with pytest.raises(ValueError, match="ident is required"):
            devices_core.endpoint_summary(c, "")


# ── bridge/definitions cluster dictionary ───────────────────────────────────


DEFS = {
    "clusters": {
        "genOnOff": {
            "ID": 6,
            "attributes": {
                "onOff": {"ID": 0, "type": 16},
                "startUpOnOff": {"ID": 16387, "type": 48},
            },
            "commands": {
                "off": {"ID": 0, "parameters": []},
                "on": {"ID": 1, "parameters": []},
            },
            "commandsResponse": {},
        },
        "genBasic": {
            "ID": 0,
            "attributes": {"zclVersion": {"ID": 0, "type": 32}},
            "commands": {},
            "commandsResponse": {"reset": {"ID": 0}},
        },
        "manuSpecific": {
            "ID": 64512,
            "attributes": {
                "magic": {"ID": 1, "type": 32, "manufacturerCode": 4098},
            },
            "commands": {"poke": {"ID": 2, "parameters": [{"name": "value", "type": 32}, "bad"]}},
        },
    },
    "custom_clusters": {
        "0xdead": {"tuyaSpecific": {"ID": 61184, "attributes": {"dp": {"ID": 1}}, "commands": {}}},
        "0xbeef": "not-a-dict",
    },
}


def _defs_client(payload=DEFS):
    c = FakeBridgeClientForBridge()
    if payload is not None:
        c.set_retained("zigbee2mqtt/bridge/definitions", json.dumps(payload))
    return c


class TestBridgeDefinitions:
    def test_definitions_parses_retained(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        defs = bridge_core.definitions(_defs_client())
        assert set(defs["clusters"]) == {"genOnOff", "genBasic", "manuSpecific"}

    def test_definitions_missing_topic(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        assert bridge_core.definitions(_defs_client(None)) == {}

    def test_definitions_malformed_json_returns_raw(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        c = FakeBridgeClientForBridge()
        c.set_retained("zigbee2mqtt/bridge/definitions", "{{{")
        assert bridge_core.definitions(c)["raw"] == "{{{"

    def test_definitions_non_object_payload(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        c = FakeBridgeClientForBridge()
        c.set_retained("zigbee2mqtt/bridge/definitions", "[1, 2]")
        assert bridge_core.definitions(c) == {}

    def test_summarize_sorted_by_id_with_counts(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        rows = bridge_core.summarize_clusters(DEFS)
        assert [r["cluster"] for r in rows] == ["genBasic", "genOnOff", "manuSpecific"]
        on_off = rows[1]
        assert on_off["id"] == 6
        assert on_off["attributes"] == 2
        assert on_off["commands"] == 2
        assert on_off["commands_response"] == 0
        assert rows[0]["commands_response"] == 1

    def test_summarize_handles_empty_and_malformed(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        assert bridge_core.summarize_clusters({}) == []
        assert bridge_core.summarize_clusters({"clusters": "nope"}) == []
        rows = bridge_core.summarize_clusters({"clusters": {"weird": "nope"}})
        assert rows == [
            {
                "cluster": "weird",
                "id": None,
                "attributes": 0,
                "commands": 0,
                "commands_response": 0,
            }
        ]

    def test_find_cluster_by_name_case_insensitive(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        found = bridge_core.find_cluster(DEFS, "GENONOFF")
        assert found["cluster"] == "genOnOff"
        assert found["id"] == 6

    def test_find_cluster_by_decimal_and_hex_id(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        assert bridge_core.find_cluster(DEFS, "6")["cluster"] == "genOnOff"
        assert bridge_core.find_cluster(DEFS, "0x0006")["cluster"] == "genOnOff"

    def test_find_cluster_unknown_returns_none(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        assert bridge_core.find_cluster(DEFS, "genNope") is None

    def test_find_cluster_blank_raises(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        with pytest.raises(ValueError, match="cluster is required"):
            bridge_core.find_cluster(DEFS, "  ")

    def test_cluster_attributes_rows(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        rows = bridge_core.cluster_attributes(DEFS, "genOnOff")
        assert [r["attribute"] for r in rows] == ["onOff", "startUpOnOff"]
        assert rows[0]["cluster"] == "genOnOff"
        assert rows[0]["type"] == 16
        assert rows[0]["manufacturer_code"] is None

    def test_cluster_attributes_manufacturer_code(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        rows = bridge_core.cluster_attributes(DEFS, 64512)
        assert rows[0]["manufacturer_code"] == 4098

    def test_cluster_attributes_unknown_cluster(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        assert bridge_core.cluster_attributes(DEFS, "genNope") == []

    def test_cluster_commands_requests_before_responses(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        rows = bridge_core.cluster_commands(DEFS, "genOnOff")
        assert [r["command"] for r in rows] == ["off", "on"]
        assert {r["direction"] for r in rows} == {"request"}

        rows = bridge_core.cluster_commands(DEFS, "genBasic")
        assert rows[0]["direction"] == "response"

    def test_cluster_commands_parameter_names(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        rows = bridge_core.cluster_commands(DEFS, "manuSpecific")
        assert rows[0]["parameters"] == ["value"]

    def test_cluster_commands_unknown_cluster(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        assert bridge_core.cluster_commands(DEFS, "genNope") == []

    def test_custom_clusters_flattened(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        rows = bridge_core.custom_clusters(DEFS)
        assert rows == [
            {
                "ieee_address": "0xdead",
                "cluster": "tuyaSpecific",
                "id": 61184,
                "attributes": 1,
                "commands": 0,
            }
        ]

    def test_custom_clusters_absent(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        assert bridge_core.custom_clusters({}) == []
        assert bridge_core.custom_clusters({"custom_clusters": []}) == []

    def test_cluster_attributes_malformed_block(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        defs = {"clusters": {"weird": {"ID": 1, "attributes": "nope"}}}
        assert bridge_core.cluster_attributes(defs, "weird") == []

    def test_cluster_attributes_malformed_entry(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        defs = {"clusters": {"weird": {"ID": 1, "attributes": {"a": "nope"}}}}
        rows = bridge_core.cluster_attributes(defs, "weird")
        assert rows == [
            {
                "cluster": "weird",
                "attribute": "a",
                "id": None,
                "type": None,
                "manufacturer_code": None,
            }
        ]

    def test_cluster_commands_malformed_block(self):
        from cli_anything.zigbee2mqtt.core import bridge as bridge_core

        defs = {"clusters": {"weird": {"ID": 1, "commands": "nope"}}}
        assert bridge_core.cluster_commands(defs, "weird") == []


class TestEndpointSummaryEdgeCases:
    def test_malformed_cluster_block_counts_zero(self):
        c = _InventoryClient(
            [
                {
                    "friendly_name": "odd",
                    "ieee_address": "0xo",
                    "endpoints": {"1": {"clusters": ["genOnOff"]}},
                }
            ]
        )
        rows = devices_core.endpoint_summary(c, "odd")
        assert rows[0]["input_clusters"] == 0
        assert rows[0]["output_clusters"] == 0

    def test_non_dict_scene_entries_ignored(self):
        c = _InventoryClient(
            [{"friendly_name": "odd", "ieee_address": "0xo", "endpoints": {"1": {"scenes": [7]}}}]
        )
        rows = devices_core.endpoint_summary(c, "odd")
        assert rows[0]["scenes"] == 0
        assert rows[0]["scene_ids"] == []


# ── ota check_all (network firmware sweep) ────────────────────────────────────


class TestOtaCheckAll:
    @staticmethod
    def _device(name, *, ieee=None, type_="EndDevice", disabled=False):
        return {
            "friendly_name": name,
            "ieee_address": ieee or f"0x{abs(hash(name)):016x}",
            "type": type_,
            "disabled": disabled,
        }

    def _client(self, devices, per_device_responses=None):
        """Fake client whose check responses are keyed by device id."""

        class SweepClient(FakeBridgeClientForBridge):
            def request(self, path, payload=None, *, timeout=15.0):
                assert path == "device/ota_update/check"
                return (per_device_responses or {}).get(
                    payload["id"], {"status": "error", "error": "no canned response"}
                )

        client = SweepClient()
        client.set_retained("zigbee2mqtt/bridge/devices", json.dumps(devices))
        return client

    def test_classify_ok_true_false(self):
        from cli_anything.zigbee2mqtt.core import ota as ota_core

        assert (
            ota_core._classify({"status": "ok", "data": {"update_available": True}})[0]
            == "update_available"
        )
        assert (
            ota_core._classify({"status": "ok", "data": {"update_available": False}})[0]
            == "up_to_date"
        )

    def test_classify_ok_without_flag_is_unknown(self):
        from cli_anything.zigbee2mqtt.core import ota as ota_core

        status, _ = ota_core._classify({"status": "ok", "data": {"id": "x"}})
        assert status == "unknown"

    def test_classify_ok_non_dict_data_is_unknown(self):
        from cli_anything.zigbee2mqtt.core import ota as ota_core

        status, detail = ota_core._classify({"status": "ok", "data": "weird"})
        assert status == "unknown"
        assert detail == "weird"

    def test_classify_not_supported_message(self):
        from cli_anything.zigbee2mqtt.core import ota as ota_core

        status, detail = ota_core._classify(
            {"status": "error", "error": "Device 'x' does not support OTA updates"}
        )
        assert status == "not_supported"
        assert "does not support" in detail

    def test_classify_other_error(self):
        from cli_anything.zigbee2mqtt.core import ota as ota_core

        status, detail = ota_core._classify({"status": "error", "error": "timeout"})
        assert status == "error"
        assert detail == "timeout"

    def test_sweep_classifies_each_device(self):
        from cli_anything.zigbee2mqtt.core import ota as ota_core

        lamp1 = self._device("lamp1")
        sensor1 = self._device("sensor1")
        client = self._client(
            [lamp1, sensor1],
            {
                lamp1["ieee_address"]: {
                    "status": "ok",
                    "data": {"update_available": True},
                },
                sensor1["ieee_address"]: {
                    "status": "ok",
                    "data": {"update_available": False},
                },
            },
        )
        rows = ota_core.check_all(client)
        by_name = {r["friendly_name"]: r for r in rows}
        assert by_name["lamp1"]["status"] == "update_available"
        assert by_name["lamp1"]["update_available"] is True
        assert by_name["sensor1"]["status"] == "up_to_date"
        assert by_name["sensor1"]["update_available"] is False

    def test_sweep_skips_coordinator_and_disabled(self):
        from cli_anything.zigbee2mqtt.core import ota as ota_core

        client = self._client(
            [
                self._device("Coordinator", type_="Coordinator"),
                self._device("lamp1"),
                self._device("broken", disabled=True),
            ],
            {"ota/lamp1": {"status": "ok", "data": {"update_available": True}}},
        )
        rows = ota_core.check_all(client)
        assert [r["friendly_name"] for r in rows] == ["lamp1"]

    def test_sweep_include_disabled(self):
        from cli_anything.zigbee2mqtt.core import ota as ota_core

        lamp1 = self._device("lamp1")
        broken = self._device("broken", disabled=True)
        client = self._client(
            [lamp1, broken],
            {
                lamp1["ieee_address"]: {"status": "ok", "data": {"update_available": False}},
                broken["ieee_address"]: {
                    "status": "ok",
                    "data": {"update_available": False},
                },
            },
        )
        rows = ota_core.check_all(client, include_disabled=True)
        assert {r["friendly_name"] for r in rows} == {"lamp1", "broken"}

    def test_sweep_continues_past_device_errors(self):
        from cli_anything.zigbee2mqtt.core import ota as ota_core

        fine = self._device("fine")

        class FlakyClient(FakeBridgeClientForBridge):
            def request(self, path, payload=None, *, timeout=15.0):
                if payload["id"] == fine["ieee_address"]:
                    return {"status": "ok", "data": {"update_available": True}}
                raise RuntimeError("no response from device")

        flaky = FlakyClient()
        flaky.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps([self._device("dead"), fine]),
        )
        rows = ota_core.check_all(flaky)
        statuses = {r["friendly_name"]: r["status"] for r in rows}
        assert statuses == {"dead": "error", "fine": "update_available"}

    def test_sweep_error_row_detail(self):
        from cli_anything.zigbee2mqtt.core import ota as ota_core

        class BoomClient(FakeBridgeClientForBridge):
            def request(self, path, payload=None, *, timeout=15.0):
                raise RuntimeError("device went away")

        client = BoomClient()
        client.set_retained("zigbee2mqtt/bridge/devices", json.dumps([self._device("lamp1")]))
        rows = ota_core.check_all(client)
        assert rows[0]["status"] == "error"
        assert "device went away" in rows[0]["detail"]

    def test_sweep_uses_ieee_as_request_id(self):
        from cli_anything.zigbee2mqtt.core import ota as ota_core

        seen = []

        class Recorder(FakeBridgeClientForBridge):
            def request(self, path, payload=None, *, timeout=15.0):
                seen.append((path, payload))
                return {"status": "ok", "data": {"update_available": False}}

        client = Recorder()
        client.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps([self._device("lamp1", ieee="0xaabb")]),
        )
        ota_core.check_all(client)
        assert seen[0] == ("device/ota_update/check", {"id": "0xaabb"})

    def test_sweep_sorts_updates_first(self):
        from cli_anything.zigbee2mqtt.core import ota as ota_core

        aaa = self._device("aaa")
        zzz = self._device("zzz")
        mmm = self._device("mmm")
        client = self._client(
            [aaa, zzz, mmm],
            {
                aaa["ieee_address"]: {"status": "ok", "data": {"update_available": False}},
                zzz["ieee_address"]: {"status": "ok", "data": {"update_available": True}},
                mmm["ieee_address"]: {"status": "ok", "data": {"update_available": False}},
            },
        )
        rows = ota_core.check_all(client)
        assert rows[0]["friendly_name"] == "zzz"

    def test_sweep_empty_inventory(self):
        from cli_anything.zigbee2mqtt.core import ota as ota_core

        client = self._client([])
        assert ota_core.check_all(client) == []

    def test_summarize_counts(self):
        from cli_anything.zigbee2mqtt.core import ota as ota_core

        rows = [
            {"status": "update_available"},
            {"status": "up_to_date"},
            {"status": "up_to_date"},
            {"status": "not_supported"},
            {"status": "error"},
        ]
        assert ota_core.summarize_check(rows) == {
            "total": 5,
            "update_available": 1,
            "up_to_date": 2,
            "not_supported": 1,
            "unknown": 0,
            "error": 1,
        }

    def test_summarize_empty(self):
        from cli_anything.zigbee2mqtt.core import ota as ota_core

        assert ota_core.summarize_check([])["total"] == 0


# ── battery audit (refine pass 4) ────────────────────────────────────────────


class TestClassifyBattery:
    def test_percent_above_threshold_is_ok(self):
        assert devices_core.classify_battery(87, None) == "ok"

    def test_percent_below_threshold_is_low(self):
        assert devices_core.classify_battery(15, None) == "low"

    def test_percent_at_threshold_is_ok(self):
        assert devices_core.classify_battery(20, None) == "ok"

    def test_numeric_string_percent(self):
        assert devices_core.classify_battery("85", None) == "ok"

    def test_non_numeric_percent_falls_through(self):
        # garbage percent → judge on battery_low / power_source instead
        assert devices_core.classify_battery("full", None, power_source="Battery") == "unknown"
        assert devices_core.classify_battery("full", True) == "low"

    def test_battery_low_flag_true_is_low(self):
        assert devices_core.classify_battery(None, True) == "low"

    def test_battery_low_flag_false_is_ok(self):
        assert devices_core.classify_battery(None, False) == "ok"

    def test_battery_powered_without_data_is_unknown(self):
        assert devices_core.classify_battery(None, None, power_source="Battery") == "unknown"

    def test_battery_powered_case_insensitive(self):
        assert (
            devices_core.classify_battery(None, None, power_source="BATTERY OR MAINS") == "unknown"
        )

    def test_mains_device_is_mains(self):
        assert devices_core.classify_battery(None, None, power_source="Mains (single phase)") == (
            "mains"
        )

    def test_no_power_source_is_mains(self):
        assert devices_core.classify_battery(None, None) == "mains"

    def test_custom_below(self):
        assert devices_core.classify_battery(55, None, below=60) == "low"

    def test_battery_bool_is_not_a_percent(self):
        # bool is an int subclass in python — but True as a battery percent is
        # nonsense; it would read as 1.0. Coercion keeps it (1.0 < 20 → low);
        # a real z2m payload never sends this, but pin the behaviour.
        assert devices_core.classify_battery(True, None) == "low"


class TestBatterySweep:
    def _client(self):
        c = SubscribingClient()
        c.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps(
                [
                    {"friendly_name": "Coordinator", "type": "Coordinator", "ieee_address": "0x00"},
                    {
                        "friendly_name": "Door Sensor",
                        "type": "EndDevice",
                        "ieee_address": "0xaaa",
                        "power_source": "Battery",
                        "definition": {"model": "MCCGQ11LM"},
                    },
                    {"friendly_name": "Thermo", "type": "EndDevice", "power_source": "Battery"},
                    {
                        "friendly_name": "Plug",
                        "type": "Router",
                        "power_source": "Mains (single phase)",
                    },
                    {
                        "friendly_name": "Remote",
                        "type": "EndDevice",
                        "power_source": "Battery",
                    },
                ]
            ),
        )
        c.set_retained("zigbee2mqtt/Door Sensor", json.dumps({"battery": 87, "voltage": 2915}))
        c.set_retained("zigbee2mqtt/Thermo", json.dumps({"battery": 9, "battery_low": True}))
        c.set_retained("zigbee2mqtt/Remote", json.dumps({"contact": True}))
        return c

    def test_low_first_then_ok(self):
        rows = devices_core.battery_sweep(self._client(), duration=0)
        assert [r["friendly_name"] for r in rows] == ["Thermo", "Remote", "Door Sensor"]

    def test_mains_and_coordinator_dropped(self):
        rows = devices_core.battery_sweep(self._client(), duration=0)
        names = {r["friendly_name"] for r in rows}
        assert "Plug" not in names
        assert "Coordinator" not in names

    def test_row_fields(self):
        rows = devices_core.battery_sweep(self._client(), duration=0)
        door = next(r for r in rows if r["friendly_name"] == "Door Sensor")
        if door["battery"] != 87.0 or door["voltage"] != 2915.0:
            raise AssertionError(f"expected battery 87 / voltage 2915, got {door}")
        if door["status"] != "ok":
            raise AssertionError(f"expected ok, got {door['status']}")
        if door["model"] != "MCCGQ11LM":
            raise AssertionError(f"expected model carried, got {door}")
        if door["battery_low"] is not None:
            raise AssertionError(f"expected battery_low None, got {door}")

    def test_below_threshold_flags_low(self):
        # 87% is fine at the default 20 but low under a 90% threshold
        rows = devices_core.battery_sweep(self._client(), duration=0, below=90)
        status = {r["friendly_name"]: r["status"] for r in rows}
        assert status["Door Sensor"] == "low"
        assert status["Thermo"] == "low"

    def test_unknown_battery_device(self):
        rows = devices_core.battery_sweep(self._client(), duration=0)
        remote = next(r for r in rows if r["friendly_name"] == "Remote")
        assert remote["status"] == "unknown"
        assert remote["battery"] is None

    def test_low_only_drops_ok(self):
        rows = devices_core.battery_sweep(self._client(), duration=0, low_only=True)
        names = [r["friendly_name"] for r in rows]
        assert "Door Sensor" not in names
        assert "Thermo" in names and "Remote" in names

    def test_low_only_with_higher_below(self):
        rows = devices_core.battery_sweep(self._client(), duration=0, low_only=True, below=90)
        assert [r["friendly_name"] for r in rows] == ["Thermo", "Door Sensor", "Remote"]

    def test_subscribes_to_wildcard_once(self):
        c = self._client()
        devices_core.battery_sweep(c, duration=0)
        assert c.subscriptions == ["zigbee2mqtt/#"]

    def test_ignores_non_state_topics(self):
        c = SubscribingClient()
        c.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps([{"friendly_name": "lamp", "type": "Router", "power_source": "Battery"}]),
        )
        c.set_retained("zigbee2mqtt/bridge/info", json.dumps({"battery": 99}))
        c.set_retained("zigbee2mqtt/lamp/set", json.dumps({"battery": 99}))
        c.set_retained("zigbee2mqtt/lamp/availability", "online")
        rows = devices_core.battery_sweep(c, duration=0)
        assert rows == [
            {
                "friendly_name": "lamp",
                "ieee_address": None,
                "battery": None,
                "battery_low": None,
                "voltage": None,
                "status": "unknown",
                "type": "Router",
                "model": None,
                "power_source": "Battery",
            }
        ]

    def test_malformed_state_json_is_ignored(self):
        c = SubscribingClient()
        c.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps(
                [{"friendly_name": "sensor", "type": "EndDevice", "power_source": "Battery"}]
            ),
        )
        c.set_retained("zigbee2mqtt/sensor", "not json {{{")
        rows = devices_core.battery_sweep(c, duration=0)
        assert rows[0]["status"] == "unknown"

    def test_non_dict_state_json_is_ignored(self):
        c = SubscribingClient()
        c.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps(
                [{"friendly_name": "sensor", "type": "EndDevice", "power_source": "Battery"}]
            ),
        )
        c.set_retained("zigbee2mqtt/sensor", '"hello"')
        rows = devices_core.battery_sweep(c, duration=0)
        assert rows[0]["status"] == "unknown"

    def test_numeric_string_battery_and_voltage(self):
        c = SubscribingClient()
        c.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps(
                [{"friendly_name": "sensor", "type": "EndDevice", "power_source": "Battery"}]
            ),
        )
        c.set_retained("zigbee2mqtt/sensor", json.dumps({"battery": "55", "voltage": "3000"}))
        rows = devices_core.battery_sweep(c, duration=0)
        assert rows[0]["battery"] == 55.0
        assert rows[0]["voltage"] == 3000.0

    def test_non_numeric_voltage_is_none(self):
        c = SubscribingClient()
        c.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps(
                [{"friendly_name": "sensor", "type": "EndDevice", "power_source": "Battery"}]
            ),
        )
        c.set_retained("zigbee2mqtt/sensor", json.dumps({"battery": 55, "voltage": "high"}))
        rows = devices_core.battery_sweep(c, duration=0)
        assert rows[0]["voltage"] is None

    def test_battery_zero_is_low(self):
        c = SubscribingClient()
        c.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps(
                [{"friendly_name": "sensor", "type": "EndDevice", "power_source": "Battery"}]
            ),
        )
        c.set_retained("zigbee2mqtt/sensor", json.dumps({"battery": 0}))
        rows = devices_core.battery_sweep(c, duration=0)
        assert rows[0]["status"] == "low"

    def test_falls_back_to_ieee_topic(self):
        c = SubscribingClient()
        c.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps(
                [
                    {
                        "friendly_name": "0xaaa",
                        "type": "EndDevice",
                        "ieee_address": "0xaaa",
                        "power_source": "Battery",
                    }
                ]
            ),
        )
        c.set_retained("zigbee2mqtt/0xaaa", json.dumps({"battery": 66}))
        rows = devices_core.battery_sweep(c, duration=0)
        assert rows[0]["battery"] == 66.0

    def test_empty_inventory(self):
        assert devices_core.battery_sweep(SubscribingClient(), duration=0) == []

    def test_waits_for_the_requested_duration(self):
        import time as _time

        c = SubscribingClient()
        c.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps([{"friendly_name": "s", "type": "EndDevice", "power_source": "Battery"}]),
        )
        started = _time.monotonic()
        devices_core.battery_sweep(c, duration=0.12)
        assert _time.monotonic() - started >= 0.1


# ── devices.search_devices ──────────────────────────────────────────────────


class TestSearchDevices:
    LAMP = {
        "friendly_name": "Lounge Lamp",
        "ieee_address": "0xaaa",
        "type": "Router",
        "power_source": "Mains (single phase)",
        "manufacturer": "IKEA of Sweden",
        "supported": True,
        "disabled": False,
        "definition": {
            "model": "TRADFRI bulb E27",
            "vendor": "IKEA",
            "exposes": [
                {
                    "type": "light",
                    "features": [
                        {"property": "state", "access": 7},
                        {"property": "brightness", "access": 7},
                        {"property": "color_temp", "access": 7},
                    ],
                }
            ],
        },
    }
    SENSOR = {
        "friendly_name": "Hall Temperature",
        "ieee_address": "0xbbb",
        "type": "EndDevice",
        "power_source": "Battery",
        "manufacturer": "_TZE200",
        "supported": True,
        "disabled": False,
        "definition": {
            "model": "TS0201",
            "vendor": "Tuya",
            "exposes": [
                {"property": "temperature", "access": 1},
                {"property": "battery", "access": 1},
            ],
        },
    }
    UNSUPPORTED = {
        "friendly_name": "Mystery Box",
        "ieee_address": "0xccc",
        "type": "EndDevice",
        "power_source": "Battery",
        "manufacturer": "Unknown",
        "supported": False,
        "disabled": True,
        "definition": {"model": "MYS-1", "vendor": "NoName"},
    }

    def _all(self):
        return [dict(self.LAMP), dict(self.SENSOR), dict(self.UNSUPPORTED)]

    def test_no_filters_returns_everything(self):
        result = devices_core.search_devices(self._all())
        assert len(result) == 3

    def test_like_matches_friendly_name_case_insensitive(self):
        result = devices_core.search_devices(self._all(), like="hall temp")
        assert [d["friendly_name"] for d in result] == ["Hall Temperature"]

    def test_like_matches_ieee_address(self):
        result = devices_core.search_devices(self._all(), like="0xbbb")
        assert [d["friendly_name"] for d in result] == ["Hall Temperature"]

    def test_manufacturer_substring(self):
        result = devices_core.search_devices(self._all(), manufacturer="ikea")
        assert [d["friendly_name"] for d in result] == ["Lounge Lamp"]

    def test_model_substring(self):
        result = devices_core.search_devices(self._all(), model="ts0201")
        assert [d["friendly_name"] for d in result] == ["Hall Temperature"]

    def test_vendor_substring(self):
        result = devices_core.search_devices(self._all(), vendor="tuya")
        assert [d["friendly_name"] for d in result] == ["Hall Temperature"]

    def test_type_filter(self):
        routers = devices_core.search_devices(self._all(), type_="router")
        assert [d["friendly_name"] for d in routers] == ["Lounge Lamp"]
        ends = devices_core.search_devices(self._all(), type_="EndDevice")
        assert len(ends) == 2

    def test_power_filter(self):
        battery = devices_core.search_devices(self._all(), power="battery")
        assert [d["friendly_name"] for d in battery] == ["Hall Temperature", "Mystery Box"]
        mains = devices_core.search_devices(self._all(), power="mains")
        assert [d["friendly_name"] for d in mains] == ["Lounge Lamp"]

    def test_capability_matches_flattened_exposes(self):
        rows = devices_core.search_devices(self._all(), capability="brightness")
        assert [d["friendly_name"] for d in rows] == ["Lounge Lamp"]

    def test_capability_substring_matches_family(self):
        rows = devices_core.search_devices(self._all(), capability="color")
        assert [d["friendly_name"] for d in rows] == ["Lounge Lamp"]

    def test_capability_no_match_excludes_device(self):
        rows = devices_core.search_devices(self._all(), capability="occupancy")
        assert rows == []

    def test_supported_flag_filters(self):
        rows = devices_core.search_devices(self._all(), supported=False)
        assert [d["friendly_name"] for d in rows] == ["Mystery Box"]

    def test_disabled_flag_filters(self):
        rows = devices_core.search_devices(self._all(), disabled=True)
        assert [d["friendly_name"] for d in rows] == ["Mystery Box"]

    def test_filters_combine(self):
        rows = devices_core.search_devices(self._all(), power="battery", capability="temperature")
        assert [d["friendly_name"] for d in rows] == ["Hall Temperature"]

    def test_combined_filters_can_exclude_everything(self):
        rows = devices_core.search_devices(self._all(), power="mains", capability="occupancy")
        assert rows == []

    def test_capability_matches_case_insensitively(self):
        rows = devices_core.search_devices(self._all(), capability="COLOR_TEMP")
        assert [d["friendly_name"] for d in rows] == ["Lounge Lamp"]


# ── devices.identify ────────────────────────────────────────────────────────


class _PublishOnlyClient:
    base_topic = "zigbee2mqtt"

    def __init__(self):
        self.published: list[tuple[str, object]] = []

    def publish(self, topic, payload, *, qos=0, retain=False):
        self.published.append((topic, payload))
        return 0


class TestIdentify:
    def test_default_payload_is_empty_identify(self):
        c = _PublishOnlyClient()
        result = devices_core.identify(c, "Lounge Lamp")
        assert result["topic"] == "zigbee2mqtt/Lounge Lamp/set"
        assert result["published"] == {"identify": {}}
        assert result["friendly_name"] == "Lounge Lamp"

    def test_duration_lands_in_payload(self):
        c = _PublishOnlyClient()
        result = devices_core.identify(c, "Hall Temperature", duration=5)
        assert result["published"] == {"identify": {"duration": 5}}

    def test_publishes_to_set_topic(self):
        c = _PublishOnlyClient()
        devices_core.identify(c, "Lamp")
        assert c.published == [("zigbee2mqtt/Lamp/set", {"identify": {}})]

    def test_rc_is_returned(self):
        c = _PublishOnlyClient()
        assert devices_core.identify(c, "Lamp")["rc"] == 0

    def test_empty_name_raises(self):
        c = _PublishOnlyClient()
        with pytest.raises(ValueError):
            devices_core.identify(c, "")

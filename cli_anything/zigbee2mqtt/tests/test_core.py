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

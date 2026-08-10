"""E2E / workflow tests — exercise the CLI layer end-to-end via CliRunner.

These tests use CliRunner so no real MQTT broker or kubectl is needed.
Each test verifies a full command invocation parses, dispatches to the right
core function, and produces correct output or error for the happy path and
the key error path.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

# We use the CLI as the entry point
from click.testing import CliRunner

from cli_anything.zigbee2mqtt.zigbee2mqtt_cli import cli


# ── helpers ──────────────────────────────────────────────────────────────────


class FakeBridgeClient:
    """Minimal fake matching BridgeClient's public interface."""

    base_topic = "zigbee2mqtt"

    def __init__(self, **overrides):
        for k, v in overrides.items():
            setattr(self, k, v)
        self._retained: dict[str, str] = {}
        self._request_responses: dict[str, dict] = {}

    def set_retained(self, topic: str, payload: str) -> None:
        self._retained[topic] = payload

    def set_response(self, path: str, response: dict) -> None:
        self._request_responses[path] = response

    def collect_retained(self, topic: str, *, timeout: float = 5.0) -> str | None:
        return self._retained.get(topic)

    def request(self, path: str, payload=None, *, timeout: float = 15.0) -> dict:
        return self._request_responses.get(path, {})

    def publish(self, topic: str, payload, *, qos: int = 0, retain: bool = False) -> int:
        return 0

    def subscribe(self, filter_: str, callback) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


@pytest.fixture
def fake_client():
    return FakeBridgeClient


def _runner():
    return CliRunner()


# ── root / config ─────────────────────────────────────────────────────────────


class TestRootHelpAndVersion:
    def test_help_shows_all_groups(self):
        r = _runner()
        result = r.invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert "bridge" in result.output
        assert "device" in result.output
        assert "group" in result.output
        assert "ota" in result.output
        assert "network" in result.output
        assert "converter" in result.output
        assert "extension" in result.output
        assert "config" in result.output
        assert "install-code" in result.output

    def test_json_flag_accepted(self):
        r = _runner()
        result = r.invoke(cli, ["--json", "--help"])
        assert result.exit_code == 0

    def test_mqtt_host_override(self):
        r = _runner()
        result = r.invoke(cli, ["--mqtt-host", "fake-host", "bridge", "info"])
        # No broker, so it will fail — but the option must be accepted without error
        # (the failure is later in the call chain)
        # We just verify the option was accepted (exit_code != 2 which means bad args)
        assert result.exit_code != 2 or "no MQTT" in result.output


class TestConfigCommands:
    @patch(
        "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
        lambda ctx: FakeBridgeClient(),
    )
    def test_config_show(self):
        r = _runner()
        result = r.invoke(cli, ["--mqtt-host", "x", "config", "show"])
        assert result.exit_code == 0, result.output
        # Password should be redacted or absent (None values are safe)
        output_lower = result.output.lower()
        assert "mqtt_password" not in output_lower or "***" in output_lower or "none" in output_lower

    @patch(
        "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
        lambda ctx: FakeBridgeClient(),
    )
    def test_config_show_json(self):
        r = _runner()
        result = r.invoke(
            cli,
            ["--mqtt-host", "x", "--json", "config", "show"],
        )
        assert result.exit_code == 0
        # Must be valid JSON (or at least not crash)
        data = json.loads(result.output)
        assert isinstance(data, dict)


# ── bridge group ──────────────────────────────────────────────────────────────


@patch(
    "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
    lambda ctx: FakeBridgeClient(),
)
class TestBridgeCommands:
    def test_bridge_info(self, fake_client):
        client = FakeBridgeClient()
        client.set_retained("zigbee2mqtt/bridge/info", json.dumps({"version": "1.33.0"}))
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(cli, ["--mqtt-host", "x", "bridge", "info"])
            assert result.exit_code == 0, result.output
            assert "version" in result.output

    def test_bridge_info_json(self, fake_client):
        client = FakeBridgeClient()
        client.set_retained("zigbee2mqtt/bridge/info", json.dumps({"version": "1.33.0"}))
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli, ["--mqtt-host", "x", "--json", "bridge", "info"]
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["version"] == "1.33.0"

    def test_bridge_info_malformed_json(self, fake_client):
        client = FakeBridgeClient()
        client.set_retained("zigbee2mqtt/bridge/info", "not-json{{{")
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(cli, ["--mqtt-host", "x", "bridge", "info"])
            assert result.exit_code == 0  # graceful degradation

    def test_bridge_state_online(self, fake_client):
        client = FakeBridgeClient()
        client.set_retained("zigbee2mqtt/bridge/state", "online")
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(cli, ["--mqtt-host", "x", "bridge", "state"])
            assert result.exit_code == 0, result.output
            assert "online" in result.output

    def test_bridge_state_json_object(self, fake_client):
        """bridge/state can be a JSON object like {"state":"online"}."""
        client = FakeBridgeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/state", json.dumps({"state": "online"})
        )
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(cli, ["--mqtt-host", "x", "bridge", "state"])
            assert result.exit_code == 0, result.output

    def test_bridge_restart(self, fake_client):
        client = FakeBridgeClient()
        client.set_response(
            "restart",
            {"message": " restarting", "status": "ok"},
        )
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(cli, ["--mqtt-host", "x", "bridge", "restart"])
            assert result.exit_code == 0, result.output

    def test_bridge_health(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("health_check", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(cli, ["--mqtt-host", "x", "bridge", "health"])
            assert result.exit_code == 0, result.output

    def test_bridge_options_get(self, fake_client):
        client = FakeBridgeClient()
        client.set_response(
            "options", {"options": {"permit_join": True}}
        )
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(cli, ["--mqtt-host", "x", "bridge", "options-get"])
            assert result.exit_code == 0, result.output

    def test_bridge_options_set(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("options", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "bridge", "options-set",
                    '{"permit_join":false}'
                ],
            )
            assert result.exit_code == 0, result.output

    def test_bridge_options_set_invalid_json(self, fake_client):
        r = _runner()
        result = r.invoke(
            cli,
            [
                "--mqtt-host", "x", "bridge", "options-set",
                "not-valid-json"
            ],
        )
        assert result.exit_code != 0
        assert "JSON" in result.output or "decode" in result.output.lower()

    def test_bridge_watch_events(self, fake_client):
        client = FakeBridgeClient()
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                ["--mqtt-host", "x", "bridge", "watch-events", "--duration", "0.1"],
            )
            assert result.exit_code == 0, result.output

    def test_bridge_watch_logging(self, fake_client):
        client = FakeBridgeClient()
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                ["--mqtt-host", "x", "bridge", "watch-logging", "--duration", "0.1"],
            )
            assert result.exit_code == 0, result.output


# ── device group ─────────────────────────────────────────────────────────────


@patch(
    "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
    lambda ctx: FakeBridgeClient(),
)
class TestDeviceCommands:
    def test_device_list(self, fake_client):
        client = FakeBridgeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps([]),
        )
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(cli, ["--mqtt-host", "x", "device", "list"])
            assert result.exit_code == 0, result.output

    def test_device_list_full(self, fake_client):
        client = FakeBridgeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps([]),
        )
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli, ["--mqtt-host", "x", "device", "list", "--full"]
            )
            assert result.exit_code == 0

    def test_device_show_not_found(self, fake_client):
        client = FakeBridgeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps([]),
        )
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli, ["--mqtt-host", "x", "device", "show", "nonexistent"]
            )
            assert result.exit_code != 0
            assert "not found" in result.output.lower() or "no device" in result.output.lower()

    def test_device_show_json(self, fake_client):
        client = FakeBridgeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps([
                {
                    "friendly_name": "lamp1",
                    "ieee_address": "0x1234",
                    "definition": {"model": "Lamp"},
                }
            ]),
        )
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli, ["--mqtt-host", "x", "--json", "device", "show", "lamp1"]
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["friendly_name"] == "lamp1"

    def test_device_remove(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("device/remove", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "device", "remove",
                    "ghost_device", "--force",
                    "--block",
                ],
                input="y\n",
            )
            assert result.exit_code == 0, result.output

    def test_device_remove_aborted(self, fake_client):
        client = FakeBridgeClient()
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                ["--mqtt-host", "x", "device", "remove", "ghost_device"],
                input="n\n",
            )
            assert result.exit_code == 1

    def test_device_rename(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("device/rename", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "device", "rename",
                    "old_name", "new_name", "--no-ha-rename",
                ],
            )
            assert result.exit_code == 0, result.output

    def test_device_interview(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("device/interview", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli, ["--mqtt-host", "x", "device", "interview", "sensor1"]
            )
            assert result.exit_code == 0, result.output

    def test_device_configure(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("device/configure", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli, ["--mqtt-host", "x", "device", "configure", "sensor1"]
            )
            assert result.exit_code == 0, result.output

    def test_device_options(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("device/options", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "device", "options",
                    "sensor1", '{"debounce":0.5}',
                ],
            )
            assert result.exit_code == 0, result.output

    def test_device_options_invalid_json(self, fake_client):
        r = _runner()
        result = r.invoke(
            cli,
            ["--mqtt-host", "x", "device", "options", "sensor1", "not-json"],
        )
        assert result.exit_code != 0

    def test_device_set(self, fake_client):
        client = FakeBridgeClient()
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "device", "set",
                    "lamp1", "state=ON", "brightness=200",
                ],
            )
            assert result.exit_code == 0, result.output

    def test_device_get(self, fake_client):
        client = FakeBridgeClient()
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                ["--mqtt-host", "x", "device", "get", "lamp1",
                 "state", "brightness"],
            )
            assert result.exit_code == 0, result.output

    def test_device_watch(self, fake_client):
        client = FakeBridgeClient()
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "device", "watch",
                    "lamp1", "--duration", "0.1",
                ],
            )
            assert result.exit_code == 0, result.output

    def test_device_state(self, fake_client):
        client = FakeBridgeClient()
        client.set_retained(
            "zigbee2mqtt/lamp1",
            json.dumps({"state": "ON", "brightness": 200}),
        )
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli, ["--mqtt-host", "x", "--json", "device", "state", "lamp1"]
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["state"] == "ON"

    def test_device_state_not_found(self, fake_client):
        client = FakeBridgeClient()
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli, ["--mqtt-host", "x", "device", "state", "ghost"]
            )
            # empty state → graceful, no crash
            assert result.exit_code == 0, result.output

    def test_device_stale(self, fake_client):
        client = FakeBridgeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps([]),
        )
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "--json", "device", "stale",
                    "--threshold", "60", "--no-routers", "--no-end-devices",
                ],
            )
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert isinstance(data, list)

    def test_device_generate_converter(self, fake_client):
        client = FakeBridgeClient()
        client.set_response(
            "device/generate_external_definition",
            {"source": "// generated", "status": "ok"},
        )
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            with _runner().isolated_filesystem():
                open("out.js", "w").close()  # create empty file
                r = _runner()
                result = r.invoke(
                    cli,
                    [
                        "--mqtt-host", "x", "device", "generate-converter",
                        "sensor1", "-o", "out.js", "--overwrite",
                    ],
                )
                assert result.exit_code == 0, result.output

    def test_device_generate_converter_overwrite(self, fake_client):
        client = FakeBridgeClient()
        client.set_response(
            "device/generate_external_definition",
            {"source": "// generated", "status": "ok"},
        )
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            with _runner().isolated_filesystem():
                open("out.js", "w").close()  # create existing file
                r = _runner()
                result = r.invoke(
                    cli,
                    [
                        "--mqtt-host", "x", "device", "generate-converter",
                        "sensor1", "-o", "out.js", "--overwrite",
                    ],
                )
                assert result.exit_code == 0, result.output

    def test_device_generate_converter_no_overwrite(self, fake_client):
        client = FakeBridgeClient()
        client.set_response(
            "device/generate_external_definition",
            {"source": "// generated", "status": "ok"},
        )
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            with _runner().isolated_filesystem():
                open("out.js", "w").close()  # create existing file
                r = _runner()
                result = r.invoke(
                    cli,
                    [
                        "--mqtt-host", "x", "device", "generate-converter",
                        "sensor1", "-o", "out.js",
                    ],
                )
                assert result.exit_code != 0
                assert "already exists" in result.output

    def test_device_bind(self, fake_client):
        client = FakeBridgeClient()
        client.set_response(
            "device/bind",
            {"status": "ok"},
        )
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "device", "bind",
                    "switch1", "lamp1", "--cluster", "genOnOff",
                ],
            )
            assert result.exit_code == 0, result.output

    def test_device_bind_no_cluster(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("device/bind", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                ["--mqtt-host", "x", "device", "bind", "switch1", "lamp1"],
            )
            assert result.exit_code == 0, result.output

    def test_device_unbind(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("device/unbind", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "device", "unbind",
                    "switch1", "lamp1", "--cluster", "genOnOff",
                ],
            )
            assert result.exit_code == 0, result.output

    def test_device_bindings(self, fake_client):
        client = FakeBridgeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps([]),
        )
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli, ["--mqtt-host", "x", "--json", "device", "bindings"]
            )
            assert result.exit_code == 0

    def test_device_bindings_with_ident(self, fake_client):
        client = FakeBridgeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps([]),
        )
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "--json", "device", "bindings",
                    "switch1",
                ],
            )
            assert result.exit_code == 0


# ── group ─────────────────────────────────────────────────────────────────────


@patch(
    "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
    lambda ctx: FakeBridgeClient(),
)
class TestGroupCommands:
    def test_group_list(self, fake_client):
        client = FakeBridgeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/groups",
            json.dumps([]),
        )
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(cli, ["--mqtt-host", "x", "group", "list"])
            assert result.exit_code == 0, result.output

    def test_group_add(self, fake_client):
        client = FakeBridgeClient()
        client.set_response(
            "group/add",
            {"friendly_name": "kitchen", "id": 11},
        )
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                ["--mqtt-host", "x", "group", "add", "kitchen"],
            )
            assert result.exit_code == 0, result.output

    def test_group_add_with_id(self, fake_client):
        client = FakeBridgeClient()
        client.set_response(
            "group/add",
            {"friendly_name": "kitchen", "id": 5},
        )
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "group", "add",
                    "kitchen", "--id", "5",
                ],
            )
            assert result.exit_code == 0, result.output

    def test_group_remove(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("group/remove", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                ["--mqtt-host", "x", "group", "remove", "kitchen", "--force"],
                input="y\n",
            )
            assert result.exit_code == 0, result.output

    def test_group_remove_aborted(self, fake_client):
        client = FakeBridgeClient()
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                ["--mqtt-host", "x", "group", "remove", "kitchen"],
                input="n\n",
            )
            assert result.exit_code == 1

    def test_group_rename(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("group/rename", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "group", "rename",
                    "old_group", "new_group",
                ],
            )
            assert result.exit_code == 0, result.output

    def test_group_add_member(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("group/add", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "group", "add-member",
                    "kitchen", "lamp1",
                ],
            )
            assert result.exit_code == 0, result.output

    def test_group_remove_member(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("group/remove", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "group", "remove-member",
                    "kitchen", "lamp1",
                ],
            )
            assert result.exit_code == 0, result.output

    def test_group_remove_all_members(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("group/remove_all", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [ "--mqtt-host", "x", "group", "remove-all", "kitchen", "--yes" ],
            )
            assert result.exit_code == 0, result.output

    def test_group_options(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("group/options", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "group", "options",
                    "kitchen", '{"transition":1.5}',
                ],
            )
            assert result.exit_code == 0, result.output


# ── ota ──────────────────────────────────────────────────────────────────────


@patch(
    "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
    lambda ctx: FakeBridgeClient(),
)
class TestOtaCommands:
    def test_ota_check(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("ota/check", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli, ["--mqtt-host", "x", "ota", "check", "sensor1"]
            )
            assert result.exit_code == 0, result.output

    def test_ota_update(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("ota/update", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli, [ "--mqtt-host", "x", "ota", "update", "sensor1", "--yes" ]
            )
            assert result.exit_code == 0, result.output

    def test_ota_schedule(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("ota/update", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli, ["--mqtt-host", "x", "ota", "schedule", "sensor1"]
            )
            assert result.exit_code == 0, result.output


# ── network ───────────────────────────────────────────────────────────────────


@patch(
    "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
    lambda ctx: FakeBridgeClient(),
)
class TestNetworkCommands:
    def test_network_permit_join_on(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("permit_join", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                ["--mqtt-host", "x", "network", "permit-join", "on"],
            )
            assert result.exit_code == 0, result.output

    def test_network_permit_join_off(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("permit_join", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                ["--mqtt-host", "x", "network", "permit-join", "off"],
            )
            assert result.exit_code == 0, result.output

    def test_network_permit_join_via_device(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("permit_join", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "network", "permit-join", "on",
                    "--time", "60", "--device", "router_lamp",
                ],
            )
            assert result.exit_code == 0, result.output

    def test_network_map_raw(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("networkmap", {"type": "raw", "data": []})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                ["--mqtt-host", "x", "--json", "network", "map"],
            )
            assert result.exit_code == 0

    def test_network_map_graphviz(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("networkmap", {"type": "graphviz"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "network", "map",
                    "--type", "graphviz",
                ],
            )
            assert result.exit_code == 0, result.output

    def test_network_map_plantuml(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("networkmap", {"type": "plantuml"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "network", "map",
                    "--type", "plantuml",
                ],
            )
            assert result.exit_code == 0, result.output

    def test_network_map_invalid_type(self, fake_client):
        r = _runner()
        result = r.invoke(
            cli,
            ["--mqtt-host", "x", "network", "map", "--type", "invalid"],
        )
        assert result.exit_code != 0

    def test_network_touchlink_scan(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("touchlink/scan", {"devices": []})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli, ["--mqtt-host", "x", "network", "touchlink-scan"]
            )
            assert result.exit_code == 0, result.output

    def test_network_touchlink_identify(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("touchlink/identify", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "network", "touchlink-identify",
                    "0x1234567890abcdef", "11",
                ],
            )
            assert result.exit_code == 0, result.output

    def test_network_touchlink_reset(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("touchlink/factory_reset", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "network", "touchlink-reset",
                    "--ieee", "0x1234567890abcdef", "--channel", "11", "--yes",
                ],
            )
            assert result.exit_code == 0, result.output

    def test_network_coordinator_check(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("coordinator_check", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli, ["--mqtt-host", "x", "network", "coordinator-check"]
            )
            assert result.exit_code == 0, result.output

    def test_network_backup(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("backup", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli, ["--mqtt-host", "x", "network", "backup"]
            )
            assert result.exit_code == 0, result.output


# ── converter (k8s) ──────────────────────────────────────────────────────────


class TestConverterCommands:
    def test_converter_list_k8s(self):
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_k8s_target",
            lambda ctx: MagicMock(),
        ):
            with patch(
                "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.converters_core.list_converters",
                return_value=[],
            ) as mock_list:
                r = _runner()
                result = r.invoke(
                    cli,
                    [
                        "--k8s-namespace", "z2m", "--k8s-deployment", "z2m",
                        "--k8s-container", "z2m", "--k8s-data-path", "/app/data",
                        "converter", "list",
                    ],
                )
                assert result.exit_code == 0, result.output
                mock_list.assert_called_once()

    def test_converter_show(self):
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_k8s_target",
            lambda ctx: MagicMock(),
        ):
            with patch(
                "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.converters_core.show",
                return_value="// code",
            ) as mock_show:
                r = _runner()
                result = r.invoke(
                    cli,
                    [
                        "--k8s-namespace", "z2m", "--k8s-deployment", "z2m",
                        "--k8s-container", "z2m", "--k8s-data-path", "/app/data",
                        "converter", "show", "myconv.js",
                    ],
                )
                assert result.exit_code == 0, result.output
                mock_show.assert_called_once()

    def test_converter_add(self):
        with _runner().isolated_filesystem():
            open("myconv.js", "w").write("// test")
            with patch(
                "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_k8s_target",
                lambda ctx: MagicMock(),
            ):
                with patch(
                    "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.converters_core.add_from_file",
                    return_value={"status": "ok"},
                ) as mock_add:
                    r = _runner()
                    result = r.invoke(
                        cli,
                        [
                            "--k8s-namespace", "z2m", "--k8s-deployment", "z2m",
                            "--k8s-container", "z2m", "--k8s-data-path", "/app/data",
                            "converter", "add", "myconv", "myconv.js",
                        ],
                    )
                    assert result.exit_code == 0, result.output
                    mock_add.assert_called_once()

    def test_converter_remove(self):
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_k8s_target",
            lambda ctx: MagicMock(),
        ):
            with patch(
                "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.converters_core.remove",
                return_value={"status": "ok"},
            ) as mock_rm:
                r = _runner()
                result = r.invoke(
                    cli,
                    [
                        "--k8s-namespace", "z2m", "--k8s-deployment", "z2m",
                        "--k8s-container", "z2m", "--k8s-data-path", "/app/data",
                        "converter", "remove", "myconv.js",
                    ],
                    input="y\n",
                )
                assert result.exit_code == 0, result.output
                mock_rm.assert_called_once()


# ── extension ─────────────────────────────────────────────────────────────────


@patch(
    "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
    lambda ctx: FakeBridgeClient(),
)
class TestExtensionCommands:
    def test_extension_list(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("extension/list", {"extensions": []})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli, ["--mqtt-host", "x", "extension", "list"]
            )
            assert result.exit_code == 0, result.output

    def test_extension_show(self, fake_client):
        client = FakeBridgeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/extensions",
            '[{"name": "ext1", "code": "// code"}]',
        )
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli, ["--mqtt-host", "x", "extension", "show", "ext1"]
            )
            assert result.exit_code == 0, result.output

    def test_extension_save(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("extension/save", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            with _runner().isolated_filesystem():
                open("ext.js", "w").write("// ext")
                r = _runner()
                result = r.invoke(
                    cli,
                    ["--mqtt-host", "x", "extension", "save", "ext.js", "ext.js"],
                )
                assert result.exit_code == 0, result.output

    def test_extension_remove(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("extension/remove", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                ["--mqtt-host", "x", "extension", "remove", "ext1", "--yes"],
            )
            assert result.exit_code == 0, result.output


# ── install-code ──────────────────────────────────────────────────────────────


@patch(
    "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
    lambda ctx: FakeBridgeClient(),
)
class TestInstallCodeCommands:
    def test_install_code_add(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("install_code/add", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "install-code", "add",
                    "1234-567890-ABCDEF",
                ],
            )
            assert result.exit_code == 0, result.output

    def test_install_code_remove(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("install_code/remove", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(
                cli,
                [
                    "--mqtt-host", "x", "install-code", "remove",
                    "1234-567890-ABCDEF", "--yes",
                ],
            )
            assert result.exit_code == 0, result.output

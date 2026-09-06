"""E2E / workflow tests — exercise the CLI layer end-to-end via CliRunner.

These tests use CliRunner so no real MQTT broker or kubectl is needed.
Each test verifies a full command invocation parses, dispatches to the right
core function, and produces correct output or error for the happy path and
the key error path.
"""

from __future__ import annotations

import json
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
        # Fire-and-forget publishes (device/group set, scene commands) have no
        # bridge/response, so tests assert on what reached the wire.
        self.published: list[tuple[str, object]] = []

    def set_retained(self, topic: str, payload: str) -> None:
        self._retained[topic] = payload

    def set_response(self, path: str, response: dict) -> None:
        self._request_responses[path] = response

    def collect_retained(self, topic: str, *, timeout: float = 5.0) -> str | None:
        return self._retained.get(topic)

    def request(self, path: str, payload=None, *, timeout: float = 15.0) -> dict:
        return self._request_responses.get(path, {})

    def publish(self, topic: str, payload, *, qos: int = 0, retain: bool = False) -> int:
        self.published.append((topic, payload))
        return 0

    @property
    def last_published(self) -> tuple[str, object]:
        return self.published[-1]

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
        assert (
            "mqtt_password" not in output_lower or "***" in output_lower or "none" in output_lower
        )

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
            result = r.invoke(cli, ["--mqtt-host", "x", "--json", "bridge", "info"])
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
        client.set_retained("zigbee2mqtt/bridge/state", json.dumps({"state": "online"}))
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
        client.set_response("options", {"options": {"permit_join": True}})
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
                ["--mqtt-host", "x", "bridge", "options-set", '{"permit_join":false}'],
            )
            assert result.exit_code == 0, result.output

    def test_bridge_options_set_invalid_json(self, fake_client):
        r = _runner()
        result = r.invoke(
            cli,
            ["--mqtt-host", "x", "bridge", "options-set", "not-valid-json"],
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
            result = r.invoke(cli, ["--mqtt-host", "x", "device", "list", "--full"])
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
            result = r.invoke(cli, ["--mqtt-host", "x", "device", "show", "nonexistent"])
            assert result.exit_code != 0
            assert "not found" in result.output.lower() or "no device" in result.output.lower()

    def test_device_show_json(self, fake_client):
        client = FakeBridgeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps(
                [
                    {
                        "friendly_name": "lamp1",
                        "ieee_address": "0x1234",
                        "definition": {"model": "Lamp"},
                    }
                ]
            ),
        )
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(cli, ["--mqtt-host", "x", "--json", "device", "show", "lamp1"])
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
                    "--mqtt-host",
                    "x",
                    "device",
                    "remove",
                    "ghost_device",
                    "--force",
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
                    "--mqtt-host",
                    "x",
                    "device",
                    "rename",
                    "old_name",
                    "new_name",
                    "--no-ha-rename",
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
            result = r.invoke(cli, ["--mqtt-host", "x", "device", "interview", "sensor1"])
            assert result.exit_code == 0, result.output

    def test_device_configure(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("device/configure", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(cli, ["--mqtt-host", "x", "device", "configure", "sensor1"])
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
                    "--mqtt-host",
                    "x",
                    "device",
                    "options",
                    "sensor1",
                    '{"debounce":0.5}',
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
                    "--mqtt-host",
                    "x",
                    "device",
                    "set",
                    "lamp1",
                    "state=ON",
                    "brightness=200",
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
                ["--mqtt-host", "x", "device", "get", "lamp1", "state", "brightness"],
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
                    "--mqtt-host",
                    "x",
                    "device",
                    "watch",
                    "lamp1",
                    "--duration",
                    "0.1",
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
            result = r.invoke(cli, ["--mqtt-host", "x", "--json", "device", "state", "lamp1"])
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
            result = r.invoke(cli, ["--mqtt-host", "x", "device", "state", "ghost"])
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
                    "--mqtt-host",
                    "x",
                    "--json",
                    "device",
                    "stale",
                    "--threshold",
                    "60",
                    "--no-routers",
                    "--no-end-devices",
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
                        "--mqtt-host",
                        "x",
                        "device",
                        "generate-converter",
                        "sensor1",
                        "-o",
                        "out.js",
                        "--overwrite",
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
                        "--mqtt-host",
                        "x",
                        "device",
                        "generate-converter",
                        "sensor1",
                        "-o",
                        "out.js",
                        "--overwrite",
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
                        "--mqtt-host",
                        "x",
                        "device",
                        "generate-converter",
                        "sensor1",
                        "-o",
                        "out.js",
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
                    "--mqtt-host",
                    "x",
                    "device",
                    "bind",
                    "switch1",
                    "lamp1",
                    "--cluster",
                    "genOnOff",
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
                    "--mqtt-host",
                    "x",
                    "device",
                    "unbind",
                    "switch1",
                    "lamp1",
                    "--cluster",
                    "genOnOff",
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
            result = r.invoke(cli, ["--mqtt-host", "x", "--json", "device", "bindings"])
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
                    "--mqtt-host",
                    "x",
                    "--json",
                    "device",
                    "bindings",
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
                    "--mqtt-host",
                    "x",
                    "group",
                    "add",
                    "kitchen",
                    "--id",
                    "5",
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
                    "--mqtt-host",
                    "x",
                    "group",
                    "rename",
                    "old_group",
                    "new_group",
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
                    "--mqtt-host",
                    "x",
                    "group",
                    "add-member",
                    "kitchen",
                    "lamp1",
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
                    "--mqtt-host",
                    "x",
                    "group",
                    "remove-member",
                    "kitchen",
                    "lamp1",
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
                ["--mqtt-host", "x", "group", "remove-all", "kitchen", "--yes"],
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
                    "--mqtt-host",
                    "x",
                    "group",
                    "options",
                    "kitchen",
                    '{"transition":1.5}',
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
            result = r.invoke(cli, ["--mqtt-host", "x", "ota", "check", "sensor1"])
            assert result.exit_code == 0, result.output

    def test_ota_update(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("ota/update", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(cli, ["--mqtt-host", "x", "ota", "update", "sensor1", "--yes"])
            assert result.exit_code == 0, result.output

    def test_ota_schedule(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("ota/update", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(cli, ["--mqtt-host", "x", "ota", "schedule", "sensor1"])
            assert result.exit_code == 0, result.output


# ── ota check --all (network firmware sweep) ─────────────────────────────────


class _SweepClient(FakeBridgeClient):
    """Fake client whose ota check responses are keyed by device id."""

    def __init__(self, devices, per_device_responses=None):
        super().__init__()
        self.set_retained("zigbee2mqtt/bridge/devices", json.dumps(devices))
        self._per_device = per_device_responses or {}

    def request(self, path, payload=None, *, timeout=15.0):
        assert path == "device/ota_update/check"
        return self._per_device.get(
            payload["id"], {"status": "error", "error": "no canned response"}
        )


def _sweep_devices():
    return [
        {
            "friendly_name": "Coordinator",
            "ieee_address": "0xcoordinator",
            "type": "Coordinator",
            "disabled": False,
        },
        {
            "friendly_name": "lamp1",
            "ieee_address": "0xaaaa",
            "type": "Router",
            "disabled": False,
        },
        {
            "friendly_name": "sensor1",
            "ieee_address": "0xbbbb",
            "type": "EndDevice",
            "disabled": False,
        },
        {
            "friendly_name": "old_plug",
            "ieee_address": "0xcccc",
            "type": "Router",
            "disabled": True,
        },
    ]


def _sweep_responses():
    return {
        "0xaaaa": {"status": "ok", "data": {"update_available": True}},
        "0xbbbb": {"status": "ok", "data": {"update_available": False}},
        "0xcccc": {
            "status": "error",
            "error": "Device 'old_plug' does not support OTA updates",
        },
    }


class TestOtaCheckAllSweep:
    def _invoke(self, client, args):
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            return _runner().invoke(cli, ["--mqtt-host", "x", *args])

    def test_json_sweep(self):
        client = _SweepClient(_sweep_devices(), _sweep_responses())
        result = self._invoke(client, ["--json", "ota", "check", "--all"])
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        names = [r["friendly_name"] for r in rows]
        assert "Coordinator" not in names  # coordinator never swept
        assert "old_plug" not in names  # disabled devices filtered
        assert names == ["lamp1", "sensor1"]  # updates first
        by_name = {r["friendly_name"]: r for r in rows}
        assert by_name["lamp1"]["status"] == "update_available"
        assert by_name["sensor1"]["status"] == "up_to_date"

    def test_json_sweep_filters_disabled_by_default(self):
        client = _SweepClient(_sweep_devices(), _sweep_responses())
        result = self._invoke(client, ["--json", "ota", "check", "--all"])
        rows = json.loads(result.output)
        assert "old_plug" not in [r["friendly_name"] for r in rows]

    def test_json_sweep_include_disabled(self):
        client = _SweepClient(_sweep_devices(), _sweep_responses())
        result = self._invoke(client, ["--json", "ota", "check", "--all", "--include-disabled"])
        rows = json.loads(result.output)
        by_name = {r["friendly_name"]: r for r in rows}
        assert by_name["old_plug"]["status"] == "not_supported"

    def test_table_sweep_shows_summary_line(self):
        client = _SweepClient(_sweep_devices(), _sweep_responses())
        result = self._invoke(client, ["ota", "check", "--all", "--include-disabled"])
        assert result.exit_code == 0, result.output
        assert "lamp1" in result.output
        assert "checked 3: 1 update(s) available, 1 up to date, 1 not OTA-capable, 0 error(s)" in (
            result.output
        )

    def test_with_update_flag_filters_rows(self):
        client = _SweepClient(_sweep_devices(), _sweep_responses())
        result = self._invoke(client, ["--json", "ota", "check", "--all", "--with-update"])
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert [r["friendly_name"] for r in rows] == ["lamp1"]

    def test_with_update_flag_no_matches(self):
        devices = [
            {
                "friendly_name": "sensor1",
                "ieee_address": "0xbbbb",
                "type": "EndDevice",
                "disabled": False,
            }
        ]
        responses = {"0xbbbb": {"status": "ok", "data": {"update_available": False}}}
        client = _SweepClient(devices, responses)
        result = self._invoke(client, ["--json", "ota", "check", "--all", "--with-update"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == []

    def test_neither_name_nor_all_is_an_error(self):
        client = _SweepClient(_sweep_devices(), _sweep_responses())
        result = self._invoke(client, ["ota", "check"])
        assert result.exit_code != 0
        assert "give a device name or --all" in result.output

    def test_name_plus_all_is_rejected(self):
        client = _SweepClient(_sweep_devices(), _sweep_responses())
        result = self._invoke(client, ["ota", "check", "lamp1", "--all"])
        assert result.exit_code != 0
        assert "cannot combine" in result.output

    def test_single_device_check_still_works(self):
        client = FakeBridgeClient()
        client.set_response(
            "device/ota_update/check", {"status": "ok", "data": {"update_available": False}}
        )
        result = self._invoke(client, ["--json", "ota", "check", "lamp1", "--timeout", "12"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["data"]["update_available"] is False

    def test_workflow_sweep_then_schedule(self):
        """Sweep the network, then act on the device the sweep surfaced."""
        client = _SweepClient(_sweep_devices(), _sweep_responses())
        # patch the check path onto the shared FakeBridgeClient behaviour:
        # the sweep client needs to also answer device/ota_update/schedule
        client.set_response("device/ota_update/schedule", {"status": "ok"})

        def request(path, payload=None, *, timeout=15.0):
            if path == "device/ota_update/check":
                return _SweepClient.request(client, path, payload, timeout=timeout)
            return FakeBridgeClient.request(client, path, payload, timeout=timeout)

        client.request = request

        result = self._invoke(client, ["--json", "ota", "check", "--all", "--with-update"])
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        target = rows[0]["friendly_name"]
        assert target == "lamp1"

        result = self._invoke(client, ["--json", "ota", "schedule", target])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == {"status": "ok"}


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
                    "--mqtt-host",
                    "x",
                    "network",
                    "permit-join",
                    "on",
                    "--time",
                    "60",
                    "--device",
                    "router_lamp",
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
                    "--mqtt-host",
                    "x",
                    "network",
                    "map",
                    "--type",
                    "graphviz",
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
                    "--mqtt-host",
                    "x",
                    "network",
                    "map",
                    "--type",
                    "plantuml",
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
            result = r.invoke(cli, ["--mqtt-host", "x", "network", "touchlink-scan"])
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
                    "--mqtt-host",
                    "x",
                    "network",
                    "touchlink-identify",
                    "0x1234567890abcdef",
                    "11",
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
                    "--mqtt-host",
                    "x",
                    "network",
                    "touchlink-reset",
                    "--ieee",
                    "0x1234567890abcdef",
                    "--channel",
                    "11",
                    "--yes",
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
            result = r.invoke(cli, ["--mqtt-host", "x", "network", "coordinator-check"])
            assert result.exit_code == 0, result.output

    def test_network_backup(self, fake_client):
        client = FakeBridgeClient()
        client.set_response("backup", {"status": "ok"})
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            lambda ctx: client,
        ):
            r = _runner()
            result = r.invoke(cli, ["--mqtt-host", "x", "network", "backup"])
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
                        "--k8s-namespace",
                        "z2m",
                        "--k8s-deployment",
                        "z2m",
                        "--k8s-container",
                        "z2m",
                        "--k8s-data-path",
                        "/app/data",
                        "converter",
                        "list",
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
                        "--k8s-namespace",
                        "z2m",
                        "--k8s-deployment",
                        "z2m",
                        "--k8s-container",
                        "z2m",
                        "--k8s-data-path",
                        "/app/data",
                        "converter",
                        "show",
                        "myconv.js",
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
                            "--k8s-namespace",
                            "z2m",
                            "--k8s-deployment",
                            "z2m",
                            "--k8s-container",
                            "z2m",
                            "--k8s-data-path",
                            "/app/data",
                            "converter",
                            "add",
                            "myconv",
                            "myconv.js",
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
                        "--k8s-namespace",
                        "z2m",
                        "--k8s-deployment",
                        "z2m",
                        "--k8s-container",
                        "z2m",
                        "--k8s-data-path",
                        "/app/data",
                        "converter",
                        "remove",
                        "myconv.js",
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
            result = r.invoke(cli, ["--mqtt-host", "x", "extension", "list"])
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
            result = r.invoke(cli, ["--mqtt-host", "x", "extension", "show", "ext1"])
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
                    "--mqtt-host",
                    "x",
                    "install-code",
                    "add",
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
                    "--mqtt-host",
                    "x",
                    "install-code",
                    "remove",
                    "1234-567890-ABCDEF",
                    "--yes",
                ],
            )
            assert result.exit_code == 0, result.output


@patch("cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client")
@patch("cli_anything.zigbee2mqtt.core.bridge.info")
@patch("cli_anything.zigbee2mqtt.core.bridge.state")
def test_bridge_status(mock_state, mock_info, mock_make_client):
    mock_info.return_value = {"version": "1.35.0"}
    mock_state.return_value = "online"

    # Mock the client context manager
    mock_client = MagicMock()
    mock_make_client.return_value.__enter__.return_value = mock_client

    runner = _runner()
    result = runner.invoke(cli, ["--mqtt-host", "x", "bridge", "status"])
    assert result.exit_code == 0, result.output
    assert "version" in result.output
    assert "online" in result.output


@patch("cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client")
@patch("cli_anything.zigbee2mqtt.core.devices.show")
def test_device_ieee(mock_show, mock_make_client):
    mock_show.return_value = {"friendly_name": "Lamp", "ieee_address": "0x1234"}

    # Mock the client context manager
    mock_client = MagicMock()
    mock_make_client.return_value.__enter__.return_value = mock_client

    runner = _runner()
    result = runner.invoke(cli, ["--mqtt-host", "x", "device", "ieee", "Lamp"])
    assert result.exit_code == 0, result.output
    assert "0x1234" in result.output
    assert "Lamp" in result.output

    # Test not found
    mock_show.return_value = None
    result = runner.invoke(cli, ["--mqtt-host", "x", "device", "ieee", "Unknown"])
    assert result.exit_code != 0, result.output
    assert "not found" in result.output


# ── scenes ────────────────────────────────────────────────────────────────────


def _patch_client(client):
    """Patch make_client to hand back `client` for every command in the test."""
    return patch(
        "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
        lambda ctx: client,
    )


class TestSceneGroupInHelp:
    def test_root_help_lists_scene_group(self):
        result = _runner().invoke(cli, ["--help"])
        assert result.exit_code == 0, result.output
        assert "scene" in result.output

    def test_scene_help_lists_every_subcommand(self):
        result = _runner().invoke(cli, ["scene", "--help"])
        assert result.exit_code == 0, result.output
        for sub in ("list", "store", "recall", "add", "rename", "remove", "remove-all"):
            assert sub in result.output

    def test_group_help_lists_state_commands(self):
        result = _runner().invoke(cli, ["group", "--help"])
        assert result.exit_code == 0, result.output
        for sub in ("set", "get", "state"):
            assert sub in result.output


class TestSceneCommands:
    def test_scene_store(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(
                cli,
                ["--mqtt-host", "x", "scene", "store", "kitchen", "3", "--name", "Chill"],
            )
        assert result.exit_code == 0, result.output
        assert client.last_published == (
            "zigbee2mqtt/kitchen/set",
            {"scene_store": {"ID": 3, "name": "Chill"}},
        )

    def test_scene_store_json_output(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "--json", "scene", "store", "kitchen", "3"]
            )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["target"] == "kitchen"
        assert payload["topic"] == "zigbee2mqtt/kitchen/set"
        assert payload["published"] == {"scene_store": {"ID": 3}}

    def test_scene_store_endpoint(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(
                cli,
                ["--mqtt-host", "x", "scene", "store", "switch1", "1", "--endpoint", "2"],
            )
        assert result.exit_code == 0, result.output
        assert client.last_published[0] == "zigbee2mqtt/switch1/2/set"

    def test_scene_store_rejects_out_of_range_id(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "scene", "store", "kitchen", "300"])
        assert result.exit_code != 0
        assert "0-255" in result.output
        assert client.published == []

    def test_scene_store_rejects_non_numeric_id(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "scene", "store", "kitchen", "abc"])
        assert result.exit_code != 0

    def test_scene_recall(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "scene", "recall", "kitchen", "3"])
        assert result.exit_code == 0, result.output
        assert client.last_published[1] == {"scene_recall": 3}

    def test_scene_recall_bad_id(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "scene", "recall", "kitchen", "-5"])
        assert result.exit_code != 0

    def test_scene_add_full(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "scene",
                    "add",
                    "kitchen",
                    "5",
                    "--name",
                    "Dinner",
                    "--state",
                    "ON",
                    "--brightness",
                    "120",
                    "--transition",
                    "2",
                    "--color-temp",
                    "370",
                ],
            )
        assert result.exit_code == 0, result.output
        body = client.last_published[1]["scene_add"]
        assert body == {
            "ID": 5,
            "name": "Dinner",
            "transition": 2.0,
            "state": "ON",
            "brightness": 120,
            "color_temp": 370,
        }

    def test_scene_add_color_and_extra_json(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "scene",
                    "add",
                    "kitchen",
                    "5",
                    "--color",
                    '{"x":0.4,"y":0.4}',
                    "--extra",
                    '{"color_mode":"xy"}',
                ],
            )
        assert result.exit_code == 0, result.output
        body = client.last_published[1]["scene_add"]
        assert body["color"] == {"x": 0.4, "y": 0.4}
        assert body["color_mode"] == "xy"

    def test_scene_add_invalid_color_json(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "scene", "add", "kitchen", "5", "--color", "nope{"]
            )
        assert result.exit_code != 0
        assert "not valid JSON" in result.output

    def test_scene_add_color_json_not_object(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "scene", "add", "kitchen", "5", "--extra", "[1,2]"]
            )
        assert result.exit_code != 0
        assert "must be a JSON object" in result.output

    def test_scene_add_rejects_bad_brightness(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "scene", "add", "kitchen", "5", "--brightness", "999"]
            )
        assert result.exit_code != 0
        assert "brightness must be 0-254" in result.output

    def test_scene_rename(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "scene", "rename", "kitchen", "3", "Movie Night"]
            )
        assert result.exit_code == 0, result.output
        assert client.last_published[1] == {"scene_rename": {"ID": 3, "name": "Movie Night"}}

    def test_scene_rename_blank_name(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "scene", "rename", "kitchen", "3", " "]
            )
        assert result.exit_code != 0
        assert "name is required" in result.output

    def test_scene_remove(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "scene", "remove", "kitchen", "3"])
        assert result.exit_code == 0, result.output
        assert client.last_published[1] == {"scene_remove": 3}

    def test_scene_remove_all_requires_confirmation(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "scene", "remove-all", "kitchen"], input="n\n"
            )
        assert result.exit_code != 0
        assert client.published == []

    def test_scene_remove_all_confirmed(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "scene", "remove-all", "kitchen"], input="y\n"
            )
        assert result.exit_code == 0, result.output
        assert client.last_published[1] == {"scene_remove_all": ""}

    def test_scene_remove_all_yes_flag(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "scene", "remove-all", "kitchen", "--yes"]
            )
        assert result.exit_code == 0, result.output

    def test_scene_list_table(self):
        client = FakeBridgeClient()
        client.set_retained(
            "zigbee2mqtt/kitchen",
            json.dumps({"state": "ON", "scenes": [{"id": 1, "name": "Chill"}]}),
        )
        with _patch_client(client):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "scene", "list", "kitchen"])
        assert result.exit_code == 0, result.output
        assert "Chill" in result.output

    def test_scene_list_json_empty(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "--json", "scene", "list", "nope"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == []


class TestGroupStateCommands:
    def test_group_set(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(
                cli,
                ["--mqtt-host", "x", "group", "set", "kitchen", "state=ON", "brightness=200"],
            )
        assert result.exit_code == 0, result.output
        assert client.last_published == (
            "zigbee2mqtt/kitchen/set",
            {"state": "ON", "brightness": 200},
        )

    def test_group_set_requires_fields(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "group", "set", "kitchen"])
        assert result.exit_code != 0
        assert "no fields supplied" in result.output

    def test_group_set_rejects_bare_token(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "group", "set", "kitchen", "ON"])
        assert result.exit_code != 0
        assert "expected key=value" in result.output

    def test_group_get(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "group", "get", "kitchen", "state", "brightness"]
            )
        assert result.exit_code == 0, result.output
        assert client.last_published == (
            "zigbee2mqtt/kitchen/get",
            {"state": "", "brightness": ""},
        )

    def test_group_get_requires_keys(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "group", "get", "kitchen"])
        assert result.exit_code != 0
        assert "at least one key" in result.output

    def test_group_state(self):
        client = FakeBridgeClient()
        client.set_retained("zigbee2mqtt/kitchen", json.dumps({"state": "ON", "brightness": 42}))
        with _patch_client(client):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "--json", "group", "state", "kitchen"]
            )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["brightness"] == 42

    def test_group_state_empty(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "--json", "group", "state", "kitchen"]
            )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == {}


class TestSceneWorkflows:
    """Multi-command workflows combining the new surface with existing commands."""

    def test_create_group_add_member_set_then_store_scene(self):
        client = FakeBridgeClient()
        client.set_response("group/add", {"status": "ok", "data": {"id": 4}})
        client.set_response("group/members/add", {"status": "ok"})
        r = _runner()
        with _patch_client(client):
            assert r.invoke(cli, ["--mqtt-host", "x", "group", "add", "kitchen"]).exit_code == 0
            assert (
                r.invoke(
                    cli, ["--mqtt-host", "x", "group", "add-member", "kitchen", "lamp1"]
                ).exit_code
                == 0
            )
            assert (
                r.invoke(
                    cli,
                    ["--mqtt-host", "x", "group", "set", "kitchen", "state=ON", "brightness=80"],
                ).exit_code
                == 0
            )
            assert (
                r.invoke(
                    cli, ["--mqtt-host", "x", "scene", "store", "kitchen", "1", "--name", "Dim"]
                ).exit_code
                == 0
            )
        # the groupcast set landed before the store snapshot
        assert client.published[0] == (
            "zigbee2mqtt/kitchen/set",
            {"state": "ON", "brightness": 80},
        )
        assert client.published[1] == (
            "zigbee2mqtt/kitchen/set",
            {"scene_store": {"ID": 1, "name": "Dim"}},
        )

    def test_store_list_recall_remove_round_trip(self):
        client = FakeBridgeClient()
        r = _runner()
        with _patch_client(client):
            assert (
                r.invoke(cli, ["--mqtt-host", "x", "scene", "store", "kitchen", "2"]).exit_code == 0
            )
            # z2m would now republish state with the scene listed — simulate that
            client.set_retained(
                "zigbee2mqtt/kitchen",
                json.dumps({"state": "ON", "scenes": [{"id": 2, "name": "Chill"}]}),
            )
            listed = r.invoke(cli, ["--mqtt-host", "x", "--json", "scene", "list", "kitchen"])
            assert listed.exit_code == 0, listed.output
            assert json.loads(listed.output) == [{"id": 2, "name": "Chill"}]
            assert (
                r.invoke(cli, ["--mqtt-host", "x", "scene", "recall", "kitchen", "2"]).exit_code
                == 0
            )
            assert (
                r.invoke(cli, ["--mqtt-host", "x", "scene", "remove", "kitchen", "2"]).exit_code
                == 0
            )
        bodies = [p for _t, p in client.published]
        assert bodies == [
            {"scene_store": {"ID": 2}},
            {"scene_recall": 2},
            {"scene_remove": 2},
        ]

    def test_scene_list_falls_back_to_group_inventory(self):
        client = FakeBridgeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/groups",
            json.dumps(
                [{"id": 4, "friendly_name": "kitchen", "scenes": [{"id": 7, "name": "Late"}]}]
            ),
        )
        with _patch_client(client):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "--json", "scene", "list", "kitchen"]
            )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == [{"id": 7, "name": "Late"}]

    def test_device_set_still_works_after_kv_parser_refactor(self):
        client = FakeBridgeClient()
        with _patch_client(client):
            result = _runner().invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "device",
                    "set",
                    "lamp1",
                    "state=ON",
                    "brightness=200",
                    'color={"x":0.3,"y":0.3}',
                ],
            )
        assert result.exit_code == 0, result.output
        assert client.last_published == (
            "zigbee2mqtt/lamp1/set",
            {"state": "ON", "brightness": 200, "color": {"x": 0.3, "y": 0.3}},
        )


class TestScenePreflightValidation:
    """Bad scene arguments must fail before any MQTT connection is attempted."""

    @staticmethod
    def _exploding_client(ctx):
        raise AssertionError("make_client must not be called for invalid scene args")

    def _invoke(self, argv):
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            self._exploding_client,
        ):
            return _runner().invoke(cli, ["--mqtt-host", "x", *argv])

    def test_store_bad_id_never_connects(self):
        result = self._invoke(["scene", "store", "kitchen", "999"])
        assert result.exit_code != 0
        assert "scene_id must be 0-255" in result.output

    def test_recall_bad_id_never_connects(self):
        result = self._invoke(["scene", "recall", "kitchen", "256"])
        assert result.exit_code != 0
        assert "scene_id must be 0-255" in result.output

    def test_remove_bad_id_never_connects(self):
        result = self._invoke(["scene", "remove", "kitchen", "300"])
        assert result.exit_code != 0

    def test_add_bad_id_never_connects(self):
        result = self._invoke(["scene", "add", "kitchen", "700"])
        assert result.exit_code != 0

    def test_rename_blank_name_never_connects(self):
        result = self._invoke(["scene", "rename", "kitchen", "2", "  "])
        assert result.exit_code != 0
        assert "name is required" in result.output

    def test_store_blank_name_never_connects(self):
        result = self._invoke(["scene", "store", "kitchen", "2", "--name", " "])
        assert result.exit_code != 0
        assert "name is required" in result.output

    def test_remove_all_blank_target_never_connects(self):
        result = self._invoke(["scene", "remove-all", "   ", "--yes"])
        assert result.exit_code != 0
        assert "target is required" in result.output


# ── refine: exposes / availability / raw cluster access / ota unschedule ─────


class SubscribingFakeClient(FakeBridgeClient):
    """FakeBridgeClient whose subscribe() replays seeded retained topics.

    ``device availability-sweep`` reads a wildcard subscription rather than
    doing one blocking retained read per device, so the fake has to deliver
    those messages the way a broker would.
    """

    def __init__(self, **overrides):
        super().__init__(**overrides)
        self.subscriptions: list[str] = []

    def subscribe(self, filter_: str, callback) -> None:
        self.subscriptions.append(filter_)
        prefix = filter_[:-1] if filter_.endswith("#") else filter_
        for topic, payload in self._retained.items():
            if filter_.endswith("#") and topic.startswith(prefix):
                callback(topic, payload)
            elif topic == filter_:
                callback(topic, payload)


EXPOSES_INVENTORY = json.dumps(
    [
        {
            "friendly_name": "lamp1",
            "ieee_address": "0xaaa",
            "type": "Router",
            "definition": {
                "model": "LCT001",
                "exposes": [
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
                        "type": "numeric",
                        "name": "linkquality",
                        "property": "linkquality",
                        "access": 1,
                        "unit": "lqi",
                    },
                ],
            },
        },
        {"friendly_name": "sensor1", "ieee_address": "0xbbb", "type": "EndDevice"},
    ]
)


def _patched(client):
    return patch(
        "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
        lambda ctx: client,
    )


class TestDeviceExposesCommand:
    def _client(self):
        client = SubscribingFakeClient()
        client.set_retained("zigbee2mqtt/bridge/devices", EXPOSES_INVENTORY)
        return client

    def test_exposes_json(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "--json", "device", "exposes", "lamp1"]
            )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert [row["property"] for row in data] == ["state", "brightness", "linkquality"]

    def test_exposes_table_output(self):
        with _patched(self._client()):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "device", "exposes", "lamp1"])
        assert result.exit_code == 0, result.output
        assert "brightness" in result.output
        assert "published,set,get" in result.output

    def test_exposes_settable_filter(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "--json", "device", "exposes", "lamp1", "--settable"]
            )
        assert result.exit_code == 0, result.output
        props = [row["property"] for row in json.loads(result.output)]
        assert props == ["state", "brightness"]
        assert "linkquality" not in props

    def test_exposes_unknown_device_errors(self):
        with _patched(self._client()):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "device", "exposes", "ghost"])
        assert result.exit_code != 0
        assert "no exposed properties" in result.output

    def test_exposes_device_without_definition_errors(self):
        with _patched(self._client()):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "device", "exposes", "sensor1"])
        assert result.exit_code != 0


class TestDeviceAvailabilityCommands:
    def _client(self):
        client = SubscribingFakeClient()
        client.set_retained("zigbee2mqtt/bridge/devices", EXPOSES_INVENTORY)
        client.set_retained("zigbee2mqtt/lamp1/availability", '{"state":"online"}')
        client.set_retained("zigbee2mqtt/sensor1/availability", "offline")
        return client

    def test_availability_single_device(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "--json", "device", "availability", "lamp1"]
            )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["availability"] == "online"
        assert data["online"] is True

    def test_availability_unknown_device_is_null(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "--json", "device", "availability", "ghost"]
            )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["availability"] is None

    def test_availability_sweep_offline_first(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "--json",
                    "device",
                    "availability-sweep",
                    "--duration",
                    "0",
                ],
            )
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert [r["friendly_name"] for r in rows] == ["sensor1", "lamp1"]

    def test_availability_sweep_offline_only(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "--json",
                    "device",
                    "availability-sweep",
                    "--duration",
                    "0",
                    "--offline-only",
                ],
            )
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert [r["friendly_name"] for r in rows] == ["sensor1"]


class TestDeviceReadWriteCommands:
    def test_read_publishes_zcl_read(self):
        client = FakeBridgeClient()
        with _patched(client):
            result = _runner().invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "--json",
                    "device",
                    "read",
                    "lamp1",
                    "--cluster",
                    "genBasic",
                    "--attribute",
                    "zclVersion",
                    "--attribute",
                    "modelId",
                ],
            )
        assert result.exit_code == 0, result.output
        topic, payload = client.last_published
        assert topic == "zigbee2mqtt/lamp1/set"
        assert payload == {"read": {"cluster": "genBasic", "attributes": ["zclVersion", "modelId"]}}

    def test_read_with_endpoint_and_manufacturer_code(self):
        client = FakeBridgeClient()
        with _patched(client):
            result = _runner().invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "device",
                    "read",
                    "lamp1",
                    "--cluster",
                    "0x0000",
                    "--attribute",
                    "zclVersion",
                    "--endpoint",
                    "2",
                    "--manufacturer-code",
                    "4107",
                    "--option",
                    "disableDefaultResponse=true",
                ],
            )
        assert result.exit_code == 0, result.output
        topic, payload = client.last_published
        assert topic == "zigbee2mqtt/lamp1/2/set"
        assert payload["read"]["cluster"] == 0
        assert payload["read"]["options"] == {
            "disableDefaultResponse": True,
            "manufacturerCode": 4107,
        }

    def test_read_requires_attribute(self):
        client = FakeBridgeClient()
        with _patched(client):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "device", "read", "lamp1", "--cluster", "genBasic"]
            )
        assert result.exit_code != 0

    def test_write_publishes_zcl_write(self):
        client = FakeBridgeClient()
        with _patched(client):
            result = _runner().invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "--json",
                    "device",
                    "write",
                    "lamp1",
                    "--cluster",
                    "genOnOff",
                    "onOff=1",
                ],
            )
        assert result.exit_code == 0, result.output
        topic, payload = client.last_published
        assert topic == "zigbee2mqtt/lamp1/set"
        assert payload == {"write": {"cluster": "genOnOff", "payload": {"onOff": 1}}}

    def test_write_requires_fields(self):
        client = FakeBridgeClient()
        with _patched(client):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "device", "write", "lamp1", "--cluster", "genOnOff"]
            )
        assert result.exit_code != 0
        assert "no fields supplied" in result.output

    def test_write_rejects_bad_kv(self):
        client = FakeBridgeClient()
        with _patched(client):
            result = _runner().invoke(
                cli,
                ["--mqtt-host", "x", "device", "write", "lamp1", "--cluster", "genOnOff", "onOff"],
            )
        assert result.exit_code != 0
        assert "expected key=value" in result.output


class TestAttributePreflightNeverConnects:
    """Bad cluster/attribute args must fail before an MQTT connection is opened."""

    @staticmethod
    def _exploding_client(ctx):
        raise AssertionError("make_client must not be called for invalid arguments")

    def _invoke(self, argv):
        with patch(
            "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
            self._exploding_client,
        ):
            return _runner().invoke(cli, ["--mqtt-host", "x", *argv])

    def test_blank_target_never_connects(self):
        result = self._invoke(
            ["device", "read", "  ", "--cluster", "genBasic", "--attribute", "zclVersion"]
        )
        assert result.exit_code != 0
        assert "target is required" in result.output

    def test_blank_cluster_never_connects(self):
        result = self._invoke(
            ["device", "read", "lamp1", "--cluster", " ", "--attribute", "zclVersion"]
        )
        assert result.exit_code != 0
        assert "cluster is required" in result.output

    def test_blank_attribute_never_connects(self):
        result = self._invoke(
            ["device", "read", "lamp1", "--cluster", "genBasic", "--attribute", " "]
        )
        assert result.exit_code != 0

    def test_write_blank_cluster_never_connects(self):
        result = self._invoke(["device", "write", "lamp1", "--cluster", " ", "onOff=1"])
        assert result.exit_code != 0
        assert "cluster is required" in result.output


class TestOtaUnscheduleCommand:
    def test_unschedule(self):
        client = FakeBridgeClient()
        client.set_response("device/ota_update/unschedule", {"status": "ok"})
        with _patched(client):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "--json", "ota", "unschedule", "radiator"]
            )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["status"] == "ok"

    def test_unschedule_blank_id_errors(self):
        client = FakeBridgeClient()
        with _patched(client):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "ota", "unschedule", ""])
        assert result.exit_code != 0
        assert "id_ is required" in result.output


class TestRefineWorkflows:
    """Multi-command workflows combining the new commands with existing ones."""

    def test_exposes_then_set_then_state(self):
        client = SubscribingFakeClient()
        client.set_retained("zigbee2mqtt/bridge/devices", EXPOSES_INVENTORY)
        with _patched(client):
            r = _runner()
            exposed = r.invoke(cli, ["--mqtt-host", "x", "--json", "device", "exposes", "lamp1"])
            assert exposed.exit_code == 0, exposed.output
            settable = [
                row["property"]
                for row in json.loads(exposed.output)
                if "set" in (row["access_flags"] or "")
            ]
            assert "brightness" in settable
            # drive the property the exposes table just advertised
            done = r.invoke(cli, ["--mqtt-host", "x", "device", "set", "lamp1", "brightness=200"])
            assert done.exit_code == 0, done.output
        assert client.published[-1] == ("zigbee2mqtt/lamp1/set", {"brightness": 200})

    def test_read_then_state_readback(self):
        client = SubscribingFakeClient()
        client.set_retained("zigbee2mqtt/lamp1", json.dumps({"zclVersion": 3}))
        with _patched(client):
            r = _runner()
            issued = r.invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "device",
                    "read",
                    "lamp1",
                    "--cluster",
                    "genBasic",
                    "--attribute",
                    "zclVersion",
                ],
            )
            assert issued.exit_code == 0, issued.output
            back = r.invoke(cli, ["--mqtt-host", "x", "--json", "device", "state", "lamp1"])
            assert back.exit_code == 0, back.output
            assert json.loads(back.output)["zclVersion"] == 3

    def test_sweep_finds_offline_then_last_seen(self):
        client = SubscribingFakeClient()
        client.set_retained("zigbee2mqtt/bridge/devices", EXPOSES_INVENTORY)
        client.set_retained("zigbee2mqtt/sensor1/availability", "offline")
        with _patched(client):
            r = _runner()
            sweep = r.invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "--json",
                    "device",
                    "availability-sweep",
                    "--duration",
                    "0",
                    "--offline-only",
                ],
            )
            assert sweep.exit_code == 0, sweep.output
            offline = json.loads(sweep.output)
            assert offline and offline[0]["friendly_name"] == "sensor1"
            detail = r.invoke(
                cli,
                ["--mqtt-host", "x", "--json", "device", "last-seen", offline[0]["friendly_name"]],
            )
            assert detail.exit_code == 0, detail.output
            assert json.loads(detail.output)["ieee_address"] == "0xbbb"


# ── endpoint / cluster introspection commands ────────────────────────────────

CLUSTER_INVENTORY = json.dumps(
    [
        {
            "friendly_name": "plug1",
            "ieee_address": "0xaaa",
            "type": "Router",
            "endpoints": {
                "1": {
                    "clusters": {
                        "input": ["genBasic", "genOnOff", "haElectricalMeasurement"],
                        "output": ["genOta"],
                    },
                    "bindings": [
                        {
                            "cluster": "genOnOff",
                            "target": {"type": "endpoint", "ieee_address": "0xcoord"},
                        }
                    ],
                    "configured_reportings": [
                        {
                            "cluster": "genOnOff",
                            "attribute": "onOff",
                            "minimum_report_interval": 0,
                            "maximum_report_interval": 3600,
                            "reportable_change": 0,
                        }
                    ],
                    "scenes": [{"id": 3, "name": "movie"}],
                },
                "2": {"clusters": {"input": ["genOnOff"], "output": []}},
            },
        },
        {"friendly_name": "sensor1", "ieee_address": "0xbbb", "type": "EndDevice"},
    ]
)


class TestDeviceClustersCommand:
    def _client(self):
        client = SubscribingFakeClient()
        client.set_retained("zigbee2mqtt/bridge/devices", CLUSTER_INVENTORY)
        return client

    def test_clusters_json_lists_every_endpoint(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "--json", "device", "clusters", "plug1"]
            )
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert len(rows) == 5
        assert {r["endpoint"] for r in rows} == {1, 2}

    def test_clusters_marks_bound_and_reported(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "--json", "device", "clusters", "0xaaa"]
            )
        rows = {(r["endpoint"], r["cluster"]): r for r in json.loads(result.output)}
        assert rows[(1, "genOnOff")]["bound"] is True
        assert rows[(1, "genOnOff")]["reported"] is True
        assert rows[(2, "genOnOff")]["bound"] is False

    def test_clusters_direction_and_endpoint_filters(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "--json",
                    "device",
                    "clusters",
                    "plug1",
                    "--direction",
                    "output",
                    "--endpoint",
                    "1",
                ],
            )
        assert result.exit_code == 0, result.output
        assert [r["cluster"] for r in json.loads(result.output)] == ["genOta"]

    def test_clusters_table_output(self):
        with _patched(self._client()):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "device", "clusters", "plug1"])
        assert result.exit_code == 0, result.output
        assert "haElectricalMeasurement" in result.output
        assert "direction" in result.output

    def test_clusters_bad_direction_rejected_by_click(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "device", "clusters", "plug1", "--direction", "sideways"]
            )
        assert result.exit_code == 2

    def test_clusters_unknown_device_errors(self):
        with _patched(self._client()):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "device", "clusters", "ghost"])
        assert result.exit_code != 0
        assert "no clusters" in result.output

    def test_clusters_uninterviewed_device_errors(self):
        with _patched(self._client()):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "device", "clusters", "sensor1"])
        assert result.exit_code != 0


class TestDeviceEndpointsCommand:
    def _client(self):
        client = SubscribingFakeClient()
        client.set_retained("zigbee2mqtt/bridge/devices", CLUSTER_INVENTORY)
        return client

    def test_endpoints_summary_json(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "--json", "device", "endpoints", "plug1"]
            )
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert [r["endpoint"] for r in rows] == [1, 2]
        assert rows[0]["input_clusters"] == 3
        assert rows[0]["bindings"] == 1
        assert rows[0]["configured_reportings"] == 1
        assert rows[0]["scene_ids"] == [3]

    def test_endpoints_table_output(self):
        with _patched(self._client()):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "device", "endpoints", "plug1"])
        assert result.exit_code == 0, result.output
        assert "input_clusters" in result.output

    def test_endpoints_unknown_device_errors(self):
        with _patched(self._client()):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "device", "endpoints", "ghost"])
        assert result.exit_code != 0
        assert "no endpoints" in result.output


class TestDeviceReportingsCommand:
    def _client(self):
        client = SubscribingFakeClient()
        client.set_retained("zigbee2mqtt/bridge/devices", CLUSTER_INVENTORY)
        return client

    def test_reportings_all_devices(self):
        with _patched(self._client()):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "--json", "device", "reportings"])
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert len(rows) == 1
        assert rows[0]["friendly_name"] == "plug1"
        assert rows[0]["attribute"] == "onOff"
        assert rows[0]["maximum_report_interval"] == 3600

    def test_reportings_filtered_by_device(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "--json", "device", "reportings", "sensor1"]
            )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == []

    def test_reportings_endpoint_filter(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli,
                ["--mqtt-host", "x", "--json", "device", "reportings", "plug1", "--endpoint", "2"],
            )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == []

    def test_reportings_table_output(self):
        with _patched(self._client()):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "device", "reportings"])
        assert result.exit_code == 0, result.output
        assert "onOff" in result.output


DEFINITIONS_PAYLOAD = json.dumps(
    {
        "clusters": {
            "genBasic": {
                "ID": 0,
                "attributes": {"zclVersion": {"ID": 0, "type": 32}},
                "commands": {},
                "commandsResponse": {},
            },
            "genOnOff": {
                "ID": 6,
                "attributes": {
                    "onOff": {"ID": 0, "type": 16},
                    "startUpOnOff": {"ID": 16387, "type": 48},
                },
                "commands": {"off": {"ID": 0, "parameters": []}, "on": {"ID": 1}},
                "commandsResponse": {},
            },
            "emptyCluster": {"ID": 99},
        },
        "custom_clusters": {
            "0xdead": {"tuyaSpecific": {"ID": 61184, "attributes": {"dp": {"ID": 1}}}}
        },
    }
)


class TestBridgeDefinitionsCommand:
    def _client(self, payload=DEFINITIONS_PAYLOAD):
        client = SubscribingFakeClient()
        if payload is not None:
            client.set_retained("zigbee2mqtt/bridge/definitions", payload)
        return client

    def test_definitions_lists_clusters(self):
        with _patched(self._client()):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "--json", "bridge", "definitions"])
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert [r["cluster"] for r in rows] == ["genBasic", "genOnOff", "emptyCluster"]
        assert rows[1]["attributes"] == 2

    def test_definitions_table_output(self):
        with _patched(self._client()):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "bridge", "definitions"])
        assert result.exit_code == 0, result.output
        assert "genOnOff" in result.output

    def test_definitions_cluster_attributes(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli,
                ["--mqtt-host", "x", "--json", "bridge", "definitions", "--cluster", "genOnOff"],
            )
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert [r["attribute"] for r in rows] == ["onOff", "startUpOnOff"]

    def test_definitions_cluster_by_numeric_id(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "--json", "bridge", "definitions", "--cluster", "0x0006"]
            )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)[0]["cluster"] == "genOnOff"

    def test_definitions_cluster_commands(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "--json",
                    "bridge",
                    "definitions",
                    "--cluster",
                    "genOnOff",
                    "--commands",
                ],
            )
        assert result.exit_code == 0, result.output
        assert [r["command"] for r in json.loads(result.output)] == ["off", "on"]

    def test_definitions_commands_without_cluster_errors(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "bridge", "definitions", "--commands"]
            )
        assert result.exit_code != 0
        assert "--commands requires --cluster" in result.output

    def test_definitions_unknown_cluster_errors(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "bridge", "definitions", "--cluster", "genNope"]
            )
        assert result.exit_code != 0
        assert "unknown cluster" in result.output

    def test_definitions_cluster_without_attributes_errors(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "bridge", "definitions", "--cluster", "emptyCluster"]
            )
        assert result.exit_code != 0
        assert "no attributes" in result.output

    def test_definitions_custom_clusters(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "--json", "bridge", "definitions", "--custom"]
            )
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert rows[0]["cluster"] == "tuyaSpecific"
        assert rows[0]["ieee_address"] == "0xdead"

    def test_definitions_no_custom_clusters_errors(self):
        payload = json.dumps({"clusters": {"genBasic": {"ID": 0}}})
        with _patched(self._client(payload)):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "bridge", "definitions", "--custom"]
            )
        assert result.exit_code != 0
        assert "no custom clusters" in result.output

    def test_definitions_missing_topic_errors(self):
        with _patched(self._client(None)):
            result = _runner().invoke(cli, ["--mqtt-host", "x", "bridge", "definitions"])
        assert result.exit_code != 0
        assert "no retained bridge/definitions" in result.output


class TestClusterIntrospectionWorkflows:
    """New introspection commands feeding the raw-cluster commands."""

    def _client(self):
        client = SubscribingFakeClient()
        client.set_retained("zigbee2mqtt/bridge/devices", CLUSTER_INVENTORY)
        client.set_retained("zigbee2mqtt/bridge/definitions", DEFINITIONS_PAYLOAD)
        return client

    def test_endpoints_then_clusters_then_read(self):
        client = self._client()
        with _patched(client):
            r = _runner()
            eps = r.invoke(cli, ["--mqtt-host", "x", "--json", "device", "endpoints", "plug1"])
            assert eps.exit_code == 0, eps.output
            endpoint = json.loads(eps.output)[0]["endpoint"]

            cl = r.invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "--json",
                    "device",
                    "clusters",
                    "plug1",
                    "--endpoint",
                    str(endpoint),
                    "--direction",
                    "input",
                ],
            )
            assert cl.exit_code == 0, cl.output
            cluster = json.loads(cl.output)[1]["cluster"]
            assert cluster == "genOnOff"

            attrs = r.invoke(
                cli,
                ["--mqtt-host", "x", "--json", "bridge", "definitions", "--cluster", cluster],
            )
            assert attrs.exit_code == 0, attrs.output
            attribute = json.loads(attrs.output)[0]["attribute"]

            read = r.invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "--json",
                    "device",
                    "read",
                    "plug1",
                    "--cluster",
                    cluster,
                    "--attribute",
                    attribute,
                    "--endpoint",
                    str(endpoint),
                ],
            )
            assert read.exit_code == 0, read.output
            topic, payload = client.last_published
            assert topic == "zigbee2mqtt/plug1/1/set"
            assert payload == {"read": {"cluster": "genOnOff", "attributes": ["onOff"]}}

    def test_clusters_finds_unbound_output_cluster_then_binds(self):
        client = self._client()
        client.set_response("device/bind", {"status": "ok", "data": {"clusters": ["genOnOff"]}})
        with _patched(client):
            r = _runner()
            out = r.invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "--json",
                    "device",
                    "clusters",
                    "plug1",
                    "--direction",
                    "output",
                ],
            )
            assert out.exit_code == 0, out.output
            unbound = [row for row in json.loads(out.output) if not row["bound"]]
            assert [row["cluster"] for row in unbound] == ["genOta"]

            bind = r.invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "--json",
                    "device",
                    "bind",
                    "plug1/1",
                    "sensor1",
                    "--cluster",
                    "genOnOff",
                ],
            )
            assert bind.exit_code == 0, bind.output
            assert json.loads(bind.output)["status"] == "ok"

    def test_configure_reporting_then_read_back(self):
        client = self._client()
        client.set_response("device/configure_reporting", {"status": "ok"})
        with _patched(client):
            r = _runner()
            conf = r.invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "--json",
                    "device",
                    "configure-reporting",
                    "plug1",
                    "--cluster",
                    "genOnOff",
                    "--attribute",
                    "onOff",
                    "--min",
                    "0",
                    "--max",
                    "3600",
                ],
            )
            assert conf.exit_code == 0, conf.output
            back = r.invoke(cli, ["--mqtt-host", "x", "--json", "device", "reportings", "plug1"])
            assert back.exit_code == 0, back.output
            rows = json.loads(back.output)
            assert rows[0]["cluster"] == "genOnOff"
            assert rows[0]["attribute"] == "onOff"


# ── device battery (refine pass 4) ───────────────────────────────────────────


BATTERY_INVENTORY = json.dumps(
    [
        {"friendly_name": "Coordinator", "type": "Coordinator", "ieee_address": "0x00"},
        {
            "friendly_name": "Door Sensor",
            "type": "EndDevice",
            "ieee_address": "0xaaa",
            "power_source": "Battery",
            "definition": {"model": "MCCGQ11LM"},
        },
        {
            "friendly_name": "Thermo",
            "type": "EndDevice",
            "ieee_address": "0xbbb",
            "power_source": "Battery",
        },
        {
            "friendly_name": "lamp1",
            "type": "Router",
            "ieee_address": "0xccc",
            "power_source": "Mains (single phase)",
        },
        {
            "friendly_name": "Remote",
            "type": "EndDevice",
            "ieee_address": "0xddd",
            "power_source": "Battery",
        },
    ]
)


class TestDeviceBatteryCommand:
    def _client(self):
        client = SubscribingFakeClient()
        client.set_retained("zigbee2mqtt/bridge/devices", BATTERY_INVENTORY)
        client.set_retained("zigbee2mqtt/Door Sensor", json.dumps({"battery": 87, "voltage": 2915}))
        client.set_retained("zigbee2mqtt/Thermo", json.dumps({"battery": 9, "battery_low": True}))
        client.set_retained("zigbee2mqtt/Remote", json.dumps({"contact": True}))
        return client

    def test_battery_sweep_json(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "--json", "device", "battery", "--duration", "0"]
            )
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        # low → unknown → ok; mains lamp1 and coordinator dropped
        assert [r["friendly_name"] for r in rows] == ["Thermo", "Remote", "Door Sensor"]
        thermo = rows[0]
        assert thermo["status"] == "low"
        assert thermo["battery"] == 9.0
        assert thermo["battery_low"] is True
        assert thermo["voltage"] is None

    def test_battery_low_only_table(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli,
                ["--mqtt-host", "x", "device", "battery", "--duration", "0", "--low-only"],
            )
        assert result.exit_code == 0, result.output
        assert "friendly_name" in result.output  # table header
        assert "Thermo" in result.output
        assert "Remote" in result.output
        assert "Door Sensor" not in result.output  # ok → filtered out
        assert "lamp1" not in result.output  # mains → dropped

    def test_battery_below_option(self):
        with _patched(self._client()):
            result = _runner().invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "--json",
                    "device",
                    "battery",
                    "--duration",
                    "0",
                    "--below",
                    "90",
                ],
            )
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        status = {r["friendly_name"]: r["status"] for r in rows}
        assert status["Door Sensor"] == "low"
        assert status["Thermo"] == "low"

    def test_battery_no_battery_devices_is_empty(self):
        client = SubscribingFakeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/devices",
            json.dumps([{"friendly_name": "lamp1", "type": "Router"}]),
        )
        with _patched(client):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "--json", "device", "battery", "--duration", "0"]
            )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == []

    def test_battery_ignores_non_state_topics(self):
        client = self._client()
        client.set_retained("zigbee2mqtt/bridge/info", json.dumps({"battery": 99}))
        client.set_retained("zigbee2mqtt/Thermo/set", json.dumps({"battery": 99}))
        with _patched(client):
            result = _runner().invoke(
                cli, ["--mqtt-host", "x", "--json", "device", "battery", "--duration", "0"]
            )
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        thermo = next(r for r in rows if r["friendly_name"] == "Thermo")
        assert thermo["battery"] == 9.0  # from the real state topic, not bridge/info or /set

    def test_battery_single_wildcard_subscription(self):
        client = self._client()
        with _patched(client):
            _runner().invoke(
                cli, ["--mqtt-host", "x", "--json", "device", "battery", "--duration", "0"]
            )
        assert client.subscriptions == ["zigbee2mqtt/#"]

    def test_workflow_low_battery_then_read_state(self):
        """Battery audit surfaces the worst device → confirm via its state."""
        client = self._client()
        with _patched(client):
            runner = _runner()
            sweep = runner.invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "--json",
                    "device",
                    "battery",
                    "--duration",
                    "0",
                    "--low-only",
                ],
            )
            assert sweep.exit_code == 0, sweep.output
            worst = json.loads(sweep.output)[0]
            assert worst["friendly_name"] == "Thermo"

            state = runner.invoke(cli, ["--mqtt-host", "x", "--json", "device", "state", "Thermo"])
            assert state.exit_code == 0, state.output
            payload = json.loads(state.output)
            assert payload["battery"] == 9
            assert payload["battery_low"] is True

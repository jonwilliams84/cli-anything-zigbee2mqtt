"""Third refine pass — closes the last uncovered error / edge branches:

CLI layer:
* config save round-trip, bridge restart --via-kubectl and the MqttError
  abort path, watch-logging --json, log-level get/set, blank-cluster
  definitions abort
* device options / generate-converter / write error paths, disable and
  enable command bodies, scene command ValueError aborts, extension
  show --json
* REPL ImportError fallback, SystemExit swallowed in the dispatch loop,
  main() entry point
* defensive returns after _abort in _parse_json_obj / _preflight_attributes
  (stubbed _abort so the guard message itself is still asserted)

Core layer:
* devices.last_seen with a timezone-naive timestamp
* bindings.unbind without clusters, binding targets without an ieee
* converters ls passthrough for non-.js dotted names
* BridgeClient.publish connecting lazily, subscriber filters that don't match
* ReplSkin packaged-skill fallback and color detection without isatty

No MQTT broker or kubectl required — fake transports throughout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

import cli_anything.zigbee2mqtt.zigbee2mqtt_cli as cli_mod
from cli_anything.zigbee2mqtt.core import bridge as bridge_core
from cli_anything.zigbee2mqtt.core import converters as converters_core
from cli_anything.zigbee2mqtt.core import devices as devices_core
from cli_anything.zigbee2mqtt.core import k8s_backend
from cli_anything.zigbee2mqtt.core import scenes as scenes_core
from cli_anything.zigbee2mqtt.core import bindings as bindings_core
from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient, MqttError
from cli_anything.zigbee2mqtt.utils.repl_skin import ReplSkin
from cli_anything.zigbee2mqtt.zigbee2mqtt_cli import cli


# ─────────────────────────────────────────────────────────── fake client


class FakeClient:
    """Fake BridgeClient: canned request responses + retained payloads."""

    base_topic = "zigbee2mqtt"

    def __init__(self, **overrides):
        for k, v in overrides.items():
            setattr(self, k, v)
        self.requests: list[dict] = []
        self.published: list[tuple[str, object]] = []
        self._request_responses: dict[str, dict] = {}
        self._retained: dict[str, str] = {}

    def set_request(self, path: str, response: dict) -> None:
        self._request_responses[path] = response

    def set_retained(self, topic: str, payload) -> None:
        self._retained[topic] = payload if isinstance(payload, str) else json.dumps(payload)

    def request(self, path: str, payload=None, *, timeout: float = 0) -> dict:
        self.requests.append({"path": path, "payload": payload})
        return self._request_responses.get(path, {"status": "ok", "data": {}})

    def collect_retained(self, topic: str, *, timeout: float = 0):
        self.requests.append({"path": f"retained:{topic}"})
        return self._retained.get(topic)

    def publish(self, topic: str, payload, *, retain: bool = False, qos: int = 0) -> int:
        self.published.append((topic, payload))
        return 0

    def subscribe(self, filter_: str, callback) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


def _patched(client):
    return patch(
        "cli_anything.zigbee2mqtt.zigbee2mqtt_cli.make_client",
        lambda ctx: client,
    )


# ═══════════════════════════════════════════════ config / bridge


class TestConfigAndBridgePaths:
    def test_config_save_roundtrip(self, tmp_path, monkeypatch):
        target = tmp_path / "profile.json"
        monkeypatch.setattr(cli_mod.project, "save_config", lambda cfg, path: target, raising=False)
        r = CliRunner().invoke(cli, ["--mqtt-host", "x", "config", "save"])
        assert r.exit_code == 0, r.output
        assert str(target) in r.output

    def test_bridge_restart_via_kubectl(self, monkeypatch):
        monkeypatch.setattr(cli_mod, "make_k8s_target", lambda ctx: object())
        monkeypatch.setattr(k8s_backend, "restart", lambda target: None)
        monkeypatch.setattr(k8s_backend, "rollout_status", lambda target: {"complete": True})
        r = CliRunner().invoke(cli, ["--mqtt-host", "x", "bridge", "restart", "--via-kubectl"])
        assert r.exit_code == 0, r.output
        assert "kubectl" in r.output
        assert "complete" in r.output

    def test_bridge_restart_mqtt_error_aborts(self, monkeypatch):
        def boom(client):
            raise MqttError("broker unreachable")

        monkeypatch.setattr(bridge_core, "restart", boom)
        with _patched(FakeClient()):
            r = CliRunner().invoke(cli, ["--mqtt-host", "x", "bridge", "restart"])
        assert r.exit_code == 1
        assert "broker unreachable" in r.output

    def test_bridge_watch_logging_json(self, monkeypatch):
        monkeypatch.setattr(
            bridge_core,
            "watch_logging",
            lambda c, duration=15.0, callback=None: [
                {"level": "info", "message": "zigbee herdsman started"}
            ],
        )
        with _patched(FakeClient()):
            r = CliRunner().invoke(cli, ["--mqtt-host", "x", "--json", "bridge", "watch-logging"])
        assert r.exit_code == 0, r.output
        assert "zigbee herdsman started" in r.output

    def test_bridge_log_level_get_via_cli(self):
        client = FakeClient()
        client.set_retained("zigbee2mqtt/bridge/info", {"advanced": {"log_level": "debug"}})
        with _patched(client):
            r = CliRunner().invoke(cli, ["--mqtt-host", "x", "bridge", "log-level"])
        assert r.exit_code == 0, r.output
        assert "debug" in r.output

    def test_bridge_log_level_set_via_cli(self):
        client = FakeClient()
        with _patched(client):
            r = CliRunner().invoke(cli, ["--mqtt-host", "x", "bridge", "log-level", "warn"])
        assert r.exit_code == 0, r.output
        assert client.requests[0]["path"] == "options"
        assert client.requests[0]["payload"] == {"options": {"advanced": {"log_level": "warn"}}}

    def test_bridge_definitions_blank_cluster_aborts(self):
        """A whitespace-only --cluster reaches find_cluster, which rejects it."""
        client = FakeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/definitions",
            {"clusters": {"genOnOff": {"ID": 6, "attributes": {}, "commands": {}}}},
        )
        with _patched(client):
            r = CliRunner().invoke(
                cli, ["--mqtt-host", "x", "bridge", "definitions", "--cluster", " "]
            )
        assert r.exit_code == 1
        assert "cluster is required" in r.output


# ═══════════════════════════════════════════════ arg helpers (dead returns)


class TestArgHelperGuards:
    def test_parse_json_obj_invalid_json(self, monkeypatch):
        msgs: list[str] = []
        monkeypatch.setattr(cli_mod, "_abort", lambda m: msgs.append(m))
        assert cli_mod._parse_json_obj("{bad json", "thing") == {}
        assert msgs[-1].startswith("thing is not valid JSON")

    def test_parse_json_obj_non_object(self, monkeypatch):
        msgs: list[str] = []
        monkeypatch.setattr(cli_mod, "_abort", lambda m: msgs.append(m))
        assert cli_mod._parse_json_obj("[1,2]", "thing") == {}
        assert msgs[-1] == "thing must be a JSON object"

    def test_preflight_attributes_empty_payload(self, monkeypatch):
        msgs: list[str] = []
        monkeypatch.setattr(cli_mod, "_abort", lambda m: msgs.append(m))
        out = cli_mod._preflight_attributes("lamp", "genOnOff", payload={})
        assert out == {}
        assert "payload must be a non-empty dict" in msgs[-1]

    def test_preflight_attributes_happy_path(self):
        out = cli_mod._preflight_attributes("lamp", "genOnOff", payload={"onOff": 1})
        assert out["target"] == "lamp"
        assert out["cluster"] == "genOnOff"
        assert out["payload"] == {"onOff": 1}

    def test_device_options_invalid_json_returns_after_abort(self, monkeypatch):
        msgs: list[str] = []
        monkeypatch.setattr(cli_mod, "_abort", lambda m: msgs.append(m))
        with _patched(FakeClient()):
            r = CliRunner().invoke(cli, ["--mqtt-host", "x", "device", "options", "dev", "{oops"])
        assert r.exit_code == 0
        assert any("not valid JSON" in m for m in msgs)


# ═══════════════════════════════════════════════ device commands


class TestDeviceCommandPaths:
    def test_disable_and_enable_via_cli(self):
        client = FakeClient()
        with _patched(client):
            for flag, want in (("disable", True), ("enable", False)):
                r = CliRunner().invoke(cli, ["--mqtt-host", "x", "device", flag, "lamp"])
                assert r.exit_code == 0, r.output
        assert [req["path"] for req in client.requests] == ["device/options", "device/options"]
        assert client.requests[0]["payload"] == {"id": "lamp", "options": {"disabled": True}}
        assert client.requests[1]["payload"] == {"id": "lamp", "options": {"disabled": False}}

    def test_generate_converter_without_source_emits_raw(self):
        client = FakeClient()
        client.set_request("device/generate_external_definition", {"note": "nothing to generate"})
        with _patched(client):
            r = CliRunner().invoke(cli, ["--mqtt-host", "x", "device", "generate-converter", "odd"])
        assert r.exit_code == 0, r.output
        assert "nothing to generate" in r.output

    def test_device_write_value_error_aborts(self, monkeypatch):
        from cli_anything.zigbee2mqtt.core import attributes as attributes_core

        def boom(*a, **kw):
            raise ValueError("write rejected by z2m")

        monkeypatch.setattr(attributes_core, "write", boom)
        with _patched(FakeClient()):
            r = CliRunner().invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "device",
                    "write",
                    "dev",
                    "--cluster",
                    "genOnOff",
                    "onOff=1",
                ],
            )
        assert r.exit_code == 1
        assert "write rejected by z2m" in r.output


# ═══════════════════════════════════════════════ scene command error paths


class TestSceneValueErrorPaths:
    @pytest.mark.parametrize(
        "args,core_fn",
        [
            (["scene", "store", "lamp", "1"], "store"),
            (["scene", "recall", "lamp", "1"], "recall"),
            (["scene", "rename", "lamp", "1", "new-name"], "rename"),
            (["scene", "remove", "lamp", "1"], "remove"),
        ],
    )
    def test_scene_core_value_error_aborts(self, monkeypatch, args, core_fn):
        def boom(*a, **kw):
            raise ValueError("scene boom")

        monkeypatch.setattr(scenes_core, core_fn, boom)
        with _patched(FakeClient()):
            r = CliRunner().invoke(cli, ["--mqtt-host", "x", *args])
        assert r.exit_code == 1
        assert "scene boom" in r.output

    def test_scene_remove_all_core_value_error_aborts(self, monkeypatch):
        def boom(*a, **kw):
            raise ValueError("remove-all boom")

        monkeypatch.setattr(scenes_core, "remove_all", boom)
        with _patched(FakeClient()):
            r = CliRunner().invoke(
                cli, ["--mqtt-host", "x", "scene", "remove-all", "lamp"], input="y\n"
            )
        assert r.exit_code == 1
        assert "remove-all boom" in r.output


# ═══════════════════════════════════════════════ extensions


class TestExtensionShowJson:
    def test_extension_show_json(self):
        client = FakeClient()
        client.set_retained("zigbee2mqtt/bridge/extensions", [{"name": "hello", "code": "// hi"}])
        with _patched(client):
            r = CliRunner().invoke(
                cli, ["--mqtt-host", "x", "--json", "extension", "show", "hello"]
            )
        assert r.exit_code == 0, r.output
        assert json.loads(r.output)["code"] == "// hi"


# ═══════════════════════════════════════════════ repl / entry points


def _script_repl(monkeypatch, lines, tmp_path):
    """Run the REPL with a scripted get_input sequence."""
    seq = iter(list(lines))

    def fake_get_input(self, pt_session, project_name="", modified=False, context=""):
        try:
            return next(seq)
        except StopIteration:
            raise EOFError

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("CLI_ANYTHING_NO_COLOR", raising=False)
    monkeypatch.setattr(ReplSkin, "get_input", fake_get_input)
    monkeypatch.setattr(ReplSkin, "create_prompt_session", lambda self: None, raising=False)
    monkeypatch.setattr(ReplSkin, "error", lambda self, message: None)
    return CliRunner().invoke(cli, ["repl"])


class TestReplAndEntryPoints:
    def test_repl_importerror_fallback(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setitem(sys.modules, "cli_anything.zigbee2mqtt.utils.repl_skin", None)
        r = CliRunner().invoke(cli, ["repl"])
        assert r.exit_code == 0, r.output
        assert "prompt-toolkit" in r.output

    def test_repl_swallows_systemexit_from_abort(self, monkeypatch, tmp_path):
        """A dispatched command that aborts raises SystemExit — the loop must
        swallow it and keep running until the user types exit."""
        r = _script_repl(monkeypatch, ["device options dev {oops", "exit"], tmp_path)
        assert r.exit_code == 0, r.output
        assert "error: options_json is not valid JSON" in r.output
        assert "Goodbye" in r.output

    def test_main_entrypoint(self, monkeypatch):
        monkeypatch.setattr(cli_mod, "cli", MagicMock())
        cli_mod.main()
        assert cli_mod.cli.call_count == 1  # type: ignore[attr-defined]

    def test_bridge_options_set_invalid_json_returns_after_abort(self, monkeypatch):
        msgs: list[str] = []
        monkeypatch.setattr(cli_mod, "_abort", lambda m: msgs.append(m))
        with _patched(FakeClient()):
            r = CliRunner().invoke(cli, ["--mqtt-host", "x", "bridge", "options-set", "{not json"])
        assert r.exit_code == 0
        assert any("not valid JSON" in m for m in msgs)

    def test_bridge_definitions_blank_cluster_returns_after_abort(self, monkeypatch):
        msgs: list[str] = []
        monkeypatch.setattr(cli_mod, "_abort", lambda m: msgs.append(m))
        client = FakeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/definitions",
            {"clusters": {"genOnOff": {"ID": 6, "attributes": {}, "commands": {}}}},
        )
        with _patched(client):
            r = CliRunner().invoke(
                cli, ["--mqtt-host", "x", "bridge", "definitions", "--cluster", " "]
            )
        assert r.exit_code == 0
        assert any("cluster is required" in m for m in msgs)


# ═══════════════════════════════════════════════ core edge branches


class TestCoreEdgeBranches:
    def test_last_seen_naive_timestamp(self):
        client = FakeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/devices",
            [
                {
                    "friendly_name": "lamp",
                    "ieee_address": "0x1",
                    "type": "EndDevice",
                    "last_seen": "2026-01-01T10:00:00",
                }
            ],
        )
        out = devices_core.last_seen(client, "lamp")
        assert out["minutes_since_seen"] is not None
        assert out["minutes_since_seen"] > 0

    def test_unbind_without_clusters_omits_key(self):
        client = FakeClient()
        bindings_core.unbind(client, from_="a", to="b")
        assert client.requests[0]["path"] == "device/unbind"
        assert client.requests[0]["payload"] == {"from": "a", "to": "b"}

    def test_list_bindings_target_without_ieee(self):
        client = FakeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/devices",
            [
                {
                    "friendly_name": "lamp",
                    "ieee_address": "0x1",
                    "type": "EndDevice",
                    "endpoints": {
                        "1": {"bindings": [{"cluster": "genOnOff", "target": {"type": "endpoint"}}]}
                    },
                }
            ],
        )
        rows = bindings_core.list_bindings(client)
        assert len(rows) == 1
        assert rows[0]["to_type"] == "endpoint"
        assert rows[0]["to_ieee"] is None
        assert rows[0]["to_device"] is None

    def test_converters_ls_records_non_js_dotted_name(self):
        proc = MagicMock()
        proc.stdout = b"-rw-r--r-- 1 u g 9 May  1 2026 notes.txt\n"
        with patch.object(k8s_backend, "exec_", return_value=proc):
            rows = converters_core.list_converters(
                k8s_backend.K8sTarget(namespace="ns", deployment="d", container="c")
            )
        assert [row["name"] for row in rows] == ["notes.txt"]
        assert rows[0]["size_bytes"] == "9"


# ═══════════════════════════════════════════════ BridgeClient transport


def _client_with_mock_paho(monkeypatch):
    monkeypatch.setattr(BridgeClient, "connect", lambda self: None)
    bc = BridgeClient("localhost")
    bc.client = MagicMock()
    info = MagicMock()
    info.rc = 0
    bc.client.publish.return_value = info
    return bc


class TestBridgeClientTransportEdges:
    def test_publish_connects_lazily(self, monkeypatch):
        bc = _client_with_mock_paho(monkeypatch)
        bc._connected = False

        def real_connect(self):
            self._connected = True

        monkeypatch.setattr(BridgeClient, "connect", real_connect)
        rc = bc.publish("t", "hello")
        assert rc == 0
        assert bc._connected is True

    def test_subscriber_with_nonmatching_filter_is_skipped(self, monkeypatch):
        bc = _client_with_mock_paho(monkeypatch)
        calls: list[str] = []
        bc.subscribe("zigbee2mqtt/other/device", lambda t, p: calls.append(t))

        msg = MagicMock()
        msg.topic = "zigbee2mqtt/lamp"
        msg.payload = json.dumps({"state": "ON"})
        bc._on_message(None, None, msg)
        assert calls == []


# ═══════════════════════════════════════════════ ReplSkin resolution


class TestReplSkinResolution:
    def test_skill_path_falls_back_to_packaged_skill(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("totally-unknown-skill")
        parts = Path(skin.skill_path).parts
        assert parts[-3:] == ("zigbee2mqtt", "skills", "SKILL.md")

    def test_color_detection_without_isatty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))

        class Bare:
            pass  # no isatty attribute at all

        with patch.object(sys, "stdout", Bare()):
            skin = ReplSkin("zigbee2mqtt", history_file=str(tmp_path / "history"))
            assert skin._color is False

    def test_explicit_skill_path_is_kept(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("zigbee2mqtt", skill_path="/custom/SKILL.md")
        assert skin.skill_path == "/custom/SKILL.md"

    def test_skill_path_none_when_no_repo_or_package_skill(self, tmp_path, monkeypatch):
        """Neither the repo-root nor the packaged SKILL.md exists → None."""
        monkeypatch.setenv("HOME", str(tmp_path))
        with patch("pathlib.Path.is_file", return_value=False):
            skin = ReplSkin("totally-unknown-skill")
        assert skin.skill_path is None

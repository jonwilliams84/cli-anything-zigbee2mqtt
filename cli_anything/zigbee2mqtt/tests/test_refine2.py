"""Second refine pass — coverage for usage patterns and error paths still
unexercised after the first refine:

* the interactive REPL loop (banner, exit/quit, help, command dispatch,
  EOF / KeyboardInterrupt, error handling) — a whole usage pattern
* ReplSkin.print_banner, colored/no-color prompt variants, prompt_toolkit
  session factory (including ImportError fallbacks), get_input with a session
* user-facing CLI error paths (invalid JSON args, missing keys, clobber
  guards, unknown clusters/extensions, MqttError propagation)
* core branches: bridge log-level get/set, devices disable/enable/last_seen,
  flatten_endpoint_clusters malformed payloads, extensions.save validation,
  converters ls parsing, project env overrides, BridgeClient.publish payload
  coercion and the missing-paho guard

No MQTT broker required — fake transports throughout.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli_anything.zigbee2mqtt.zigbee2mqtt_cli import cli
from cli_anything.zigbee2mqtt.core import bridge as bridge_core
from cli_anything.zigbee2mqtt.core import converters as converters_core
from cli_anything.zigbee2mqtt.core import devices as devices_core
from cli_anything.zigbee2mqtt.core import extensions as extensions_core
from cli_anything.zigbee2mqtt.core import k8s_backend
from cli_anything.zigbee2mqtt.core import project as project_core
from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient, MqttError
from cli_anything.zigbee2mqtt.utils.repl_skin import ReplSkin


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
        if isinstance(getattr(self, "request_exc", None), Exception):
            raise self.request_exc
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


# ════════════════════════════════════════════════════ REPL loop (E2E)


def _script_repl(monkeypatch, lines, tmp_path, **client_overrides):
    """Run the REPL with a scripted get_input sequence.

    Returns (result, skin_error_messages). Each scripted entry is returned
    once; after the script is exhausted EOFError is raised (Ctrl-D).
    """
    import cli_anything.zigbee2mqtt.zigbee2mqtt_cli as cli_mod

    seq = iter(list(lines))

    def fake_get_input(self, pt_session, project_name="", modified=False, context=""):
        try:
            return next(seq)
        except StopIteration:
            raise EOFError

    errors: list[str] = []

    def fake_error(self, message):
        errors.append(message)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("CLI_ANYTHING_NO_COLOR", raising=False)
    monkeypatch.setattr(ReplSkin, "get_input", fake_get_input)
    monkeypatch.setattr(ReplSkin, "create_prompt_session", lambda self: None, raising=False)
    monkeypatch.setattr(ReplSkin, "error", fake_error)
    if client_overrides:
        monkeypatch.setattr(cli_mod, "make_client", lambda ctx: FakeClient(**client_overrides))
    result = CliRunner().invoke(cli, ["repl"])
    return result, errors


class TestReplLoop:
    """E2E coverage for the interactive shell usage pattern."""

    def test_banner_then_exit(self, monkeypatch, tmp_path):
        result, _ = _script_repl(monkeypatch, ["exit"], tmp_path)
        assert result.exit_code == 0, result.output
        assert "cli-anything" in result.output
        assert "Type help for commands" in result.output
        assert "Goodbye" in result.output

    def test_quit_also_exits(self, monkeypatch, tmp_path):
        result, _ = _script_repl(monkeypatch, ["quit"], tmp_path)
        assert result.exit_code == 0, result.output
        assert "Goodbye" in result.output

    def test_eof_exits(self, monkeypatch, tmp_path):
        result, _ = _script_repl(monkeypatch, [], tmp_path)
        assert result.exit_code == 0, result.output
        assert "Goodbye" in result.output

    def test_blank_lines_are_ignored(self, monkeypatch, tmp_path):
        result, _ = _script_repl(monkeypatch, ["", "   ", "exit"], tmp_path)
        assert result.exit_code == 0, result.output
        assert "Goodbye" in result.output

    def test_help_lists_commands(self, monkeypatch, tmp_path):
        result, _ = _script_repl(monkeypatch, ["help", "exit"], tmp_path)
        assert result.exit_code == 0, result.output
        assert "Commands" in result.output
        assert "bridge" in result.output

    def test_dispatches_cli_command(self, monkeypatch, tmp_path):
        result, errors = _script_repl(monkeypatch, ["config show", "exit"], tmp_path)
        assert result.exit_code == 0, result.output
        assert errors == []
        assert "mqtt_host" in result.output
        assert "Goodbye" in result.output

    def test_unknown_command_hits_error_handler(self, monkeypatch, tmp_path):
        result, errors = _script_repl(monkeypatch, ["definitely-not-a-command", "exit"], tmp_path)
        assert result.exit_code == 0, result.output
        assert errors, "unknown command should be reported via skin.error"
        assert "Goodbye" in result.output

    def test_generic_exception_reported_via_skin_error(self, monkeypatch, tmp_path):
        result, errors = _script_repl(
            monkeypatch,
            ["device rename a b", "exit"],
            tmp_path,
            request_exc=RuntimeError("boom"),
        )
        assert result.exit_code == 0, result.output
        assert errors == ["boom"]
        assert "Goodbye" in result.output

    def test_keyboard_interrupt_exits_gracefully(self, monkeypatch, tmp_path):
        def fake_get_input(self, pt_session, project_name="", modified=False, context=""):
            raise KeyboardInterrupt

        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(ReplSkin, "get_input", fake_get_input)
        monkeypatch.setattr(ReplSkin, "create_prompt_session", lambda self: None)
        result = CliRunner().invoke(cli, ["repl"])
        assert result.exit_code == 0, result.output
        assert "Goodbye" in result.output

    def test_no_subcommand_starts_repl(self, monkeypatch, tmp_path):
        """Bare `cli-anything-zigbee2mqtt` (no subcommand) drops into the REPL."""
        seq = iter(["exit"])

        def fake_get_input(self, pt_session, project_name="", modified=False, context=""):
            try:
                return next(seq)
            except StopIteration:
                raise EOFError

        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(ReplSkin, "get_input", fake_get_input)
        monkeypatch.setattr(ReplSkin, "create_prompt_session", lambda self: None)
        result = CliRunner().invoke(cli, [])
        assert result.exit_code == 0, result.output
        assert "Goodbye" in result.output


# ══════════════════════════════════════════════ ReplSkin extras


class TestPrintBanner:
    def test_banner_contains_brand_and_hints(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("NO_COLOR", "1")
        skin = ReplSkin("zigbee2mqtt", version="9.9.9")
        skin.print_banner()
        out = capsys.readouterr().out
        assert "cli-anything" in out
        assert "Zigbee2Mqtt" in out
        assert "9.9.9" in out
        assert "Type help for commands" in out
        assert "╭" in out and "╰" in out  # box drawing

    def test_banner_wraps_long_meta_lines(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("NO_COLOR", "1")
        skin = ReplSkin("zigbee2mqtt", version="1.0.0")
        long_cmd = "pip install " + "x" * 200
        skin.skill_install_cmd = long_cmd
        skin.print_banner()
        out = capsys.readouterr().out
        assert "pip install" in out and "xxx" in out
        assert out.count("╭") == 1  # single box top


class TestPromptVariants:
    def _skin(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        return ReplSkin("test")

    def test_no_color_prompt_falls_back_to_gt(self, tmp_path, monkeypatch):
        skin = self._skin(tmp_path, monkeypatch)
        assert skin._color is False
        assert skin.prompt().startswith("> ")

    def test_color_prompt_shows_icon(self, tmp_path, monkeypatch):
        skin = self._skin(tmp_path, monkeypatch)
        skin._color = True
        p = skin.prompt()
        assert "◆" in p
        assert "test" in p

    def test_prompt_with_modified_project_context(self, tmp_path, monkeypatch):
        skin = self._skin(tmp_path, monkeypatch)
        p = skin.prompt(project_name="proj", modified=True)
        assert "[proj*]" in p

    def test_prompt_with_context_arg(self, tmp_path, monkeypatch):
        skin = self._skin(tmp_path, monkeypatch)
        assert "[ctx]" in skin.prompt(context="ctx")

    def test_prompt_tokens_include_context(self, tmp_path, monkeypatch):
        skin = self._skin(tmp_path, monkeypatch)
        tokens = skin.prompt_tokens(project_name="proj", modified=True, context="")
        flattened = [t[1] for t in tokens]
        assert "proj*" in flattened

    def test_prompt_tokens_without_context(self, tmp_path, monkeypatch):
        skin = self._skin(tmp_path, monkeypatch)
        tokens = skin.prompt_tokens()
        flattened = [t[1] for t in tokens]
        assert "test" in flattened
        assert not any("*" in t for t in flattened)


class TestPromptToolkitIntegration:
    def test_get_prompt_style_returns_style(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("test")
        style = skin.get_prompt_style()
        assert style is not None
        assert type(style).__module__.startswith("prompt_toolkit")

    def test_get_prompt_style_import_error_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        import sys

        monkeypatch.setitem(sys.modules, "prompt_toolkit.styles", None)
        assert ReplSkin("test").get_prompt_style() is None

    def test_create_prompt_session_returns_session(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("test")
        session = skin.create_prompt_session()
        assert session is not None
        assert hasattr(session, "prompt")

    def test_create_prompt_session_import_error_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        import sys

        monkeypatch.setitem(sys.modules, "prompt_toolkit.history", None)
        assert ReplSkin("test").create_prompt_session() is None

    def test_get_input_with_session(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        skin = ReplSkin("test")

        fake_session = MagicMock()
        fake_session.prompt.return_value = "  hello  "
        assert skin.get_input(fake_session) == "hello"
        assert fake_session.prompt.called


# ══════════════════════════════════════════════ CLI error paths (E2E)


class TestCliArgumentErrorPaths:
    def test_bridge_options_set_invalid_json(self):
        with _patched(FakeClient()):
            r = CliRunner().invoke(cli, ["--mqtt-host", "x", "bridge", "options-set", "{not json"])
        assert r.exit_code == 1
        assert "not valid JSON" in r.output

    def test_scene_add_color_json_must_be_object(self):
        """--color goes through _parse_json_obj, which enforces object-ness."""
        with _patched(FakeClient()):
            r = CliRunner().invoke(
                cli,
                ["--mqtt-host", "x", "scene", "add", "lamp", "1", "--color", "[1,2]"],
            )
        assert r.exit_code == 1
        assert "--color must be a JSON object" in r.output

    def test_device_options_invalid_json(self):
        with _patched(FakeClient()):
            r = CliRunner().invoke(cli, ["--mqtt-host", "x", "device", "options", "dev", "{oops"])
        assert r.exit_code == 1
        assert "not valid JSON" in r.output

    def test_group_options_invalid_json(self):
        with _patched(FakeClient()):
            r = CliRunner().invoke(cli, ["--mqtt-host", "x", "group", "options", "g", "{oops"])
        assert r.exit_code == 1
        assert "--options must be a JSON object" in r.output

    def test_group_options_json_must_be_object(self):
        with _patched(FakeClient()):
            r = CliRunner().invoke(cli, ["--mqtt-host", "x", "group", "options", "g", '"str"'])
        assert r.exit_code == 1
        assert "options must be a JSON object" in r.output

    def test_device_get_requires_keys(self):
        with _patched(FakeClient()):
            r = CliRunner().invoke(cli, ["--mqtt-host", "x", "device", "get", "lamp"])
        assert r.exit_code == 1
        assert "provide at least one key" in r.output

    def test_bridge_commands_requires_cluster(self):
        with _patched(FakeClient()):
            r = CliRunner().invoke(cli, ["--mqtt-host", "x", "bridge", "definitions", "--commands"])
        assert r.exit_code == 1
        assert "--commands requires --cluster" in r.output


class TestDeviceCommandEdgePaths:
    def test_device_rename_mqtt_error_is_reported(self):
        client = FakeClient()
        client.request_exc = MqttError("broker unreachable")
        with _patched(client):
            r = CliRunner().invoke(cli, ["--mqtt-host", "x", "device", "rename", "old", "new"])
        assert r.exit_code == 1
        assert "broker unreachable" in r.output

    def test_device_generate_converter_prints_source(self, tmp_path):
        client = FakeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/devices",
            [{"friendly_name": "odd-device", "ieee_address": "0x1"}],
        )
        client.set_request(
            "device/generate_external_definition",
            {"status": "ok", "source": "// generated converter", "model": "OddDevice"},
        )
        with _patched(client):
            r = CliRunner().invoke(
                cli, ["--mqtt-host", "x", "device", "generate-converter", "odd-device"]
            )
        assert r.exit_code == 0, r.output
        assert "// generated converter" in r.output

    def test_device_generate_converter_saves_to_file(self, tmp_path):
        client = FakeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/devices",
            [{"friendly_name": "odd-device", "ieee_address": "0x1"}],
        )
        client.set_request(
            "device/generate_external_definition",
            {"status": "ok", "source": "// body", "model": "OddDevice"},
        )
        out = tmp_path / "conv.js"
        with _patched(client):
            r = CliRunner().invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "device",
                    "generate-converter",
                    "odd-device",
                    "--output",
                    str(out),
                ],
            )
        assert r.exit_code == 0, r.output
        assert out.read_text() == "// body"
        assert "OddDevice" in r.output

    def test_device_generate_converter_will_not_clobber(self, tmp_path):
        client = FakeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/devices",
            [{"friendly_name": "odd-device", "ieee_address": "0x1"}],
        )
        client.set_request(
            "device/generate_external_definition",
            {"status": "ok", "source": "// body", "model": "OddDevice"},
        )
        out = tmp_path / "conv.js"
        out.write_text("existing")
        with _patched(client):
            r = CliRunner().invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "device",
                    "generate-converter",
                    "odd-device",
                    "--output",
                    str(out),
                ],
            )
        assert r.exit_code == 1
        assert "--overwrite" in r.output
        assert out.read_text() == "existing"

    def test_device_read_value_error_is_reported(self, monkeypatch):
        from cli_anything.zigbee2mqtt.core import attributes as attributes_core

        def boom(*a, **kw):
            raise ValueError("bad zcl combo")

        monkeypatch.setattr(attributes_core, "read", boom)
        with _patched(FakeClient()):
            r = CliRunner().invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "device",
                    "read",
                    "dev",
                    "--cluster",
                    "genOnOff",
                    "--attribute",
                    "onOff",
                ],
            )
        assert r.exit_code == 1
        assert "bad zcl combo" in r.output


class TestBridgeDefinitionsEdgePaths:
    def test_unknown_cluster_aborts(self):
        client = FakeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/definitions",
            {"clusters": {"genOnOff": {"ID": 6, "attributes": {}, "commands": {}}}},
        )
        with _patched(client):
            r = CliRunner().invoke(
                cli,
                ["--mqtt-host", "x", "bridge", "definitions", "--cluster", "noSuchCluster"],
            )
        assert r.exit_code == 1
        assert "unknown cluster" in r.output

    def test_cluster_with_no_attributes_says_so(self):
        client = FakeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/definitions",
            {"clusters": {"emptyCluster": {"ID": 99}}},
        )
        with _patched(client):
            r = CliRunner().invoke(
                cli,
                [
                    "--mqtt-host",
                    "x",
                    "--json",
                    "bridge",
                    "definitions",
                    "--cluster",
                    "emptyCluster",
                ],
            )
        assert r.exit_code == 1
        assert "no attributes" in r.output


class TestGroupAndExtensionPaths:
    def test_group_members_lists_names(self):
        client = FakeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/groups",
            [
                {"id": 1, "friendly_name": "kitchen", "members": [{"ieee_address": "0x1"}]},
            ],
        )
        with _patched(client):
            r = CliRunner().invoke(
                cli, ["--mqtt-host", "x", "--json", "group", "members", "kitchen"]
            )
        assert r.exit_code == 0, r.output
        assert "0x1" in r.output

    def test_extension_show_unknown_aborts(self):
        client = FakeClient()
        client.set_request("extension/list", {"status": "ok", "data": []})
        with _patched(client):
            r = CliRunner().invoke(cli, ["--mqtt-host", "x", "extension", "show", "ghost"])
        assert r.exit_code == 1
        assert "no extension named" in r.output


# ══════════════════════════════════════════════ core unit branches


class TestBridgeLogLevel:
    def test_get_log_level_reads_retained_info(self):
        client = FakeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/info",
            {"advanced": {"log_level": "debug"}},
        )
        assert bridge_core.get_log_level(client) == {"log_level": "debug"}

    def test_get_log_level_missing_advanced(self):
        client = FakeClient()
        client.set_retained("zigbee2mqtt/bridge/info", {})
        assert bridge_core.get_log_level(client) == {"log_level": None}

    def test_set_log_level_valid(self):
        client = FakeClient()
        bridge_core.set_log_level(client, "warn")
        assert client.requests[0]["path"] == "options"
        assert client.requests[0]["payload"] == {"options": {"advanced": {"log_level": "warn"}}}

    def test_set_log_level_invalid(self):
        client = FakeClient()
        with pytest.raises(ValueError, match="level must be one of"):
            bridge_core.set_log_level(client, "verbose")


class TestDeviceEnableDisable:
    def test_disable_and_enable_wire_the_options_endpoint(self):
        client = FakeClient()
        devices_core.disable(client, "lamp")
        devices_core.enable(client, "lamp")
        assert [r["path"] for r in client.requests] == ["device/options", "device/options"]
        assert client.requests[0]["payload"] == {"id": "lamp", "options": {"disabled": True}}
        assert client.requests[1]["payload"] == {"id": "lamp", "options": {"disabled": False}}

    @pytest.mark.parametrize("func", [devices_core.disable, devices_core.enable])
    def test_empty_id_rejected(self, func):
        with pytest.raises(ValueError):
            func(FakeClient(), "")


class TestDeviceLastSeen:
    DEVICES = [
        {
            "friendly_name": "lamp",
            "ieee_address": "0x1",
            "type": "EndDevice",
            "last_seen": "2026-01-01T10:00:00Z",
        }
    ]

    def _client(self):
        client = FakeClient()
        client.set_retained("zigbee2mqtt/bridge/devices", self.DEVICES)
        return client

    def test_last_seen_computes_minutes(self):
        out = devices_core.last_seen(self._client(), "lamp")
        assert out["friendly_name"] == "lamp"
        assert isinstance(out["minutes_since_seen"], float)

    def test_last_seen_without_timestamp(self):
        client = FakeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/devices",
            [{"friendly_name": "lamp", "ieee_address": "0x1", "type": "EndDevice"}],
        )
        out = devices_core.last_seen(client, "lamp")
        assert out["minutes_since_seen"] is None

    def test_last_seen_unparseable_timestamp(self):
        client = FakeClient()
        client.set_retained(
            "zigbee2mqtt/bridge/devices",
            [
                {
                    "friendly_name": "lamp",
                    "ieee_address": "0x1",
                    "type": "EndDevice",
                    "last_seen": "definitely not iso8601",
                }
            ],
        )
        assert devices_core.last_seen(client, "lamp")["minutes_since_seen"] is None

    def test_last_seen_unknown_device(self):
        assert devices_core.last_seen(self._client(), "ghost") == {}


class TestFlattenEndpointClustersEdgeCases:
    def test_endpoints_not_a_dict_yields_nothing(self):
        assert devices_core.flatten_endpoint_clusters({"endpoints": "garbage"}) == []

    def test_clusters_not_a_dict_treated_as_empty(self):
        device = {"endpoints": {"1": {"clusters": [1, 2, 3]}}}
        rows = devices_core.flatten_endpoint_clusters(device)
        assert rows == []

    def test_endpoint_values_that_are_not_dicts_are_skipped(self):
        device = {"endpoints": {"1": "just a string"}}
        assert devices_core.flatten_endpoint_clusters(device) == []

    def test_invalid_direction_raises(self):
        with pytest.raises(ValueError, match="direction"):
            devices_core.flatten_endpoint_clusters({}, direction="sideways")


class TestExtensionsSaveValidation:
    def test_empty_name_rejected(self):
        with pytest.raises(ValueError):
            extensions_core.save(FakeClient(), name="", code="x")

    def test_name_needs_js_suffix(self):
        with pytest.raises(ValueError, match="\\.js"):
            extensions_core.save(FakeClient(), name="nope", code="x")

    def test_empty_code_rejected(self):
        with pytest.raises(ValueError):
            extensions_core.save(FakeClient(), name="ok.js", code="")

    def test_valid_save_hits_extension_save_endpoint(self):
        client = FakeClient()
        extensions_core.save(client, name="ok.js", code="// code")
        assert client.requests[0]["path"] == "extension/save"


class TestConvertersLsParsing:
    LS_OUTPUT = (
        "total 0\n"
        "-rw-r--r-- 1 u g 123 May  1 2026 good.js\n"
        "-rw-r--r-- 1 u g 222 May  2 2026 backup.js.bak\n"
        "drwxr-xr-x 2 u g 60 May  3 2026 .\n"
        "drwxr-xr-x 2 u g 60 May  4 2026 ..\n"
        "should-never-reach-9-fields\n"
        "-rw-r--r-- 1 u g notes.txt\n"
    )

    def _list(self, stdout: str):
        proc = MagicMock()
        proc.stdout = stdout.encode()
        with patch.object(k8s_backend, "exec_", return_value=proc):
            return converters_core.list_converters(
                k8s_backend.K8sTarget(namespace="ns", deployment="d", container="c")
            )

    def test_parses_rows_and_skips_junk(self):
        rows = self._list(self.LS_OUTPUT)
        names = [r["name"] for r in rows]
        assert "good.js" in names
        assert "backup.js.bak" in names
        assert "." not in names and ".." not in names
        good = next(r for r in rows if r["name"] == "good.js")
        assert good["size_bytes"] == "123"
        assert good["modified"] == "May 1 2026"

    def test_short_lines_and_missing_size(self):
        rows = self._list("onlyninefields-line-without-size -1 -2 -3 -4 -5 -6 -7 -8\n")
        # a 9-field line: size is parts[4]
        assert rows[0]["size_bytes"] == "-4"


class TestProjectEnvOverrides:
    def test_int_env_override(self, monkeypatch):
        monkeypatch.setenv("CLI_Z2M_MQTT_PORT", "8888")
        cfg = project_core.load_config(path=None)
        assert cfg["mqtt_port"] == 8888

    def test_bad_int_env_falls_back_to_string(self, monkeypatch):
        monkeypatch.setenv("CLI_Z2M_MQTT_PORT", "not-a-number")
        cfg = project_core.load_config(path=None)
        assert cfg["mqtt_port"] == "not-a-number"

    def test_bool_env_override(self, monkeypatch):
        monkeypatch.setenv("CLI_Z2M_MQTT_HOST", "broker.local")
        cfg = project_core.load_config(path=None)
        assert cfg["mqtt_host"] == "broker.local"

    def test_corrupt_config_file_falls_back_to_defaults(self, tmp_path, monkeypatch):
        bad = tmp_path / "profile.json"
        bad.write_text("{not json")
        monkeypatch.setattr(project_core, "DEFAULT_CONFIG_PATH", bad)
        cfg = project_core.load_config(path=bad)
        assert cfg["base_topic"] == "zigbee2mqtt"


# ══════════════════════════════════════════════ BridgeClient transport


def _client_with_mock_paho(monkeypatch):
    """A BridgeClient whose paho client is a MagicMock (never connects)."""
    monkeypatch.setattr(BridgeClient, "connect", lambda self: None)
    bc = BridgeClient("localhost")
    bc._connected = True
    bc.client = MagicMock()
    info = MagicMock()
    info.rc = 0
    bc.client.publish.return_value = info
    return bc


class TestBridgeClientPublish:
    def test_dict_payload_is_json_encoded(self, monkeypatch):
        bc = _client_with_mock_paho(monkeypatch)
        bc.publish("t", {"a": 1})
        body = bc.client.publish.call_args[0][1]
        assert json.loads(body) == {"a": 1}

    def test_bool_payload(self, monkeypatch):
        bc = _client_with_mock_paho(monkeypatch)
        bc.publish("t", True)
        assert bc.client.publish.call_args[0][1] == "true"
        bc.publish("t", False)
        assert bc.client.publish.call_args[0][1] == "false"

    def test_numeric_payload(self, monkeypatch):
        bc = _client_with_mock_paho(monkeypatch)
        bc.publish("t", 7)
        assert bc.client.publish.call_args[0][1] == "7"
        bc.publish("t", 1.5)
        assert bc.client.publish.call_args[0][1] == "1.5"

    def test_none_payload_becomes_empty_string(self, monkeypatch):
        bc = _client_with_mock_paho(monkeypatch)
        bc.publish("t", None)
        assert bc.client.publish.call_args[0][1] == ""

    def test_string_payload_passthrough(self, monkeypatch):
        bc = _client_with_mock_paho(monkeypatch)
        bc.publish("t", "hello")
        assert bc.client.publish.call_args[0][1] == "hello"


class TestBridgeClientGuards:
    def test_missing_paho_raises_mqtt_error(self, monkeypatch):
        import cli_anything.zigbee2mqtt.core.mqtt_client as m

        monkeypatch.setattr(m, "mqtt", None)
        with pytest.raises(MqttError, match="paho-mqtt not installed"):
            BridgeClient("localhost")

    def test_subscribe_connects_first(self, monkeypatch):
        bc = BridgeClient("localhost")
        bc.client = MagicMock()
        connected = []
        real_setattr = None

        def fake_connect(self):
            connected.append(True)
            self._connected = True

        monkeypatch.setattr(bc, "connect", fake_connect.__get__(bc))
        bc._connected = False
        bc.subscribe("zigbee2mqtt/#", lambda t, p: None)
        assert connected == [True]
        bc.client.subscribe.assert_called_once()

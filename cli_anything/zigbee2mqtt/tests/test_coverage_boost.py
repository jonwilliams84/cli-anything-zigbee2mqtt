"""Tests for uncovered logic in converters, k8s_backend, and admin modules.

These target error paths, edge cases, and branches that the existing suite
never exercises — not trivial wiring or constants.
"""

from __future__ import annotations

import types
from unittest.mock import patch

import pytest

from cli_anything.zigbee2mqtt.core import admin as admin_core
from cli_anything.zigbee2mqtt.core import bridge as bridge_core
from cli_anything.zigbee2mqtt.core import converters as converters_core
from cli_anything.zigbee2mqtt.core import groups as groups_core
from cli_anything.zigbee2mqtt.core import k8s_backend as k8s_core


# ════════════════════════════════════════════════════════════════════════
# Fake BridgeClient (same pattern as test_refine.py)
# ════════════════════════════════════════════════════════════════════════


class FakeClient:
    base_topic = "zigbee2mqtt"

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self._request_responses: dict[str, dict] = {}
        self._retained: dict[str, str] = {}

    def set_request(self, path: str, response: dict) -> None:
        self._request_responses[path] = response

    def set_retained(self, topic: str, payload: str) -> None:
        self._retained[topic] = payload

    def set_retained_groups(self, payload: str) -> None:
        self.set_retained(f"{self.base_topic}/bridge/groups", payload)

    def set_retained_info(self, payload: str) -> None:
        self.set_retained(f"{self.base_topic}/bridge/info", payload)

    def set_retained_state(self, payload: str) -> None:
        self.set_retained(f"{self.base_topic}/bridge/state", payload)

    def collect_retained(self, topic: str, *, timeout: float = 5.0):
        return self._retained.get(topic)

    def request(self, path: str, payload=None, *, timeout: float = 0):
        self.requests.append({"path": path, "payload": payload, "timeout": timeout})
        return self._request_responses.get(path, {"status": "ok", "data": {}})


# ════════════════════════════════════════════════════════════════════════
# k8s_backend — validation and error paths
# ════════════════════════════════════════════════════════════════════════


class TestK8sBackendValidation:
    """write_external_converter / remove_external_converter name validation."""

    def test_write_rejects_path_traversal(self):
        target = k8s_core.K8sTarget()
        with pytest.raises(ValueError, match="bare filename"):
            k8s_core.write_external_converter(target, "../evil.js", "code")

    def test_write_rejects_dotfile(self):
        target = k8s_core.K8sTarget()
        with pytest.raises(ValueError, match="bare filename"):
            k8s_core.write_external_converter(target, ".hidden", "code")

    def test_write_appends_js_extension(self):
        """A name without .js suffix should get it appended automatically."""
        target = k8s_core.K8sTarget()
        captured_args: list = []

        def fake_exec(tgt, argv, *, stdin=None, check=True):
            captured_args.append((argv, stdin, check))
            # Return a fake completed-process-like object
            return types.SimpleNamespace(stdout=b"", stderr=b"", returncode=0)

        with patch.object(k8s_core, "exec_", fake_exec):
            k8s_core.write_external_converter(target, "my_converter", "content")

        # The shell command should reference my_converter.js (appended .js)
        shell_cmd = captured_args[0][0][2]  # argv = ["sh", "-c", <cmd>]
        assert "my_converter.js" in shell_cmd
        assert "my_converter." not in shell_cmd.replace("my_converter.js", "")

    def test_write_backup_command_includes_cp(self):
        """When backup=True, the setup command should include a cp backup step."""
        target = k8s_core.K8sTarget()
        captured_args: list = []

        def fake_exec(tgt, argv, *, stdin=None, check=True):
            captured_args.append((argv, stdin, check))
            return types.SimpleNamespace(stdout=b"", stderr=b"", returncode=0)

        with patch.object(k8s_core, "exec_", fake_exec):
            k8s_core.write_external_converter(target, "test.js", "content", backup=True)

        shell_cmd = captured_args[0][0][2]
        assert "cp" in shell_cmd  # backup uses cp
        assert ".bak" in shell_cmd

    def test_write_no_backup_omits_cp(self):
        """When backup=False, no cp backup step should be present."""
        target = k8s_core.K8sTarget()
        captured_args: list = []

        def fake_exec(tgt, argv, *, stdin=None, check=True):
            captured_args.append((argv, stdin, check))
            return types.SimpleNamespace(stdout=b"", stderr=b"", returncode=0)

        with patch.object(k8s_core, "exec_", fake_exec):
            k8s_core.write_external_converter(target, "test.js", "content", backup=False)

        shell_cmd = captured_args[0][0][2]
        assert "cp " not in shell_cmd
        # Should still have mkdir and cat
        assert "mkdir" in shell_cmd
        assert "cat >" in shell_cmd

    def test_write_passes_content_as_stdin(self):
        """The converter content should be passed as stdin to the exec call."""
        target = k8s_core.K8sTarget()
        captured_args: list = []

        def fake_exec(tgt, argv, *, stdin=None, check=True):
            captured_args.append((argv, stdin, check))
            return types.SimpleNamespace(stdout=b"", stderr=b"", returncode=0)

        with patch.object(k8s_core, "exec_", fake_exec):
            k8s_core.write_external_converter(target, "test.js", "module.exports = {};")

        stdin_data = captured_args[0][1]
        assert stdin_data == "module.exports = {};"

    def test_remove_rejects_path_traversal(self):
        target = k8s_core.K8sTarget()
        with pytest.raises(ValueError, match="bare filename"):
            k8s_core.remove_external_converter(target, "../evil.js")

    def test_remove_rejects_dotfile(self):
        target = k8s_core.K8sTarget()
        with pytest.raises(ValueError, match="bare filename"):
            k8s_core.remove_external_converter(target, ".hidden")

    def test_remove_with_backup_uses_mv(self):
        """remove with backup=True should use mv to a .removed.bak file."""
        target = k8s_core.K8sTarget()
        captured_args: list = []

        def fake_exec(tgt, argv, *, stdin=None, check=True):
            captured_args.append((argv, stdin, check))
            return types.SimpleNamespace(stdout=b"", stderr=b"", returncode=0)

        with patch.object(k8s_core, "exec_", fake_exec):
            k8s_core.remove_external_converter(target, "old.js", backup=True)

        shell_cmd = captured_args[0][0][2]
        assert "mv" in shell_cmd
        assert ".removed.bak" in shell_cmd

    def test_remove_without_backup_uses_rm(self):
        """remove with backup=False should use rm -f."""
        target = k8s_core.K8sTarget()
        captured_args: list = []

        def fake_exec(tgt, argv, *, stdin=None, check=True):
            captured_args.append((argv, stdin, check))
            return types.SimpleNamespace(stdout=b"", stderr=b"", returncode=0)

        with patch.object(k8s_core, "exec_", fake_exec):
            k8s_core.remove_external_converter(target, "old.js", backup=False)

        argv = captured_args[0][0]
        assert argv[0] == "rm"
        assert "-f" in argv
        assert "old.js" in " ".join(argv)


class TestK8sBackendKubectlMissing:
    """_kubectl() should raise RuntimeError when kubectl is not on PATH."""

    def test_kubectl_not_found_raises(self):
        with patch("cli_anything.zigbee2mqtt.core.k8s_backend.shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="kubectl not found"):
                k8s_core._kubectl()

    def test_kubectl_found_returns_path(self):
        with patch(
            "cli_anything.zigbee2mqtt.core.k8s_backend.shutil.which",
            return_value="/usr/local/bin/kubectl",
        ):
            result = k8s_core._kubectl()
            assert result == "/usr/local/bin/kubectl"


class TestK8sBackendRunFailure:
    """_run should raise RuntimeError when kubectl exits non-zero and check=True."""

    def test_run_raises_on_nonzero_with_check(self):
        fake_proc = types.SimpleNamespace(stdout=b"", stderr=b"error: pod not found", returncode=1)
        with patch(
            "cli_anything.zigbee2mqtt.core.k8s_backend._kubectl", return_value="/fake/kubectl"
        ):
            with patch("subprocess.run", return_value=fake_proc):
                with pytest.raises(RuntimeError, match="failed"):
                    k8s_core._run(["get", "pods"], check=True)

    def test_run_returns_proc_on_nonzero_without_check(self):
        """When check=False, a non-zero exit should NOT raise."""
        fake_proc = types.SimpleNamespace(stdout=b"some output", stderr=b"warning", returncode=1)
        with patch(
            "cli_anything.zigbee2mqtt.core.k8s_backend._kubectl", return_value="/fake/kubectl"
        ):
            with patch("subprocess.run", return_value=fake_proc):
                proc = k8s_core._run(["get", "pods"], check=False)
                assert proc.returncode == 1
                assert proc.stdout == b"some output"


class TestK8sBackendExecArgv:
    """exec_ should build the correct kubectl exec argument list."""

    def test_exec_args_without_stdin(self):
        captured: list = []

        def fake_run(args, **kwargs):
            captured.append((args, kwargs.get("input"), kwargs.get("check", True)))
            return types.SimpleNamespace(stdout=b"ok", stderr=b"", returncode=0)

        target = k8s_core.K8sTarget(namespace="ns1", deployment="dep1", container="ctr1")
        with patch(
            "cli_anything.zigbee2mqtt.core.k8s_backend._kubectl", return_value="/fake/kubectl"
        ):
            with patch("subprocess.run", side_effect=fake_run):
                k8s_core.exec_(target, ["ls", "/app/data"])

        args = captured[0][0]
        assert args[0] == "/fake/kubectl"
        assert "-n" in args
        assert "ns1" in args
        assert "exec" in args
        assert "deploy/dep1" in args
        assert "-c" in args
        assert "ctr1" in args
        assert "--" in args
        assert args[-2:] == ["ls", "/app/data"]
        # No -i flag when stdin is None
        assert "-i" not in args

    def test_exec_args_with_stdin_adds_i_flag(self):
        captured: list = []

        def fake_run(args, **kwargs):
            captured.append((args, kwargs.get("input"), kwargs.get("check", True)))
            return types.SimpleNamespace(stdout=b"ok", stderr=b"", returncode=0)

        target = k8s_core.K8sTarget()
        with patch(
            "cli_anything.zigbee2mqtt.core.k8s_backend._kubectl", return_value="/fake/kubectl"
        ):
            with patch("subprocess.run", side_effect=fake_run):
                k8s_core.exec_(target, ["cat"], stdin="hello world")

        args = captured[0][0]
        stdin_data = captured[0][1]
        assert "-i" in args
        assert stdin_data == b"hello world"


class TestK8sBackendRolloutStatus:
    """rollout_status should return combined stdout+stderr, never raise."""

    def test_rollout_status_returns_output(self):
        fake_proc = types.SimpleNamespace(
            stdout=b"deployment successfully rolled out\n",
            stderr=b"",
            returncode=0,
        )
        target = k8s_core.K8sTarget()
        with patch(
            "cli_anything.zigbee2mqtt.core.k8s_backend._kubectl", return_value="/fake/kubectl"
        ):
            with patch("subprocess.run", return_value=fake_proc):
                result = k8s_core.rollout_status(target)
                assert "successfully rolled out" in result

    def test_rollout_status_includes_stderr(self):
        """Even on failure, rollout_status returns output rather than raising."""
        fake_proc = types.SimpleNamespace(
            stdout=b"",
            stderr=b"error: timed out waiting for rollout\n",
            returncode=1,
        )
        target = k8s_core.K8sTarget()
        with patch(
            "cli_anything.zigbee2mqtt.core.k8s_backend._kubectl", return_value="/fake/kubectl"
        ):
            with patch("subprocess.run", return_value=fake_proc):
                result = k8s_backend_rollout(target)
                assert "timed out" in result

    def test_rollout_status_empty_output(self):
        fake_proc = types.SimpleNamespace(stdout=b"", stderr=b"", returncode=0)
        target = k8s_core.K8sTarget()
        with patch(
            "cli_anything.zigbee2mqtt.core.k8s_backend._kubectl", return_value="/fake/kubectl"
        ):
            with patch("subprocess.run", return_value=fake_proc):
                result = k8s_core.rollout_status(target)
                assert result == ""


# Helper to avoid confusion — just calls rollout_status
def k8s_backend_rollout(target):
    return k8s_core.rollout_status(target)


class TestK8sBackendListExternalConverters:
    """list_external_converters parses ls -1 output into a list of names."""

    def test_parses_lines(self):
        fake_proc = types.SimpleNamespace(
            stdout=b"auto-rename.js\nlog-tap.js\n\n", stderr=b"", returncode=0
        )
        target = k8s_core.K8sTarget()
        with patch.object(k8s_core, "exec_", return_value=fake_proc):
            result = k8s_core.list_external_converters(target)
        assert result == ["auto-rename.js", "log-tap.js"]

    def test_empty_output(self):
        fake_proc = types.SimpleNamespace(stdout=b"", stderr=b"", returncode=0)
        target = k8s_core.K8sTarget()
        with patch.object(k8s_core, "exec_", return_value=fake_proc):
            result = k8s_core.list_external_converters(target)
        assert result == []

    def test_strips_whitespace(self):
        fake_proc = types.SimpleNamespace(
            stdout=b"  spaced.js  \n\ttabbed.js\t\n", stderr=b"", returncode=0
        )
        target = k8s_core.K8sTarget()
        with patch.object(k8s_core, "exec_", return_value=fake_proc):
            result = k8s_core.list_external_converters(target)
        assert result == ["spaced.js", "tabbed.js"]


class TestK8sBackendReadExternalConverter:
    """read_external_converter returns decoded stdout from cat."""

    def test_returns_content(self):
        fake_proc = types.SimpleNamespace(
            stdout=b"module.exports = class Foo {};", stderr=b"", returncode=0
        )
        target = k8s_core.K8sTarget()
        with patch.object(k8s_core, "exec_", return_value=fake_proc):
            result = k8s_core.read_external_converter(target, "foo.js")
        assert "module.exports" in result

    def test_empty_file(self):
        fake_proc = types.SimpleNamespace(stdout=b"", stderr=b"", returncode=0)
        target = k8s_core.K8sTarget()
        with patch.object(k8s_core, "exec_", return_value=fake_proc):
            result = k8s_core.read_external_converter(target, "empty.js")
        assert result == ""


# ════════════════════════════════════════════════════════════════════════
# converters — list_converters parsing, add/remove/show/add_from_file
# ════════════════════════════════════════════════════════════════════════


class TestConvertersList:
    """list_converters parses `ls -la` output into structured rows."""

    LS_OUTPUT = (
        "total 8\n"
        "drwxr-xr-x 2 root root 4096 Jul 28 12:00 .\n"
        "drwxr-xr-x 3 root root 4096 Jul 28 11:00 ..\n"
        "-rw-r--r-- 1 root root  512 Jul 28 12:00 auto-rename.js\n"
        "-rw-r--r-- 1 root root 1024 Jul 27 10:00 log-tap.js\n"
        "-rw-r--r-- 1 root root  256 Jul 26 09:00 old.bak\n"
    )

    def test_parses_js_files(self):
        fake_proc = types.SimpleNamespace(stdout=self.LS_OUTPUT.encode(), stderr=b"", returncode=0)
        target = k8s_core.K8sTarget()
        with patch.object(k8s_core, "exec_", return_value=fake_proc):
            rows = converters_core.list_converters(target)
        names = [r["name"] for r in rows]
        assert "auto-rename.js" in names
        assert "log-tap.js" in names
        assert "old.bak" in names

    def test_skips_dot_and_dotdot(self):
        fake_proc = types.SimpleNamespace(stdout=self.LS_OUTPUT.encode(), stderr=b"", returncode=0)
        target = k8s_core.K8sTarget()
        with patch.object(k8s_core, "exec_", return_value=fake_proc):
            rows = converters_core.list_converters(target)
        names = [r["name"] for r in rows]
        assert "." not in names
        assert ".." not in names

    def test_extracts_size_and_modified(self):
        fake_proc = types.SimpleNamespace(stdout=self.LS_OUTPUT.encode(), stderr=b"", returncode=0)
        target = k8s_core.K8sTarget()
        with patch.object(k8s_core, "exec_", return_value=fake_proc):
            rows = converters_core.list_converters(target)
        auto_row = next(r for r in rows if r["name"] == "auto-rename.js")
        assert auto_row["size_bytes"] == "512"
        assert "Jul 28 12:00" in auto_row["modified"]

    def test_empty_output(self):
        fake_proc = types.SimpleNamespace(stdout=b"", stderr=b"", returncode=0)
        target = k8s_core.K8sTarget()
        with patch.object(k8s_core, "exec_", return_value=fake_proc):
            rows = converters_core.list_converters(target)
        assert rows == []

    def test_short_line_skipped(self):
        """Lines with fewer than 9 fields should be skipped."""
        fake_proc = types.SimpleNamespace(stdout=b"short line\n", stderr=b"", returncode=0)
        target = k8s_core.K8sTarget()
        with patch.object(k8s_core, "exec_", return_value=fake_proc):
            rows = converters_core.list_converters(target)
        assert rows == []

    def test_none_stdout_handled(self):
        """exec_ returning None stdout should not crash."""
        fake_proc = types.SimpleNamespace(stdout=None, stderr=b"", returncode=0)
        target = k8s_core.K8sTarget()
        with patch.object(k8s_core, "exec_", return_value=fake_proc):
            rows = converters_core.list_converters(target)
        assert rows == []


class TestConvertersShow:
    """show delegates to k8s_backend.read_external_converter."""

    def test_returns_content(self):
        target = k8s_core.K8sTarget()
        with patch.object(
            k8s_core, "read_external_converter", return_value="module.exports = {};"
        ) as mock_read:
            result = converters_core.show(target, "test.js")
        assert result == "module.exports = {};"
        mock_read.assert_called_once_with(target, "test.js")


class TestConvertersAdd:
    """add writes content and returns metadata dict."""

    def test_add_returns_metadata(self):
        target = k8s_core.K8sTarget()
        content = "module.exports = class Foo {};"
        with patch.object(k8s_core, "write_external_converter") as mock_write:
            result = converters_core.add(target, name="foo.js", content=content)
        mock_write.assert_called_once_with(target, "foo.js", content, backup=True)
        assert result["name"] == "foo.js"
        assert result["bytes"] == len(content.encode("utf-8"))
        assert result["backup"] is True

    def test_add_backup_false(self):
        target = k8s_core.K8sTarget()
        with patch.object(k8s_core, "write_external_converter") as mock_write:
            result = converters_core.add(target, name="foo.js", content="x", backup=False)
        mock_write.assert_called_once_with(target, "foo.js", "x", backup=False)
        assert result["backup"] is False

    def test_add_byte_count_is_utf8(self):
        """bytes should reflect UTF-8 encoding, not character count."""
        target = k8s_core.K8sTarget()
        content = "héllo"  # 5 chars, 6 bytes in UTF-8
        with patch.object(k8s_core, "write_external_converter"):
            result = converters_core.add(target, name="foo.js", content=content)
        assert result["bytes"] == 6  # 'é' is 2 bytes in UTF-8


class TestConvertersAddFromFile:
    """add_from_file reads a local file and delegates to add()."""

    def test_add_from_file(self, tmp_path):
        target = k8s_core.K8sTarget()
        js_file = tmp_path / "converter.js"
        js_file.write_text("module.exports = {};", encoding="utf-8")
        with patch.object(k8s_core, "write_external_converter") as mock_write:
            result = converters_core.add_from_file(
                target, name="converter.js", local_path=str(js_file)
            )
        mock_write.assert_called_once_with(
            target, "converter.js", "module.exports = {};", backup=True
        )
        assert result["name"] == "converter.js"
        assert result["bytes"] == len("module.exports = {};".encode("utf-8"))

    def test_add_from_file_missing_path_raises(self):
        target = k8s_core.K8sTarget()
        with pytest.raises(FileNotFoundError):
            converters_core.add_from_file(
                target, name="foo.js", local_path="/nonexistent/path/file.js"
            )


class TestConvertersRemove:
    """remove delegates to k8s_backend and returns metadata."""

    def test_remove_returns_metadata(self):
        target = k8s_core.K8sTarget()
        with patch.object(k8s_core, "remove_external_converter") as mock_remove:
            result = converters_core.remove(target, "old.js", backup=True)
        mock_remove.assert_called_once_with(target, "old.js", backup=True)
        assert result["name"] == "old.js"
        assert result["removed"] is True
        assert result["backup"] is True

    def test_remove_backup_false(self):
        target = k8s_core.K8sTarget()
        with patch.object(k8s_core, "remove_external_converter") as mock_remove:
            result = converters_core.remove(target, "old.js", backup=False)
        mock_remove.assert_called_once_with(target, "old.js", backup=False)
        assert result["backup"] is False


# ════════════════════════════════════════════════════════════════════════
# admin — network_map validation, permit_join payload, touchlink payloads
# ════════════════════════════════════════════════════════════════════════


class TestAdminPermitJoin:
    """permit_join builds the correct payload with optional device field."""

    def test_basic_payload(self):
        fc = FakeClient()
        admin_core.permit_join(fc, value=True)
        call = fc.requests[0]
        assert call["path"] == "permit_join"
        assert call["payload"]["value"] is True
        assert call["payload"]["time"] == 254
        assert "device" not in call["payload"]

    def test_custom_time(self):
        fc = FakeClient()
        admin_core.permit_join(fc, value=False, time_secs=60)
        call = fc.requests[0]
        assert call["payload"]["value"] is False
        assert call["payload"]["time"] == 60

    def test_with_device(self):
        fc = FakeClient()
        admin_core.permit_join(fc, value=True, device="router_living")
        call = fc.requests[0]
        assert call["payload"]["device"] == "router_living"

    def test_value_is_coerced_to_bool(self):
        """bool(value) should coerce truthy/falsy values."""
        fc = FakeClient()
        admin_core.permit_join(fc, value=1)
        assert fc.requests[0]["payload"]["value"] is True

        fc2 = FakeClient()
        admin_core.permit_join(fc2, value=0)
        assert fc2.requests[0]["payload"]["value"] is False

    def test_time_is_coerced_to_int(self):
        fc = FakeClient()
        admin_core.permit_join(fc, value=True, time_secs=120.5)
        assert fc.requests[0]["payload"]["time"] == 120


class TestAdminNetworkMap:
    """network_map validates type_ and builds the correct payload."""

    def test_valid_type_raw(self):
        fc = FakeClient()
        admin_core.network_map(fc, type_="raw")
        call = fc.requests[0]
        assert call["path"] == "networkmap"
        assert call["payload"]["type"] == "raw"
        assert call["payload"]["routes"] is True

    def test_valid_type_graphviz(self):
        fc = FakeClient()
        admin_core.network_map(fc, type_="graphviz", routes=False)
        call = fc.requests[0]
        assert call["payload"]["type"] == "graphviz"
        assert call["payload"]["routes"] is False

    def test_valid_type_plantuml(self):
        fc = FakeClient()
        admin_core.network_map(fc, type_="plantuml")
        assert fc.requests[0]["payload"]["type"] == "plantuml"

    def test_invalid_type_raises(self):
        fc = FakeClient()
        with pytest.raises(ValueError, match="type_ must be"):
            admin_core.network_map(fc, type_="xml")

    def test_empty_type_raises(self):
        fc = FakeClient()
        with pytest.raises(ValueError, match="type_ must be"):
            admin_core.network_map(fc, type_="")


class TestAdminTouchlink:
    """touchlink functions build correct payloads."""

    def test_scan(self):
        fc = FakeClient()
        admin_core.touchlink_scan(fc)
        call = fc.requests[0]
        assert call["path"] == "touchlink/scan"
        assert call["payload"] == {}

    def test_identify(self):
        fc = FakeClient()
        admin_core.touchlink_identify(fc, ieee="0x1234", channel=15)
        call = fc.requests[0]
        assert call["path"] == "touchlink/identify"
        assert call["payload"]["ieee_address"] == "0x1234"
        assert call["payload"]["channel"] == 15

    def test_factory_reset_with_target(self):
        fc = FakeClient()
        admin_core.touchlink_factory_reset(fc, ieee="0xabcd", channel=11)
        call = fc.requests[0]
        assert call["path"] == "touchlink/factory_reset"
        assert call["payload"]["ieee_address"] == "0xabcd"
        assert call["payload"]["channel"] == 11

    def test_factory_reset_without_target(self):
        """factory_reset with no ieee/channel should send empty payload."""
        fc = FakeClient()
        admin_core.touchlink_factory_reset(fc)
        call = fc.requests[0]
        assert call["path"] == "touchlink/factory_reset"
        assert call["payload"] == {}

    def test_factory_reset_only_ieee(self):
        fc = FakeClient()
        admin_core.touchlink_factory_reset(fc, ieee="0xdead")
        call = fc.requests[0]
        assert call["payload"] == {"ieee_address": "0xdead"}
        assert "channel" not in call["payload"]

    def test_factory_reset_only_channel(self):
        fc = FakeClient()
        admin_core.touchlink_factory_reset(fc, channel=25)
        call = fc.requests[0]
        assert call["payload"] == {"channel": 25}
        assert "ieee_address" not in call["payload"]


class TestAdminCoordinatorCheck:
    """coordinator_check sends the right request."""

    def test_coordinator_check(self):
        fc = FakeClient()
        admin_core.coordinator_check(fc)
        call = fc.requests[0]
        assert call["path"] == "coordinator_check"
        assert call["payload"] == {}


class TestAdminBackup:
    """backup sends the right request."""

    def test_backup(self):
        fc = FakeClient()
        admin_core.backup(fc)
        call = fc.requests[0]
        assert call["path"] == "backup"
        assert call["payload"] == {}


# ════════════════════════════════════════════════════════════════════════
# groups — list_groups parsing, add/remove/rename/membership payloads
# ════════════════════════════════════════════════════════════════════════


class TestGroupsList:
    """list_groups parses retained bridge/groups JSON, handling edge cases."""

    def test_parses_list(self):
        fc = FakeClient()
        fc.set_retained_groups('[{"id": 1, "friendly_name": "kitchen"}]')
        result = groups_core.list_groups(fc)
        assert len(result) == 1
        assert result[0]["friendly_name"] == "kitchen"

    def test_empty_when_no_retained(self):
        fc = FakeClient()
        result = groups_core.list_groups(fc)
        assert result == []

    def test_empty_on_malformed_json(self):
        fc = FakeClient()
        fc.set_retained_groups("not json")
        result = groups_core.list_groups(fc)
        assert result == []

    def test_empty_when_not_a_list(self):
        """If the retained payload is valid JSON but not a list, return []."""
        fc = FakeClient()
        fc.set_retained_groups('{"not": "a list"}')
        result = groups_core.list_groups(fc)
        assert result == []


class TestGroupsAdd:
    """add builds the correct payload with optional id."""

    def test_add_without_id(self):
        fc = FakeClient()
        groups_core.add(fc, "living-room")
        call = fc.requests[0]
        assert call["path"] == "group/add"
        assert call["payload"]["friendly_name"] == "living-room"
        assert "id" not in call["payload"]

    def test_add_with_id(self):
        fc = FakeClient()
        groups_core.add(fc, "kitchen", id_=5)
        call = fc.requests[0]
        assert call["payload"]["friendly_name"] == "kitchen"
        assert call["payload"]["id"] == 5


class TestGroupsRemove:
    """remove sends group/remove with id and force flag."""

    def test_remove_default(self):
        fc = FakeClient()
        groups_core.remove(fc, "kitchen")
        call = fc.requests[0]
        assert call["path"] == "group/remove"
        assert call["payload"]["id"] == "kitchen"
        assert call["payload"]["force"] is False

    def test_remove_force(self):
        fc = FakeClient()
        groups_core.remove(fc, "kitchen", force=True)
        assert fc.requests[0]["payload"]["force"] is True


class TestGroupsRename:
    """rename sends group/rename with from/to."""

    def test_rename(self):
        fc = FakeClient()
        groups_core.rename(fc, "old-name", "new-name")
        call = fc.requests[0]
        assert call["path"] == "group/rename"
        assert call["payload"]["from"] == "old-name"
        assert call["payload"]["to"] == "new-name"


class TestGroupsMembership:
    """add_member / remove_member / remove_all_members build correct payloads."""

    def test_add_member(self):
        fc = FakeClient()
        groups_core.add_member(fc, "kitchen", "bulb_1")
        call = fc.requests[0]
        assert call["path"] == "group/members/add"
        assert call["payload"]["group"] == "kitchen"
        assert call["payload"]["device"] == "bulb_1"

    def test_remove_member_default(self):
        fc = FakeClient()
        groups_core.remove_member(fc, "kitchen", "bulb_1")
        call = fc.requests[0]
        assert call["path"] == "group/members/remove"
        assert call["payload"]["group"] == "kitchen"
        assert call["payload"]["device"] == "bulb_1"
        assert call["payload"]["skip_disable_reporting"] is False

    def test_remove_member_skip_reporting(self):
        fc = FakeClient()
        groups_core.remove_member(fc, "kitchen", "bulb_1", skip_disable_reporting=True)
        assert fc.requests[0]["payload"]["skip_disable_reporting"] is True

    def test_remove_all_members(self):
        fc = FakeClient()
        groups_core.remove_all_members(fc, "kitchen")
        call = fc.requests[0]
        assert call["path"] == "group/members/remove_all"
        assert call["payload"]["group"] == "kitchen"
        assert "device" not in call["payload"]


# ════════════════════════════════════════════════════════════════════════
# bridge — info/state parsing edge cases
# ════════════════════════════════════════════════════════════════════════


class TestBridgeInfo:
    """info() parses retained bridge/info, handling empty/malformed payloads."""

    def test_info_returns_parsed_json(self):
        fc = FakeClient()
        fc.set_retained_info('{"version": "1.35.0", "coordinator": {"type": "EZSP"}}')
        result = bridge_core.info(fc)
        assert result["version"] == "1.35.0"
        assert result["coordinator"]["type"] == "EZSP"

    def test_info_empty_returns_empty_dict(self):
        fc = FakeClient()
        result = bridge_core.info(fc)
        assert result == {}

    def test_info_malformed_returns_raw(self):
        fc = FakeClient()
        fc.set_retained_info("not json at all")
        result = bridge_core.info(fc)
        assert result == {"raw": "not json at all"}


class TestBridgeState:
    """state() handles plain strings, JSON objects, and empty payloads."""

    def test_state_plain_string(self):
        fc = FakeClient()
        fc.set_retained_state("online")
        assert bridge_core.state(fc) == "online"

    def test_state_json_object(self):
        fc = FakeClient()
        fc.set_retained_state('{"state": "online"}')
        assert bridge_core.state(fc) == "online"

    def test_state_empty(self):
        fc = FakeClient()
        assert bridge_core.state(fc) == ""

    def test_state_malformed_json_returns_raw(self):
        """If the payload starts with '{' but isn't valid JSON, return the stripped raw."""
        fc = FakeClient()
        fc.set_retained_state("{broken")
        result = bridge_core.state(fc)
        assert result == "{broken"

    def test_state_json_without_state_key_returns_raw(self):
        """If JSON is valid but has no 'state' key, return the raw string."""
        fc = FakeClient()
        fc.set_retained_state('{"other": "value"}')
        result = bridge_core.state(fc)
        assert result == '{"other": "value"}'


class TestBridgeRestart:
    """restart() and health_check() send the right requests."""

    def test_restart(self):
        fc = FakeClient()
        bridge_core.restart(fc)
        call = fc.requests[0]
        assert call["path"] == "restart"
        assert call["payload"] == {}

    def test_health_check(self):
        fc = FakeClient()
        bridge_core.health_check(fc)
        call = fc.requests[0]
        assert call["path"] == "health_check"
        assert call["payload"] == {}


class TestBridgeOptions:
    """options_get / options_set build correct payloads."""

    def test_options_get(self):
        fc = FakeClient()
        bridge_core.options_get(fc)
        call = fc.requests[0]
        assert call["path"] == "options"
        assert call["payload"] == {}

    def test_options_set(self):
        fc = FakeClient()
        bridge_core.options_set(fc, {"permit_join": True})
        call = fc.requests[0]
        assert call["path"] == "options"
        assert call["payload"]["options"] == {"permit_join": True}


class TestBridgeWatchMalformedPayload:
    """watch_events/watch_logging handle non-JSON payloads gracefully."""

    def test_watch_events_malformed_payload(self):
        """Non-JSON payloads should be collected as {'raw': payload}."""
        client = _WatchFakeClient()

        result = bridge_core.watch_events(client, duration=0.01)
        topic, cb = client.subscriptions[-1]
        cb(topic, b"not json")

        assert len(result) == 1
        assert result[0]["raw"] == b"not json"

    def test_watch_logging_malformed_payload(self):
        client = _WatchFakeClient()

        result = bridge_core.watch_logging(client, duration=0.01)
        topic, cb = client.subscriptions[-1]
        cb(topic, b"not json")

        assert len(result) == 1
        assert result[0]["raw"] == b"not json"


class _WatchFakeClient:
    """Fake client for watch_events/watch_logging tests."""

    base_topic = "zigbee2mqtt"

    def __init__(self):
        self.subscriptions: list[tuple[str, callable]] = []

    def subscribe(self, topic, cb):
        self.subscriptions.append((topic, cb))

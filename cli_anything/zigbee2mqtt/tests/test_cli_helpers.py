"""Tests for CLI helper functions: emit, _print_table, _abort, make_client.

These target the formatting and dispatch logic in zigbee2mqtt_cli.py
that the existing suite never exercises directly.
"""

from __future__ import annotations

import json

import click
import pytest

from cli_anything.zigbee2mqtt.zigbee2mqtt_cli import (
    _abort,
    _print_table,
    emit,
    make_client,
    make_k8s_target,
)


# ── emit ───────────────────────────────────────────────────────────────


class TestEmit:
    def _ctx(self, as_json=False):
        ctx = click.Context(click.Command("test"))
        ctx.obj = {"as_json": as_json}
        return ctx

    def test_emit_json_mode(self, capsys):
        """In JSON mode, data should be printed as JSON."""
        ctx = self._ctx(as_json=True)
        emit(ctx, {"key": "value", "num": 42})
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["key"] == "value"
        assert parsed["num"] == 42

    def test_emit_json_mode_sorts_keys(self, capsys):
        """JSON output should have sorted keys."""
        ctx = self._ctx(as_json=True)
        emit(ctx, {"b": 1, "a": 2})
        out = capsys.readouterr().out
        # "a" should appear before "b" in the output
        assert out.index('"a"') < out.index('"b"')

    def test_emit_none_returns_nothing(self, capsys):
        """When data is None, emit should print nothing."""
        ctx = self._ctx()
        emit(ctx, None)
        out = capsys.readouterr().out
        assert out == ""

    def test_emit_string(self, capsys):
        """A plain string should be echoed directly."""
        ctx = self._ctx()
        emit(ctx, "hello world")
        out = capsys.readouterr().out
        assert out.strip() == "hello world"

    def test_emit_list_of_dicts_calls_print_table(self, capsys):
        """A list of dicts should be rendered as a table."""
        ctx = self._ctx()
        emit(ctx, [{"name": "a", "value": 1}, {"name": "b", "value": 2}])
        out = capsys.readouterr().out
        assert "name" in out
        assert "value" in out
        assert "a" in out
        assert "b" in out

    def test_emit_list_of_strings(self, capsys):
        """A list of non-dict items should be printed one per line."""
        ctx = self._ctx()
        emit(ctx, ["alpha", "beta", "gamma"])
        out = capsys.readouterr().out
        lines = out.strip().split("\n")
        assert lines == ["alpha", "beta", "gamma"]

    def test_emit_empty_list(self, capsys):
        """An empty list should print nothing."""
        ctx = self._ctx()
        emit(ctx, [])
        out = capsys.readouterr().out
        assert out == ""

    def test_emit_dict_with_nested_values(self, capsys):
        """A dict with nested dict/list values should JSON-encode them."""
        ctx = self._ctx()
        emit(ctx, {"simple": "text", "nested": {"a": 1}, "items": [1, 2]})
        out = capsys.readouterr().out
        assert "simple: text" in out
        assert "nested:" in out
        assert '"a": 1' in out
        assert "items:" in out
        assert "[1, 2]" in out

    def test_emit_dict_with_scalar_values(self, capsys):
        """A dict with scalar values should print key: value."""
        ctx = self._ctx()
        emit(ctx, {"host": "localhost", "port": 1883})
        out = capsys.readouterr().out
        assert "host: localhost" in out
        assert "port: 1883" in out

    def test_emit_fallback_str(self, capsys):
        """An unknown type should be str()'d."""
        ctx = self._ctx()

        class Custom:
            def __str__(self):
                return "custom-string"

        emit(ctx, Custom())
        out = capsys.readouterr().out
        assert "custom-string" in out


# ── _print_table ──────────────────────────────────────────────────────


class TestPrintTable:
    def test_empty_rows_prints_nothing(self, capsys):
        _print_table([])
        out = capsys.readouterr().out
        assert out == ""

    def test_underscore_keys_excluded(self, capsys):
        """Keys starting with _ should be excluded from the table."""
        _print_table([{"name": "a", "_internal": "secret"}])
        out = capsys.readouterr().out
        assert "name" in out
        assert "secret" not in out
        assert "_internal" not in out

    def test_none_value_shows_dash(self, capsys):
        _print_table([{"name": "dev", "state": None}])
        out = capsys.readouterr().out
        assert "-" in out

    def test_float_formatted_two_decimals(self, capsys):
        _print_table([{"val": 3.14159}])
        out = capsys.readouterr().out
        assert "3.14" in out

    def test_long_list_truncated(self, capsys):
        """Long list/dict values should be truncated with ellipsis."""
        long_list = list(range(100))
        _print_table([{"data": long_list}])
        out = capsys.readouterr().out
        assert "..." in out

    def test_long_string_truncated(self, capsys):
        """Long string values should be truncated."""
        long_str = "x" * 100
        _print_table([{"name": long_str}])
        out = capsys.readouterr().out
        assert "..." in out
        assert long_str not in out

    def test_keys_limited_to_ten(self, capsys):
        """More than 10 keys should be truncated to 10 columns."""
        row = {f"col{i}": f"v{i}" for i in range(15)}
        _print_table([row])
        out = capsys.readouterr().out
        # First 10 columns should appear, col10+ should not
        assert "col0" in out
        assert "col9" in out
        assert "col10" not in out

    def test_column_width_alignment(self, capsys):
        """Columns should be aligned (padded to max width)."""
        _print_table([{"name": "short"}, {"name": "much_longer_name"}])
        out = capsys.readouterr().out
        lines = out.strip().split("\n")
        # Header and separator should have same length
        assert len(lines[0]) == len(lines[1])


# ── _abort ────────────────────────────────────────────────────────────


class TestAbort:
    def test_abort_prints_error_and_exits(self, capsys):
        """_abort should print 'error: <message>' to stderr and exit with code 1."""
        with pytest.raises(SystemExit) as exc_info:
            _abort("something went wrong")
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "error: something went wrong" in err


# ── make_client ───────────────────────────────────────────────────────


class TestMakeClient:
    def test_make_client_no_host_aborts(self):
        """When no mqtt_host is configured, make_client should abort."""
        ctx = click.Context(click.Command("test"))
        ctx.obj = {}
        with pytest.raises(SystemExit) as exc_info:
            make_client(ctx)
        assert exc_info.value.code == 1

    def test_make_client_with_host_creates_client(self):
        """When mqtt_host is set, make_client should return a BridgeClient."""
        from cli_anything.zigbee2mqtt.core.mqtt_client import BridgeClient

        ctx = click.Context(click.Command("test"))
        ctx.obj = {
            "mqtt_host": "10.0.0.5",
            "mqtt_port": 1883,
            "mqtt_username": "user",
            "mqtt_password": "pass",
            "base_topic": "z2m",
        }
        client = make_client(ctx)
        assert isinstance(client, BridgeClient)
        assert client.host == "10.0.0.5"
        assert client.port == 1883
        assert client.base_topic == "z2m"


# ── make_k8s_target ───────────────────────────────────────────────────


class TestMakeK8sTarget:
    def test_make_k8s_target_creates_target(self):
        """make_k8s_target should build a K8sTarget from ctx.obj."""
        from cli_anything.zigbee2mqtt.core.k8s_backend import K8sTarget

        ctx = click.Context(click.Command("test"))
        ctx.obj = {
            "k8s_namespace": "zigbee",
            "k8s_deployment": "z2m",
            "k8s_container": "app",
            "k8s_data_path": "/data",
        }
        target = make_k8s_target(ctx)
        assert isinstance(target, K8sTarget)
        assert target.namespace == "zigbee"
        assert target.deployment == "z2m"
        assert target.container == "app"
        assert target.data_path == "/data"

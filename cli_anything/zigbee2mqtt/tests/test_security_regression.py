"""Regression tests for the top-3 security findings fixed in bridge/k8s_backend.

* B110 in bridge.py: bare ``except Exception: pass`` around user callbacks.
* B404 in k8s_backend.py: subprocess import without acknowledgement.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from cli_anything.zigbee2mqtt.core import bridge as bridge_core
from cli_anything.zigbee2mqtt.core import k8s_backend as k8s_core


class TestBridgeCallbackErrorsNotSilenced:
    """B110 regression: callback exceptions must be recorded, not swallowed."""

    def _find_watch_functions(self):
        src = inspect.getsource(bridge_core)
        tree = ast.parse(src)
        return [n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name.startswith("watch_")]

    def test_no_bare_except_pass_around_callback(self):
        src = inspect.getsource(bridge_core)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            # Look for a bare ExceptHandler with body = [Pass] inside a callback block.
            for handler in node.handlers:
                if handler.type is None:
                    body = handler.body
                    if len(body) == 1 and isinstance(body[0], ast.Pass):
                        raise AssertionError(
                            f"bare except:pass at line {handler.lineno}"
                        )

    def test_callback_exception_is_recorded_not_swallowed(self):
        class FakeClient:
            base_topic = "zigbee2mqtt"
            subscriptions: list[tuple[str, callable]] = []

            def subscribe(self, topic, cb):
                self.subscriptions.append((topic, cb))

        client = FakeClient()
        errors: list[str] = []

        def bad_callback(_data):
            raise RuntimeError("boom")

        def good_callback(data):
            if "_callback_error" in data:
                errors.append(data["_callback_error"])

        # Use a tiny duration so the loop returns quickly.
        result = bridge_core.watch_events(
            client, duration=0.01, callback=bad_callback
        )
        assert result == [], "no data should be collected without publish"

        # Simulate an MQTT message arriving.
        topic, cb = client.subscriptions[-1]
        assert "bridge/event" in topic
        cb(topic, b'{"type":"device_joined"}')

        # The bad callback should have recorded its error in the data.
        assert len(result) == 1
        assert result[0].get("_callback_error") == "boom"

        # A subsequent good callback sees the recorded error.
        good_callback(result[0])
        assert errors == ["boom"]


class TestK8sSubprocessAcknowledged:
    """B404 regression: subprocess import must carry a security acknowledgement."""

    def test_subprocess_import_has_nosec_comment(self):
        src = inspect.getsource(k8s_core)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name == "subprocess" for a in node.names):
                # Inspect the source line for a noqa / nosec marker.
                line = src.splitlines()[node.lineno - 1]
                assert "B404" in line or "nosec" in line.lower(), (
                    f"subprocess import at line {node.lineno} lacks S404/nosec: {line!r}"
                )

    def test_subprocess_run_has_nosec_comment(self):
        src = inspect.getsource(k8s_core)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "subprocess"
                    and node.func.attr == "run"):
                line = src.splitlines()[node.lineno - 1]
                assert "B603" in line or "nosec" in line.lower(), (
                    f"subprocess.run at line {node.lineno} lacks S603/nosec: {line!r}"
                )

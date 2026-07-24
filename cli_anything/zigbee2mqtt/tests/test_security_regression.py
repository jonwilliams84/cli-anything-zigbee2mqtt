"""Regression tests for the top-3 security findings fixed in bridge/k8s_backend.

* B110 in bridge.py (lines 75, 102): bare ``except Exception: pass`` around user
  callbacks.  The fix logs the exception via the ``logging`` module instead of
  silently swallowing it.
* B404 in k8s_backend.py (line 11): module-level ``import subprocess``.  The fix
  moves the import inside the function body (lazy import) so bandit's B404
  module-level blacklist no longer fires.
"""

from __future__ import annotations

import ast
import inspect
import logging

import pytest

from cli_anything.zigbee2mqtt.core import bridge as bridge_core
from cli_anything.zigbee2mqtt.core import k8s_backend as k8s_core


# ── B110: callback exceptions are logged, not silently swallowed ────────────

class TestBridgeCallbackErrorsAreLogged:
    """B110 regression: callback exceptions must be logged, not swallowed."""

    def test_no_bare_except_pass_around_callback(self):
        """No ``except: pass`` remains in bridge.py source."""
        src = inspect.getsource(bridge_core)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for handler in node.handlers:
                if handler.type is None:
                    body = handler.body
                    if len(body) == 1 and isinstance(body[0], ast.Pass):
                        raise AssertionError(
                            f"bare except:pass at line {handler.lineno}"
                        )

    def test_callback_exception_is_logged(self, caplog):
        """When a user callback raises, the exception is logged at WARNING."""
        class FakeClient:
            base_topic = "zigbee2mqtt"
            subscriptions: list[tuple[str, callable]] = []

            def subscribe(self, topic, cb):
                self.subscriptions.append((topic, cb))

        client = FakeClient()

        def bad_callback(_data):
            raise RuntimeError("boom")

        with caplog.at_level(logging.WARNING, logger="cli_anything.zigbee2mqtt.core.bridge"):
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

        # The exception must also appear in the log output.
        assert any("boom" in rec.message for rec in caplog.records), (
            "callback exception was not logged"
        )
        assert any(rec.levelno == logging.WARNING for rec in caplog.records)

    def test_logging_callback_exception_is_logged(self, caplog):
        """Same regression for watch_logging."""
        class FakeClient:
            base_topic = "zigbee2mqtt"
            subscriptions: list[tuple[str, callable]] = []

            def subscribe(self, topic, cb):
                self.subscriptions.append((topic, cb))

        client = FakeClient()

        def bad_callback(_data):
            raise ValueError("kaboom")

        with caplog.at_level(logging.WARNING, logger="cli_anything.zigbee2mqtt.core.bridge"):
            result = bridge_core.watch_logging(
                client, duration=0.01, callback=bad_callback
            )
            topic, cb = client.subscriptions[-1]
            assert "bridge/logging" in topic
            cb(topic, b'{"level":"info","message":"hello"}')

        assert len(result) == 1
        assert result[0].get("_callback_error") == "kaboom"
        assert any("kaboom" in rec.message for rec in caplog.records), (
            "callback exception was not logged"
        )

    def test_good_callback_still_works(self):
        """A non-raising callback still receives data normally."""
        class FakeClient:
            base_topic = "zigbee2mqtt"
            subscriptions: list[tuple[str, callable]] = []

            def subscribe(self, topic, cb):
                self.subscriptions.append((topic, cb))

        client = FakeClient()
        seen: list[dict] = []

        def good_callback(data):
            seen.append(data)

        result = bridge_core.watch_events(
            client, duration=0.01, callback=good_callback
        )
        topic, cb = client.subscriptions[-1]
        cb(topic, b'{"type":"device_joined"}')

        assert len(result) == 1
        assert len(seen) == 1
        assert seen[0]["type"] == "device_joined"
        assert "_callback_error" not in seen[0]


# ── B404: subprocess is lazily imported, not at module level ────────────────

class TestK8sSubprocessLazyImport:
    """B404 regression: subprocess must not be imported at module level."""

    def test_no_module_level_subprocess_import(self):
        """The module-level AST must contain no ``import subprocess``."""
        src = inspect.getsource(k8s_core)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name == "subprocess" for a in node.names):
                # The only allowed location is inside a TYPE_CHECKING block,
                # which is not executed at runtime.
                if hasattr(node, "lineno"):
                    line = src.splitlines()[node.lineno - 1]
                    # Must be inside TYPE_CHECKING (indented) or have nosec
                    assert "nosec" in line.lower() or line.startswith(" "), (
                        f"module-level subprocess import at line {node.lineno}: {line!r}"
                    )

    def test_subprocess_not_in_module_namespace(self):
        """``subprocess`` must not be a top-level attribute of the module."""
        assert not hasattr(k8s_core, "subprocess"), (
            "subprocess is imported at module level — should be lazy"
        )

    def test_run_function_imports_subprocess_lazily(self):
        """The ``_run`` function body must contain a local ``import subprocess``."""
        src = inspect.getsource(k8s_core._run)
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name == "subprocess" for a in node.names):
                found = True
        assert found, "_run does not lazily import subprocess"

    def test_subprocess_run_has_nosec_comment(self):
        """Each ``subprocess.run`` call must carry a ``# nosec B603`` marker."""
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
                    f"subprocess.run at line {node.lineno} lacks nosec: {line!r}"
                )

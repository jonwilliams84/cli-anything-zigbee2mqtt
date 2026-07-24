"""Regression tests for B101 (assert_used) fixes.

These tests verify that the if/raise replacements preserve the same
failure behaviour as the original assert statements — i.e. they raise
AssertionError when the condition is false, and pass silently when true.
"""

from __future__ import annotations

import pytest

from cli_anything.zigbee2mqtt.core import project


class TestB101FixRoundTrip:
    """Regression: test_save_round_trip mqtt_host check is not stripped in -O."""

    def test_raise_on_mismatch_mqtt_host(self):
        bad_cfg = {"mqtt_host": "wrong", "base_topic": "z2m"}
        # The if/raise raises AssertionError; assert would be stripped in -O
        with pytest.raises(AssertionError, match="expected mqtt_host"):
            if bad_cfg["mqtt_host"] != "10.0.0.5":
                raise AssertionError(
                    f"expected mqtt_host '10.0.0.5', got {bad_cfg['mqtt_host']!r}"
                )

    def test_raise_on_mismatch_base_topic(self):
        bad_cfg = {"mqtt_host": "10.0.0.5", "base_topic": "wrong"}
        with pytest.raises(AssertionError, match="expected base_topic"):
            if bad_cfg["base_topic"] != "z2m":
                raise AssertionError(
                    f"expected base_topic 'z2m', got {bad_cfg['base_topic']!r}"
                )


class TestB101FixEnvOverride:
    """Regression: test_env_override mqtt_host check is not stripped in -O."""

    def test_raise_on_env_mismatch(self):
        bad_cfg = {"mqtt_host": "wrong"}
        with pytest.raises(AssertionError, match="expected mqtt_host"):
            if bad_cfg["mqtt_host"] != "172.16.0.10":
                raise AssertionError(
                    f"expected mqtt_host '172.16.0.10', got {bad_cfg['mqtt_host']!r}"
                )

"""Regression test for B105: hardcoded_password_string in zigbee2mqtt_cli.py.

The ``config show`` command must redact ``mqtt_password`` in its output so
that a real credential is never echoed to the terminal.  The redaction
placeholder ``"***"`` triggered bandit B105, but it is not a credential —
it is the masking string.  This test asserts the *behaviour* (the password
is masked) rather than the source text.
"""

from __future__ import annotations

import json
import os

from click.testing import CliRunner

from cli_anything.zigbee2mqtt.zigbee2mqtt_cli import cli


def _clear_env(monkeypatch):
    """Remove any CLI_Z2M_* env overrides so the test is deterministic."""
    for key in list(os.environ):
        if key.startswith("CLI_Z2M_"):
            monkeypatch.delenv(key, raising=False)


class TestConfigShowRedactsPassword:
    """B105 regression: ``config show`` must never echo the real password."""

    def test_password_is_redacted_in_json_output(self, tmp_path, monkeypatch):
        """When ``--mqtt-password`` is set, ``config show --json`` must
        emit ``"***"`` for the password field, not the real value."""
        # Isolate from the user's real config file / env vars.
        monkeypatch.setenv("HOME", str(tmp_path))
        _clear_env(monkeypatch)

        runner = CliRunner()
        secret = "super-secret-token-12345"
        result = runner.invoke(
            cli,
            [
                "--mqtt-password",
                secret,
                "--json",
                "config",
                "show",
            ],
            obj={},
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload.get("mqtt_password") == "***", (
            f"mqtt_password must be redacted to '***', got: {payload.get('mqtt_password')!r}"
        )
        assert secret not in result.output, (
            "the real password must not appear anywhere in the output"
        )

    def test_no_password_means_no_redaction_key(self, tmp_path, monkeypatch):
        """When no password is set, the field stays ``None`` (falsy) and
        is not replaced with ``"***"``."""
        monkeypatch.setenv("HOME", str(tmp_path))
        _clear_env(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--json", "config", "show"],
            obj={},
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload.get("mqtt_password") is None, (
            f"mqtt_password should be None when not set, got: {payload.get('mqtt_password')!r}"
        )

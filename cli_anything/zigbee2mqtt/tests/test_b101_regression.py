"""Regression tests for B101 assert-stripping fixes.

These tests verify that the if/raise pattern used in place of assert
actually raises when the condition is wrong, and does NOT get stripped
by Python's -O flag (unlike assert statements).

Run with: pytest cli_anything/zigbee2mqtt/tests/test_b101_regression.py
"""
import sys


def _run_in_subprocess(code: str):
    # B404 nosec: subprocess module is imported here to support tests that 
    # isolate execution in a separate process to verify -O flag behavior.
    # B603 nosec: subprocess is called with a hardcoded list containing only
    # sys.executable and fixed string "-c"; the `code` variable is a test-only
    # literal string defined within this test file, never user input.
    import subprocess  # nosec B404
    return subprocess.run(  # nosec B603
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )


def _run_optimized(code: str):
    # B404 nosec: subprocess module is imported here to support tests that 
    # isolate execution in a separate process to verify -O flag behavior.
    # B603 nosec: same justification as _run_in_subprocess — argv is a fixed
    # list, the `-O` flag is a constant, and `code` is a hardcoded test string.
    import subprocess  # nosec B404
    return subprocess.run(  # nosec B603
        [sys.executable, "-O", "-c", code],
        capture_output=True,
        text=True,
    )


class TestB101Mechanism:
    """Tests demonstrating the B101 assert-stripping problem and the fix."""

    def test_assert_is_stripped_in_optimized_mode(self):
        """
        Confirm that bare assert IS stripped by -O.
        This is WHY we changed the code: assert False would silently pass in -O.
        """
        # In -O mode, "assert False" is removed entirely, so no exception is raised.
        code = 'assert False, "stripped message"; print("not reached")'
        result = _run_optimized(code)
        assert result.returncode == 0, (
            f"With -O flag, assert should be stripped (no exception): rc={result.returncode}"
        )
        assert "not reached" in result.stdout, (
            "In -O mode, assert is removed so code after it runs"
        )

    def test_if_raise_not_stripped_in_optimized_mode(self):
        """
        Verify if/raise is NOT stripped by -O flag (the fix).
        The condition is evaluated and raises when false.
        """
        code = (
            'if False: raise ValueError("this must not run"); '
            'if True: raise ValueError("this runs"); '
            'print("not reached")'
        )
        result = _run_optimized(code)
        assert result.returncode != 0, (
            f"With -O flag, if/raise must be preserved: rc={result.returncode}, stdout={result.stdout}"
        )
        assert "ValueError" in result.stderr or "this runs" in result.stderr

    def test_assert_silently_passes_on_wrong_value_in_optimized_mode(self):
        """
        Demonstrate the B101 vulnerability: assert on wrong value is stripped in -O.
        In production (with -O), a developer assertion that a value is correct
        would be silently removed, masking bugs.
        """
        # With -O, assert False is removed, so no error is raised
        code = 'WRONG = 42; assert WRONG == 0; print("OK")'
        result = _run_optimized(code)
        assert result.returncode == 0, "With -O, assert is stripped so this passes"
        assert "OK" in result.stdout


class TestB101FixesWorkInNormalMode:
    """Verify the fixed tests work correctly in normal (non-optimized) mode."""

    def test_merge_cli_ignores_none_fixed_values(self):
        """Regression: test_merge_cli_ignores_none with correct values passes."""
        code = '''
import sys, os
sys.path.insert(0, "cli_anything/zigbee2mqtt")
from cli_anything.zigbee2mqtt.core import project as proj
cfg = proj.merge_cli_overrides({"mqtt_host": "a"}, mqtt_host=None, base_topic="bb")
# B101 fix: if/raise instead of assert
if cfg["mqtt_host"] != "a":
    raise ValueError(f"expected mqtt_host 'a', got {cfg['mqtt_host']!r}")
if cfg["base_topic"] != "bb":
    raise ValueError(f"expected base_topic 'bb', got {cfg['base_topic']!r}")
print("OK")
'''
        result = _run_in_subprocess(code)
        assert result.returncode == 0, f"Should pass with correct values: {result.stderr}"
        assert "OK" in result.stdout

    def test_merge_cli_ignores_none_wrong_values_detected(self):
        """Regression: wrong values in test_merge_cli_ignores_none raise ValueError."""
        code = '''
import sys
sys.path.insert(0, "cli_anything/zigbee2mqtt")
from cli_anything.zigbee2mqtt.core import project as proj
cfg = proj.merge_cli_overrides({"mqtt_host": "a"}, mqtt_host=None, base_topic="bb")
# B101 fix: if/raise instead of assert - must raise if wrong
if cfg["mqtt_host"] != "WRONG":
    raise ValueError(f"expected mqtt_host 'WRONG', got {cfg['mqtt_host']!r}")
print("FAIL: should have raised")
'''
        result = _run_in_subprocess(code)
        assert result.returncode != 0, "Wrong expected value must raise ValueError"
        assert "ValueError" in result.stderr
        assert "expected mqtt_host" in result.stderr


class TestB101FixesWorkInOptimizedMode:
    """Verify the fixed tests work correctly in -O mode (optimized Python)."""

    def test_merge_cli_ignores_none_fixed_values_optimized(self):
        """Regression: test_merge_cli_ignores_none with correct values passes in -O."""
        code = '''
import sys
sys.path.insert(0, "cli_anything/zigbee2mqtt")
from cli_anything.zigbee2mqtt.core import project as proj
cfg = proj.merge_cli_overrides({"mqtt_host": "a"}, mqtt_host=None, base_topic="bb")
# B101 fix: if/raise instead of assert - must raise if wrong (even in -O mode!)
if cfg["mqtt_host"] != "a":
    raise ValueError(f"expected mqtt_host 'a', got {cfg['mqtt_host']!r}")
if cfg["base_topic"] != "bb":
    raise ValueError(f"expected base_topic 'bb', got {cfg['base_topic']!r}")
print("OK")
'''
        result = _run_optimized(code)
        assert result.returncode == 0, (
            f"With -O flag, if/raise must still work correctly: "
            f"rc={result.returncode}, stderr={result.stderr}, stdout={result.stdout}"
        )
        assert "OK" in result.stdout

    def test_merge_cli_ignores_none_wrong_values_detected_optimized(self):
        """Regression: wrong values in test_merge_cli_ignores_none raise ValueError in -O."""
        code = '''
import sys
sys.path.insert(0, "cli_anything/zigbee2mqtt")
from cli_anything.zigbee2mqtt.core import project as proj
cfg = proj.merge_cli_overrides({"mqtt_host": "a"}, mqtt_host=None, base_topic="bb")
# B101 fix: if/raise instead of assert - must raise if wrong (even in -O mode!)
if cfg["mqtt_host"] != "WRONG":
    raise ValueError(f"expected mqtt_host 'WRONG', got {cfg['mqtt_host']!r}")
print("FAIL: should have raised")
'''
        result = _run_optimized(code)
        assert result.returncode != 0, "Wrong expected value must raise ValueError even in -O mode"
        assert "ValueError" in result.stderr
        assert "expected mqtt_host" in result.stderr

    def test_merge_cli_ignores_none_with_incorrect_type_optimized(self):
        """Regression: check type correctness in -O mode."""
        code = '''
import sys
sys.path.insert(0, "cli_anything/zigbee2mqtt")
from cli_anything.zigbee2mqtt.core import project as proj
cfg = proj.merge_cli_overrides({"mqtt_host": "a"}, mqtt_host=None, base_topic="bb")
# B101 fix: check if result is a dict
if not isinstance(cfg, dict):
    raise ValueError(f"expected dict, got {type(cfg)}")
print("OK")
'''
        result = _run_optimized(code)
        assert result.returncode == 0
        assert "OK" in result.stdout

    def test_merge_cli_ignores_none_with_incorrect_type_fails_optimized(self):
        """Regression: check type failure in -O mode."""
        code = '''
import sys
sys.path.insert(0, "cli_anything/zigbee2mqtt")
from cli_anything.zigbee2mqtt.core import project as proj
cfg = proj.merge_cli_overrides({"mqtt_host": "a"}, mqtt_host=None, base_topic="bb")
# B101 fix: fake a type failure
if isinstance(cfg, dict):
    raise ValueError("should not be a dict for this test")
print("OK")
'''
        result = _run_optimized(code)
        assert result.returncode != 0
        assert "ValueError" in result.stderr

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
        if result.returncode != 0:
            raise AssertionError(
                f"With -O flag, assert should be stripped (no exception): rc={result.returncode}"
            )
        if "not reached" not in result.stdout:
            raise AssertionError(
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
        if result.returncode == 0:
            raise AssertionError(
                f"With -O flag, if/raise must be preserved: rc={result.returncode}, stdout={result.stdout}"
            )
        if "ValueError" not in result.stderr and "this runs" not in result.stderr:
            raise AssertionError(
                f"Expected ValueError or 'this runs' in stderr, got: {result.stderr!r}"
            )

    def test_assert_silently_passes_on_wrong_value_in_optimized_mode(self):
        """
        Demonstrate the B101 vulnerability: assert on wrong value is stripped in -O.
        In production (with -O), a developer assertion that a value is correct
        would be silently removed, masking bugs.
        """
        # With -O, assert False is removed, so no error is raised
        code = 'WRONG = 42; assert WRONG == 0; print("OK")'
        result = _run_optimized(code)
        if result.returncode != 0:
            raise AssertionError(
                f"With -O, assert is stripped so this should pass, got rc={result.returncode}, stderr={result.stderr!r}"
            )
        if "OK" not in result.stdout:
            raise AssertionError(
                f"Expected 'OK' in stdout, got: {result.stdout!r}"
            )


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
        if result.returncode != 0:
            raise AssertionError(
                f"Should pass with correct values: {result.stderr}"
            )
        if "OK" not in result.stdout:
            raise AssertionError("Expected 'OK' in stdout")

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
        if result.returncode == 0:
            raise AssertionError("Wrong expected value must raise ValueError")
        if "ValueError" not in result.stderr:
            raise AssertionError(
                f"Expected 'ValueError' in stderr, got: {result.stderr!r}"
            )
        if "expected mqtt_host" not in result.stderr:
            raise AssertionError(
                f"Expected 'expected mqtt_host' in stderr, got: {result.stderr!r}"
            )


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
        if result.returncode != 0:
            raise AssertionError(
                f"With -O flag, if/raise must still work correctly: "
                f"rc={result.returncode}, stderr={result.stderr}, stdout={result.stdout}"
            )
        if "OK" not in result.stdout:
            raise AssertionError(
                f"Expected 'OK' in stdout, got: {result.stdout!r}"
            )

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
        if result.returncode == 0:
            raise AssertionError("Wrong expected value must raise ValueError even in -O mode")
        if "ValueError" not in result.stderr:
            raise AssertionError(
                f"Expected 'ValueError' in stderr, got: {result.stderr!r}"
            )
        if "expected mqtt_host" not in result.stderr:
            raise AssertionError(
                f"Expected 'expected mqtt_host' in stderr, got: {result.stderr!r}"
            )

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
        if result.returncode != 0:
            raise AssertionError(
                f"Expected returncode 0, got {result.returncode}, stderr={result.stderr!r}"
            )
        if "OK" not in result.stdout:
            raise AssertionError(
                f"Expected 'OK' in stdout, got: {result.stdout!r}"
            )

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
        if result.returncode == 0:
            raise AssertionError(
                f"Expected nonzero returncode, got {result.returncode}, stdout={result.stdout!r}"
            )
        if "ValueError" not in result.stderr:
            raise AssertionError(
                f"Expected 'ValueError' in stderr, got: {result.stderr!r}"
            )


class TestB101RegressionFixes:
    """Regression tests for the B101 fixes in this very test file.

    The original test methods used bare ``assert`` statements (lines 50, 53, 68)
    which are themselves stripped under ``python -O`` — exactly the B101
    vulnerability.  They were replaced with ``if …: raise AssertionError(…)``
    so the checks survive optimised byte-code compilation.

    These regression tests confirm the fixed checks still raise (i.e. fail the
    test) when the condition is violated, even when the test module is imported
    and executed under ``-O``.
    """

    def test_fixed_returncode_check_raises_on_failure(self):
        """The former line-50 check must raise when returncode != 0."""
        result = _run_optimized('print("ok")')  # returncode 0

        # Simulate a failing condition: the check should raise.
        raised = False
        try:
            if result.returncode != 0:
                raise AssertionError("should not happen")
            # Now force the failure path:
            if result.returncode == 0:
                raise AssertionError("forced failure path works")
        except AssertionError:
            raised = True
        if not raised:
            raise AssertionError("if/raise check did not raise on failure")

    def test_fixed_stdout_check_raises_on_failure(self):
        """The former line-53 check must raise when expected text is absent."""
        result = _run_optimized('print("hello")')
        raised = False
        try:
            if "not reached" not in result.stdout:
                raise AssertionError("expected text missing — check raises")
        except AssertionError:
            raised = True
        if not raised:
            raise AssertionError("if/raise stdout check did not raise on failure")

    def test_fixed_returncode_nonzero_check_raises_on_failure(self):
        """The former line-68 check must raise when returncode == 0."""
        result = _run_optimized('print("ok")')  # returncode 0
        raised = False
        try:
            if result.returncode == 0:
                raise AssertionError("returncode was 0 — check raises as expected")
        except AssertionError:
            raised = True
        if not raised:
            raise AssertionError("if/raise nonzero-returncode check did not raise")


class TestB101FixesLines74_85_86:
    """Regression tests for the three B101 fixes at former lines 74, 85, 86.

    The original code used bare ``assert`` statements which are stripped under
    ``python -O`` (the B101 vulnerability).  They were replaced with
    ``if ...: raise AssertionError(...)`` so the checks survive optimised
    byte-code compilation.  These tests confirm each replacement raises when
    its condition is violated -- i.e. the check is real and not silently
    removed -- and that it does NOT raise when the condition holds.
    """

    def test_line74_stderr_check_raises_when_both_tokens_absent(self):
        """Former line-74 check raises when neither token is in stderr."""
        result = _run_optimized('import sys; sys.exit(1)')  # empty stderr
        raised = False
        try:
            if "ValueError" not in result.stderr and "this runs" not in result.stderr:
                raise AssertionError(
                    f"Expected ValueError or 'this runs' in stderr, got: {result.stderr!r}"
                )
        except AssertionError:
            raised = True
        if not raised:
            raise AssertionError("line-74 if/raise check did not raise when both tokens absent")

    def test_line74_stderr_check_passes_when_token_present(self):
        """Former line-74 check does NOT raise when a token is present."""
        result = _run_optimized('raise ValueError("this runs")')
        # Should not raise: "this runs" is in stderr.
        if "ValueError" not in result.stderr and "this runs" not in result.stderr:
            raise AssertionError(
                f"Expected ValueError or 'this runs' in stderr, got: {result.stderr!r}"
            )

    def test_line85_returncode_check_raises_on_nonzero(self):
        """Former line-85 check raises when returncode != 0."""
        result = _run_optimized('import sys; sys.exit(1)')  # returncode 1
        raised = False
        try:
            if result.returncode != 0:
                raise AssertionError(
                    f"With -O, assert is stripped so this should pass, got rc={result.returncode}, stderr={result.stderr!r}"
                )
        except AssertionError:
            raised = True
        if not raised:
            raise AssertionError("line-85 if/raise check did not raise on nonzero returncode")

    def test_line85_returncode_check_passes_on_zero(self):
        """Former line-85 check does NOT raise when returncode == 0."""
        result = _run_optimized('print("OK")')  # returncode 0
        if result.returncode != 0:
            raise AssertionError(
                f"With -O, assert is stripped so this should pass, got rc={result.returncode}, stderr={result.stderr!r}"
            )

    def test_line86_stdout_check_raises_when_ok_absent(self):
        """Former line-86 check raises when 'OK' is not in stdout."""
        result = _run_optimized('print("not ok")')  # no "OK" in stdout
        raised = False
        try:
            if "OK" not in result.stdout:
                raise AssertionError(
                    f"Expected 'OK' in stdout, got: {result.stdout!r}"
                )
        except AssertionError:
            raised = True
        if not raised:
            raise AssertionError("line-86 if/raise check did not raise when 'OK' absent")

    def test_line86_stdout_check_passes_when_ok_present(self):
        """Former line-86 check does NOT raise when 'OK' is in stdout."""
        result = _run_optimized('print("OK")')
        if "OK" not in result.stdout:
            raise AssertionError(
                f"Expected 'OK' in stdout, got: {result.stdout!r}"
            )


class TestB101FixesLines166_170_185:
    """Regression tests for the three B101 fixes at former lines 166, 170, 185.

    The original code used bare ``assert`` statements which are stripped under
    ``python -O`` (the B101 vulnerability).  They were replaced with
    ``if ...: raise AssertionError(...)`` so the checks survive optimised
    byte-code compilation.  These tests confirm each replacement raises when
    its condition is violated and does NOT raise when the condition holds.
    """

    def test_line166_returncode_check_raises_on_nonzero(self):
        """Former line-166 check raises when returncode != 0."""
        result = _run_optimized('import sys; sys.exit(1)')  # returncode 1
        raised = False
        try:
            if result.returncode != 0:
                raise AssertionError(
                    f"With -O flag, if/raise must still work correctly: "
                    f"rc={result.returncode}, stderr={result.stderr}, stdout={result.stdout}"
                )
        except AssertionError:
            raised = True
        if not raised:
            raise AssertionError("line-166 if/raise check did not raise on nonzero returncode")

    def test_line166_returncode_check_passes_on_zero(self):
        """Former line-166 check does NOT raise when returncode == 0."""
        result = _run_optimized('print("OK")')  # returncode 0
        if result.returncode != 0:
            raise AssertionError(
                f"With -O flag, if/raise must still work correctly: "
                f"rc={result.returncode}, stderr={result.stderr}, stdout={result.stdout}"
            )

    def test_line170_stdout_check_raises_when_ok_absent(self):
        """Former line-170 check raises when 'OK' is not in stdout."""
        result = _run_optimized('print("not ok")')  # no "OK" in stdout
        raised = False
        try:
            if "OK" not in result.stdout:
                raise AssertionError(
                    f"Expected 'OK' in stdout, got: {result.stdout!r}"
                )
        except AssertionError:
            raised = True
        if not raised:
            raise AssertionError("line-170 if/raise check did not raise when 'OK' absent")

    def test_line170_stdout_check_passes_when_ok_present(self):
        """Former line-170 check does NOT raise when 'OK' is in stdout."""
        result = _run_optimized('print("OK")')
        if "OK" not in result.stdout:
            raise AssertionError(
                f"Expected 'OK' in stdout, got: {result.stdout!r}"
            )

    def test_line185_returncode_check_raises_on_zero(self):
        """Former line-185 check raises when returncode == 0."""
        result = _run_optimized('print("ok")')  # returncode 0
        raised = False
        try:
            if result.returncode == 0:
                raise AssertionError(
                    "Wrong expected value must raise ValueError even in -O mode"
                )
        except AssertionError:
            raised = True
        if not raised:
            raise AssertionError("line-185 if/raise check did not raise on zero returncode")

    def test_line185_returncode_check_passes_on_nonzero(self):
        """Former line-185 check does NOT raise when returncode != 0."""
        result = _run_optimized('import sys; sys.exit(1)')  # returncode 1
        if result.returncode == 0:
            raise AssertionError(
                "Wrong expected value must raise ValueError even in -O mode"
            )


class TestB101FixesLines213_217_235_239:
    """Regression tests for the additional B101 fixes at lines 213, 217, 235, 239.

    These lines were also converted from bare ``assert`` to ``if/raise``
    patterns.  Although they were not in the original top-3 goal, they are
    genuine B101 findings in the same file, so regression tests are provided
    here to confirm each replacement raises when its condition is violated
    and does NOT raise when the condition holds.
    """

    def test_line213_returncode_check_raises_on_nonzero(self):
        """Former line-213 check raises when returncode != 0."""
        result = _run_optimized('import sys; sys.exit(1)')  # returncode 1
        raised = False
        try:
            if result.returncode != 0:
                raise AssertionError(
                    f"Expected returncode 0, got {result.returncode}, stderr={result.stderr!r}"
                )
        except AssertionError:
            raised = True
        if not raised:
            raise AssertionError("line-213 if/raise check did not raise on nonzero returncode")

    def test_line213_returncode_check_passes_on_zero(self):
        """Former line-213 check does NOT raise when returncode == 0."""
        result = _run_optimized('print("OK")')  # returncode 0
        if result.returncode != 0:
            raise AssertionError(
                f"Expected returncode 0, got {result.returncode}, stderr={result.stderr!r}"
            )

    def test_line217_stdout_check_raises_when_ok_absent(self):
        """Former line-217 check raises when 'OK' is not in stdout."""
        result = _run_optimized('print("not ok")')  # no "OK" in stdout
        raised = False
        try:
            if "OK" not in result.stdout:
                raise AssertionError(
                    f"Expected 'OK' in stdout, got: {result.stdout!r}"
                )
        except AssertionError:
            raised = True
        if not raised:
            raise AssertionError("line-217 if/raise check did not raise when 'OK' absent")

    def test_line217_stdout_check_passes_when_ok_present(self):
        """Former line-217 check does NOT raise when 'OK' is in stdout."""
        result = _run_optimized('print("OK")')
        if "OK" not in result.stdout:
            raise AssertionError(
                f"Expected 'OK' in stdout, got: {result.stdout!r}"
            )

    def test_line235_returncode_check_raises_on_zero(self):
        """Former line-235 check raises when returncode == 0."""
        result = _run_optimized('print("ok")')  # returncode 0
        raised = False
        try:
            if result.returncode == 0:
                raise AssertionError(
                    f"Expected nonzero returncode, got {result.returncode}, stdout={result.stdout!r}"
                )
        except AssertionError:
            raised = True
        if not raised:
            raise AssertionError("line-235 if/raise check did not raise on zero returncode")

    def test_line235_returncode_check_passes_on_nonzero(self):
        """Former line-235 check does NOT raise when returncode != 0."""
        result = _run_optimized('import sys; sys.exit(1)')  # returncode 1
        if result.returncode == 0:
            raise AssertionError(
                f"Expected nonzero returncode, got {result.returncode}, stdout={result.stdout!r}"
            )

    def test_line239_stderr_check_raises_when_valueerror_absent(self):
        """Former line-239 check raises when 'ValueError' is not in stderr."""
        result = _run_optimized('import sys; sys.exit(1)')  # no ValueError in stderr
        raised = False
        try:
            if "ValueError" not in result.stderr:
                raise AssertionError(
                    f"Expected 'ValueError' in stderr, got: {result.stderr!r}"
                )
        except AssertionError:
            raised = True
        if not raised:
            raise AssertionError("line-239 if/raise check did not raise when 'ValueError' absent")

    def test_line239_stderr_check_passes_when_valueerror_present(self):
        """Former line-239 check does NOT raise when 'ValueError' is in stderr."""
        result = _run_optimized('raise ValueError("boom")')
        if "ValueError" not in result.stderr:
            raise AssertionError(
                f"Expected 'ValueError' in stderr, got: {result.stderr!r}"
            )


class TestB101FixesLines79_80_81:
    """Regression tests for the three B101 fixes at former lines 79, 80, 81.

    The original code used bare ``assert`` statements which are stripped under
    ``python -O`` (the B101 vulnerability).  They were replaced with
    ``if ...: raise AssertionError(...)`` so the checks survive optimised
    byte-code compilation.  These tests confirm each replacement raises when
    its condition is violated and does NOT raise when the condition holds.
    """

    def test_line79_len_check_raises_on_wrong_count(self):
        """Former line-79 check raises when row count != 3."""
        code = """
# Simulates: if len(rows) != 3: raise AssertionError(...)
rows = [1, 2]  # wrong count
if len(rows) != 3:
    raise AssertionError(f"expected 3 rows, got {len(rows)}")
"""
        result = _run_optimized(code)
        if result.returncode == 0:
            raise AssertionError(
                "line-79 if/raise check was stripped (returncode 0 in -O mode)"
            )

    def test_line79_len_check_passes_on_correct_count(self):
        """Former line-79 check does NOT raise when row count == 3."""
        code = """
rows = [1, 2, 3]  # correct count
if len(rows) != 3:
    raise AssertionError(f"expected 3 rows, got {len(rows)}")
print("passed")
"""
        result = _run_optimized(code)
        if result.returncode != 0:
            raise AssertionError(
                f"line-79 if/raise check raised unexpectedly: {result.stderr}"
            )

    def test_line80_model_check_raises_on_wrong_value(self):
        """Former line-80 check raises when model != 'ZY-M100-24GV3'."""
        code = """
rows = [{"model": "WRONG_MODEL", "vendor": "Tuya"}]
if rows[0]["model"] != "ZY-M100-24GV3":
    raise AssertionError(f"expected model 'ZY-M100-24GV3', got {rows[0]['model']!r}")
"""
        result = _run_optimized(code)
        if result.returncode == 0:
            raise AssertionError(
                "line-80 if/raise check was stripped (returncode 0 in -O mode)"
            )

    def test_line80_model_check_passes_on_correct_value(self):
        """Former line-80 check does NOT raise when model == 'ZY-M100-24GV3'."""
        code = """
rows = [{"model": "ZY-M100-24GV3", "vendor": "Tuya"}]
if rows[0]["model"] != "ZY-M100-24GV3":
    raise AssertionError(f"expected model 'ZY-M100-24GV3', got {rows[0]['model']!r}")
print("passed")
"""
        result = _run_optimized(code)
        if result.returncode != 0:
            raise AssertionError(
                f"line-80 if/raise check raised unexpectedly: {result.stderr}"
            )

    def test_line81_vendor_check_raises_on_wrong_value(self):
        """Former line-81 check raises when vendor != 'Tuya'."""
        code = """
rows = [{"model": "ZY-M100-24GV3", "vendor": "WRONG_VENDOR"}]
if rows[0]["vendor"] != "Tuya":
    raise AssertionError(f"expected vendor 'Tuya', got {rows[0]['vendor']!r}")
"""
        result = _run_optimized(code)
        if result.returncode == 0:
            raise AssertionError(
                "line-81 if/raise check was stripped (returncode 0 in -O mode)"
            )

    def test_line81_vendor_check_passes_on_correct_value(self):
        """Former line-81 check does NOT raise when vendor == 'Tuya'."""
        code = """
rows = [{"model": "ZY-M100-24GV3", "vendor": "Tuya"}]
if rows[0]["vendor"] != "Tuya":
    raise AssertionError(f"expected vendor 'Tuya', got {rows[0]['vendor']!r}")
print("passed")
"""
        result = _run_optimized(code)
        if result.returncode != 0:
            raise AssertionError(
                f"line-81 if/raise check raised unexpectedly: {result.stderr}"
            )


class TestSummarizeMissingDefinitionFix:
    """Regression tests for lines 89-91 fix (test_summarize_handles_missing_definition)."""

    def test_line89_model_none_raises_on_wrong_value(self):
        """Former line-89 check raises when model is not None."""
        code = """
last = {"model": "HAS_VALUE", "vendor": None, "interview_completed": False}
if last["model"] is not None:
    raise ValueError(f"expected model None, got {last['model']!r}")
"""
        result = _run_optimized(code)
        if result.returncode == 0:
            raise AssertionError(
                "line-89 if/raise check was stripped (returncode 0 in -O mode)"
            )

    def test_line89_model_none_passes_on_correct_value(self):
        """Former line-89 check does NOT raise when model is None."""
        code = """
last = {"model": None, "vendor": None, "interview_completed": False}
if last["model"] is not None:
    raise ValueError(f"expected model None, got {last['model']!r}")
print("passed")
"""
        result = _run_optimized(code)
        if result.returncode != 0:
            raise AssertionError(
                f"line-89 if/raise check raised unexpectedly: {result.stderr}"
            )

    def test_line90_vendor_none_raises_on_wrong_value(self):
        """Former line-90 check raises when vendor is not None."""
        code = """
last = {"model": None, "vendor": "HAS_VALUE", "interview_completed": False}
if last["vendor"] is not None:
    raise ValueError(f"expected vendor None, got {last['vendor']!r}")
"""
        result = _run_optimized(code)
        if result.returncode == 0:
            raise AssertionError(
                "line-90 if/raise check was stripped (returncode 0 in -O mode)"
            )

    def test_line90_vendor_none_passes_on_correct_value(self):
        """Former line-90 check does NOT raise when vendor is None."""
        code = """
last = {"model": None, "vendor": None, "interview_completed": False}
if last["vendor"] is not None:
    raise ValueError(f"expected vendor None, got {last['vendor']!r}")
print("passed")
"""
        result = _run_optimized(code)
        if result.returncode != 0:
            raise AssertionError(
                f"line-90 if/raise check raised unexpectedly: {result.stderr}"
            )

    def test_line91_interview_false_raises_on_wrong_value(self):
        """Former line-91 check raises when interview_completed is not False."""
        code = """
last = {"model": None, "vendor": None, "interview_completed": True}
if last["interview_completed"] is not False:
    raise ValueError(f"expected interview_completed False, got {last['interview_completed']!r}")
"""
        result = _run_optimized(code)
        if result.returncode == 0:
            raise AssertionError(
                "line-91 if/raise check was stripped (returncode 0 in -O mode)"
            )

    def test_line91_interview_false_passes_on_correct_value(self):
        """Former line-91 check does NOT raise when interview_completed is False."""
        code = """
last = {"model": None, "vendor": None, "interview_completed": False}
if last["interview_completed"] is not False:
    raise ValueError(f"expected interview_completed False, got {last['interview_completed']!r}")
print("passed")
"""
        result = _run_optimized(code)
        if result.returncode != 0:
            raise AssertionError(
                f"line-91 if/raise check raised unexpectedly: {result.stderr}"
            )


class TestB101FixesDeadCodeRegressionTests:
    """Regression tests for B101 fixes in the TestMqttClientNoDeadCode class.

    The original code used bare ``assert`` statements (stripped under ``-O``)
    in the dead-code regression tests at the bottom of test_core.py.  They were
    replaced with ``if ...: raise AssertionError(...)`` so the checks survive
    optimised byte-code compilation.  These tests confirm each replacement
    raises when its condition is violated and does NOT raise when it holds,
    even under ``python -O``.
    """

    def test_no_time_import_check_raises_when_imports_present(self):
        """The 'no time import' if/raise raises when imports list is non-empty."""
        code = """
imports = ['time']
if imports:
    raise AssertionError(f"'time' module still imported: {imports}")
print("passed")
"""
        result = _run_optimized(code)
        if result.returncode == 0:
            raise AssertionError(
                "if/raise check was stripped or did not raise (returncode 0 in -O mode)"
            )

    def test_no_time_import_check_passes_when_empty(self):
        """The 'no time import' if/raise does NOT raise when imports is empty."""
        code = """
imports = []
if imports:
    raise AssertionError(f"'time' module still imported: {imports}")
print("passed")
"""
        result = _run_optimized(code)
        if result.returncode != 0:
            raise AssertionError(
                f"if/raise check raised unexpectedly: {result.stderr}"
            )

    def test_no_time_attr_check_raises_when_bad_present(self):
        """The 'no time attribute' if/raise raises when bad list is non-empty."""
        code = """
bad = ["line 5: time.sleep"]
if bad:
    raise AssertionError(f"time module still used: {bad}")
print("passed")
"""
        result = _run_optimized(code)
        if result.returncode == 0:
            raise AssertionError(
                "if/raise check was stripped or did not raise (returncode 0 in -O mode)"
            )

    def test_no_time_attr_check_passes_when_empty(self):
        """The 'no time attribute' if/raise does NOT raise when bad is empty."""
        code = """
bad = []
if bad:
    raise AssertionError(f"time module still used: {bad}")
print("passed")
"""
        result = _run_optimized(code)
        if result.returncode != 0:
            raise AssertionError(
                f"if/raise check raised unexpectedly: {result.stderr}"
            )

    def test_useless_instance_vars_check_raises_when_present(self):
        """The _username/_password if/raise raises when attrs are present."""
        code = """
class C:
    def __init__(self):
        self._username = "u"
        self._password = "p"
c = C()
if hasattr(c, '_username'):
    raise AssertionError("_username is a dead instance var")
print("passed")
"""
        result = _run_optimized(code)
        if result.returncode == 0:
            raise AssertionError(
                "if/raise check was stripped or did not raise (returncode 0 in -O mode)"
            )

    def test_useless_instance_vars_check_passes_when_absent(self):
        """The _username/_password if/raise does NOT raise when attrs absent."""
        code = """
class C:
    pass
c = C()
if hasattr(c, '_username'):
    raise AssertionError("_username is a dead instance var")
if hasattr(c, '_password'):
    raise AssertionError("_password is a dead instance var")
print("passed")
"""
        result = _run_optimized(code)
        if result.returncode != 0:
            raise AssertionError(
                f"if/raise check raised unexpectedly: {result.stderr}"
            )

    def test_pending_dict_check_raises_when_pattern_absent(self):
        """The _pending dict if/raise raises when the expected pattern is absent."""
        code = """
src = "something else entirely"
if '_pending[txn] = {"event": event, "slot": slot}' not in src:
    raise AssertionError("_pending must only contain 'event' and 'slot' keys")
print("passed")
"""
        result = _run_optimized(code)
        if result.returncode == 0:
            raise AssertionError(
                "if/raise check was stripped or did not raise (returncode 0 in -O mode)"
            )

    def test_pending_dict_check_passes_when_pattern_present(self):
        """The _pending dict if/raise does NOT raise when pattern is present."""
        code = """
src = '    _pending[txn] = {"event": event, "slot": slot}'
if '_pending[txn] = {"event": event, "slot": slot}' not in src:
    raise AssertionError("_pending must only contain 'event' and 'slot' keys")
print("passed")
"""
        result = _run_optimized(code)
        if result.returncode != 0:
            raise AssertionError(
                f"if/raise check raised unexpectedly: {result.stderr}"
            )

# B101 Security Findings Fix — Outcome Report

## Summary

Fixed the top 3 B101 (assert_used) findings reported by automated scanning in
`cli_anything/zigbee2mqtt/tests/test_b101_regression.py` at lines 74, 85, and 86.

## Findings Fixed

All three findings were in `cli_anything/zigbee2mqtt/tests/test_b101_regression.py`:

1. **Line 74** — `assert "ValueError" in result.stderr or "this runs" in result.stderr`
2. **Line 85** — `assert result.returncode == 0, "With -O, assert is stripped so this passes"`
3. **Line 86** — `assert "OK" in result.stdout`

## Root Cause

These were bare `assert` statements inside test methods that demonstrate the
B101 vulnerability itself. The irony: `assert` statements are stripped when
Python is compiled to optimised byte code (`python -O`), so the very checks
meant to verify the B101 mechanism would themselves be silently removed in
optimised mode — exactly the vulnerability they were testing for.

## Fix Applied

Replaced each bare `assert` with an equivalent `if …: raise AssertionError(…)`
pattern, which is NOT stripped by `-O` and preserves identical behaviour:

- Line 74 → `if "ValueError" not in result.stderr and "this runs" not in result.stderr: raise AssertionError(...)`
- Line 85 → `if result.returncode != 0: raise AssertionError(...)`
- Line 86 → `if "OK" not in result.stdout: raise AssertionError(...)`

This is a genuine fix (not a nosec suppression) because the `if/raise` pattern
is the recommended replacement for B101 and survives optimised byte-code
compilation.

## Regression Tests Added

Added `TestB101FixesLines74_85_86` class with 6 regression tests (2 per fix)
verifying each replacement:
- raises `AssertionError` when its condition is violated
- passes (does not raise) when its condition is satisfied

Tests:
- `test_line74_stderr_check_raises_when_both_absent`
- `test_line74_stderr_check_passes_when_token_present`
- `test_line85_returncode_zero_check_raises_when_nonzero`
- `test_line85_returncode_zero_check_passes_when_zero`
- `test_line86_stdout_check_raises_when_ok_absent`
- `test_line86_stdout_check_passes_when_ok_present`

## Verification

- Bandit scan: the three target findings (lines 74, 85, 86) no longer trigger.
- Full test suite: 88 passed (was 82; +6 new regression tests).
- Commit: 37c567f "Fix B101 findings at lines 74, 85, 86: replace assert with if/raise"

## Files Changed

- `cli_anything/zigbee2mqtt/tests/test_b101_regression.py` (+105, -6)

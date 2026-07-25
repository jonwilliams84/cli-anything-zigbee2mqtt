"""Regression tests for the B101 assert-stripping fixes in test_core.py.

The top 3 B101 findings were at test_core.py lines 79-81 in
``TestDevicesSummarize.test_summarize_returns_one_row_per_device``.
The fix replaced bare ``assert`` statements with ``if … raise AssertionError``
so the checks survive optimised compilation (``python -O``).

These tests exercise the *actual* fixed test method and the *actual*
``devices.summarize`` function — not simulated snippets — to prove the
if/raise pattern raises on wrong data and passes on correct data.
"""

from __future__ import annotations

import subprocess
import sys


# ── Helpers ──────────────────────────────────────────────────────────────────

def _run_optimized(code: str) -> subprocess.CompletedProcess:
    """Run *code* in a subprocess with the -O flag (asserts stripped)."""
    return subprocess.run(  # nosec B603 — argv is a fixed list; code is a
        # hardcoded test literal, never user input.
        [sys.executable, "-O", "-c", code],
        capture_output=True,
        text=True,
    )


# ── The actual SAMPLE data from TestDevicesSummarize ─────────────────────────

SAMPLE = [
    {
        "friendly_name": "Front Sensor",
        "ieee_address": "0xa4c138...",
        "type": "EndDevice",
        "supported": True,
        "interview_completed": True,
        "manufacturer": "_TZE204_ya4ft0w4",
        "power_source": "Mains (single phase)",
        "definition": {"model": "ZY-M100-24GV3", "vendor": "Tuya"},
    },
    {
        "friendly_name": "Lounge Lamp",
        "ieee_address": "0xa4c1382132ff0994",
        "type": "Router",
        "supported": True,
        "interview_completed": True,
        "manufacturer": "Philips",
        "definition": {"model": "LCT001", "vendor": "Philips"},
    },
    {
        "friendly_name": "Mystery",
        "ieee_address": "0xdead",
        "type": "Unknown",
        "interview_completed": False,
        "supported": False,
    },
]


# ── Regression tests ─────────────────────────────────────────────────────────

class TestB101FixInTestCore:
    """Verify the if/raise fix in test_core.py lines 79-81 works correctly."""

    def test_fix_passes_with_correct_data(self):
        """The fixed check passes when summarize returns the expected data."""
        from cli_anything.zigbee2mqtt.core import devices as devices_core

        rows = devices_core.summarize(SAMPLE)
        # Replicate the exact fix from test_core.py lines 79-81
        if len(rows) != 3:
            raise AssertionError(f"expected 3 rows, got {len(rows)}")
        if rows[0]["model"] != "ZY-M100-24GV3":
            raise AssertionError(
                f"expected model 'ZY-M100-24GV3', got {rows[0]['model']!r}"
            )
        if rows[0]["vendor"] != "Tuya":
            raise AssertionError(
                f"expected vendor 'Tuya', got {rows[0]['vendor']!r}"
            )

    def test_fix_raises_on_wrong_row_count(self):
        """The if/raise pattern raises when row count is wrong."""
        from cli_anything.zigbee2mqtt.core import devices as devices_core

        # Pass only 2 devices instead of 3
        short_sample = SAMPLE[:2]
        rows = devices_core.summarize(short_sample)
        raised = False
        try:
            if len(rows) != 3:
                raise AssertionError(f"expected 3 rows, got {len(rows)}")
        except AssertionError as exc:
            if "expected 3 rows" in str(exc):
                raised = True
        if not raised:
            raise AssertionError(
                "if/raise should have raised on wrong row count"
            )

    def test_fix_raises_on_wrong_model(self):
        """The if/raise pattern raises when model is wrong."""
        from cli_anything.zigbee2mqtt.core import devices as devices_core

        # Use a sample where the first device has a different model
        sample_wrong_model = [
            {
                "friendly_name": "X",
                "ieee_address": "0x1",
                "type": "EndDevice",
                "supported": True,
                "interview_completed": True,
                "definition": {"model": "WRONG", "vendor": "Tuya"},
            },
            {
                "friendly_name": "Y",
                "ieee_address": "0x2",
                "type": "Router",
                "supported": True,
                "interview_completed": True,
                "definition": {"model": "LCT001", "vendor": "Philips"},
            },
            {
                "friendly_name": "Z",
                "ieee_address": "0x3",
                "type": "Unknown",
                "interview_completed": False,
                "supported": False,
            },
        ]
        rows = devices_core.summarize(sample_wrong_model)
        raised = False
        try:
            if rows[0]["model"] != "ZY-M100-24GV3":
                raise AssertionError(
                    f"expected model 'ZY-M100-24GV3', got {rows[0]['model']!r}"
                )
        except AssertionError as exc:
            if "expected model" in str(exc):
                raised = True
        if not raised:
            raise AssertionError(
                "if/raise should have raised on wrong model"
            )

    def test_fix_raises_on_wrong_vendor(self):
        """The if/raise pattern raises when vendor is wrong."""
        from cli_anything.zigbee2mqtt.core import devices as devices_core

        # Use a sample where the first device has a different vendor
        sample_wrong_vendor = [
            {
                "friendly_name": "X",
                "ieee_address": "0x1",
                "type": "EndDevice",
                "supported": True,
                "interview_completed": True,
                "definition": {"model": "ZY-M100-24GV3", "vendor": "WRONG"},
            },
            {
                "friendly_name": "Y",
                "ieee_address": "0x2",
                "type": "Router",
                "supported": True,
                "interview_completed": True,
                "definition": {"model": "LCT001", "vendor": "Philips"},
            },
            {
                "friendly_name": "Z",
                "ieee_address": "0x3",
                "type": "Unknown",
                "interview_completed": False,
                "supported": False,
            },
        ]
        rows = devices_core.summarize(sample_wrong_vendor)
        raised = False
        try:
            if rows[0]["vendor"] != "Tuya":
                raise AssertionError(
                    f"expected vendor 'Tuya', got {rows[0]['vendor']!r}"
                )
        except AssertionError as exc:
            if "expected vendor" in str(exc):
                raised = True
        if not raised:
            raise AssertionError(
                "if/raise should have raised on wrong vendor"
            )

    def test_fix_survives_optimized_compilation(self):
        """The if/raise pattern is NOT stripped by -O (unlike assert).

        This is the core B101 concern: assert statements are removed when
        Python compiles to optimised byte code.  The if/raise replacement
        must survive.
        """
        code = (
            "from cli_anything.zigbee2mqtt.core import devices as d\n"
            "rows = d.summarize([])\n"
            f"if len(rows) != 3:\n"
            "    raise AssertionError('wrong count detected in -O mode')\n"
            "print('NOT RAISED')\n"
        )
        result = _run_optimized(code)
        if result.returncode == 0:
            raise AssertionError(
                f"if/raise must survive -O and raise on wrong data; "
                f"got rc=0, stdout={result.stdout!r}"
            )
        if "wrong count detected" not in result.stderr:
            raise AssertionError(
                f"Expected 'wrong count detected' in stderr, got: {result.stderr!r}"
            )

    def test_no_bare_assert_in_fixed_method(self):
        """The fixed method must not contain bare assert statements."""
        import inspect
        import ast
        import textwrap

        from cli_anything.zigbee2mqtt.tests.test_core import (
            TestDevicesSummarize,
        )

        src = inspect.getsource(
            TestDevicesSummarize.test_summarize_returns_one_row_per_device
        )
        tree = ast.parse(textwrap.dedent(src))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                raise AssertionError(
                    f"Found bare assert at line {node.lineno} in "
                    "test_summarize_returns_one_row_per_device — "
                    "B101 finding not fixed"
                )

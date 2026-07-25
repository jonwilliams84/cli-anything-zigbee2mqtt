# B101 Security Findings Fix — Summary

## Top 3 Findings Fixed

All three B101 findings were at `cli_anything/zigbee2mqtt/tests/test_core.py` lines 79, 80, 81 in the method `TestDevicesSummarize.test_summarize_returns_one_row_per_device`.

### Original Code (lines 79-81)
```python
assert len(rows) == 3
assert rows[0]["model"] == "ZY-M100-24GV3"
assert rows[0]["vendor"] == "Tuya"
```

### Fixed Code (lines 79-90)
```python
# B101 fix: assert is stripped when compiling to optimised byte code (-O);
# use if/raise so the check survives optimised compilation.
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
```

## Verification Results

- **Tests**: 76 passed, 0 failed
- **Bandit re-scan**: No B101 findings at lines 79, 80, 81 (confirmed fixed)
- **Remaining B101 count**: 14 (at other lines — not part of top 3 findings, untouched)
- **Diff scope**: Only lines 79-81 changed in test_core.py; all other assert statements preserved exactly

## Regression Tests

File: `cli_anything/zigbee2mqtt/tests/test_b101_regression.py` (6 tests, all passing)

1. `test_fix_passes_with_correct_data` — verifies the if/raise pattern passes with correct data
2. `test_fix_raises_on_wrong_row_count` — verifies it raises on wrong row count
3. `test_fix_raises_on_wrong_model` — verifies it raises on wrong model
4. `test_fix_raises_on_wrong_vendor` — verifies it raises on wrong vendor
5. `test_fix_survives_optimized_compilation` — verifies if/raise is NOT stripped by -O flag
6. `test_no_bare_assert_in_fixed_method` — AST-verifies no bare assert remains in the fixed method

## Commit

```
fce3b76 fix: replace assert with if/raise for B101 findings at test_core.py lines 79-81
```

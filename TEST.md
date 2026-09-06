# TEST.md — test strategy & results for cli-anything-zigbee2mqtt

All tests run without a real MQTT broker or kubectl: core modules are
exercised against fake `BridgeClient` objects, and the CLI layer end-to-end
via Click's `CliRunner` with `make_client` patched.

## Running the suite

```bash
/work/venv/bin/python -m pytest cli_anything/zigbee2mqtt/tests \
  --cov=cli_anything --cov-fail-under=80 -q --durations=10
/work/venv/bin/ruff check cli_anything/ --output-format=github
/work/venv/bin/ruff format --check --diff cli_anything/
/work/venv/bin/bandit -r cli_anything/ -ll -x '*/tests/*,*/test_*.py,*/conftest.py'
```

This is exactly the CI gate; it must exit 0 before merge.

## Test files

| File | What it covers |
|---|---|
| `test_core.py` | Every core module against fake clients: bridge (info/state/options/definitions/watch), devices (list/rename/remove/set/get/state/stale/exposes/endpoints/clusters/reportings/availability), groups, scenes, ota (check/update/schedule/unschedule **+ `check_all` network firmware sweep**), attributes (raw ZCL read/write), bindings, install_code, extensions, converters, k8s backend, mqtt_client, project config |
| `test_full_e2e.py` | CLI layer end-to-end via CliRunner: every command group happy path + key error paths, `--json` output shapes, workflow tests (e.g. `ota check --all` → `ota schedule` on the device the sweep surfaced) |
| `test_cli_helpers.py` | CLI helper functions (`emit`, `_print_table`, `_parse_kv_fields`, …) |
| `test_refine.py` | The v0.2.0 refine pass: bindings, generate-converter, configure-reporting, install codes, extensions, group options |
| `test_coverage_boost.py` / `test_coverage_boost2.py` | Branch coverage for watch callbacks, malformed payloads, k8s backend |
| `test_repl_skin.py` | REPL prompt/parsing helpers |
| `test_b101_regression.py`, `test_b105_regression.py`, `test_security_regression.py` | Regression locks: no `time.time()` misuse, shell-safety, callback exceptions logged |

## Latest run (v0.2.0, 2026-09-06)

- 744 passed, 0 failed
- Coverage: **92.8%** total (gate requires 80%)
- `ruff check` / `ruff format --check` / `bandit -ll`: clean
- New in this run: `TestOtaCheckAll` (15 unit tests for `check_all` /
  `summarize_check` / `_classify`) and `TestOtaCheckAllSweep` (10 E2E tests
  for `ota check --all`, `--include-disabled`, `--with-update`, arg
  validation, and the sweep → schedule workflow).

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
| `test_core.py` | Every core module against fake clients: bridge (info/state/options/definitions/watch), devices (list/rename/remove/set/get/state/stale/exposes/endpoints/clusters/reportings/availability **+ `battery_sweep` network battery audit**), groups, scenes, ota (check/update/schedule/unschedule **+ `check_all` network firmware sweep**), attributes (raw ZCL read/write), bindings, install_code, extensions, converters, k8s backend, mqtt_client, project config |
| `test_full_e2e.py` | CLI layer end-to-end via CliRunner: every command group happy path + key error paths, `--json` output shapes, workflow tests (e.g. `ota check --all` → `ota schedule` on the device the sweep surfaced) |
| `test_cli_helpers.py` | CLI helper functions (`emit`, `_print_table`, `_parse_kv_fields`, …) |
| `test_refine.py` | The v0.2.0 refine pass: bindings, generate-converter, configure-reporting, install codes, extensions, group options |
| `test_coverage_boost.py` / `test_coverage_boost2.py` | Branch coverage for watch callbacks, malformed payloads, k8s backend |
| `test_refine2.py` | Second refine pass: interactive REPL loop E2E (banner, exit/quit/help, command dispatch, unknown-command and exception handling, EOF/Ctrl-C, bare-invocation fallback), ReplSkin banner/prompt/session factory (incl. ImportError fallbacks), CLI argument error paths (invalid JSON, missing keys, clobber guard, unknown cluster/extension, MqttError propagation), core branches (log-level get/set, device disable/enable/last_seen, flatten edge cases, extension save validation, converter ls parsing, env overrides, BridgeClient payload coercion + missing-paho guard) |
| `test_repl_skin.py` | REPL prompt/parsing helpers |
| `test_b101_regression.py`, `test_b105_regression.py`, `test_security_regression.py` | Regression locks: no `time.time()` misuse, shell-safety, callback exceptions logged |

## Latest run (refine pass 2, 2026-09-06)

- 815 passed, 0 failed
- Coverage: **97.6%** total (gate requires 80%)
- `ruff check` / `ruff format --check` / `bandit -ll`: clean
- New in this run: `test_refine2.py` — 71 tests for the previously
  untested interactive REPL usage pattern, ReplSkin banner/session paths,
  user-facing CLI error paths, and remaining core branches (see table
  above). Overall coverage went 92.8% → 97.6%; the REPL loop and
  `print_banner` went from 0% to fully covered.

## Latest run (refine pass 3, 2026-09-06)

- 851 passed, 0 failed
- Coverage: **99.9%** total (gate requires 80%)
- `ruff check` / `ruff format --check` / `bandit -ll`: clean
- New in this run: `test_refine3.py` — 36 tests closing the last
  uncovered CLI error/edge branches (config save, bridge restart
  `--via-kubectl` and its MqttError abort, watch-logging `--json`,
  log-level get/set, blank-cluster definitions abort, device
  disable/enable command bodies, generate-converter without source,
  device write ValueError, all five scene command aborts, extension
  show `--json`, REPL ImportError fallback and SystemExit swallowing,
  `main()` entry point, defensive returns after `_abort` in
  `_parse_json_obj` / `_preflight_attributes` / options-set /
  definitions) plus core branches (`devices.last_seen` with a naive
  timestamp, `bindings.unbind` without clusters, binding targets
  without an ieee, converters ls passthrough, `BridgeClient.publish`
  lazy connect, non-matching subscriber filters, ReplSkin packaged-
  skill fallback and no-isatty color detection). Overall coverage went
  97.6% → 99.9%; `zigbee2mqtt_cli.py` and `repl_skin.py` are now at
  100%. The only line left uncovered is `project.load_config`'s
  boolean-env branch (line 52) — unreachable because no `DEFAULTS`
  key is a boolean.

## Latest run (refine pass 4, 2026-09-06)

- 888 passed, 0 failed
- Coverage: **99.8%** total (gate requires 80%)
- `ruff check` / `ruff format --check` / `bandit -ll`: clean
- New in this run: `device battery` — network-wide battery audit
  (`battery_sweep` + `classify_battery` in `core/devices.py`, CLI command in
  `zigbee2mqtt_cli.py`). One `<base>/#` subscription collects every retained
  device state and joins `battery` / `battery_low` / `voltage` onto the
  bridge inventory; rows sort worst-first (low → unknown → ok, then percent
  ascending); `--below`, `--low-only`, `--json`, `--duration` options;
  mains devices and the coordinator are dropped; sleeping battery devices
  classify as `unknown`. 37 new tests: 30 unit (`test_core.py`:
  `TestClassifyBattery` + `TestBatterySweep`) and 7 E2E/workflow
  (`test_full_e2e.py`: `TestDeviceBatteryCommand`, including the
  `battery --low-only` → `device state` confirm-a-low-device workflow).

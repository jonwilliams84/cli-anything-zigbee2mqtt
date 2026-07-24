# cli-anything-zigbee2mqtt

CLI + Python harness for [Zigbee2MQTT](https://www.zigbee2mqtt.io). Drives a running z2m
process over its MQTT request/response bridge — bridge control, device/group management,
OTA, network admin, bindings, install-codes, external converters, and extensions. Sibling
of `cli-anything-homeassistant`. Python 3.10+, Click + paho-mqtt.

## Layout
- `cli_anything/zigbee2mqtt/zigbee2mqtt_cli.py` — Click CLI + REPL (entry point `main`); 900+ lines, all command wiring.
- `cli_anything/zigbee2mqtt/core/` — one module per command group: `mqtt_client.py` (`BridgeClient`, request/response correlation), `bridge.py`, `devices.py`, `bindings.py`, `groups.py`, `ota.py`, `admin.py`, `converters.py`, `extensions.py`, `install_code.py`, `k8s_backend.py` (kubectl helpers), `project.py` (local profile).
- `cli_anything/zigbee2mqtt/tests/` — `test_core.py`, `test_refine.py`. Run against a fake MQTT transport; no broker needed.
- `cli_anything/zigbee2mqtt/skills/SKILL.md` and `skills/cli-anything-zigbee2mqtt/SKILL.md` — agent-facing skill docs (keep in sync with CLI changes).
- `setup.py` is the only manifest (no pyproject/requirements). README.md (root) is the full command reference.

## Build / test / run
```bash
pip install -e .                                              # install + console_script
python3 -m pytest cli_anything/zigbee2mqtt/tests/ -v          # full suite, no broker
cli-anything-zigbee2mqtt --help
cli-anything-zigbee2mqtt --mqtt-host <broker> --base-topic zigbee2mqtt config save  # first-time setup
```
No lint/CI config present. No release automation — version is hand-bumped in `setup.py`
(note: README/SKILL describe v0.2.0 but `setup.py` still says `version="0.1.0"`).

## Architecture notes
- Every mutation publishes `zigbee2mqtt/bridge/request/<path>` and reads the matching
  `zigbee2mqtt/bridge/response/<path>`, correlated by a `transaction` id — so success/failure
  is reliably detectable. Add new MQTT commands through `BridgeClient` in `mqtt_client.py`.
- Most commands need only the MQTT broker. Two paths require `kubectl`: `bridge restart --via-kubectl`
  and the `converter` subcommand (manages `data/external_converters/*.js` files inside the z2m
  container via `core/k8s_backend.py`). Extensions, by contrast, are managed entirely over MQTT.
- Every command supports `--json` for machine-readable output.

## Conventions / gotchas
- Connection profile lives at `~/.config/cli-anything-zigbee2mqtt.json`; per-key env overrides
  (`CLI_Z2M_MQTT_HOST`, `CLI_Z2M_BASE_TOPIC`, …). NEVER commit the profile (gitignored — it holds broker creds).
- New command group = new `core/` module + wiring in `zigbee2mqtt_cli.py` + a test using the
  fake transport + an entry in README.md and both SKILL.md files.
- `device rename` preserves the HA unique_id (no entity re-discovery), per README — keep that property if touching rename.

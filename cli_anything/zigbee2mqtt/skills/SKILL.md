---
name: cli-anything-zigbee2mqtt
description: CLI harness for Zigbee2MQTT — bridge control, device list/rename/remove/configure/interview, direct bind/unbind + bindings inspection, last-seen staleness sweeps, retained-state one-shot reads, generate starter external converters from interview data, manual attribute reporting setup, group management with options, OTA firmware updates, permit-join, network map, touchlink, install-code pre-registration for join-protected devices, and external converter + extension file management. Talks to a running z2m process over its MQTT request/response API.
---

# cli-anything-zigbee2mqtt

Agent-facing CLI for Zigbee2MQTT. Every mutation goes through z2m's MQTT
request/response bridge (`zigbee2mqtt/bridge/request/<path>` →
`zigbee2mqtt/bridge/response/<path>`), correlated by transaction id, so an
agent can reliably tell whether an operation succeeded.

## When to use

- Renaming, removing, configuring, or re-interviewing a Zigbee device.
- Triggering OTA firmware checks / updates.
- Opening the network (`permit_join`) to add a new device or close it.
- Reading the current device or group inventory in JSON form.
- Generating a network map (raw / graphviz / plantuml).
- Pushing or removing an external converter file (e.g. to override an exposes
  block in `zigbee-herdsman-converters` without forking it).
- Restarting the z2m process — polite via MQTT, or hard via kubectl rollout.

## Install

```bash
pip install -e /path/to/cli-anything-zigbee2mqtt
cli-anything-zigbee2mqtt --mqtt-host <broker> --base-topic zigbee2mqtt config save
```

Two external deps:
1. MQTT broker the z2m bridge already talks to.
2. `kubectl` is only required for `bridge restart --via-kubectl` and the
   `converter` subcommand. The MQTT-only commands work without it.

## Command groups

| Group | Examples |
|---|---|
| `bridge` | `bridge info`, `bridge state`, `bridge restart`, `bridge restart --via-kubectl`, `bridge health`, `bridge options-get`, `bridge options-set '{"advanced":{"log_level":"info"}}'`, `bridge watch-events --duration 30`, `bridge watch-logging --duration 10` |
| `device` | `device list`, `device list --full`, `device show <name>`, `device rename <from> <to>`, `device remove <name> --force --block`, `device configure <name>`, `device interview <name>`, `device options <name> '{"debounce":1}'`, `device set <name> state=ON brightness=180`, `device get <name> state brightness`, `device watch <name> --duration 10`, `device state <name>` (one-shot retained), `device stale --threshold 60`, `device generate-converter <name> -o starter.js`, `device configure-reporting <name> --cluster genOnOff --attribute onOff --min 0 --max 300`, `device bind <from> <to> --cluster genOnOff`, `device unbind <from> <to>`, `device bindings [<name>]` |
| `group` | `group list`, `group add <name>`, `group remove <name>`, `group rename <from> <to>`, `group add-member <group> <device>`, `group remove-member <group> <device>`, `group remove-all <group>`, `group options <name> '{"transition":1.5,"retain":true}'` |
| `ota` | `ota check <name>`, `ota update <name>`, `ota schedule <name>` |
| `network` | `network permit-join on --time 60`, `network permit-join off`, `network map --type graphviz`, `network touchlink-scan`, `network coordinator-check`, `network backup` |
| `install-code` | `install-code add <QR-text>`, `install-code remove <QR-text>` — pre-register codes for join-protected devices (Bosch, certain Aqara) |
| `converter` | `converter list`, `converter show <name.js>`, `converter add <name.js> ./local.js`, `converter remove <name.js>` |
| `extension` | `extension list`, `extension show <name.js>`, `extension save <name.js> ./local.js`, `extension remove <name.js>` — z2m extensions (deeper than converters) via MQTT |
| `config` | `config show`, `config save` |
| `repl` | Interactive shell (default with no subcommand) |

All commands support `--json` for machine-readable output.

## Agent guidance

**Renames are HA-aware by default** — `device rename A B` also renames the
linked Home Assistant entities (`homeassistant_rename: true`) so unique_ids stay
stable. Pass `--no-ha-rename` if you specifically want fresh entities.

**OTA updates can take many minutes.** `ota update --timeout 1200` gives 20
min. While waiting, monitor progress in another shell with
`bridge watch-events --duration 1200`.

**Removing a device is destructive** — defaults prompt for confirmation. For
unattended use, set `--force` (network removal skipped) and/or `--block`
(prevent rejoining). Always combine with `--yes` to skip the prompt.

**Permit-join auto-closes.** `permit-join on --time 60` opens for 60s then
closes. Don't leave it open indefinitely.

**External converters override the upstream `zigbee-herdsman-converters`
definitions.** Drop a `.js` file in z2m's `data/external_converters/` and z2m
re-publishes MQTT discovery with whatever you've overridden. `converter add`
leaves a timestamped `.bak` next to the existing file (if any) for rollback.

**Restart timing**: a polite `bridge restart` takes ~10-30s while z2m flushes
state and reconnects to the coordinator. The hard variant via `--via-kubectl`
adds the rollout time (~20-60s for the pod to be replaced).

## Typical workflows

### Pair a new device

```bash
cli-anything-zigbee2mqtt network permit-join on --time 120
# put the device in pairing mode
cli-anything-zigbee2mqtt bridge watch-events --duration 120
# device should appear as a `device_joined` event; then `device_interview` events
cli-anything-zigbee2mqtt device list
cli-anything-zigbee2mqtt device rename <auto-generated-name> <friendly-name>
```

### Quick health sweep

```bash
cli-anything-zigbee2mqtt --json bridge info | jq '.coordinator, .restart_required, .permit_join'
cli-anything-zigbee2mqtt --json bridge health
cli-anything-zigbee2mqtt --json device list | jq '[.[] | select(.interview_completed==false)]'
```

### Patch a device's exposes (the way we fixed Tuya ZY-M100-24GV3 sensitivity)

```bash
# Author the .js file locally based on the upstream definition,
# then push and restart z2m so it auto-loads.
cli-anything-zigbee2mqtt converter add zy-m100-fix.js ./zy-m100-fix.js
cli-anything-zigbee2mqtt bridge restart --via-kubectl
# Verify the overridden discovery is published:
cli-anything-zigbee2mqtt --json device show <name> | jq '.definition.exposes'
```

### Onboard an unsupported device — generate a starter converter

```bash
# Pair the device first (no support → state is just raw cluster reads).
cli-anything-zigbee2mqtt network permit-join on --time 120
# After pairing, ask z2m to write a starter converter from the interview data:
cli-anything-zigbee2mqtt device generate-converter <auto-name> -o new-device.js
# Edit new-device.js, then push it:
cli-anything-zigbee2mqtt converter add new-device.js ./new-device.js
cli-anything-zigbee2mqtt bridge restart
```

### Direct device-to-device binding (sub-100ms switch → light)

```bash
# Bind a wall switch to a bulb so the switch works even when z2m is down
cli-anything-zigbee2mqtt device bind switch_kitchen light_kitchen \
  --cluster genOnOff --cluster genLevelCtrl

# Inspect every binding in the network
cli-anything-zigbee2mqtt --json device bindings | jq '.'

# Or just one device
cli-anything-zigbee2mqtt device bindings switch_kitchen

# Remove it
cli-anything-zigbee2mqtt device unbind switch_kitchen light_kitchen
```

### Find dead devices

```bash
# Anything that hasn't spoken in 6 hours, sorted oldest-first
cli-anything-zigbee2mqtt --json device stale --threshold 360 | jq '.[].friendly_name'

# Quick check on the one device you care about
cli-anything-zigbee2mqtt device state sensor_balcony
```

### Fix wrong reporting intervals

```bash
# A battery temperature sensor reporting every 30s drains in a week —
# slow it down without re-interviewing.
cli-anything-zigbee2mqtt device configure-reporting balcony-temp \
  --cluster msTemperatureMeasurement --attribute measuredValue \
  --min 300 --max 1800 --change 0.5
```

### Onboard a join-code-protected device (Bosch, some Aqara)

```bash
cli-anything-zigbee2mqtt install-code add "G$M001 1234ABCD..."
cli-anything-zigbee2mqtt network permit-join on --time 120
# Now put the device in pairing mode.
```

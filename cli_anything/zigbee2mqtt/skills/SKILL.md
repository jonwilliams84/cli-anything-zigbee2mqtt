---
name: cli-anything-zigbee2mqtt
description: CLI harness for Zigbee2MQTT — bridge control, device list/rename/remove/configure/interview, exposes introspection, endpoint/cluster and configured-reporting introspection, the bridge cluster dictionary (bridge definitions), availability checks and offline sweeps, raw ZCL cluster attribute read/write, direct bind/unbind + bindings inspection, last-seen staleness sweeps, retained-state one-shot reads, generate starter external converters from interview data, manual attribute reporting setup, group management with options plus groupcast set/get/state, Zigbee scene store/recall/add/rename/remove on devices or groups, OTA firmware updates, permit-join, network map, touchlink, install-code pre-registration for join-protected devices, and external converter + extension file management. Talks to a running z2m process over its MQTT request/response API.
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
- Finding out which properties a device accepts before writing to it
  (`device exposes`) — including units, ranges and enum values.
- Checking which devices are unreachable right now (`device availability-sweep`).
- Finding which endpoints and clusters a device has, and which cluster/attribute
  names z2m will accept (`device endpoints`, `device clusters`, `bridge definitions`).
- Reaching a cluster attribute no converter models (`device read` / `device write`).
- Auditing or verifying attribute reporting (`device reportings`).
- Driving a whole room at once (`group set`) instead of looping over members.
- Capturing or replaying a lighting scene (`scene store` / `scene recall`).
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
| `bridge` | `bridge info`, `bridge state`, `bridge restart`, `bridge restart --via-kubectl`, `bridge health`, `bridge options-get`, `bridge options-set '{"advanced":{"log_level":"info"}}'`, `bridge definitions`, `bridge definitions --cluster genOnOff [--commands]`, `bridge definitions --custom`, `bridge watch-events --duration 30`, `bridge watch-logging --duration 10` |
| `device` | `device list`, `device list --full`, `device show <name>`, `device rename <from> <to>`, `device remove <name> --force --block`, `device configure <name>`, `device interview <name>`, `device options <name> '{"debounce":1}'`, `device set <name> state=ON brightness=180`, `device get <name> state brightness`, `device watch <name> --duration 10`, `device state <name>` (one-shot retained), `device stale --threshold 60`, `device generate-converter <name> -o starter.js`, `device configure-reporting <name> --cluster genOnOff --attribute onOff --min 0 --max 300`, `device bind <from> <to> --cluster genOnOff`, `device unbind <from> <to>`, `device bindings [<name>]`, `device exposes <name> --settable`, `device endpoints <name>`, `device clusters <name> --direction input|output`, `device reportings [<name>]`, `device availability <name>`, `device availability-sweep --offline-only`, `device read <name> --cluster genBasic --attribute zclVersion`, `device write <name> --cluster genOnOff onOff=1`, `device disable <name>`, `device enable <name>`, `device last-seen <name>` |
| `group` | `group list`, `group add <name>`, `group remove <name>`, `group rename <from> <to>`, `group add-member <group> <device>`, `group remove-member <group> <device>`, `group remove-all <group>`, `group options <name> '{"transition":1.5,"retain":true}'`, `group members <name>`, `group set <name> state=ON brightness=200 transition=2`, `group get <name> state brightness`, `group state <name>` (one-shot retained) |
| `scene` | `scene list <target>`, `scene store <target> <id> --name Chill`, `scene recall <target> <id>`, `scene add <target> <id> --state ON --brightness 200 --color-temp 370 --transition 2`, `scene rename <target> <id> <name>`, `scene remove <target> <id>`, `scene remove-all <target> --yes` — TARGET is a device or group friendly_name; add `--endpoint <n>` for multi-gang devices |
| `ota` | `ota check <name>`, `ota update <name>`, `ota schedule <name>`, `ota unschedule <name>` |
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

**Group commands are a single groupcast.** `group set kitchen state=ON` changes
every member at once (visually atomic, one radio transmission). Never loop
`device set` over members — that is N unicasts and the lights step raggedly.

**Scene commands are Zigbee cluster commands, not bridge requests.** They publish
to `<base>/<target>/set` and have NO `bridge/response`, so the exit code only
proves the publish succeeded. To verify, read it back: `scene list <target>`
(from the retained state payload) or `group state <target>` after a recall.

**`scene store` snapshots live state; `scene add` writes values directly.** Store
means "set the lights how you want them, then capture" — so `group set` first,
then `scene store`. Use `scene add` when scripting unattended (no need to disturb
the current light state). Scene ids are 0-255 and per endpoint.

**Check `device exposes` before `device set`.** It is a local read of the
retained inventory (no round trip, works on a sleeping battery device) and gives
the exact property names, `access` flags (`published,set,get`), units, ranges and
enum values. `--settable` narrows it to what is writable. Guessing property names
and watching a `device set` silently do nothing is the most common failure mode.

**`availability` null means unknown, not offline.** The availability topics only
exist when z2m's `availability` feature is enabled. Use
`device availability-sweep --offline-only` for the reachability verdict and
`device stale --threshold N` (raw `last_seen`) as the independent cross-check —
if availability is null everywhere, fall back to `device stale`.

**Find the cluster before you name it.** `device endpoints <name>` gives the
per-endpoint counts (clusters / bindings / reports / scenes) so you know which
`--endpoint` to use; `device clusters <name> --direction input` lists the clusters
`device read` / `device write` / `device configure-reporting` can target, and
`--direction output` lists the ones `device bind` can wire up — with `bound` /
`reported` columns showing what already exists. Both are local reads of the
retained inventory, so they cost nothing and work on a sleeping device.
`bridge definitions --cluster <name>` then gives the exact attribute (or
`--commands`) names that cluster defines, including manufacturer clusters added
by external converters (`--custom`). Guessing cluster names is the second most
common failure mode after guessing property names.

**`device reportings` is the read-back for `device configure-reporting`.** The
bridge response only confirms the request was accepted; `device reportings
<name>` shows what z2m actually recorded. With no argument it sweeps the network,
which finds sensors whose interview never configured reports.

**`device read` / `device write` are ZCL cluster commands, not bridge requests.**
Exit 0 only means the publish succeeded. The read's answer arrives
asynchronously on the device's state topic, so chain
`device read <name> --cluster … --attribute …` with `device state <name>`, or run
`device watch <name>` alongside. Cluster/attribute may be names or numeric ids
(`0x0006`); manufacturer-specific attributes need `--manufacturer-code` or the
device rejects the frame. Prefer the modelled property via `device set` whenever
`device exposes` lists one — raw writes bypass all converter validation.

**`ota unschedule` backs out a queued update.** A `schedule` stays pending until
the device next checks in (hours, on battery), so cancel with `ota unschedule
<name>` rather than waiting it out.

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

### Find out what a device supports, then drive it

```bash
# Exactly which properties `device set` will accept, with ranges and enums
cli-anything-zigbee2mqtt --json device exposes 'Lounge Lamp' --settable \
  | jq '.[] | {property, type, value_min, value_max, values}'

# Then write one of them, and read the result back
cli-anything-zigbee2mqtt device set 'Lounge Lamp' brightness=200
cli-anything-zigbee2mqtt --json device state 'Lounge Lamp'
```

### Walk down to a raw attribute (endpoints → clusters → definitions → read)

```bash
# 1. Which endpoints does it have, and where do the clusters/reports live?
cli-anything-zigbee2mqtt --json device endpoints plug1

# 2. Which clusters can be read/written on that endpoint?
cli-anything-zigbee2mqtt --json device clusters plug1 --endpoint 1 --direction input \
  | jq '.[] | {cluster, reported}'

# 3. Which attribute names does that cluster define?
cli-anything-zigbee2mqtt --json bridge definitions --cluster genOnOff \
  | jq '.[] | {attribute, id, type}'

# 4. Read it, then collect the answer from the state topic
cli-anything-zigbee2mqtt device read plug1 --cluster genOnOff --attribute onOff --endpoint 1
cli-anything-zigbee2mqtt --json device state plug1
```

### Audit attribute reporting across the network

```bash
# Every configured report, network-wide (local read, no round trips)
cli-anything-zigbee2mqtt --json device reportings | jq 'group_by(.friendly_name) | map({device: .[0].friendly_name, reports: length})'

# A sensor with no rows never had reports configured — set one up, then verify
cli-anything-zigbee2mqtt device configure-reporting sensor1 \
  --cluster msTemperatureMeasurement --attribute measuredValue \
  --min 300 --max 1800 --change 0.5
cli-anything-zigbee2mqtt --json device reportings sensor1
```

### Reach an attribute the converter does not model

```bash
# Issue the ZCL read (fire-and-forget) ...
cli-anything-zigbee2mqtt device read 'Lounge Lamp' \
  --cluster genBasic --attribute zclVersion --attribute modelId
# ... then pick the answer up off the device's state topic
cli-anything-zigbee2mqtt --json device state 'Lounge Lamp'

# Manufacturer-specific write (Tuya / Bosch attributes need the code)
cli-anything-zigbee2mqtt device write 'Radiator' \
  --cluster hvacThermostat --manufacturer-code 4617 --endpoint 1 \
  operatingMode=1
```

### Find dead devices

```bash
# Anything that hasn't spoken in 6 hours, sorted oldest-first
cli-anything-zigbee2mqtt --json device stale --threshold 360 | jq '.[].friendly_name'

# Quick check on the one device you care about
cli-anything-zigbee2mqtt device state sensor_balcony
cli-anything-zigbee2mqtt device availability sensor_balcony

# Whole-network reachability in one pass (offline first)
cli-anything-zigbee2mqtt --json device availability-sweep --offline-only \
  | jq '.[].friendly_name'
```

### Fix wrong reporting intervals

```bash
# A battery temperature sensor reporting every 30s drains in a week —
# slow it down without re-interviewing.
cli-anything-zigbee2mqtt device configure-reporting balcony-temp \
  --cluster msTemperatureMeasurement --attribute measuredValue \
  --min 300 --max 1800 --change 0.5
```

### Build a room scene and recall it

```bash
# One group per room; the group is addressable exactly like a device.
cli-anything-zigbee2mqtt group add kitchen-lights
cli-anything-zigbee2mqtt group add-member kitchen-lights light_kitchen_1
cli-anything-zigbee2mqtt group add-member kitchen-lights light_kitchen_2

# Dial the room in, then snapshot it as scene 1 on every member.
cli-anything-zigbee2mqtt group set kitchen-lights state=ON brightness=60 color_temp=450
cli-anything-zigbee2mqtt scene store kitchen-lights 1 --name Chill

# Verify it landed, then replay it (one groupcast).
cli-anything-zigbee2mqtt --json scene list kitchen-lights
cli-anything-zigbee2mqtt scene recall kitchen-lights 1
cli-anything-zigbee2mqtt --json group state kitchen-lights

# Scripted alternative — no need to disturb the lights first.
cli-anything-zigbee2mqtt scene add kitchen-lights 2 --name Dinner \
  --state ON --brightness 200 --color-temp 370 --transition 2
```

### Onboard a join-code-protected device (Bosch, some Aqara)

```bash
cli-anything-zigbee2mqtt install-code add "G$M001 1234ABCD..."
cli-anything-zigbee2mqtt network permit-join on --time 120
# Now put the device in pairing mode.
```

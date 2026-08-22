# cli-anything-zigbee2mqtt

A command-line + Python harness for [Zigbee2MQTT](https://www.zigbee2mqtt.io) —
bridge control, device management, OTA firmware updates, network admin, group
management, and external-converter file management. Talks to a running z2m
process over its MQTT request/response API; no frontend dependency.

Sibling of [`cli-anything-homeassistant`](https://github.com/jonwilliams84/cli-anything-homeassistant)
in the same `cli-anything-*` family.

## Install

```bash
git clone https://github.com/jonwilliams84/cli-anything-zigbee2mqtt.git
cd cli-anything-zigbee2mqtt
pip install -e .
cli-anything-zigbee2mqtt --help
```

External deps:
- An MQTT broker the z2m bridge already publishes to (required for every command except `converter`).
- `kubectl` (only needed for `bridge restart --via-kubectl` and the `converter`
  subcommand, which manage files inside the z2m container).

## First-time setup

```bash
cli-anything-zigbee2mqtt \
  --mqtt-host 10.32.100.5 --base-topic zigbee2mqtt \
  config save
```

Profile lives at `~/.config/cli-anything-zigbee2mqtt.json`. Per-key env
overrides also work: `CLI_Z2M_MQTT_HOST`, `CLI_Z2M_BASE_TOPIC`, etc.

## Command groups

| Group | Examples |
|---|---|
| `bridge` | `info / state / status / restart / health / options-get / options-set / definitions / log-level / watch-events / watch-logging` |
| `device` | `list / show / rename / remove / configure / interview / options / set / get / watch / state / stale / exposes / endpoints / clusters / reportings / availability / availability-sweep / read / write / generate-converter / configure-reporting / bind / unbind / bindings / disable / enable / last-seen` |
| `group` | `list / members / add / remove / rename / add-member / remove-member / remove-all / options / set / get / state` |
| `scene` | `list / store / recall / add / rename / remove / remove-all` — Zigbee scenes on a device or group |
| `ota` | `check / update / schedule / unschedule` |
| `network` | `permit-join on/off / map / touchlink-* / coordinator-check / backup` |
| `install-code` | `add / remove` — pre-register codes for join-protected devices (Bosch, certain Aqara) |
| `converter` | `list / show / add / remove` — manages `data/external_converters/*.js` via kubectl |
| `extension` | `list / show / save / remove` — z2m extensions (deeper than converters); managed entirely over MQTT |
| `config` | `show / save` (local connection profile) |
| `repl` | Interactive shell (default with no subcommand) |

All commands support `--json` for machine-readable output.

## Quick examples

```bash
# Bridge state + version
cli-anything-zigbee2mqtt bridge state
cli-anything-zigbee2mqtt bridge info

# Device inventory
cli-anything-zigbee2mqtt device list
cli-anything-zigbee2mqtt device show 'Lounge Lamp'

# Rename a device (keeps HA unique_id, no entity re-discovery needed)
cli-anything-zigbee2mqtt device rename 'Old Name' 'New Name'

# Send / read state
cli-anything-zigbee2mqtt device set 'Lounge Lamp' state=ON brightness=180
cli-anything-zigbee2mqtt device get 'Lounge Lamp' state brightness

# OTA
cli-anything-zigbee2mqtt ota check 'Radiator - Master Bedroom'
cli-anything-zigbee2mqtt ota update 'Radiator - Master Bedroom'
cli-anything-zigbee2mqtt ota schedule 'Radiator - Master Bedroom'
cli-anything-zigbee2mqtt ota unschedule 'Radiator - Master Bedroom'   # back out

# What can this device actually do? (local read of the retained inventory)
cli-anything-zigbee2mqtt device exposes 'Lounge Lamp'
cli-anything-zigbee2mqtt --json device exposes 'Lounge Lamp' --settable

# Is it reachable right now? (z2m availability feature)
cli-anything-zigbee2mqtt device availability 'Lounge Lamp'
cli-anything-zigbee2mqtt --json device availability-sweep --offline-only

# Which endpoints / clusters does it have? (local, no round trip)
cli-anything-zigbee2mqtt device endpoints 'Lounge Lamp'
cli-anything-zigbee2mqtt device clusters 'Lounge Lamp' --direction input
cli-anything-zigbee2mqtt --json device clusters wall_switch --direction output  # bind candidates
cli-anything-zigbee2mqtt --json device reportings          # every configured report, network-wide

# Which cluster / attribute names will z2m accept?
cli-anything-zigbee2mqtt bridge definitions
cli-anything-zigbee2mqtt bridge definitions --cluster genOnOff
cli-anything-zigbee2mqtt bridge definitions --cluster genOnOff --commands
cli-anything-zigbee2mqtt --json bridge definitions --custom   # manufacturer clusters

# Raw ZCL access for attributes no converter models
cli-anything-zigbee2mqtt device read 'Lounge Lamp' --cluster genBasic --attribute zclVersion
cli-anything-zigbee2mqtt device state 'Lounge Lamp'        # the answer lands here
cli-anything-zigbee2mqtt device write 'Lounge Lamp' --cluster genOnOff onOff=1 \
  --manufacturer-code 4107

# Open the network for 60 seconds (pair a new device)
cli-anything-zigbee2mqtt network permit-join on --time 60

# Network map (graphviz DOT, ready for `dot -Tpng -o map.png`)
cli-anything-zigbee2mqtt --json network map --type graphviz

# External converter file management (uses kubectl)
cli-anything-zigbee2mqtt converter list
cli-anything-zigbee2mqtt converter add my-override.js ./my-override.js
cli-anything-zigbee2mqtt bridge restart --via-kubectl

# v0.2.0 refine surface
cli-anything-zigbee2mqtt device state 'Lounge Lamp'         # one-shot retained
cli-anything-zigbee2mqtt --json device stale --threshold 360 # >6h silent
cli-anything-zigbee2mqtt device generate-converter <name> -o new-device.js
cli-anything-zigbee2mqtt device configure-reporting <name> \
  --cluster msTemperatureMeasurement --attribute measuredValue \
  --min 300 --max 1800 --change 0.5
cli-anything-zigbee2mqtt device bind switch_kitchen light_kitchen \
  --cluster genOnOff
cli-anything-zigbee2mqtt --json device bindings
cli-anything-zigbee2mqtt group options kitchen-lights \
  '{"transition":1.5,"retain":true}'
cli-anything-zigbee2mqtt install-code add "QR-CODE-TEXT-HERE"
cli-anything-zigbee2mqtt extension list
cli-anything-zigbee2mqtt extension save my-ext.js ./my-ext.js

# Groups as a single addressable light (one Zigbee groupcast, not N unicasts)
cli-anything-zigbee2mqtt group set kitchen-lights state=ON brightness=200 transition=2
cli-anything-zigbee2mqtt group get kitchen-lights state brightness
cli-anything-zigbee2mqtt --json group state kitchen-lights

# Scenes — store the room as it is now, recall it later
cli-anything-zigbee2mqtt group set kitchen-lights state=ON brightness=60
cli-anything-zigbee2mqtt scene store kitchen-lights 1 --name Chill
cli-anything-zigbee2mqtt --json scene list kitchen-lights
cli-anything-zigbee2mqtt scene recall kitchen-lights 1

# Or write a scene without touching the lights first
cli-anything-zigbee2mqtt scene add kitchen-lights 2 --name Dinner \
  --state ON --brightness 200 --color-temp 370 --transition 2
cli-anything-zigbee2mqtt scene rename kitchen-lights 2 'Dinner Party'
cli-anything-zigbee2mqtt scene remove kitchen-lights 2
cli-anything-zigbee2mqtt scene remove-all kitchen-lights --yes
```

### Scenes: what to know

Scenes are a Zigbee cluster feature, so they are published to the device/group
command topic (`<base>/<target>/set`) rather than the bridge request topic:
`scene_store` snapshots the target's **current** state, `scene_add` writes the
values explicitly, and `scene_recall` replays them. Store on a *group* so every
member keeps the same scene id — then one `scene recall` restores the whole room
in a single groupcast. Multi-gang devices keep scenes per endpoint, so pass
`--endpoint <n>`. Valid ids are 0-255. `scene list` reads back the `scenes`
array z2m publishes in the target's retained state (falling back to the retained
`bridge/groups` inventory) so you can verify a store actually landed.

### Exposes, availability and raw ZCL: what to know

`device exposes` is a **local** read of the retained `bridge/devices` inventory —
no round trip, and it works on a sleeping battery device. It flattens z2m's
nested exposes tree (a `light` expose carries `state` / `brightness` / … as
features; a `composite` namespaces its children as `parent.child`) into one row
per property with the `access` bitmask decoded — `published,set,get`. Run it
before `device set` to get the exact property names, units and ranges;
`--settable` keeps only what is writable.

`device availability` / `device availability-sweep` read the retained
`<base>/<name>/availability` topics, which only exist when z2m's `availability`
feature is enabled — otherwise everything comes back `null` (unknown, not
offline). The sweep subscribes once to `<base>/#` rather than doing one blocking
read per device, so it stays fast on a large network, and it joins onto the
device inventory so devices that never published availability still show up.
Rows come back offline-first. `last_seen` (see `device stale`) is the
complementary signal: availability is z2m's verdict, `last_seen` is the raw
evidence.

`device read` / `device write` reach attributes that no converter models, by
publishing `{"read": …}` / `{"write": …}` to the device command topic. Like
scenes they are Zigbee cluster commands with **no** `bridge/response`, so a zero
exit code only proves the publish succeeded — the value comes back
asynchronously on the device's own state topic. Read it with `device state
<name>` (retained) or capture it live with `device watch <name>`. Cluster and
attribute ids may be names (`genBasic`, `zclVersion`) or numbers (`0x0000`, `6`);
manufacturer-specific attributes need `--manufacturer-code`.

### Endpoints, clusters and the cluster dictionary

The raw commands above only help once you know *which* cluster to name, and that
information is already on the wire. `device endpoints` and `device clusters` are
**local** reads of the retained `bridge/devices` inventory (same source as
`device exposes`, so no round trip and no device wake-up):

* `device endpoints <name>` — one row per endpoint with cluster / binding /
  report / scene counts. Start here on a multi-gang switch or multi-outlet plug
  to work out which `--endpoint` to pass to `device read`, `device write` or
  `scene store`.
* `device clusters <name>` — one row per (endpoint, cluster). `--direction input`
  lists the clusters the device *implements* (what `device read` / `device write`
  / `device configure-reporting` can target); `--direction output` lists what it
  *sends* (what `device bind` can wire to a target). The `bound` and `reported`
  columns say whether a binding or an attribute report already exists for that
  cluster, so unbound outputs and unreported sensors are visible at a glance.
* `device reportings [<name>]` — the read-back for `device configure-reporting`.
  The bridge response only confirms the request was accepted; this shows what
  z2m actually recorded (intervals and reportable change included). With no
  argument it sweeps the whole network — the quick way to find sensors whose
  interview never set reports up.

`bridge definitions` reads the retained `bridge/definitions` topic (z2m 1.35+),
which is the zigbee-herdsman cluster dictionary the running bridge is using —
the authoritative list of names the ZCL commands accept, including manufacturer
clusters contributed by external converters. Bare, it lists every cluster with
its numeric id and definition counts; `--cluster genOnOff` (name, `6` or
`0x0006`) lists that cluster's attributes, `--commands` its commands instead, and
`--custom` lists the per-device custom clusters. Typical loop:

```bash
cli-anything-zigbee2mqtt device clusters plug1 --direction input   # genOnOff on ep 1
cli-anything-zigbee2mqtt bridge definitions --cluster genOnOff     # attribute: onOff
cli-anything-zigbee2mqtt device read plug1 --cluster genOnOff --attribute onOff --endpoint 1
cli-anything-zigbee2mqtt device state plug1                        # the answer lands here
```

## Architecture

```
cli_anything/zigbee2mqtt/
├── zigbee2mqtt_cli.py      # Click CLI + REPL
├── core/
│   ├── mqtt_client.py      # BridgeClient — MQTT request/response correlation
│   ├── bridge.py           # info/state/restart/health/options/watch
│   ├── devices.py          # list/show/rename/remove/configure/interview/set/get
│   │                       # + state (retained one-shot) / stale / generate-converter
│   │                       # / configure-reporting / exposes (local introspection)
│   │                       # / availability + availability_sweep
│   ├── attributes.py       # raw ZCL cluster read / write (device read|write)
│   ├── bindings.py         # device/bind, device/unbind, list_bindings (local)
│   ├── groups.py           # group CRUD + membership + options
│   │                       # + set_state / get_state / read_state (groupcast control)
│   ├── scenes.py           # scene store/recall/add/remove/remove_all/rename + list
│   ├── ota.py              # OTA check / update / schedule / unschedule
│   ├── admin.py            # permit-join / map / touchlink / coordinator / backup
│   ├── converters.py       # external_converters/ file mgmt (kubectl)
│   ├── extensions.py       # extension save/remove/list/show (MQTT)
│   ├── install_code.py     # install_code/add / remove
│   ├── k8s_backend.py      # kubectl helpers
│   └── project.py          # local connection profile
└── utils/
    └── repl_skin.py
```

Every *bridge* mutation is a `zigbee2mqtt/bridge/request/<path>` publish correlated
by a `transaction` id, with the response read from
`zigbee2mqtt/bridge/response/<path>`. Device/group commands — `device set`,
`group set`, and every `scene` subcommand — are Zigbee cluster commands instead,
so they publish to `<base>/<target>/set` (or `<base>/<target>/<endpoint>/set`) and
have no response topic; verify them by reading the target's retained state
(`device state` / `group state` / `scene list`).
File-level state (the external converters) lives in the z2m container's
filesystem and is managed via `kubectl exec` through `core/k8s_backend.py`.

## Tests

```bash
python3 -m pytest cli_anything/zigbee2mqtt/tests/ -v
```

645 tests (unit + CLI end-to-end via `CliRunner`) cover the BridgeClient against
a fake MQTT transport, every mutator in bindings / install_code / extensions /
groups / scenes / attributes, the read-side helpers in devices.py (read_state /
find_stale / exposes / availability_sweep / generate_external_definition /
configure_reporting), and multi-command workflows (create group → add member →
groupcast set → store/recall scene; `device exposes` → `device set`; raw
`device read` → `device state` read-back). No broker and no kubectl needed.

## License

MIT — see [LICENSE](./LICENSE).

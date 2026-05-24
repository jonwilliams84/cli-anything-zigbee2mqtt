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
| `bridge` | `info / state / restart / health / options-get / options-set / watch-events / watch-logging` |
| `device` | `list / show / rename / remove / configure / interview / options / set / get / watch / state / stale / generate-converter / configure-reporting / bind / unbind / bindings` |
| `group` | `list / add / remove / rename / add-member / remove-member / remove-all / options` |
| `ota` | `check / update / schedule` |
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
│   │                       # / configure-reporting
│   ├── bindings.py         # device/bind, device/unbind, list_bindings (local)
│   ├── groups.py           # group CRUD + membership + options
│   ├── ota.py              # OTA check / update / schedule
│   ├── admin.py            # permit-join / map / touchlink / coordinator / backup
│   ├── converters.py       # external_converters/ file mgmt (kubectl)
│   ├── extensions.py       # extension save/remove/list/show (MQTT)
│   ├── install_code.py     # install_code/add / remove
│   ├── k8s_backend.py      # kubectl helpers
│   └── project.py          # local connection profile
└── utils/
    └── repl_skin.py
```

Every mutation is a `zigbee2mqtt/bridge/request/<path>` publish correlated by a
`transaction` id, with the response read from `zigbee2mqtt/bridge/response/<path>`.
File-level state (the external converters) lives in the z2m container's
filesystem and is managed via `kubectl exec` through `core/k8s_backend.py`.

## Tests

```bash
python3 -m pytest cli_anything/zigbee2mqtt/tests/ -v
```

57 unit tests cover the BridgeClient (against a fake MQTT transport), every
mutator in bindings / install_code / extensions / groups.options, and the
read-side helpers in devices.py (read_state / find_stale /
generate_external_definition / configure_reporting). No broker needed.

## License

MIT — see [LICENSE](./LICENSE).

# cli-anything-zigbee2mqtt

CLI harness for [zigbee2mqtt](https://www.zigbee2mqtt.io) — bridge control,
device management, OTA, network admin, and external-converter file management
from the command line. Talks to a running z2m process over its MQTT control
surface (no frontend / HTTP dependency).

## Install

```bash
pip install -e .
cli-anything-zigbee2mqtt --help
```

## First-time config

```bash
cli-anything-zigbee2mqtt --mqtt-host 10.32.100.5 --base-topic zigbee2mqtt config save
```

Profile lives at `~/.config/cli-anything-zigbee2mqtt.json`. Per-key env overrides
are available as `CLI_Z2M_<KEY>`.

## Quick examples

```bash
# Bridge status
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
cli-anything-zigbee2mqtt --json ota check --all --with-update   # network firmware sweep
cli-anything-zigbee2mqtt ota update 'Radiator - Master Bedroom'
cli-anything-zigbee2mqtt ota unschedule 'Radiator - Master Bedroom'

# Introspect + reachability (all read the retained topics, no device wake-up)
cli-anything-zigbee2mqtt device exposes 'Lounge Lamp' --settable
cli-anything-zigbee2mqtt device endpoints 'Lounge Lamp'
cli-anything-zigbee2mqtt device clusters 'Lounge Lamp' --direction input
cli-anything-zigbee2mqtt --json device reportings 'Lounge Lamp'
cli-anything-zigbee2mqtt --json device availability-sweep --offline-only

# Cluster dictionary the running bridge accepts (retained bridge/definitions)
cli-anything-zigbee2mqtt bridge definitions
cli-anything-zigbee2mqtt bridge definitions --cluster genOnOff

# Raw ZCL attribute access (answer arrives on the device state topic)
cli-anything-zigbee2mqtt device read 'Lounge Lamp' --cluster genBasic --attribute zclVersion
cli-anything-zigbee2mqtt device write 'Lounge Lamp' --cluster genOnOff onOff=1

# Open the network for 60 seconds (pair a new device)
cli-anything-zigbee2mqtt network permit-join on --time 60

# Network map (graphviz DOT, ready to render with `dot -Tpng`)
cli-anything-zigbee2mqtt --json network map --type graphviz

# Group control + scenes
cli-anything-zigbee2mqtt group set kitchen-lights state=ON brightness=60
cli-anything-zigbee2mqtt scene store kitchen-lights 1 --name Chill
cli-anything-zigbee2mqtt scene recall kitchen-lights 1
cli-anything-zigbee2mqtt --json scene list kitchen-lights

# External converter file management (uses kubectl)
cli-anything-zigbee2mqtt converter list
cli-anything-zigbee2mqtt converter add my-override.js ./my-override.js
cli-anything-zigbee2mqtt converter remove my-override.js
```

## Command groups

| Group | Purpose |
|---|---|
| `bridge` | info / state / status / restart / health / options-get / options-set / definitions / log-level / watch-events / watch-logging |
| `device` | list / show / rename / remove / configure / interview / options / set / get / watch / exposes / endpoints / clusters / reportings / read / write / bind / unbind / bindings |
| `group` | list / members / add / remove / rename / add-member / remove-member / remove-all / options / set / get / state |
| `scene` | list / store / recall / add / rename / remove / remove-all (Zigbee scenes on a device or group) |
| `ota` | check / update / schedule / unschedule (`check --all` sweeps the network) |
| `network` | permit-join / map / touchlink-* / coordinator-check / backup |
| `converter` | list / show / add / remove (external_converters via kubectl) |
| `config` | show / save |
| `repl` | Interactive shell (default if no subcommand) |

All commands support `--json` for machine-readable output.

## Architecture

```
cli_anything/zigbee2mqtt/
├── zigbee2mqtt_cli.py      # Click CLI + REPL
├── core/
│   ├── mqtt_client.py      # BridgeClient — request/response over MQTT
│   ├── bridge.py           # bridge info/state/restart/health/options/watch/definitions
│   ├── devices.py          # list/show/rename/remove/configure/interview/set/get
│   │                       # + exposes / endpoints / clusters / reportings introspection
│   ├── groups.py           # group CRUD + membership + groupcast set/get/state
│   ├── scenes.py           # scene store/recall/add/remove/rename + list
│   ├── ota.py              # OTA check / update / schedule / unschedule
│   │                       # + check_all (whole-network firmware sweep)
│   ├── admin.py            # permit-join / map / touchlink / coordinator / backup
│   ├── converters.py       # external_converters/ file mgmt (uses k8s_backend)
│   ├── k8s_backend.py      # kubectl helpers
│   └── project.py          # local connection profile
└── utils/
    └── repl_skin.py        # shared REPL UI
```

The control plane is MQTT — every mutation is a request to
`zigbee2mqtt/bridge/request/<path>` correlated by a `transaction` id, with the
response read from `zigbee2mqtt/bridge/response/<path>`. Each `BridgeClient`
opens a single MQTT connection and dispatches both responses and event/log
subscriptions.

Device- and group-level commands (`device set`, `group set`, all of `scene`) are
Zigbee cluster commands rather than bridge requests, so they publish to
`<base>/<target>/set` and are verified by reading the target's retained state
instead of a response topic.

The exception is file-level state (the external converters in
`/app/data/external_converters/`) — those live in the z2m container's filesystem
and are managed via `kubectl exec` through `core/k8s_backend.py`.

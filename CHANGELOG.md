# Changelog

All notable changes to this project are documented here.

## [0.10.0] — 2026-10-10

- **Group lighting convenience layer**: the device lighting shortcuts added in
  v0.7.0 (`device on/off/toggle/brightness/color/color-temp`) now have group
  twins — until now the only way to light a room was hand-writing a
  `group set` payload, and getting `state` casing or a mireds value wrong
  published to the wire anyway.
  - `group on <GROUP> [--transition S]`, `group off <GROUP> [--transition S]`,
    `group toggle <GROUP>`, `group brightness <GROUP> 0-254`
    `[--transition S]`, `group color <GROUP> red|#0088ff|'0,136,255'`
    `[--transition S]`, `group color-temp <GROUP> mireds [--kelvin]
    [--transition S]` — GROUP is the group's friendly_name or numeric id.
    One `--transition` flag each, exactly like the device commands.
  - Values are validated **before** any MQTT connection (`device on z2m
    light 300` aborts with "brightness must be between 0 and 254", never
    reaches the broker), and the group is resolved against the retained
    `bridge/groups` inventory first: an unknown group aborts without
    publishing instead of firing a groupcast into a (possibly device-named)
    topic.
  - `--json` output keeps the device lighting shape plus the group id:
    `{"friendly_name", "id", "topic", "published", "rc"}` — gate scripts on
    `rc` / `published` like the device lighting commands.
- Why it matters: writing to a group is a single Zigbee groupcast — one radio
  frame for every bulb in the room, visually atomic. It is both faster and
  more robust than looping `device set` across members, and should be the
  default for room control. Recommended loop: `group add` + `group
  add-member` to build the room, `group members` to verify it, `group on /
  brightness / color-temp` to drive it, `scene store` to snapshot the result.
- Core functions `find_group` and `set_group_light` in `core/groups.py`
  (payload validation reuses `devices.light_payload`, so group and device
  lighting can never drift apart).
- 32 new tests (14 unit + 18 E2E/workflow); total suite 1087 passed, gate
  green (coverage 98.9%, ruff check/format clean, bandit clean).

## [0.9.0] — 2026-10-03

- **Raw MQTT passthrough**: new `mqtt` command group — `mqtt publish`,
  `mqtt read`, `mqtt watch`, `mqtt topics`. z2m's control surface *is* MQTT:
  bridge request/response topics, per-device `<name>/set` and `<name>/get`
  writes, and retained state dumps. Every command until now went through a
  typed wrapper; these four reach anything the wrappers don't model — a
  payload key no convenience command covers, a retained dump
  (`bridge/info`, `bridge/devices`, a device's state topic), an MQTT
  wildcard tail, or a future/tweaked bridge API the moment z2m ships it —
  without leaving the CLI.
  - `mqtt publish <topic> [payload]` — topic resolves against the base
    topic (`lamp/set` → `zigbee2mqtt/lamp/set`; a topic that already starts
    with the base topic is used verbatim). A payload string that parses as
    JSON is sent as JSON (so the wire is indistinguishable from
    `device set`); any other text is published verbatim; an omitted payload
    publishes an empty message. `--retain` and `--qos 0-2` set the MQTT
    flags; bad values abort before any broker connection. Output
    `{"topic", "payload", "published", "rc", "retain", "qos"}` — gate
    scripts on `published`/`rc` like `device identify`.
  - `mqtt read <topic> [--timeout S]` — one-shot retained read, JSON-decoded
    when possible. `payload: null` means "nothing ever published on this
    topic" — a valid answer, not an error, and the fastest way to read any
    retained z2m dump.
  - `mqtt watch <filter> [--duration S]` — collect every message matching an
    MQTT wildcard filter (e.g. `sensor1/#`, `bridge/logging`) for
    `--duration` seconds (default 15, Ctrl-C stops early); consumer-callback
    errors are logged, never fatal (same contract as `bridge watch-events`).
  - `mqtt topics [--prefix P] [--duration S]` — enumerate the topics under
    `<base>/#`: subscribing pulls every retained dump in immediately, so a
    short default window (2s) snapshots the whole topic tree with payload
    size and a truncated preview per row — topic discovery without knowing
    names up front.
- Core functions `normalize_topic`, `coerce_payload`, `check_qos`,
  `publish_raw`, `read_raw`, `watch_topic`, `list_topics` in the new
  `core/mqtt_raw.py`; the topic-resolution and payload-coercion helpers are
  client-free, like the rest of the introspection helpers.
- Bug fix riding along: every watch loop (`mqtt watch`, `bridge
  watch-events` / `watch-logging`, `device watch`) treated `--duration 0` as
  "tail forever" — `time.time() + duration if duration else None` is falsy
  on 0 — so a zero-width window hung the CLI. All four now use
  `duration is not None`; `--duration 0` is a non-blocking drain.
- Why it matters: the typed groups are coverage, not a cage. When z2m adds or
  changes a bridge API, `mqtt publish` / `mqtt read` works against it the
  same minute; and one-off payloads that no convenience command models are a
  one-liner instead of a second MQTT tool. Recommended loop: `mqtt topics`
  to discover, `mqtt read` to inspect, `mqtt publish` to act, `mqtt watch`
  to confirm.
- 64 new tests (40 unit + 24 E2E/workflow); total suite 1055 passed, gate
  green (coverage 99.15%, ruff check/format clean, bandit clean).

## [0.8.0] — 2026-09-27

- `device ping`: the bridge liveness round trip. Publishes `{"id": ...}` to
  `bridge/request/device/ping`; z2m performs a real Zigbee read on the
  device's basic cluster and reports `data.successful`. This is the third —
  and only *active* — liveness view: `device availability` reads a retained
  flag the device last published, `device stale` reads raw `last_seen`, but
  only `device ping` proves the radio link works **right now**.
  - IDENT accepts a friendly name or IEEE address; unknown devices abort
    before any publish (same as `device identify`).
  - `--timeout S` (default 15) tunes how long the bridge gets to answer.
  - A bridge timeout or z2m error surfaces as `successful: false` with an
    `error` field — never a traceback — and `--json` output stays parseable,
    so scripts can gate on it: the command **exits 1** when the device does
    not answer.
  - The flip side, stated in the help text: a sleeping battery device will
    not answer a ping even when perfectly healthy — use
    `device availability` there. `device ping` + `device availability` /
    `availability-sweep` now cover both halves of "is it alive?".
- Core function `ping` in `core/devices.py`.
- Why it matters: after a `device availability-sweep --offline-only` or a
  firmware upgrade, `device ping <name>` is the one-liner that separates
  "flag says offline" from "the device really does not answer".
- 15 new tests (6 unit + 9 E2E/workflow); total suite 991 passed, gate
  green (coverage 99.3%, ruff check/format clean, bandit clean).

## [0.7.0] — 2026-09-18

- Lighting convenience layer: `device on / off / toggle / brightness / color /
  color-temp`. Until now the only way to drive a bulb was hand-writing a
  `device set` payload (`device set 'Lounge Lamp' state=ON brightness=128`) or
  a raw `device write` on the genOnOff/genLevelCtrl clusters. The new
  shortcuts cover the everyday cases with validated arguments:
  - `device on <name> [--transition S]` / `device off` / `device toggle`
    publish `{"state": "ON" | "OFF" | "TOGGLE"}`.
  - `device brightness <name> 0-254 [--transition S]` — the standard Zigbee
    range; `255` is rejected ("max" is not a settable level on z2m).
  - `device color <name> red | #ff8800 | '255,136,0' [--transition S]` —
    named colors, hex or RGB triples, all normalised to the
    `{"color": {"r","g","b"}}` object z2m expects.
  - `device color-temp <name> <mireds> [--kelvin] [--transition S]` — mireds
    150-500 (lower = cooler); `--kelvin` converts (e.g. `2700 --kelvin` →
    `color_temp 370`).
  All six resolve IEEE addresses to friendly names first (same as
  `device identify`), so `device toggle 0xa4c138…` works, and an unknown
  device aborts instead of publishing to a nonexistent topic. Payloads are
  validated BEFORE any MQTT connection is opened — `device brightness <name>
  255` reports "brightness must be between 0 and 254" rather than a broker
  connection error. Output is `{"friendly_name", "topic", "published",
  "rc"}` (same shape as `device identify`); like `device set` there is no
  bridge/response, so confirm with `device state <name>`. Room-wide changes
  still belong in a `group set` groupcast.
- Core functions `light_payload`, `set_light`, `parse_color`,
  `kelvin_to_mireds`, `check_brightness`, `check_color_temp`,
  `check_transition` in `core/devices.py`.
- Why it matters: `device find --capability brightness` → `device brightness
  <name> 128` is now a two-command light-dimming one-liner instead of
  property-name archaeology.
- 53 new tests (34 unit + 19 E2E/workflow); total suite 976 passed, gate
  green (coverage 99.3%, ruff check/format clean, bandit clean).

## [0.6.0] — 2026-09-11

- `device find`: multi-criteria search over the paired-device inventory.
  Every filter is a local read of the retained `bridge/devices` payload — no
  round trip, works against sleeping devices — and they all combine:
  `--like` (substring on friendly_name / IEEE address), `--manufacturer`,
  `--model`, `--vendor`, `--type` (EndDevice / Router / Coordinator),
  `--power` (e.g. `battery`, `mains`), `--capability` (matches any flattened
  exposes property, so `--capability color` finds `color_temp` too),
  `--supported/--unsupported` and `--disabled/--enabled`. `--json` stays a
  pure JSON list even with zero hits; text mode says "No devices match".
- `device identify`: trigger the Zigbee Identify effect so a device flashes —
  publishes `{"identify": {"duration": N}}` (or `{"identify": {}}`) to
  `<base>/<name>/set`, the documented z2m way to reach the Identify cluster.
  IEEE addresses are resolved to friendly names first, and an unknown device
  aborts instead of publishing to a nonexistent topic. `--duration` tunes the
  flash window; only devices implementing Identify (most bulbs, some sensors)
  react.
- Why it matters: the two commands compose —
  `device find --capability brightness` → `device identify <name>` is the
  answer to "what's this bulb actually called?" without touching the frontend.
- Core functions `search_devices` and `identify` in `core/devices.py`.
  40 new tests (21 unit + 19 E2E/workflow); total suite 923 passed, gate
  green (coverage ≥ 80%, ruff check/format clean, bandit clean).

## [0.5.0] — 2026-09-06

- `device battery`: network-wide battery audit. One `<base>/#` subscription
  collects every retained device state and joins `battery` (percent),
  `battery_low` and `voltage` (mV) onto the `bridge/devices` inventory — no
  per-device round trip. Rows sort worst-first (`low` → `unknown` → `ok`,
  then percent ascending) so the top row is the device that needs a fresh
  cell; mains-powered devices and the coordinator are dropped, and battery
  devices that never published state (sleeping sensors) classify as
  `unknown`. `--below N` redefines "low" (default 20%), `--low-only` drops
  healthy devices, `--timeout` tunes the inventory read, `--duration` the
  state-collection window. Core functions `battery_sweep` and
  `classify_battery` in `core/devices.py`. 37 new tests (unit + E2E +
  workflow); total coverage 99.8%.

## [0.4.0] — 2026-09-06

- Updated `test.md`. (1 file changed, 24 insertions(+))

## [0.3.0] — 2026-09-06

- Updated `test.md`. (1 file changed, 9 insertions(+), 7 deletions(-))

## [0.2.0] — 2026-09-06

- `ota check --all`: whole-network OTA firmware sweep. One
  `device/ota_update/check` round trip per device (coordinator and disabled
  devices skipped; `--include-disabled` overrides), sorted update-available-
  first. Each device is classified as `update_available` / `up_to_date` /
  `not_supported` / `unknown` / `error` — a dead device becomes an error row
  and never hides the rest of the network's firmware state. `--with-update`
  narrows to devices that actually have pending firmware, `--timeout` tunes
  the per-device round trip. Core functions `check_all`, `summarize_check`
  and `_classify` in `core/ota.py`. 25 new tests (unit + E2E + workflow).

## [0.1.1] — 2026-09-03

- README: a Releases section — every merge to `main` is tagged and published as a
  semver GitHub Release by the Release workflow, `setup.py` holds the version of
  record, and this file carries a section per released version.

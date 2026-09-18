# Changelog

All notable changes to this project are documented here.

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

# Changelog

All notable changes to this project are documented here.

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

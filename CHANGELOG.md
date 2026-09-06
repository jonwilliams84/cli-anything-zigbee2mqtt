# Changelog

All notable changes to this project are documented here.

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

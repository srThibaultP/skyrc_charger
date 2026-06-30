# SkyRC Charger – Home Assistant Integration (MC3000 + MC5000)

Custom Home Assistant integration that monitors and controls SkyRC battery
chargers over BLE — supports **both** the MC3000 (8 slots) and the MC5000
(4 slots) from a single codebase. You pick the model when adding the
integration; each model uses its own protocol "driver" under a shared
interface, so sensors/buttons/services look and behave the same way
regardless of which charger you own.

## Origins / credits

- MC3000 BLE handling: built on the [`skyrc-ble`](https://pypi.org/project/skyrc-ble/)
  library, same as used in [jperquin/ha-skyrc-mc3000](https://github.com/jperquin/ha-skyrc-mc3000),
  whose integration structure (config flow, coordinator, entities, companion
  app mode) this project was based on.
- MC5000 BLE protocol: re-implemented in pure Python from the reverse
  engineering documented in [rssdev10/skyrc-mc-rs](https://github.com/rssdev10/skyrc-mc-rs)
  (`docs/PROTOCOL.md`), originally written in Rust.

## Installation

Copy `custom_components/skyrc_charger` into your Home Assistant
`config/custom_components/` folder (or add this repo as a HACS custom
repository, type "Integration"), then restart Home Assistant.

`Settings → Devices & services → Add integration → SkyRC Charger`, choose
your model (MC3000 or MC5000), then pick or enter the BLE address.

You can add **multiple chargers** (e.g. one MC3000 and one MC5000, or
several of either) — each becomes its own config entry/device.

## Entities

For each slot (1-8 on MC3000, 1-4 on MC5000):
`sensor.*_status`, `*_battery_type`, `*_mode`, `*_voltage`, `*_current`,
`*_capacity`, `*_temperature`, `*_internal_resistance`, `*_elapsed_time`,
`select.*_expected_chemistry`, `button.*_start`, `button.*_stop`.

Device-level: `sensor.*_input_voltage` (MC3000 only — not exposed by the
MC5000 protocol), `switch.*_companion_app_mode`, `button.*_refresh`,
`button.*_stop_all`.

## Companion App Mode

Both chargers only allow one BLE client at a time. Turn on
`switch.*_companion_app_mode` to have Home Assistant disconnect and pause
polling so the official SkyRC app can connect; turn it back off when done.

## Services

`skyrc_charger.refresh`, `skyrc_charger.start_slot`,
`skyrc_charger.stop_slot`, `skyrc_charger.stop_all` — all take an
`entry_id` (find it under Settings → Devices & services → your charger →
⋮ → Device info, or via Developer Tools → States on any of its entities).

`start_slot` additionally accepts optional `chemistry`, `mode`,
`current_ma`, `capacity_mah` fields. These are **ignored on the MC3000**
(which only starts the program already configured on the charger itself,
same safety model as the original integration). On the **MC5000 they
matter**: unlike the MC3000, the MC5000 BLE protocol has no "start the
currently configured program" command — a full configuration packet must
be sent before every start. Sensible per-chemistry defaults are used for
anything you don't specify (current defaults to 1000 mA, capacity to
3000 mAh, target/cutoff voltage from the chemistry's standard values).

## Known limitations

- **MC5000 per-slot stop is unreliable.** The reverse-engineered protocol
  is ambiguous about whether the per-slot bitmask in the stop command
  (`0x93`) actually stops that slot or instead *starts* it (the doc
  contradicts itself on this point). To avoid accidentally starting the
  wrong slot, `stop_slot` and the per-slot stop button currently stop
  **all** MC5000 slots — this is the one stop behaviour that is solidly
  validated against real BLE captures. Open an issue / contribute a fix
  if you can confirm the real per-slot semantics from your own device.
- **No MC5000 input-voltage / device settings sensors** — the protocol
  doc doesn't document where these live yet.
- The MC5000 driver is **not officially supported by SkyRC**; it's a
  community reverse-engineering effort. Test carefully, keep an eye on
  your charger the first few times, and don't leave it unattended with
  unfamiliar chemistries/currents.
- As with the original MC3000 integration, no profile-editing is exposed
  (delta-peak, trickle, keep-voltage, etc. use defaults baked into the
  driver) — full parameter control could be added as `number` entities in
  a future version.

## Safety

Same philosophy as the original MC3000 integration: this is a convenience
layer for monitoring and start/stop, not a replacement for checking your
charger's configured battery type, current, and safety cut-offs before
starting a slot — especially on the MC5000, where Home Assistant is now
the one constructing the charge profile that gets sent to the device.

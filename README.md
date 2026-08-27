# SkyRC Charger – Home Assistant Integration (MC3000 + MC5000)

Custom Home Assistant integration that monitors and controls SkyRC battery
chargers over BLE — supports **both** the MC3000 and the MC5000 (four slots
each) from a single codebase. You pick the model when adding the
integration; each model uses its own protocol "driver" under a shared
interface, so sensors/buttons/services look and behave the same way
regardless of which charger you own.

## Origins / credits

- MC3000 BLE handling: built on the [`skyrc-ble`](https://github.com/jperquin/skyrc-ble)
  library, same as used in [jperquin/ha-skyrc-mc3000](https://github.com/jperquin/ha-skyrc-mc3000),
  whose integration structure (config flow, coordinator, entities, companion
  app mode, voltage curves, program writing) this project was based on and
  keeps tracking.
- MC5000 BLE protocol: re-implemented in pure Python from the reverse
  engineering documented in [rssdev10/skyrc-mc-rs](https://github.com/rssdev10/skyrc-mc-rs)
  (`docs/PROTOCOL.md`), originally written in Rust.

## Installation

Copy `custom_components/skyrc_charger` into your Home Assistant
`config/custom_components/` folder (or add this repo as a HACS custom
repository, type "Integration"), then restart Home Assistant.

`Settings → Devices & services → Add integration → SkyRC Charger`, choose
your model (MC3000 or MC5000), then pick or enter the BLE address. The
device list comes from Home Assistant's own Bluetooth manager, so chargers
seen through an ESPHome or Shelly Bluetooth proxy show up as well.

You can add **multiple chargers** (e.g. one MC3000 and one MC5000, or
several of either) — each becomes its own config entry/device.

## Entities

Entity ids are built from the charger's name, i.e. the title of its config
entry. A charger named "SkyRC MC3000" gives `sensor.skyrc_mc3000_slot_1_voltage`
and so on.

For each of the four slots:
`sensor.*_status`, `*_battery_type`, `*_mode`, `*_voltage`, `*_current`,
`*_capacity`, `*_temperature`, `*_internal_resistance`, `*_elapsed_time`,
`*_cycle_count`, `select.*_expected_chemistry`, `button.*_start`,
`button.*_stop`.

Device-level: `switch.*_companion_app_mode`, `button.*_refresh`,
`button.*_start_all`, `button.*_stop_all`.

MC3000 only: `sensor.*_input_voltage`, plus the diagnostic
`*_temperature_unit`, `*_display_mode`, `*_cooling_fan_mode`,
`*_system_beep` and `*_screensaver` sensors, the per-slot
`sensor.*_voltage_curve_points` / `button.*_fetch_voltage_curve` pair and
`switch.*_auto_fetch_voltage_curves`. The MC5000 protocol doesn't document
any of these.

`examples/skyrc-mc3000-dashboard.yaml` is a ready-made Lovelace dashboard
covering all of it, including charts of the logged voltage curves.

## Companion App Mode

Both chargers only allow one BLE client at a time. Turn on
`switch.*_companion_app_mode` to have Home Assistant disconnect and pause
polling so the official SkyRC app can connect; turn it back off when done.

## Services

All services take an optional `entry_id` naming the charger to act on. You
can leave it out while a single charger is configured.

| Service | What it does |
| --- | --- |
| `skyrc_charger.refresh` | Force an immediate poll. |
| `skyrc_charger.start_slot` | Start one slot, after the chemistry interlock. |
| `skyrc_charger.stop_slot` | Stop one slot. |
| `skyrc_charger.start_all` | Start every slot in one go. |
| `skyrc_charger.stop_all` | Stop every slot. |
| `skyrc_charger.fetch_voltage_curve` | MC3000: read back a slot's logged voltage curve. |
| `skyrc_charger.write_program` | MC3000: write a complete program to a slot (without starting it). |

### Chemistry interlock

`select.*_expected_chemistry` says what you believe is in a slot, and its
options follow the chemistry names of the model you have — the MC3000 and
MC5000 name them differently, so a single shared list could never match.

On the **MC3000**, `start_slot` and `start_all` refuse to start a slot whose
reported chemistry disagrees with that select, unless it is set to `any`.

On the **MC5000** the select is used as the chemistry to write into the
program instead: unlike the MC3000, the MC5000 BLE protocol has no "start
the currently configured program" command — a full configuration packet must
be sent before every start. `start_slot` and `start_all` therefore also take
optional `chemistry`, `mode`, `current_ma` and `capacity_mah`. Sensible
per-chemistry defaults are used for anything you don't specify (current
defaults to 1000 mA, capacity to 3000 mAh, target/cutoff voltage from the
chemistry's standard values).

### Writing a complete MC3000 program

The MC3000 cannot change one field of a program: the protocol only accepts a
whole one, so `write_program` takes every current, voltage and protection
value together. It writes the program without starting it — use
`start_slot` afterwards.

## Connection handling and logging

These chargers drop their BLE link constantly: the MC3000 firmware closes it
after a few seconds of idling, and both models vanish entirely while the
mains side is off. Every drop is recovered on the next poll, so it is not
worth a log line each time.

- Connections go through
  [`bleak_retry_connector.establish_connection()`](https://github.com/Bluetooth-Devices/bleak-retry-connector),
  which retries the transient adapter/proxy errors that a bare
  `BleakClient.connect()` reports as a hard failure, reuses Home Assistant's
  cached GATT services and cooperates with its connection slot allocator.
- The `BLEDevice` is refreshed from Home Assistant's Bluetooth manager before
  every reconnect; reconnecting with a stale one is the usual cause of
  reconnect loops.
- A poll failure is logged once when an outage starts and once an hour while
  it lasts, not once per ten second poll. Recovery is logged at info.
- The `skyrc_ble` library's own per-drop warnings are demoted to debug. Set
  `logger: logs: skyrc_ble: debug` to see them again.

Last known values keep being served during an outage so the dashboard
doesn't flicker on a one-poll dropout; after about five minutes without
contact the entities go unavailable rather than presenting stale readings as
current.

## Icon / logo

`brand_assets/icon.png`, `icon@2x.png` and `logo.png` are included. Home
Assistant only displays an integration's icon in the "Add integration"
list/search and config-flow header if it's published in the official
[home-assistant/brands](https://github.com/home-assistant/brands)
repository — there's no purely local way to set it for a custom
integration. To get the icon to show up: fork that repo, add these three
files under `custom_integrations/skyrc_charger/`, and open a PR (review is
usually quick for new custom integrations). Until merged, the integration
shows the generic puzzle-piece icon; this is a Home Assistant limitation,
not something this component can work around on its own. Entity icons
(buttons, sensors, etc.) already work normally without this.

## Known limitations

- **MC5000 chemistry sensor (`Battery Type`) is unreliable.** The
  protocol doc itself flags the 0x91 status response's chemistry byte as
  "observed, unconfirmed" — and testing against the doc's own two
  reference captures shows it actually contradicts itself (a NiMH session
  reads back as `liion` and a Li-Ion session reads back as `nimh`). This
  is a charger/protocol-level ambiguity, not a parsing bug on our side;
  there's currently no reliable way to read back the chemistry the MC5000
  itself thinks a slot is using. Treat this sensor as informational only,
  and use `select.*_expected_chemistry` as your source of truth — it's
  what you set in Home Assistant and what gets sent when you start a
  slot from here, so it always reflects your intent accurately.
- **MC5000 per-slot stop is unreliable** — confirmed by the protocol doc
  itself, which states no per-slot stop command was ever observed, only
  global stop-all. `stop_slot` and the per-slot stop button therefore
  always stop all MC5000 slots.
- **No MC5000 input-voltage / device settings sensors, voltage curves or
  program writing** — the protocol doc doesn't document where any of these
  live yet.
- The MC5000 driver is **not officially supported by SkyRC**; it's a
  community reverse-engineering effort. Test carefully, keep an eye on
  your charger the first few times, and don't leave it unattended with
  unfamiliar chemistries/currents.
- Full MC5000 profile editing (delta-peak, trickle, keep-voltage, …) is not
  exposed; those use defaults baked into the driver.

## Tests

The unit tests stub out Home Assistant, so they need nothing installed:

```bash
cd tests && python3 -m unittest discover
```

## Safety

Same philosophy as the original MC3000 integration: this is a convenience
layer for monitoring and start/stop, not a replacement for checking your
charger's configured battery type, current, and safety cut-offs before
starting a slot — especially on the MC5000, where Home Assistant is now
the one constructing the charge profile that gets sent to the device.

# Washer Config Controls Design — SmartThings

Date: 2026-09-24
Status: Draft v3 (updated from live Cloud and local-device verification on 2026-09-25)
Scope: `homeassistant/components/smartthings` — remove service, add native washer config entities, minimize diff for upstream.
Supersedes: `docs/superpowers/specs/2026-09-24-washer-send-to-washer-design.md` (service approach, do not implement).

## 1. Goal

Replace the uncommitted `smartthings.send_washer_cycle` service with native Home Assistant configuration entities that mirror the SmartThings app's "Send to washer" screen, following pre-existing SmartThings integration patterns so the change is upstream-contributable.

Non-remote-gated (work Smart-Control-off, like the app):
- Cycle select (raw course codes from device; no static friendly table in v1).
- Detergent density + softener amount + softener density selects (detergent amount select already exists).
- Extra rinse — write-only select using the private course option transported by the generic Cloud `execute` capability (see §6).

Remote-gated:
- Delay end — select with 15-minute steps (user decision; number fallback documented), gated on remote control.

Unrelated (keep, minimal diff):
- Sound stays as the already-added `switch.py` toggle (`samsungce.audioVolumeLevel` 1/0). No select conversion.

## 2. Explicit non-goals

- No `services.yaml` / `services.py` / `washer_cycle.py` normalizer module; no static friendly→code table in the integration (raw codes only in v1; a shared table belongs in `pysmartthings` if ever added).
- No Super Speed toggle, Stay Connected, AI Pattern, Remaining Laundry, My-cycles-write, dryer parity in v1.
- Pre Soak remains out of scope because controlled live captures found no Cloud or local OCF option token for its physical-panel toggle. Only the derived cycle time changes (+30 minutes on Normal), which is not a safe write mapping.
- Local OCF is used only as a diagnostic read-back during discovery; the integration remains Cloud-only.
- No `scan_interval` / polling options; entities keep `_attr_should_poll = False`; integration stays thin (protocol in `pysmartthings`).

## 3. Screenshot source of truth

App shows: power Off/On + `Send to washer` button, `Cycle: Normal`, tiles `Temp Level 3 / Spin Level 4 / Soil Level 3` (already selects), `Super speed` / `Extra rinse` toggles, `Delay end` (greyed unless Smart Control), `Remaining laundry`, `Stay connected` ON, `Detergent dispenser: High amount`, `Softener dispenser: Off`, `AI pattern` ON, `My cycles`, `Sound` toggle. Cycle list: Normal, Super Speed, Small Load, Delicates, Bedding, Self Clean+, Little Extra, AI OptiWash, Heavy Duty, Steam Whites, Steam Sanitize, Steam Normal, Towels, Activewear, Steam Bulky, Power Steam, Power Rinse, Spin Only, Rinse+Spin, Outdoor, Denim, Wool, Colors, Perm Press, Eco Cold, Steam Allergen. Temp L1-5 Tap Cold→Extra hot; Spin Rinse Hold & No Spin / Drain & No Spin / L2 Low→L5 Extra high; Soil L1 Extra light→L5 Extra heavy.

## 4. Live device constraints (WF53BB8900ATUS, Table_02)

- `custom.supportedOptions`: `course=01`, `referenceTable=Table_02`, 26 `supportedCourses`.
- `samsungce.washerCycle`: `washerCycle=Table_02_Course_01`, per-cycle `supportedCycles[].supportedOptions.{soilLevel,spinLevel,waterTemperature}` with defaults/options.
- Dispensers: `supportedAmount=[none,less,standard,extra]`, `supportedDensity=[normal,high,extraHigh]`.
- `samsungce.washerDelayEnd`: `minimumReservableTime=45`; `remoteControlStatus=false`.
- `samsungce.audioVolumeLevel`: 0-1.
- `custom.disabledCapabilities` includes `custom.washerRinseCycles`, `samsungce.washerBubbleSoak`, and `samsungce.clothingExtraCare`; their null/disabled status does not represent this model's Extra Rinse control.
- The generic `execute` capability accepts `execute("course/vs/0", {"x.com.samsung.da.options": ["ExtraRinse_On|Off"]})`. Both directions were sent through the Cloud API and verified against the washer's read-only local OCF state: `ExtraRinse_Off` changed Normal from 52 to 45 minutes and `ExtraRinse_On` restored it to 52 minutes.
- SmartThings device status does not report the private option after either app or direct Cloud writes; it reports only derived duration fields. Therefore the HA entity is intentionally write-only and must not cache an authoritative-looking state.
- Physical Pre Soak on/off produced no change in `/course/vs/0` options and no dedicated Cloud event; only Normal duration changed between 45 and 75 minutes.
- Cloud returns cycle **codes only** (`01`, `53`…); friendly names live in app/OCF.

## 5. Revert list (minimize diff)

Delete (service path only):
- `homeassistant/components/smartthings/services.py`
- `homeassistant/components/smartthings/services.yaml`
- `homeassistant/components/smartthings/washer_cycle.py`
- `tests/components/smartthings/test_services.py`
- `tests/components/smartthings/test_washer_cycle.py`

Keep for tests (reviewer fix: new entities need Table_02 coverage; existing `siemens_washer` fixture lacks these caps):
- `tests/components/smartthings/fixtures/device_status/washer_us_table02.json`
- `tests/components/smartthings/fixtures/devices/washer_us_table02.json`

Revert:
- `homeassistant/components/smartthings/__init__.py` — remove `async_setup_services` import + call.
- `homeassistant/components/smartthings/icons.json` — remove `services` block only.
- `homeassistant/components/smartthings/strings.json` — remove `services` block only.
- `tests/.../test_switch.ambr` — drop service-branch snapshots only on regen.
- Regenerate `translations/en.json` via `python3 -m script.translations develop --integration smartthings` and verify the services block is gone.

Keep (not junk):
- `switch.py` Sound toggle (`SAMSUNG_CE_AUDIO_VOLUME_LEVEL`, `on_key=1`/`off_key=0`, `translation_key="sound"`) plus the `str | bool | int` widening on `on_key`/`off_key` it required, its `strings.json`/`icons.json` entries, snapshot.
- Existing soil/spin/temp/detergent-amount selects, rinse-cycles number, operating-state select.
- Superseded docs stay untouched: `docs/superpowers/specs/2026-09-24-washer-send-to-washer-design.md`, `docs/superpowers/plans/2026-09-24-washer-send-to-washer.md` (archive, do not delete in this change).

## 6. New entities (all `EntityCategory.CONFIG`)

Follow `select.py:152 SmartThingsSelectDescription` (`key`, `options_attribute`, `status_attribute`, `command`, `options_map`, `requires_remote_control_status`) and `switch.py:55 SmartThingsCommandSwitchEntityDescription`. Remote gating uses the existing `ServiceValidationError` pattern (`select.py:465`).

- **Cycle select** (non-gated; bespoke subclass, not a `CAPABILITIES_TO_SELECT` row): capabilities `{CUSTOM_SUPPORTED_OPTIONS, SAMSUNG_CE_WASHER_CYCLE}`; `translation_key="washer_cycle"`; `unique_id="{device_id}_main_washer_cycle"` (do not copy the duplicated-`status_attribute` bug in `select.py:421`). `options()` = device `custom.supportedOptions.supportedCourses` raw codes (v1: no friendly map; `01` shows as `01`). `current_option()` = `custom.supportedOptions.course` (fallback: parse trailing `_XX` from `samsungce.washerCycle.washerCycle`). `async_select_option(code)`: validate code in `supportedCourses`, then `setCourse(code)` via `Command.SET_COURSE` followed by `setWasherCycle(f"{referenceTableId}_Course_{code}")` via `Command.SET_WASHER_CYCLE`, where `referenceTableId` is read live from `custom.supportedOptions.referenceTable.id` (generic — no `Table_02` hardcode; unknown/missing table → `ServiceValidationError` with diagnostics hint). If the second command fails after the first succeeded, surface the error (no rollback possible via Cloud).
- **Dispenser selects** (non-gated; secondary map because `async_setup_entry` in `select.py:356` is keyed one-row-per-capability and cannot emit two selects from one capability): add `AUTO_DISPENSE_TO_SELECT: dict[Attribute, SmartThingsSelectDescription]` mirroring `DISHWASHER_WASHING_OPTIONS_TO_SELECT` (`select.py:322`), with rows for `Attribute.AMOUNT`→`detergent_amount`/`softener_amount` and `Attribute.DENSITY`→`detergent_density`/`softener_density` per capability (`SAMSUNG_CE_AUTO_DISPENSE_DETERGENT`, `SAMSUNG_CE_AUTO_DISPENSE_SOFTENER`), commands `SET_AMOUNT`/`SET_DENSITY`, `options_attribute` `SUPPORTED_AMOUNT`/`SUPPORTED_DENSITY`. Extend `async_setup_entry` with a second loop over this map. Keeps existing `detergent_amount` row working (migrate it into the map rather than duplicating).
- **Extra rinse**: add `SmartThingsWasherExtraRinseSelectEntity` with options `not_reported`, `off`, and `on`, `EntityCategory.CONFIG`, and no device read-back. Its state remains `not_reported` (displayed as `Not Reported`) after commands; like other non-optimistic HA entities, it changes state only from device read-back, which this private option does not provide. This keeps `off` and `on` selectable repeatedly without a timer or invented state. Selecting `Not Reported` sends no washer command. Gate it to washer models exposing `EXECUTE`, `CUSTOM_SUPPORTED_OPTIONS`, and `SAMSUNG_CE_WASHER_CYCLE`, and skip it when either `CUSTOM_WASHER_RINSE_CYCLES` or the standard `LAUNDRY_WASHER_RINSE_MODE` is present. Selection sends `Capability.EXECUTE` / `Command.EXECUTE` with `argument=["course/vs/0", {"x.com.samsung.da.options": ["ExtraRinse_On" or "ExtraRinse_Off"]}]`. Keep the existing rinse-count number for other models where `CUSTOM_WASHER_RINSE_CYCLES` is genuinely enabled; it is not a substitute on Table_02.
- **Pre soak**: no entity for Table_02. Keep the existing capability-gated `SAMSUNG_CE_WASHER_BUBBLE_SOAK` switch for models that really expose it, but do not equate Bubble Soak with this model's physical Pre Soak control.
- **Delay end select** (remote-gated; bespoke subclass per user decision for 15-min steps): capabilities `{SAMSUNG_CE_WASHER_DELAY_END, REMOTE_CONTROL_STATUS}`; `translation_key="delay_end"`; `value_is_integer=True`. Synthetic options (device exposes only `remainingTime` + `minimumReservableTime`, no settable list): `range(ceil(minimum/15)*15, 1441, 15)` as strings. `current_option()`: `None` when `remainingTime==0`, else nearest step as string (write-acknowledged value; document as best-effort read-back). `async_select_option`: remote-gate first, then `setDelayTime(int(option))` via `Command.SET_DELAY_TIME`. Documented fallback: if upstream rejects ~93 options, replace with a number entity (`minimumReservableTime..1440`, step 15, `mode=BOX`, unit `min`) on the same capability/command.
- **Sound**: no change (switch stays).

## 7. Strings / icons (follow existing patterns)

- Select: `strings.json` `entity.select.<key>.{name, state.<option>}` mirroring `soil_level`/`spin_level`/`water_temperature`/`detergent_amount` maps (snake_case states, `[%key:common::state::*%]` where applicable). New keys: `washer_cycle`, `detergent_density`, `softener_amount`, `softener_density`, `delay_end`, and `extra_rinse` with `Not Reported`/`Off`/`On` translations. No new switch strings for Pre Soak.
- Switch: `entity.switch.<key>.name` only (like `sound`, `bubble_soak`).
- `icons.json`: `entity.select.<key>.default` / `entity.switch.<key>.default` with `mdi:` values.
- Regenerate `translations/en.json`; hassfest must pass.

## 8. Testing

- Update `test_select.py` (+ exact hidden Extra Rinse payload in both directions, remote-gate on delay end, cycle two-command sequence), `test_switch.py` (sound stays; bubble-soak only if capability present), snapshots via syrupy.
- Delete service tests with the service. No new fixture unless a missing capability appears live — reuse existing washer fixtures.
- Live verification (user-confirmed only, via HA MCP + `script/sync_custom_component.sh <ha-host> smartthings`): read-only status compare, then select cycle, dispenser, delay (remote off → error; on → accepted), sound toggle read-back.

## 9. Risks

- Cycle shows raw codes in v1 (Cloud gives codes only); no static map to maintain.
- Private options are not returned by Cloud status, so the select always reports `Not Reported`; `Off` and `On` are commands rather than claimed state. A local OCF dependency would be required for authoritative read-back and is not added to this Cloud integration.
- Pre Soak cannot be safely written until an explicit token or command is observed; duration-only inference is ambiguous.
- Delay-end select length (~93 options); number fallback documented.
- `setCourse`/`setWasherCycle` order must be live-verified once.

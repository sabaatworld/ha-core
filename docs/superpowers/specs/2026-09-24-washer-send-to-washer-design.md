# Washer Send-to-Washer Design — SmartThings

Date: 2026-09-24
Status: Draft v2 (addressing independent review 2026-09-24, no commit per instruction)
Scope: `homeassistant/components/smartthings` — new `send_washer_cycle` service + Sound switch (Approach A + Sound)

Review findings (blocking, incorporated below): missing friendly→code table,
Table_02 code variance (live 26-code US set vs fixture `da_wm_wm_01011` 24-code
EU set), Level→value acceptance, Spin specials mapping, defaults when
per-cycle keys absent, dispenser amount vs density, delay arg format,
setCourse/setWasherCycle order assumption, Sound switch class, remote-gate
contradiction, service plumbing/hassfest, Super Speed cycle-vs-toggle and
F2 course-vs-preset ambiguity.

## 1. Goal

Mirror the SmartThings app's "Send to washer" for Samsung washers in Home Assistant:
configure Cycle + Temp/Spin/Soil (+ dispensers, delay) while the washer is On (or Off)
without requiring Smart Control / remote control, then send atomically.
Delay End remains remote-control-only (app greys it out, diagnostics confirms).

Non-goals (V1): Super Speed / Extra Rinse / Bubble Soak / Stay Connected /
AI Pattern / Remaining Laundry / My-Cycles-write. Sound is a separate toggle,
not part of the send bundle.

## 2. Screenshot catalog (source of truth for V1 fields)

Base card: Power Off/On, `Send to washer`, `Cycle: Normal`,
tiles `Temp Level 3 / Spin Level 4 / Soil Level 3`,
`Delay end` (disabled unless Smart Control),
`Remaining laundry`, `Stay connected` ON.

Scrolled: `Super speed` ("Shorten washing time") OFF,
`Extra rinse` ("Add additional rinse") OFF,
`Detergent dispenser: High amount`, `Softener dispenser: Off`,
`AI pattern` ON, `My cycles`, `Sound` OFF.

Popups:
- Temp: L1 Tap Cold, L2 Cold, L3 Warm (default), L4 Hot, L5 Extra hot
- Spin: Rinse Hold & No Spin, Drain & No Spin, L2 Low, L3 Medium,
  L4 High (default), L5 Extra high (L1 Extra Low absent when unsupported)
- Soil: L1 Extra light, L2 Light, L3 Normal (default), L4 Heavy, L5 Extra heavy

Cycles (26): Normal, Super Speed, Small Load, Delicates, Bedding,
Self Clean+, Little Extra (Customized), AI OptiWash, Heavy Duty,
Steam Whites, Steam Sanitize, Steam Normal, Towels, Activewear,
Steam Bulky, Power Steam, Power Rinse, Spin Only, Rinse+Spin,
Outdoor, Denim, Wool, Colors, Perm Press, Eco Cold, Steam Allergen.

## 3. Live device (WF53BB8900ATUS, Table_02)

- `custom.supportedOptions`: `course=01`, `referenceTable=Table_02`,
  `supportedCourses=[01,8C,53,51,5B,57,64,5A,85,54,56,5C,55,66,58,65,59,52,68,67,63,5D,5F,5E,60,F2]` (26).
- `samsungce.washerCycle`: `washerCycle=Table_02_Course_01`,
  `supportedCycles[]` with per-cycle `soilLevel/spinLevel/waterTemperature`
  `{raw, default, options}`. E.g. 01: soil normal, spin high, temp warm.
- `custom.washerSoilLevel/SpinLevel/WaterTemperature`: live + supported lists
  (already in `select.py`).
- `samsungce.washerCyclePreset`: `maxNumberOfPresets=10`,
  `F2={recipeId:1210, title:Little Extra, cycle:01, options:{soil:heavy, spin:high, temp:warm}}`.
- `samsungce.autoDispenseDetergent/Softener`: `amount=none`,
  `supportedAmount=[none,less,standard,extra]`, `density=normal`,
  `supportedDensity=[normal,high,extraHigh]`.
- `samsungce.audioVolumeLevel`: `volumeLevel=0`, range 0-1 (Sound toggle).
- `samsungce.washerDelayEnd`: `remainingTime=0`, `minimumReservableTime=45`.
- `remoteControlStatus=false`, `switch=off`.
- Disabled/null (V1 out of scope, likely OCF-only):
  `washerBubbleSoak`, `custom.washerRinseCycles`, `washerWaterLevel/Valve/WashingTime`,
  `custom.washerAutoDetergent/Softener`.

Current HA: `select.washer` (stop/run/pause), soil/spin/temp/detergent selects,
remote/power/child-lock binaries, machine/job/completion sensors. No Cycle entity.

## 4. Architecture (approved)

- New device-targeted service `smartthings.send_washer_cycle` via
  `services.yaml` + `services.py` (no `remote_control` gate except `delay_end`).
- New `Sound` config switch in `switch.py` via `samsungce.audioVolumeLevel`
  (`0`=off / `1`=on). Not part of send bundle.
- Thin integration: protocol in `pysmartthings` (v4.0.3 already has
  `custom.supportedOptions.setCourse`, `samsungce.washerCycle.setWasherCycle`,
  `custom.setWasher*`, `autoDispense.setAmount/setDensity`).
- Course table: static `Table_02` friendly→code map in integration
  (established pattern: `DISHWASHER_WASHING_COURSE_TO_HA`, wilbiev Table_02).
  Dynamic construction impossible — Cloud API returns only codes (`01`, `53`…),
  friendly names live in app/OCF. Accept raw 2-char code as fallback.
  Gate on `referenceTable.id`; fail clearly on unknown tables.

## 5. Send sequence + Level mapping (approved)

1. Resolve `cycle` friendly → code (fallback raw code, case-insensitive).
2. `custom.supportedOptions.setCourse(XX)` then
   `samsungce.washerCycle.setWasherCycle(Table_02_Course_XX)`.
3. `custom.setWasherSoilLevel / setWasherSpinLevel / setWasherWaterTemperature`
   for specified-or-defaulted fields.
4. `samsungce.autoDispenseDetergent/Softener.setAmount + setDensity`
   for specified-or-defaulted dispenser fields.
5. `delay_end` via `samsungce.washerDelayEnd.setDelayTime` (this model
   exposes `washerDelayEnd`; do not use `washerOperatingState.setDelayEnd`)
   only when remote — else `ServiceValidationError`.

Levels are a fixed global scale filtered per cycle, not per-cycle indexes:
- Temp L1-5 = tapCold/cold/warm/hot/extraHot.
- Soil L1-5 = extraLight/light/normal/heavy/extraHeavy.
- Spin L1-5 = extraLow/low/medium/high/extraHigh + specials
  rinseHold/noSpin (app shows "Rinse Hold & No Spin" / "Drain & No Spin").
  Absent levels (e.g. extraLow on cycle 01) are omitted in the app and rejected here.

## 6. Support detection + defaults (approved)

Sources: `supportedCourses + referenceTable`, `supportedCycles[]`
options+defaults, `supportedWasher*`, `supportedAmount/Density`,
minus `custom.disabledCapabilities`.
Omitted fields default to the chosen cycle's `supportedOptions` defaults
(e.g. 01 → soil normal / spin high / temp warm).
Strict fail-fast (`ServiceValidationError`): unknown cycle, option not in
that cycle's list, dispenser value unsupported, `delay_end` without remote,
unknown `referenceTable`. Sound switch created only if `audioVolumeLevel` present.

## 7. Errors

- `ServiceValidationError` with actionable messages (mirroring existing
  `requires_remote_control_status` pattern in `select.py:465-476`,
  `switch.py:533-551`).
- Unknown table → instruct to file diagnostics (`custom.supportedOptions`
  + `samsungce.washerCycle` status) so the table can be extended.
- Cloud/OCF divergence (app uses OCF COAP per SmartThings staff) → document
  that Super Speed / Extra Rinse / Bubble Soak may no-op via Cloud; V1 excludes them.

## 8. Testing

- Unit: `tests/test_services.py` (new) — validation, defaults, command order
  (assert `execute_device_command` calls), strict reject, delay gate,
  unknown table, raw-code fallback. Fixtures: Table_02 26-course status
  snapshot (from 2026-09-23 diagnostics) + F2 preset.
- Existing: `test_select.py` / `test_switch.py` (+ new Sound switch snapshot).
- Live (user-confirmed only): read-only status compare (course ↔ `washerCycle`,
  levels ↔ `custom.*`), then send tests: Normal defaults, Normal + overrides,
  delay without/with remote, Sound toggle. Note env gap: repo venv currently
  lacks `pysmartthings` (`script/setup` required before `pytest`).

## 9. Files touched (V1)

- `homeassistant/components/smartthings/services.yaml` (new)
- `homeassistant/components/smartthings/services.py` (new)
- `homeassistant/components/smartthings/switch.py` (Sound entry)
- `homeassistant/components/smartthings/strings.json`, `icons.json`,
  `translations/en.json` (regenerate via `script.translations develop`)
- `tests/components/smartthings/test_services.py` + fixtures (new)
- `tests/.../test_switch.py` snapshots (Sound)

## 10. Phase 2 (explicitly deferred)

Super Speed, Extra Rinse, Bubble Soak, Stay Connected, AI Pattern,
Remaining Laundry, My-Cycles-write (`setWasherCyclePreset/delete`),
dryer parity, additional reference tables (00/01/03).

## 11. Risks

- OCF-vs-Cloud: some app toggles may never work via Cloud API.
- Table maintenance: new models/tables need community diagnostics.
- Spin L1 edge: `extraLow` absent on many cycles — strict reject is correct.
- Ordering: `setCourse` vs `setWasherCycle` order must be live-verified;
  spec assumes course-then-cycle per community reports.

## 12. Review resolutions (v2 amendments)

- Friendly→code: preliminary US Table_02 map to be built during implementation
  via live test (set each app cycle, read `washerCycle`/`course`); verified so
  far: `01=Normal`, `F2=Little Extra (custom course, selectable V1; preset-write
  deferred)`. Table is label layer only — always validate against the device's
  own `supportedCourses + supportedCycles[]`; always accept raw 2-char codes
  (case-insensitive) as fallback. Same `Table_02` ID with different code sets
  (live US 26 vs fixture EU 24) means unknown codes fail fast with diagnostics
  instruction, never guessed.
- Service accepts both Level labels (`Level 1-5`, `Tap Cold`…`Extra hot`) and
  raw values (`warm`, `high`, `1400`…). Validation source is
  `supportedCycles[chosen].supportedOptions.*.options` per cycle, not global
  `supportedWasher*`. Spin specials (preliminary, verify live):
  `Rinse Hold & No Spin → rinseHold`, `Drain & No Spin → noSpin`.
- Defaults: omitted temp/spin/soil default to chosen cycle's
  `supportedOptions` defaults; if a key is absent for that cycle, skip (do not
  send, do not fail). Omitted dispenser/delay fields are left untouched
  (not sent). Dispenser schema is four fields: `detergent_amount`,
  `detergent_density`, `softener_amount`, `softener_density`; app
  "High amount" ≈ `density=high`, "Off" ≈ `amount=none` (verify live).
- `delay_end`: int minutes, `minimumReservableTime <= value <= 1440`,
  via `samsungce.washerDelayEnd.setDelayTime`; remote-only.
- Order assumption explicitly flagged: `setCourse(XX)` then
  `setWasherCycle(Table_02_Course_XX)`, `setCourse` arg is bare `XX`
  (per `custom.supportedOptions.setCourse(course)`); live verification protocol
  (both orders, read back `course` + `washerCycle`) is a plan step.
- Sound: custom switch calling `setVolumeLevel(1)`/`setVolumeLevel(0)`,
  `is_on = volumeLevel == 1`, translation_key `sound` (avoid collision with
  existing `sound_effect`), unique_id
  `…_samsungce.audioVolumeLevel_volumeLevel_volumeLevel`.
- Remote gate: entities keep `requires_remote_control_status` (e.g. water-temp
  select); the device-targeted service intentionally bypasses it (except delay)
  because app Send works Smart-Control-off — live-verified in plan.
- Plumbing: `services.yaml` field schema + device target selector,
  `device_id → config entry → client` resolution, registration from
  `__init__.py`, `strings.json` services section, hassfest-compliant.
  Correct test path: `tests/components/smartthings/test_services.py` + new
  Table_02 US fixture (do not reuse `da_wm_wm_01011`).
- Naming: Super Speed *cycle* is V1 (in 26); Super Speed *toggle* (shorten time)
  is Phase 2. `F2/Little Extra` *course* is V1 selectable; preset *write* is Phase 2.

# Washer Config Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the uncommitted `send_washer_cycle` service with native washer config entities (cycle, dispensers, extra rinse, delay end) while keeping the Sound switch, minimizing diff for upstream. Document why this model's physical Pre Soak cannot yet be controlled safely.

**Architecture:** Delete service files and revert service plumbing; add a bespoke cycle select subclass (two commands), a secondary auto-dispense select map (amount+density fan-out), a write-only Extra Rinse select over the generic `execute` capability, keep the capability-gated bubble-soak switch for other models, and add a bespoke 15-minute-step delay-end select with remote gating. All `EntityCategory.CONFIG`, existing `ServiceValidationError` patterns.

**Tech Stack:** Python 3.14, Home Assistant select/switch/number entity patterns, `pysmartthings==4.0.3` (`Capability`, `Command`, `Attribute`), pytest + syrupy, `script.translations`, hassfest, `script/sync_custom_component.sh`.

**Spec:** `docs/superpowers/specs/2026-09-24-washer-config-controls-design.md`

## Global Constraints

- Phase 2 is out of scope — do NOT implement Super Speed toggle, Stay Connected, AI Pattern, Remaining Laundry, preset-write, dryer parity.
- Python minimum is 3.14 — `except TypeA, TypeB:` without parens is allowed; lazy annotations allowed, no quoted forward refs needed.
- Integration must stay thin — protocol parsing lives in `pysmartthings`, not the integration.
- Never add `scan_interval` / polling options; entities keep `_attr_should_poll = False`.
- After editing `strings.json`, regenerate with `python3 -m script.translations develop --integration smartthings`.
- After finishing, run `uv run --no-sync prek run --all-files` and relevant `pytest`.
- Cycle shows raw codes only in v1; no static friendly→code table in the integration.
- Local OCF may be used for read-only discovery verification, but production remains Cloud-only.

---

## File Structure

- Revert/delete: `homeassistant/components/smartthings/services.py`, `services.yaml`, `washer_cycle.py`, `tests/components/smartthings/test_services.py`, `tests/components/smartthings/test_washer_cycle.py`; revert `__init__.py`, `strings.json` (services block only), `icons.json` (services block only); regenerate `translations/en.json`.
- Keep for tests: `tests/components/smartthings/fixtures/device_status/washer_us_table02.json`, `tests/components/smartthings/fixtures/devices/washer_us_table02.json`.
- Modify `homeassistant/components/smartthings/select.py` — add `SmartThingsWasherCycleSelectEntity` (bespoke), `AUTO_DISPENSE_TO_SELECT` map + setup loop, `SmartThingsWasherDelayEndSelectEntity` (bespoke 15-min), and `SmartThingsWasherExtraRinseSelectEntity` over private `execute` options.
- Modify `homeassistant/components/smartthings/strings.json`, `icons.json`, `translations/en.json` (regenerated).
- Modify `tests/components/smartthings/test_select.py`, snapshots; verify `test_switch.py` (sound + bubble-soak gating), `test_number.py` (rinse cycles).

---

### Task 1: Revert service junk

**Files:**
- Delete: `homeassistant/components/smartthings/services.py`, `homeassistant/components/smartthings/services.yaml`, `homeassistant/components/smartthings/washer_cycle.py`, `tests/components/smartthings/test_services.py`, `tests/components/smartthings/test_washer_cycle.py`
- Modify: `homeassistant/components/smartthings/__init__.py`, `homeassistant/components/smartthings/strings.json`, `homeassistant/components/smartthings/icons.json`, `homeassistant/components/smartthings/translations/en.json` (regenerated)
- Test: `tests/components/smartthings/test_select.py`, `test_switch.py`, `test_number.py`

**Interfaces:**
- Consumes: nothing.
- Produces: clean tree with only Sound-switch diff remaining before new entities.

- [ ] **Step 1: Delete service files**

```bash
rm homeassistant/components/smartthings/services.py homeassistant/components/smartthings/services.yaml homeassistant/components/smartthings/washer_cycle.py tests/components/smartthings/test_services.py tests/components/smartthings/test_washer_cycle.py
```

- [ ] **Step 2: Revert `__init__.py` registration**

Remove the line `from .services import async_setup_services` and the call `async_setup_services(hass)` in `async_setup_entry` (added after `async_forward_entry_setups`). Keep all other imports.

- [ ] **Step 3: Remove service blocks from strings/icons**

In `strings.json`, delete the top-level `"services": {"send_washer_cycle": ...}` object only. In `icons.json`, delete the top-level `"services": {"send_washer_cycle": ...}` object only. Keep `entity.switch.sound` entries.

- [ ] **Step 4: Regenerate translations and register fixture + snapshots**

Run: `python3 -m script.translations develop --integration smartthings`
Add `"washer_us_table02"` to `DEVICE_FIXTURES` in `tests/components/smartthings/__init__.py` if absent (required for `conftest.py` snapshot path covering new entities).
Run: `uv run --no-sync pytest tests/components/smartthings/test_select.py tests/components/smartthings/test_switch.py tests/components/smartthings/test_number.py -q`
If snapshots are stale (service-block removal), run once with `--snapshot-update` for `test_select/test_switch/test_number.ambr`, then re-run without the flag.
Expected: PASS with only pre-existing entities.

---

### Task 2: Washer cycle select (raw codes, two commands)

**Files:**
- Modify: `homeassistant/components/smartthings/select.py`
- Modify: `homeassistant/components/smartthings/strings.json`, `homeassistant/components/smartthings/icons.json`
- Test: `tests/components/smartthings/test_select.py`

**Interfaces:**
- Consumes: `Capability.CUSTOM_SUPPORTED_OPTIONS`, `Capability.SAMSUNG_CE_WASHER_CYCLE`, `Attribute.SUPPORTED_COURSES`, `Attribute.REFERENCE_TABLE`, `Command.SET_COURSE`, `Command.SET_WASHER_CYCLE`, `MAIN`.
- Produces: `SmartThingsWasherCycleSelectEntity` with `options()` = raw `supportedCourses`, `current_option()` = `course`, `async_select_option(code)` sending `setCourse` then `setWasherCycle`.

- [ ] **Step 1: Write the failing test**

```python
from pysmartthings import Attribute, Capability, Command


@pytest.mark.parametrize("device_fixture", ["washer_us_table02"])
async def test_washer_cycle_select_sends_both_commands(
    hass: HomeAssistant, devices: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Selecting code 53 sends setCourse(53) then setWasherCycle(<table>_Course_53)."""
    await setup_integration(hass, mock_config_entry)
    entity_id = "select.theater_washer_cycle"
    assert hass.states.get(entity_id) is not None
    await hass.services.async_call(
        "select", "select_option", {"entity_id": entity_id, "option": "53"}, blocking=True
    )
    calls = devices.execute_device_command.await_args_list
    assert calls[0].args[1] == Capability.CUSTOM_SUPPORTED_OPTIONS
    assert calls[0].args[2] == Command.SET_COURSE
    assert calls[0].args[3] == MAIN
    assert calls[0].kwargs["argument"] == "53"
    assert calls[1].args[1] == Capability.SAMSUNG_CE_WASHER_CYCLE
    assert calls[1].args[2] == Command.SET_WASHER_CYCLE
    assert calls[1].kwargs["argument"].endswith("_Course_53")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/components/smartthings/test_select.py::test_washer_cycle_select_sends_both_commands -v`
Expected: FAIL (entity/select missing).

- [ ] **Step 3: Write minimal implementation**

Add in `select.py` (do not add to `CAPABILITIES_TO_SELECT` — it supports one command only):

```python
class SmartThingsWasherCycleSelectEntity(SmartThingsEntity, SelectEntity):
    """Washer course select (raw codes, two commands)."""

    _attr_translation_key = "washer_cycle"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, client: SmartThings, device: FullDevice) -> None:
        super().__init__(
            client,
            device,
            {Capability.CUSTOM_SUPPORTED_OPTIONS, Capability.SAMSUNG_CE_WASHER_CYCLE},
        )
        self._attr_unique_id = f"{device.device.device_id}_{MAIN}_washer_cycle"

    @property
    def options(self) -> list[str]:
        return (
            self.get_attribute_value(
                Capability.CUSTOM_SUPPORTED_OPTIONS, Attribute.SUPPORTED_COURSES
            )
            or []
        )

    @property
    def current_option(self) -> str | None:
        course = self.get_attribute_value(
            Capability.CUSTOM_SUPPORTED_OPTIONS, Attribute.COURSE
        )
        if course is not None:
            return course
        washer_cycle = self.get_attribute_value(
            Capability.SAMSUNG_CE_WASHER_CYCLE, Attribute.WASHER_CYCLE
        )
        if isinstance(washer_cycle, str) and "_Course_" in washer_cycle:
            return washer_cycle.rsplit("_Course_", 1)[-1]
        return None

    async def async_select_option(self, option: str) -> None:
        supported = self.options
        if option not in supported:
            raise ServiceValidationError(f"Unsupported cycle for this washer: {option}")
        ref = self.get_attribute_value(
            Capability.CUSTOM_SUPPORTED_OPTIONS, Attribute.REFERENCE_TABLE
        )
        table_id = ref.get("id") if isinstance(ref, dict) else None
        if not table_id:
            raise ServiceValidationError(
                "Washer does not expose a reference table; file diagnostics "
                "including custom.supportedOptions and samsungce.washerCycle status"
            )
        await self.execute_device_command(
            Capability.CUSTOM_SUPPORTED_OPTIONS, Command.SET_COURSE, option
        )
        await self.execute_device_command(
            Capability.SAMSUNG_CE_WASHER_CYCLE,
            Command.SET_WASHER_CYCLE,
            f"{table_id}_Course_{option}",
        )
```

Extend `async_setup_entry` with:

```python
entities.extend(
    SmartThingsWasherCycleSelectEntity(entry_data.client, device)
    for device in entry_data.devices.values()
    if Capability.CUSTOM_SUPPORTED_OPTIONS in device.status[MAIN]
    and Capability.SAMSUNG_CE_WASHER_CYCLE in device.status[MAIN]
)
```

Add `strings.json` `entity.select.washer_cycle: {"name": "Cycle"}` (no per-code state map in v1) and `icons.json` `entity.select.washer_cycle: {"default": "mdi:washing-machine"}`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/components/smartthings/test_select.py -q`
Expected: PASS.

---

### Task 3: Dispenser density + softener selects (secondary map)

**Files:**
- Modify: `homeassistant/components/smartthings/select.py`, `strings.json`, `icons.json`
- Test: `tests/components/smartthings/test_select.py`

**Interfaces:**
- Consumes: `Capability.SAMSUNG_CE_AUTO_DISPENSE_DETERGENT`, `Capability.SAMSUNG_CE_AUTO_DISPENSE_SOFTENER`, `Attribute.AMOUNT`, `Attribute.DENSITY`, `Attribute.SUPPORTED_AMOUNT`, `Attribute.SUPPORTED_DENSITY`, `Command.SET_AMOUNT`, `Command.SET_DENSITY`.
- Produces: `AUTO_DISPENSE_TO_SELECT` map + setup loop emitting detergent density, softener amount, softener density (migrates existing detergent amount into the map).

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize("device_fixture", ["washer_us_table02"])
async def test_dispenser_selects_exist(
    hass: HomeAssistant, devices: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get("select.theater_washer_detergent_density") is not None
    assert hass.states.get("select.theater_washer_softener_amount") is not None
    assert hass.states.get("select.theater_washer_softener_density") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/components/smartthings/test_select.py::test_dispenser_selects_exist -v`
Expected: FAIL (only detergent amount exists).

- [ ] **Step 3: Write minimal implementation**

Add (mirroring `DISHWASHER_WASHING_OPTIONS_TO_SELECT`) as a single map named `AUTO_DISPENSE_TO_SELECT` keyed by `(Capability, Attribute)` tuple. First remove the existing single `SAMSUNG_CE_AUTO_DISPENSE_DETERGENT` row from `CAPABILITIES_TO_SELECT` (`select.py:199`) to avoid a duplicate `detergent_amount`:

```python
DENSITY_TO_HA = {
    "normal": "normal",
    "high": "high",
    "extraHigh": "extra_high",
}

AUTO_DISPENSE_TO_SELECT: dict[
    tuple[Capability, Attribute], SmartThingsSelectDescription
] = {
    (Capability.SAMSUNG_CE_AUTO_DISPENSE_DETERGENT, Attribute.AMOUNT): SmartThingsSelectDescription(
        key=Capability.SAMSUNG_CE_AUTO_DISPENSE_DETERGENT,
        translation_key="detergent_amount",
        options_attribute=Attribute.SUPPORTED_AMOUNT,
        status_attribute=Attribute.AMOUNT,
        command=Command.SET_AMOUNT,
        entity_category=EntityCategory.CONFIG,
    ),
    (Capability.SAMSUNG_CE_AUTO_DISPENSE_DETERGENT, Attribute.DENSITY): SmartThingsSelectDescription(
        key=Capability.SAMSUNG_CE_AUTO_DISPENSE_DETERGENT,
        translation_key="detergent_density",
        options_attribute=Attribute.SUPPORTED_DENSITY,
        status_attribute=Attribute.DENSITY,
        command=Command.SET_DENSITY,
        options_map=DENSITY_TO_HA,
        entity_category=EntityCategory.CONFIG,
    ),
    (Capability.SAMSUNG_CE_AUTO_DISPENSE_SOFTENER, Attribute.AMOUNT): SmartThingsSelectDescription(
        key=Capability.SAMSUNG_CE_AUTO_DISPENSE_SOFTENER,
        translation_key="softener_amount",
        options_attribute=Attribute.SUPPORTED_AMOUNT,
        status_attribute=Attribute.AMOUNT,
        command=Command.SET_AMOUNT,
        entity_category=EntityCategory.CONFIG,
    ),
    (Capability.SAMSUNG_CE_AUTO_DISPENSE_SOFTENER, Attribute.DENSITY): SmartThingsSelectDescription(
        key=Capability.SAMSUNG_CE_AUTO_DISPENSE_SOFTENER,
        translation_key="softener_density",
        options_attribute=Attribute.SUPPORTED_DENSITY,
        status_attribute=Attribute.DENSITY,
        command=Command.SET_DENSITY,
        options_map=DENSITY_TO_HA,
        entity_category=EntityCategory.CONFIG,
    ),
}
```

Remove the old single `SAMSUNG_CE_AUTO_DISPENSE_DETERGENT` row from `CAPABILITIES_TO_SELECT` (migrated into the map) and extend `async_setup_entry` with one loop over `AUTO_DISPENSE_TO_SELECT` when `capability in device.status[MAIN]`. The generic `SmartThingsSelectEntity` unique_id already includes the status attribute, so amount/density no longer collide.

Strings (snake_case, mirroring soil/spin/temp): `detergent_density: {"name": "Detergent dispense density", "state": {"normal": "Normal", "high": "High", "extra_high": "Extra high"}}`, `softener_amount: {"name": "Softener dispense amount", "state": {"none": "[%key:common::state::off%]", "less": "Less", "standard": "Standard", "extra": "Extra"}}`, `softener_density` mirroring detergent density. Icons default `mdi:beaker-outline`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/components/smartthings/test_select.py -q`
Expected: PASS.

---

### Task 4: Extra rinse (private course option over generic execute)

**Files:**
- Modify: `homeassistant/components/smartthings/select.py`, `strings.json`, `icons.json`
- Test: `tests/components/smartthings/test_select.py`, `test_number.py`

**Interfaces:**
- Consumes: `Capability.EXECUTE`, `Command.EXECUTE`, and washer-identifying Cloud capabilities.
- Produces: write-only `extra_rinse` select sending the empirically verified `ExtraRinse_On` / `ExtraRinse_Off` course option. Existing rinse-count support remains unchanged for models that expose it.

- [x] **Step 1: Capture and verify the private payload**

The SmartThings app changed local `/course/vs/0` from `ExtraRinse_Off` to `ExtraRinse_On`. Direct Cloud commands in both directions were accepted and independently read back locally. The exact command argument is:

```python
[
    "course/vs/0",
    {"x.com.samsung.da.options": ["ExtraRinse_On"]},
]
```

- [x] **Step 2: Write the failing command-contract test**

Parameterize `off` / `ExtraRinse_Off` and `on` / `ExtraRinse_On`; assert the entity exists for `washer_us_table02`, exposes both options, and calls `execute_device_command(device_id, Capability.EXECUTE, Command.EXECUTE, MAIN, argument=[...])` exactly.

- [x] **Step 3: Write minimal implementation**

Add `SmartThingsWasherExtraRinseSelectEntity`, with `options=["not_reported", "off", "on"]` and a fixed `current_option="not_reported"`. Do not optimistically assign the requested option: as with other non-optimistic HA entities, state changes only from device read-back, which this private option does not expose. Selecting `Not Reported` sends no washer command. Gate to devices exposing `EXECUTE`, `CUSTOM_SUPPORTED_OPTIONS`, and `SAMSUNG_CE_WASHER_CYCLE`; skip when `CUSTOM_WASHER_RINSE_CYCLES` or the standard `LAUNDRY_WASHER_RINSE_MODE` exists. The missing device read-back is intentional because Cloud status hides `x.com.samsung.da.options`.

- [ ] **Step 4: Regenerate translations and run tests**

Run: `uv run --no-sync pytest tests/components/smartthings/test_number.py tests/components/smartthings/test_select.py -q`
Expected: PASS.

---

### Task 5: Pre Soak discovery result (no unsafe mapping)

**Files:**
- Test: `tests/components/smartthings/test_switch.py`
- Modify: none (verify only)

**Interfaces:**
- Consumes: controlled Cloud event capture and read-only local `/course/vs/0` capture with physical Pre Soak off/on.
- Produces: documented absence of a safe Cloud command mapping; the existing `SAMSUNG_CE_WASHER_BUBBLE_SOAK` switch remains available only on models that expose it.

- [x] **Step 1: Compare physical Pre Soak off/on**

On Normal, physical Pre Soak changed duration from 45 to 75 minutes. It did not add or change any `/course/vs/0` option token, `SpecialFunction_6` remained unchanged, and Cloud emitted only derived time attributes.

- [x] **Step 2: Verify capability gating**

Run: `uv run --no-sync pytest tests/components/smartthings/test_switch.py -q`
Expected: PASS; `switch.theater_washer_bubble_soak` absent for `washer_us_table02` (in `disabledCapabilities`), `switch.theater_washer_sound` present.

---

### Task 6: Delay end select (15-minute steps, remote-gated)

**Files:**
- Modify: `homeassistant/components/smartthings/select.py`, `strings.json`, `icons.json`
- Test: `tests/components/smartthings/test_select.py`

**Interfaces:**
- Consumes: `Capability.SAMSUNG_CE_WASHER_DELAY_END`, `Attribute.MINIMUM_RESERVABLE_TIME`, `Attribute.REMAINING_TIME`, `Command.SET_DELAY_TIME`, `Capability.REMOTE_CONTROL_STATUS`.
- Produces: `SmartThingsWasherDelayEndSelectEntity` with synthetic 15-min options, remote gate.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize("device_fixture", ["washer_us_table02"])
async def test_delay_end_requires_remote(
    hass: HomeAssistant, devices: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, mock_config_entry)
    with pytest.raises(ServiceValidationError, match="remote control"):
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": "select.theater_washer_delay_end", "option": "60"},
            blocking=True,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/components/smartthings/test_select.py::test_delay_end_requires_remote -v`
Expected: FAIL (entity missing).

- [ ] **Step 3: Write minimal implementation**

```python
class SmartThingsWasherDelayEndSelectEntity(SmartThingsEntity, SelectEntity):
    """Delay end as 15-minute-step select (remote-gated)."""

    _attr_translation_key = "delay_end"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, client: SmartThings, device: FullDevice) -> None:
        super().__init__(
            client,
            device,
            {Capability.SAMSUNG_CE_WASHER_DELAY_END, Capability.REMOTE_CONTROL_STATUS},
        )
        self._attr_unique_id = f"{device.device.device_id}_{MAIN}_washer_delay_end"

    @property
    def options(self) -> list[str]:
        minimum = (
            self.get_attribute_value(
                Capability.SAMSUNG_CE_WASHER_DELAY_END,
                Attribute.MINIMUM_RESERVABLE_TIME,
            )
            or 0
        )
        start = ((int(minimum) + 14) // 15) * 15
        return [str(v) for v in range(start, 1441, 15)]

    @property
    def current_option(self) -> str | None:
        remaining = self.get_attribute_value(
            Capability.SAMSUNG_CE_WASHER_DELAY_END, Attribute.REMAINING_TIME
        )
        if not remaining:
            return None
        options = [int(o) for o in self.options]
        nearest = min(options, key=lambda v: abs(v - int(remaining)))
        return str(nearest)

    async def async_select_option(self, option: str) -> None:
        if Capability.REMOTE_CONTROL_STATUS not in self._internal_state:
            raise ServiceValidationError(
                "Can only be updated when remote control is enabled"
            )
        if (
            self.get_attribute_value(
                Capability.REMOTE_CONTROL_STATUS, Attribute.REMOTE_CONTROL_ENABLED
            )
            == "false"
        ):
            raise ServiceValidationError(
                "Can only be updated when remote control is enabled"
            )
        await self.execute_device_command(
            Capability.SAMSUNG_CE_WASHER_DELAY_END, Command.SET_DELAY_TIME, int(option)
        )
```

Setup gate: create only when both `SAMSUNG_CE_WASHER_DELAY_END in device.status[MAIN]` and `REMOTE_CONTROL_STATUS in device.status[MAIN]`. Strings `entity.select.delay_end: {"name": "Delay end"}`. Icon `mdi:timer-outline`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/components/smartthings/test_select.py -q`
Expected: PASS.

---

### Task 7: Sound keep + translations, hassfest, lint

**Files:**
- Modify: `translations/en.json` (regenerated)
- Test: hassfest + prek + full subset

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces: green static checks with Sound switch kept.

- [ ] **Step 1: Regenerate translations**

Run: `python3 -m script.translations develop --integration smartthings`
Expected: `services.send_washer_cycle` gone; `select.washer_cycle/detergent_density/softener_amount/softener_density/delay_end/extra_rinse` present; `switch.sound` kept.

- [ ] **Step 2: Run hassfest for the integration**

Run: `python3 -m script.hassfest --integration smartthings`
Expected: PASS (exact strings/icons keys; `delay_end` has name only, no 93-entry state map).

- [ ] **Step 3: Run lint over touched files**

Run: `uv run --no-sync prek run --all-files`
Expected: PASS.

- [ ] **Step 4: Run the SmartThings test subset**

Run: `uv run --no-sync pytest tests/components/smartthings/test_select.py tests/components/smartthings/test_switch.py tests/components/smartthings/test_number.py -q`
Expected: PASS.

- [ ] **Step 5: Verify Sound keep + snapshot regen**

Confirm `switch.py` still has `SAMSUNG_CE_AUDIO_VOLUME_LEVEL` with `on_key=1`/`off_key=0` and the `str | bool | int` widening; run `uv run --no-sync pytest tests/components/smartthings/test_switch.py -q --snapshot-update` only if snapshots are stale, then re-run without the flag. Decision criterion for delay-end fallback: if hassfest or reviewer rejects the ~93-option select, replace Task 6 entity with a number (`minimumReservableTime..1440`, step 15, `mode=BOX`, unit `min`) on the same capability/command.

---

### Task 8: Live verification + sync (manual, user-confirmed only)

**Files:** none (procedure only).

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces: live-confirmed cycle/dispenser/rinse/delay/sound behavior.

- [ ] **Step 1: Read-only compare via HA MCP (no state change)**

Use HA MCP to read washer selects/number/switch states; compare app `Cycle Normal` vs `select.washer_cycle == 01`, levels vs `custom.*`, `minimumReservableTime == 45`.

- [ ] **Step 2: Send selects live (washer On, user confirms each call)**

Select cycle `01`, dispenser density, Extra Rinse off/on, delay without remote (expect remote-control error), then with Smart Control on (expect accepted), sound toggle read-back.

- [ ] **Step 3: Sync integration to HA**

Run: `script/sync_custom_component.sh <ha-host> smartthings`
Then verify via HA MCP that new entities appear. Record `<ha-host>` used and `setCourse`/`setWasherCycle` order outcome back into the spec.

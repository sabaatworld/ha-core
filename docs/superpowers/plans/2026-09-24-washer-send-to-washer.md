# Washer Send-to-Washer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `smartthings.send_washer_cycle` service (atomic app-like Send to washer) plus a Sound switch for Samsung washers.

**Architecture:** New `services.yaml`/`services.py` device-targeted service that validates against the device's own `custom.supportedOptions` + `samsungce.washerCycle.supportedCycles[]` and sends `setCourse` → `setWasherCycle` → `custom.set*` → `autoDispense.set*` → optional `setDelayTime`; plus a `Sound` config switch via `samsungce.audioVolumeLevel`. Static US Table_02 friendly→code label map (verified entries only, raw-code fallback always).

**Tech Stack:** Python 3.14, Home Assistant core services pattern (`probatio` schema, `service.async_get_device_and_config_entry`), `pysmartthings==4.0.3` (`Capability`, `Command`, `Attribute`), pytest + syrupy snapshots.

**Spec:** `docs/superpowers/specs/2026-09-24-washer-send-to-washer-design.md`

## Global Constraints

- No commits — leave the working tree dirty; do not run `git commit` (user instruction overrides the usual per-task commit).
- Phase 2 is out of scope — do NOT implement Super Speed toggle, Extra Rinse,
  Bubble Soak, Stay Connected, AI Pattern, Remaining Laundry, preset-write
  (`setWasherCyclePreset`/`delete`), dryer parity, or tables 00/01/03.
- Python minimum is 3.14 — `except TypeA, TypeB:` without parens is allowed; lazy annotations allowed, no quoted forward refs needed.
- Integration must stay thin — protocol parsing lives in `pysmartthings`, not the integration.
- Never add `scan_interval` / polling options; entities keep `_attr_should_poll = False`.
- After editing `strings.json`, regenerate with `python3 -m script.translations develop --integration smartthings`.
- After finishing, run `uv run --no-sync prek run --all-files` and relevant `pytest`.
- First-time env needs `script/setup` (repo venv currently lacks `pysmartthings`; run setup before pytest).

---

## File Structure

- Create `homeassistant/components/smartthings/washer_cycle.py` — pure helpers: `WASHER_CYCLE_TABLE_02` (verified labels only), `normalize_cycle()`, `normalize_level()`, per-cycle default/validation readers. No HA imports (unit-testable).
- Create `homeassistant/components/smartthings/services.yaml` — `send_washer_cycle` fields + device target.
- Create `homeassistant/components/smartthings/services.py` — probatio schema, device resolution, validation, command sequence.
- Modify `homeassistant/components/smartthings/__init__.py` — register services on setup (import + `async_setup_services` call in `async_setup_entry`).
- Modify `homeassistant/components/smartthings/switch.py` — Sound switch description + creation gate.
- Modify `homeassistant/components/smartthings/strings.json`, `icons.json`, `translations/en.json` (regenerated).
- Create `tests/components/smartthings/test_services.py` + fixture `tests/components/smartthings/fixtures/device_status/washer_us_table02.json` (26-course US snapshot derived from 2026-09-23 live diagnostics).
- Modify `tests/components/smartthings/__init__.py` if a fixture registry needs the new device name.

---

### Task 1: Washer cycle helpers (pure module)

**Files:**
- Create: `homeassistant/components/smartthings/washer_cycle.py`
- Test: `tests/components/smartthings/test_washer_cycle.py` (new, colocated with task)

**Interfaces:**
- Consumes: nothing (pure).
- Produces:
  - `WASHER_CYCLE_TABLE_02: dict[str, str]` (friendly lowercase → 2-char code; verified: `normal → 01`, `little extra → F2`)
  - `normalize_cycle(value: str) -> str` (friendly or raw code → uppercase code; raises `ValueError` on unknown)
  - `normalize_temp(value: str) -> str`, `normalize_spin(value: str) -> str`, `normalize_soil(value: str) -> str` (Level labels + friendly + raw → API value; raises `ValueError`)
  - `cycle_entry(supported_cycles: list[dict], code: str) -> dict | None`
  - `default_for(entry: dict, option: str) -> str | None` (absent key → `None` = skip)

- [ ] **Step 1: Write the failing test**

```python
from homeassistant.components.smartthings.washer_cycle import (
    cycle_entry,
    default_for,
    normalize_cycle,
    normalize_soil,
    normalize_spin,
    normalize_temp,
)


def test_normalize_cycle_verified_and_raw() -> None:
    assert normalize_cycle("Normal") == "01"
    assert normalize_cycle("little extra") == "F2"
    assert normalize_cycle("53") == "53"
    assert normalize_cycle("f2") == "F2"


def test_normalize_levels_and_defaults() -> None:
    assert normalize_temp("Level 3 (Warm)") == "warm"
    assert normalize_temp("40") == "40"
    assert normalize_soil("Level 1 (Extra light)") == "extraLight"
    assert normalize_spin("Drain & No Spin") == "noSpin"
    assert normalize_spin("1400") == "1400"
    entry = {
        "cycle": "01",
        "supportedOptions": {
            "soilLevel": {"default": "normal", "options": ["normal"]},
            "spinLevel": {"default": "high", "options": ["high"]},
        },
    }
    assert cycle_entry([entry], "01") == entry
    assert cycle_entry([entry], "FF") is None
    assert default_for(entry, "soilLevel") == "normal"
    assert default_for(entry, "waterTemperature") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/components/smartthings/test_washer_cycle.py::test_normalize_cycle_verified_and_raw -v`
Expected: FAIL with "No module named 'homeassistant.components.smartthings.washer_cycle'" (or collection error).

- [ ] **Step 3: Write minimal implementation**

```python
"""Pure helpers for washer Send-to-washer (no HA imports)."""

WASHER_CYCLE_TABLE_02: dict[str, str] = {
    # Verified rows only — do NOT guess the remaining 24. Raw 2-char codes
    # always work as fallback; Task 6 expands this table via live tests.
    "normal": "01",
    "little extra": "F2",
}

_LEVEL_TEMP = {
    "level 1": "tapCold",
    "level 1 (tap cold)": "tapCold",
    "tap cold": "tapCold",
    "tapcold": "tapCold",
    "level 2": "cold",
    "level 2 (cold)": "cold",
    "cold": "cold",
    "level 3": "warm",
    "level 3 (warm)": "warm",
    "warm": "warm",
    "level 4": "hot",
    "level 4 (hot)": "hot",
    "hot": "hot",
    "level 5": "extraHot",
    "level 5 (extra hot)": "extraHot",
    "extra hot": "extraHot",
    "extrahot": "extraHot",
    # Raw passthroughs (validated per-cycle against supportedOptions.options):
    "none": "none",
    "20": "20",
    "30": "30",
    "40": "40",
    "50": "50",
    "60": "60",
    "65": "65",
    "70": "70",
    "75": "75",
    "80": "80",
    "90": "90",
    "95": "95",
    "cool": "cool",
    "ecowarm": "ecoWarm",
    "eco warm": "ecoWarm",
    "semihot": "semiHot",
    "semi hot": "semiHot",
    "extralow": "extraLow",
    "extra low": "extraLow",
    "mediumlow": "mediumLow",
    "medium low": "mediumLow",
    "medium": "medium",
    "high": "high",
}

_LEVEL_SOIL = {
    "level 1": "extraLight",
    "level 1 (extra light)": "extraLight",
    "extra light": "extraLight",
    "extralight": "extraLight",
    "level 2": "light",
    "level 2 (light)": "light",
    "light": "light",
    "level 3": "normal",
    "level 3 (normal)": "normal",
    "normal": "normal",
    "level 4": "heavy",
    "level 4 (heavy)": "heavy",
    "heavy": "heavy",
    "level 5": "extraHeavy",
    "level 5 (extra heavy)": "extraHeavy",
    "extra heavy": "extraHeavy",
    "extraheavy": "extraHeavy",
    # Raw passthroughs:
    "none": "none",
    "up": "up",
    "down": "down",
}

_LEVEL_SPIN = {
    "level 1": "extraLow",
    "level 1 (extra low)": "extraLow",
    "extra low": "extraLow",
    "extralow": "extraLow",
    "level 2": "low",
    "level 2 (low)": "low",
    "low": "low",
    "level 3": "medium",
    "level 3 (medium)": "medium",
    "medium": "medium",
    "level 4": "high",
    "level 4 (high)": "high",
    "high": "high",
    "level 5": "extraHigh",
    "level 5 (extra high)": "extraHigh",
    "extra high": "extraHigh",
    "extrahigh": "extraHigh",
    "rinse hold & no spin": "rinseHold",
    "rinse hold": "rinseHold",
    "rinsehold": "rinseHold",
    "drain & no spin": "noSpin",
    "drain and no spin": "noSpin",
    "no spin": "noSpin",
    "nospin": "noSpin",
    # Raw passthroughs:
    "none": "none",
    "delicate": "delicate",
    "200": "200",
    "400": "400",
    "600": "600",
    "800": "800",
    "1000": "1000",
    "1200": "1200",
    "1400": "1400",
    "1600": "1600",
}


def _norm(value: str) -> str:
    return " ".join(value.strip().lower().split())


def normalize_cycle(value: str) -> str:
    """Resolve friendly name or raw 2-char code to uppercase code."""
    key = _norm(value)
    if key in WASHER_CYCLE_TABLE_02:
        return WASHER_CYCLE_TABLE_02[key]
    compact = key.replace(" ", "").replace("_", "")
    if len(compact) == 2 and all(c in "0123456789abcdef" for c in compact):
        return compact.upper()
    raise ValueError(f"Unknown washer cycle: {value}")


def normalize_temp(value: str) -> str:
    """Map Level label / friendly / raw to API waterTemperature value."""
    key = _norm(value)
    if key in _LEVEL_TEMP:
        return _LEVEL_TEMP[key]
    raise ValueError(f"Unknown water temperature: {value}")


def normalize_soil(value: str) -> str:
    """Map Level label / friendly / raw to API soilLevel value."""
    key = _norm(value)
    if key in _LEVEL_SOIL:
        return _LEVEL_SOIL[key]
    raise ValueError(f"Unknown soil level: {value}")


def normalize_spin(value: str) -> str:
    """Map Level label / friendly / raw to API spinLevel value."""
    key = _norm(value)
    if key in _LEVEL_SPIN:
        return _LEVEL_SPIN[key]
    raise ValueError(f"Unknown spin level: {value}")


def cycle_entry(supported_cycles: list[dict], code: str) -> dict | None:
    """Return the supportedCycles entry for code, or None."""
    for entry in supported_cycles:
        if str(entry.get("cycle", "")).upper() == code.upper():
            return entry
    return None


def default_for(entry: dict, option: str) -> str | None:
    """Return per-cycle default for soilLevel/spinLevel/waterTemperature, or None to skip."""
    options = entry.get("supportedOptions", {})
    node = options.get(option)
    if not isinstance(node, dict):
        return None
    default = node.get("default")
    return str(default) if default not in (None, "none", "") else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/components/smartthings/test_washer_cycle.py -v`
Expected: PASS

---

### Task 2: Service definition (`services.yaml` + strings)

**Files:**
- Create: `homeassistant/components/smartthings/services.yaml`
- Modify: `homeassistant/components/smartthings/strings.json`
- Test: hassfest services check (runs in CI; local: `python3 -m script.hassfest --integration smartthings` if available, else rely on schema test in Task 3)

**Interfaces:**
- Consumes: Task 1 normalize functions (field docs reference them).
- Produces: service name `send_washer_cycle` with fields consumed by Task 3.

- [ ] **Step 1: Write the service YAML**

```yaml
send_washer_cycle:
  name: Send washer cycle
  description: Configure a Samsung washer cycle (like the app's Send to washer) without requiring Smart Control. Delay end requires remote control.
  target:
    device:
      - integration: smartthings
  fields:
    cycle:
      name: Cycle
      description: Friendly cycle name (e.g. Normal) or raw 2-char course code (e.g. 01). Unknown codes fail fast.
      required: true
      selector:
        text: null
    water_temperature:
      name: Water temperature
      description: Level 1-5, Tap Cold/Cold/Warm/Hot/Extra hot, or raw value.
      required: false
      selector:
        text: null
    spin_level:
      name: Spin level
      description: Level 1-5, Low/Medium/High/Extra high, Rinse Hold, or No Spin.
      required: false
      selector:
        text: null
    soil_level:
      name: Soil level
      description: Level 1-5 or Extra light/Light/Normal/Heavy/Extra heavy.
      required: false
      selector:
        text: null
    detergent_amount:
      name: Detergent amount
      description: none, less, standard, or extra. Omitted = leave untouched.
      required: false
      selector:
        select:
          options:
            - none
            - less
            - standard
            - extra
    detergent_density:
      name: Detergent density
      description: normal, high, or extraHigh. Omitted = leave untouched.
      required: false
      selector:
        select:
          options:
            - normal
            - high
            - extraHigh
    softener_amount:
      name: Softener amount
      description: none, less, standard, or extra. Omitted = leave untouched.
      required: false
      selector:
        select:
          options:
            - none
            - less
            - standard
            - extra
    softener_density:
      name: Softener density
      description: normal, high, or extraHigh. Omitted = leave untouched.
      required: false
      selector:
        select:
          options:
            - normal
            - high
            - extraHigh
    delay_end:
      name: Delay end
      description: Delay in minutes. Requires remote control (Smart Control on) and must be >= minimumReservableTime and <= 1440. Omitted = leave untouched.
      required: false
      selector:
        number:
          min: 1
          max: 1440
          step: 1
          unit_of_measurement: min
```

- [ ] **Step 2: Add strings.json services section**

Read `homeassistant/components/smartthings/strings.json`, find the top-level keys, and add a `services` object mirroring the YAML names/descriptions:

```json
"services": {
  "send_washer_cycle": {
    "name": "Send washer cycle",
    "description": "Configure a Samsung washer cycle (like the app's Send to washer) without requiring Smart Control. Delay end requires remote control.",
    "fields": {
      "cycle": { "name": "Cycle", "description": "Friendly cycle name (e.g. Normal) or raw 2-char course code (e.g. 01)." },
      "water_temperature": { "name": "Water temperature", "description": "Level 1-5, Tap Cold/Cold/Warm/Hot/Extra hot, or raw value." },
      "spin_level": { "name": "Spin level", "description": "Level 1-5, Low/Medium/High/Extra high, Rinse Hold, or No Spin." },
      "soil_level": { "name": "Soil level", "description": "Level 1-5 or Extra light/Light/Normal/Heavy/Extra heavy." },
      "detergent_amount": { "name": "Detergent amount", "description": "none, less, standard, or extra." },
      "detergent_density": { "name": "Detergent density", "description": "normal, high, or extraHigh." },
      "softener_amount": { "name": "Softener amount", "description": "none, less, standard, or extra." },
      "softener_density": { "name": "Softener density", "description": "normal, high, or extraHigh." },
      "delay_end": { "name": "Delay end", "description": "Delay in minutes. Requires remote control." }
    }
  }
}
```

- [ ] **Step 3: Validate YAML + JSON parse**

Run: `uv run --no-sync python -c "import json,yaml; yaml.safe_load(open('homeassistant/components/smartthings/services.yaml')); json.load(open('homeassistant/components/smartthings/strings.json')); print('parse ok')"`
Expected: `parse ok`

---

### Task 3: Service implementation (`services.py` + registration)

**Files:**
- Create: `homeassistant/components/smartthings/services.py`
- Modify: `homeassistant/components/smartthings/__init__.py` (register in `async_setup_entry`)
- Test: `tests/components/smartthings/test_services.py` (written in Task 5; this task's code must satisfy it)

**Interfaces:**
- Consumes: Task 1 (`normalize_cycle`, `normalize_temp/spin/soil`, `cycle_entry`, `default_for`); `entry.runtime_data.client/devices`; `service.async_get_device_and_config_entry`.
- Produces: `async_setup_services(hass)`; `SERVICE_SEND_WASHER_CYCLE = "send_washer_cycle"`; behavior contract asserted in Task 5 (order: setCourse → setWasherCycle → soil/spin/temp → dispensers → delay; skip-if-absent; strict errors).

- [ ] **Step 1: Write the implementation**

```python
"""Services for the SmartThings integration."""

import probatio

from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, service

from . import SmartThingsConfigEntry
from .const import DOMAIN, MAIN
from .washer_cycle import (
    cycle_entry,
    default_for,
    normalize_cycle,
    normalize_soil,
    normalize_spin,
    normalize_temp,
)

SERVICE_SEND_WASHER_CYCLE = "send_washer_cycle"

SCHEMA_SEND_WASHER_CYCLE = probatio.Schema(
    {
        probatio.Required(ATTR_DEVICE_ID): cv.string,
        probatio.Required("cycle"): cv.string,
        probatio.Optional("water_temperature"): cv.string,
        probatio.Optional("spin_level"): cv.string,
        probatio.Optional("soil_level"): cv.string,
        probatio.Optional("detergent_amount"): cv.string,
        probatio.Optional("detergent_density"): cv.string,
        probatio.Optional("softener_amount"): cv.string,
        probatio.Optional("softener_density"): cv.string,
        probatio.Optional("delay_end"): cv.positive_int,
    },
    extra=probatio.ALLOW_EXTRA,
)
```

Handler logic (same file, below the schema):

```python
def _resolve_device(hass: HomeAssistant, device_id: str):
    device_entry, config_entry = service.async_get_device_and_config_entry(
        hass, DOMAIN, device_id
    )
    entry: SmartThingsConfigEntry = config_entry
    st_device_id = next(
        identifier[1]
        for identifier in device_entry.identifiers
        if identifier[0] == DOMAIN
    )
    full = entry.runtime_data.devices.get(st_device_id)
    if full is None:
        raise ServiceValidationError(f"Washer device not found: {device_id}")
    return entry, full


def _status(full, capability, attribute):
    return full.status[MAIN][capability][attribute].value


async def async_send_washer_cycle(call: ServiceCall) -> None:
    """Send a washer cycle bundle (app-like, no Smart Control needed except delay)."""
    from pysmartthings import Attribute, Capability, Command

    data = call.data
    entry, full = _resolve_device(call.hass, data[ATTR_DEVICE_ID])
    client = entry.runtime_data.client
    st_device_id = full.device.device_id
    status_main = full.status[MAIN]

    try:
        code = normalize_cycle(data["cycle"])
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err

    if Capability.CUSTOM_SUPPORTED_OPTIONS not in status_main:
        raise ServiceValidationError("Washer does not support course selection")
    supported_courses = _status(
        full, Capability.CUSTOM_SUPPORTED_OPTIONS, Attribute.SUPPORTED_COURSES
    )
    if code not in [str(c).upper() for c in (supported_courses or [])]:
        raise ServiceValidationError(f"Unsupported cycle for this washer: {code}")
    ref = status_main.get(Capability.CUSTOM_SUPPORTED_OPTIONS, {}).get(
        Attribute.REFERENCE_TABLE
    )
    table_id = ref.value.get("id") if ref is not None and ref.value else None
    if table_id is not None and table_id != "Table_02":
        raise ServiceValidationError(
            f"Unsupported washer reference table {table_id}; file diagnostics "
            "including custom.supportedOptions and samsungce.washerCycle status"
        )

    if Capability.SAMSUNG_CE_WASHER_CYCLE not in status_main:
        raise ServiceValidationError("Washer does not expose samsungce.washerCycle")
    raw_cycles = _status(
        full, Capability.SAMSUNG_CE_WASHER_CYCLE, Attribute.SUPPORTED_CYCLES
    )
    entry_node = cycle_entry(raw_cycles or [], code)
    if entry_node is None:
        raise ServiceValidationError(f"Unsupported cycle for this washer: {code}")

    def per_cycle_options(option: str) -> list[str] | None:
        node = (entry_node.get("supportedOptions", {}) or {}).get(option)
        if not isinstance(node, dict):
            return None
        return [str(o) for o in (node.get("options") or [])]

    async def run(capability, command, argument) -> None:
        await client.execute_device_command(
            st_device_id, capability, command, MAIN, argument=argument
        )

    await run(
        Capability.CUSTOM_SUPPORTED_OPTIONS, Command.SET_COURSE, code
    )
    await run(
        Capability.SAMSUNG_CE_WASHER_CYCLE,
        Command.SET_WASHER_CYCLE,
        f"Table_02_Course_{code}",
    )

    for field, option, normalizer, capability, command in (
        ("soil_level", "soilLevel", normalize_soil,
         Capability.CUSTOM_WASHER_SOIL_LEVEL, Command.SET_WASHER_SOIL_LEVEL),
        ("spin_level", "spinLevel", normalize_spin,
         Capability.CUSTOM_WASHER_SPIN_LEVEL, Command.SET_WASHER_SPIN_LEVEL),
        ("water_temperature", "waterTemperature", normalize_temp,
         Capability.CUSTOM_WASHER_WATER_TEMPERATURE,
         Command.SET_WASHER_WATER_TEMPERATURE),
    ):
        raw = data.get(field)
        if raw is None:
            value = default_for(entry_node, option)
            if value is None:
                continue
        else:
            try:
                value = normalizer(str(raw))
            except ValueError as err:
                raise ServiceValidationError(str(err)) from err
            allowed = per_cycle_options(option)
            if allowed is not None and value not in allowed:
                raise ServiceValidationError(
                    f"Unsupported {field} {value} for cycle {code}"
                )
        if capability not in status_main:
            continue
        await run(capability, command, value)

    for amount_field, density_field, disp_cap in (
        ("detergent_amount", "detergent_density",
         Capability.SAMSUNG_CE_AUTO_DISPENSE_DETERGENT),
        ("softener_amount", "softener_density",
         Capability.SAMSUNG_CE_AUTO_DISPENSE_SOFTENER),
    ):
        if data.get(amount_field) is not None:
            if disp_cap not in status_main:
                raise ServiceValidationError("Dispenser not supported by this washer")
            supported = status_main[disp_cap][Attribute.SUPPORTED_AMOUNT].value or []
            if str(data[amount_field]) not in [str(v) for v in supported]:
                raise ServiceValidationError(
                    f"Unsupported {amount_field} {data[amount_field]} for this washer"
                )
            await run(disp_cap, Command.SET_AMOUNT, str(data[amount_field]))
        if data.get(density_field) is not None:
            if disp_cap not in status_main:
                raise ServiceValidationError("Dispenser not supported by this washer")
            supported = status_main[disp_cap][Attribute.SUPPORTED_DENSITY].value or []
            if str(data[density_field]) not in [str(v) for v in supported]:
                raise ServiceValidationError(
                    f"Unsupported {density_field} {data[density_field]} for this washer"
                )
            await run(disp_cap, Command.SET_DENSITY, str(data[density_field]))

    if data.get("delay_end") is not None:
        # Fail-closed when the capability is present; fail-open (no gate) when
        # the device does not expose remoteControlStatus at all.
        if (
            Capability.REMOTE_CONTROL_STATUS in status_main
            and _status(
                full,
                Capability.REMOTE_CONTROL_STATUS,
                Attribute.REMOTE_CONTROL_ENABLED,
            )
            == "false"
        ):
            raise ServiceValidationError(
                "Delay end can only be used when remote control is enabled"
            )
        delay = int(data["delay_end"])
        minimum = 0
        if Capability.SAMSUNG_CE_WASHER_DELAY_END in status_main:
            minimum = (
                _status(
                    full,
                    Capability.SAMSUNG_CE_WASHER_DELAY_END,
                    Attribute.MINIMUM_RESERVABLE_TIME,
                )
                or 0
            )
        if delay < int(minimum) or delay > 1440:
            raise ServiceValidationError(
                f"Delay end must be between {minimum} and 1440 minutes"
            )
        await run(
            Capability.SAMSUNG_CE_WASHER_DELAY_END, Command.SET_DELAY_TIME, delay
        )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Set up SmartThings services (idempotent across config entries)."""
    if hass.services.has_service(DOMAIN, SERVICE_SEND_WASHER_CYCLE):
        return
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_WASHER_CYCLE,
        async_send_washer_cycle,
        schema=SCHEMA_SEND_WASHER_CYCLE,
    )
```

- [ ] **Step 2: Register in `__init__.py`**

In `homeassistant/components/smartthings/__init__.py`, add the import next to the other `.const` import:

```python
from .services import async_setup_services
```

and call it at the end of `async_setup_entry` before `return True` (after `async_forward_entry_setups`):

```python
async_setup_services(hass)
```

- [ ] **Step 3: Syntax-check the new module**

Run: `uv run --no-sync python -c "import ast; ast.parse(open('homeassistant/components/smartthings/services.py').read()); ast.parse(open('homeassistant/components/smartthings/washer_cycle.py').read()); print('syntax ok')"`
Expected: `syntax ok`

---

### Task 4: Sound switch

**Files:**
- Modify: `homeassistant/components/smartthings/switch.py`
- Modify: `homeassistant/components/smartthings/strings.json`, `icons.json`
- Test: existing `tests/components/smartthings/test_switch.py` + new snapshot for `switch.washer_sound`

**Interfaces:**
- Consumes: `Capability.SAMSUNG_CE_AUDIO_VOLUME_LEVEL`, `Attribute.VOLUME_LEVEL`, `Command.SET_VOLUME_LEVEL`.
- Produces: config switch `sound` with `is_on = volume == 1`, `turn_on → setVolumeLevel(1)`, `turn_off → setVolumeLevel(0)`.

- [ ] **Step 1: Widen key types + add the description + creation gate**

`SmartThingsCommandSwitchEntityDescription` currently types
`on_key: str | bool` / `off_key: str | bool` (`switch.py:44-60`), which rejects
the int args `setVolumeLevel` needs. First widen both to `str | bool | int`,
then extend `CAPABILITY_TO_COMMAND_SWITCHES` with:

```python
Capability.SAMSUNG_CE_AUDIO_VOLUME_LEVEL: SmartThingsCommandSwitchEntityDescription(
    key=Capability.SAMSUNG_CE_AUDIO_VOLUME_LEVEL,
    translation_key="sound",
    status_attribute=Attribute.VOLUME_LEVEL,
    command=Command.SET_VOLUME_LEVEL,
    entity_category=EntityCategory.CONFIG,
    on_key=1,
    off_key=0,
),
```

The existing `async_setup_entry` comprehension over `CAPABILITY_TO_COMMAND_SWITCHES` picks it up automatically when the capability is in `device.status[MAIN]` — no other code change. Resulting unique_id:
`{device_id}_main_samsungce.audioVolumeLevel_volumeLevel_volumeLevel`.
`SmartThingsCommandSwitch._current_state` returns the raw int; `is_on` compares
against `on_key=1`.

- [ ] **Step 2: Add strings + icon**

In `strings.json` under `entity.switch`, add:

```json
"sound": { "name": "Sound" }
```

In `icons.json` under `entity.switch` (match existing pattern), add:

```json
"sound": { "default": "mdi:volume-high" }
```

- [ ] **Step 3: Verify switch entity appears for the live-shape status**

Run: `uv run --no-sync pytest tests/components/smartthings/test_switch.py -q`
Expected: PASS (snapshot for existing switches unchanged; new Sound snapshot added in Task 5 if the fixture contains `audioVolumeLevel`).

---

### Task 5: Service tests + US Table_02 fixture

**Files:**
- Create: `tests/components/smartthings/test_services.py`
- Create: `tests/components/smartthings/fixtures/device_status/washer_us_table02.json`
- Test: the new test file itself

**Interfaces:**
- Consumes: Tasks 1–3 (helpers + service handler).
- Produces: regression net for command order, defaults, strict errors, delay gate, raw-code fallback.

- [ ] **Step 1: Create the fixture from live diagnostics**

Source: an anonymized device diagnostics dump for Washer captured 2026-09-23 —
`data.status.components.main` subtree. Extract with:

```bash
python3 -c "
import json
src = json.load(open('/tmp/washer_diagnostics.json'))
main = src['data']['status']['components']['main']
keep = ['custom.supportedOptions','samsungce.washerCycle','custom.washerSoilLevel','custom.washerSpinLevel','custom.washerWaterTemperature','samsungce.autoDispenseDetergent','samsungce.autoDispenseSoftener','samsungce.washerDelayEnd','remoteControlStatus','samsungce.audioVolumeLevel','custom.disabledCapabilities','samsungce.washerCyclePreset']
print(json.dumps({'main': {k: main[k] for k in keep if k in main}}, indent=2)[:400])
"
```

Required keys in `washer_us_table02.json`: `custom.supportedOptions`
(course `01`, `referenceTable Table_02`, 26 `supportedCourses`
`[01,8C,53,51,5B,57,64,5A,85,54,56,5C,55,66,58,65,59,52,68,67,63,5D,5F,5E,60,F2]`),
`samsungce.washerCycle` (`washerCycle Table_02_Course_01`, full 26-entry
`supportedCycles` with per-cycle `soilLevel/spinLevel/waterTemperature`
defaults+options), `custom.washerSoilLevel/SpinLevel/WaterTemperature`,
`samsungce.autoDispenseDetergent/Softener` (`supportedAmount/Density`),
`samsungce.washerDelayEnd` (`minimumReservableTime 45`),
`remoteControlStatus` (`false`), `samsungce.audioVolumeLevel` (range 0-1),
`samsungce.washerCyclePreset` (F2 Little Extra), `custom.disabledCapabilities`.
Do not reuse `da_wm_wm_01011` (EU 24-code set).

- [ ] **Step 2: Write the failing tests**

TDD note: write this whole test file BEFORE implementing Task 3. To follow
test-first order, execute Step 2 + Step 3 (expect FAIL) before Task 3 Step 1,
then return to Task 3 and continue sequentially.

```python
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.components.smartthings import DOMAIN
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError


@pytest.fixture
def mock_washer_service(
    hass: HomeAssistant, mock_smartthings: AsyncMock
) -> tuple[AsyncMock, str]:
    """Register the service with a US Table_02 washer; remote=false."""
    from homeassistant.components.smartthings.services import async_setup_services
    from homeassistant.helpers import device_registry as dr

    from . import get_device_status

    async_setup_services(hass)
    client = mock_smartthings
    client.execute_device_command = AsyncMock()
    status = get_device_status("washer_us_table02").components
    full = MagicMock()
    full.device.device_id = "washer-us-01"
    full.status = {"main": status["main"]}
    config_entry = MagicMock()
    config_entry.runtime_data.devices = {"washer-us-01": full}
    config_entry.runtime_data.client = client
    device_entry = MagicMock()
    device_entry.identifiers = {(DOMAIN, "washer-us-01")}
    patcher = patch(
        "homeassistant.helpers.service.async_get_device_and_config_entry",
        return_value=(device_entry, config_entry),
    )
    patcher.start()
    registry = dr.async_get(hass)
    ha_device = registry.async_get_or_create(
        config_entry_id="test-entry", identifiers={(DOMAIN, "washer-us-01")}
    )
    yield client, ha_device.id
    patcher.stop()


async def test_send_defaults_and_order(
    hass: HomeAssistant, mock_washer_service: tuple[AsyncMock, str]
) -> None:
    """Cycle 01 with no overrides sends course, washerCycle, then per-cycle defaults."""
    client, device_id = mock_washer_service
    await hass.services.async_call(
        DOMAIN,
        "send_washer_cycle",
        {ATTR_DEVICE_ID: device_id, "cycle": "Normal"},
        blocking=True,
    )
    calls = client.execute_device_command.await_args_list
    assert [c.args[2] for c in calls[:2]] == ["setCourse", "setWasherCycle"]
    assert calls[0].kwargs["argument"] == "01"
    assert calls[1].kwargs["argument"] == "Table_02_Course_01"
    arguments = [c.kwargs.get("argument") for c in calls]
    assert "normal" in arguments  # soil default
    assert "high" in arguments  # spin default
    assert "warm" in arguments  # temp default


async def test_send_rejects_unsupported_spin_for_cycle(
    hass: HomeAssistant, mock_washer_service: tuple[AsyncMock, str]
) -> None:
    """extraLow is not in cycle 01 options, so it must fail fast."""
    _, device_id = mock_washer_service
    with pytest.raises(ServiceValidationError, match="Unsupported spin_level"):
        await hass.services.async_call(
            DOMAIN,
            "send_washer_cycle",
            {
                ATTR_DEVICE_ID: device_id,
                "cycle": "Normal",
                "spin_level": "extraLow",
            },
            blocking=True,
        )


async def test_send_delay_requires_remote(
    hass: HomeAssistant, mock_washer_service: tuple[AsyncMock, str]
) -> None:
    """Delay without remote control fails; the fixture has remote=false."""
    _, device_id = mock_washer_service
    with pytest.raises(ServiceValidationError, match="remote control"):
        await hass.services.async_call(
            DOMAIN,
            "send_washer_cycle",
            {
                ATTR_DEVICE_ID: device_id,
                "cycle": "Normal",
                "delay_end": 120,
            },
            blocking=True,
        )


async def test_send_accepts_raw_code(
    hass: HomeAssistant, mock_washer_service: tuple[AsyncMock, str]
) -> None:
    """Raw 2-char codes (e.g. 53) bypass the friendly table but still validate."""
    client, device_id = mock_washer_service
    await hass.services.async_call(
        DOMAIN,
        "send_washer_cycle",
        {ATTR_DEVICE_ID: device_id, "cycle": "53"},
        blocking=True,
    )
    first = client.execute_device_command.await_args_list[0]
    assert first.args[2] == "setCourse"
    assert first.kwargs["argument"] == "53"


async def test_sound_switch_exists(
    hass: HomeAssistant, mock_washer_service: tuple[AsyncMock, str]
) -> None:
    """US fixture contains audioVolumeLevel, so switch.washer_sound must exist."""
    assert hass.states.get("switch.washer_sound") is not None
```

- [ ] **Step 3: Run tests to verify they fail (before Task 3)**

Run: `uv run --no-sync pytest tests/components/smartthings/test_services.py -v`
Expected: FAIL (no `services.py` / `washer_us_table02` fixture yet). Implement
Tasks 2–4 next, then re-run for PASS.

- [ ] **Step 4: Run the full SmartThings suite for regressions**

Run: `uv run --no-sync pytest tests/components/smartthings/test_services.py tests/components/smartthings/test_switch.py tests/components/smartthings/test_select.py -q`
Expected: PASS

---

### Task 6: Live verification protocol (manual, user-confirmed only)

**Files:** none (procedure only).

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces: verified friendly→code rows + order confirmation recorded back into `washer_cycle.py` + spec.

- [ ] **Step 1: Read-only compare (no state change)**

In HA Developer Tools → Services, read (do not call): compare app `Cycle Normal` vs `sensor.washer_washer_machine_state`, `custom.supportedOptions.course` (`01`), `samsungce.washerCycle.washerCycle` (`Table_02_Course_01`). Record matches.

- [ ] **Step 2: Send Normal defaults (washer On, Smart Control off, user confirms)**

Call `smartthings.send_washer_cycle` with `device_id` of Washer and `cycle: Normal` only. Expected: washer shows Normal/Warm/High/Normal; no error; `course` stays `01`.

- [ ] **Step 3: Send Normal + overrides (user confirms)**

Call with `cycle: Normal, water_temperature: Hot, spin_level: Level 5 (Extra high), soil_level: Level 4 (Heavy)`. Expected: three `custom.*` attributes update; unsupported combo (e.g. `extraLow` on 01) returns "Unsupported spin_level".

- [ ] **Step 4: Delay gate (user confirms)**

Call with `delay_end: 120` while remote off → expect "Delay end can only be used when remote control is enabled". Enable Smart Control, retry → expect accepted.

- [ ] **Step 5: Sound toggle (user confirms)**

Toggle `switch.washer_sound` off → expect `audioVolumeLevel.volumeLevel == 0` and app Sound off; on → `1` and app Sound on. Sound is independent of any sent cycle.

- [ ] **Step 6: Command-order check (user confirms, Normal cycle)**

Send `cycle: Normal` and read back both `custom.supportedOptions.course`
(expect `01`) and `samsungce.washerCycle.washerCycle`
(expect `Table_02_Course_01`). If both match, order `setCourse`-then-`setWasherCycle`
is confirmed. If `course` matches but `washerCycle` lags, try the reverse order
once (single-shot experiment, user-confirmed) and record which order converges.

- [ ] **Step 7: Record results in the spec**

Edit `docs/superpowers/specs/2026-09-24-washer-send-to-washer-design.md` §12:
append one row per live-tested cycle (`app name → course code → washerCycle value`)
plus one line stating the observed `setCourse`/`setWasherCycle` order outcome,
and add `homeassistant/components/smartthings/washer_cycle.py` (thin pure helper)
to spec §9's file list.
No code change in this step; code updates repeat Task 1 with the new rows.

---

### Task 7: Translations, hassfest, lint

**Files:**
- Modify: `homeassistant/components/smartthings/translations/en.json` (regenerated)
- Test: hassfest + prek

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces: green static checks.

- [ ] **Step 1: Regenerate translations**

Run: `python3 -m script.translations develop --integration smartthings`
Expected: `translations/en.json` updated with `services.send_washer_cycle` + `switch.sound`.

- [ ] **Step 2: Run lint over touched files**

Run: `uv run --no-sync prek run --all-files`
Expected: PASS (fix issues inline, no new disables).

- [ ] **Step 3: Run the SmartThings test subset once more**

Run: `uv run --no-sync pytest tests/components/smartthings/test_services.py tests/components/smartthings/test_washer_cycle.py tests/components/smartthings/test_switch.py -q`
Expected: PASS

# LIFX Virtual Power State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make normal LIFX light and Device Group turn-off commands visually dim to zero while Home Assistant reports them off and later turn-on restores their intended color.

**Architecture:** A coordinator-owned virtual-off marker and resume HSBK are shared by the physical entity and every Device Group. Hardware power remains authoritative and is refreshed on every normal poll; a new real power-off or visible color clears the marker. Physical lights persist the marker and resume HSBK through `RestoreEntity` extra data, while Groups reuse the same state and keep their current any-member-on display rule.

**Tech Stack:** Home Assistant Python 3.14, aiolifx 1.2.2, LIFX LAN `SetWaveformOptional`, `SetColor`, `SetLightPower`, RestoreEntity, pytest.

## Global Constraints

- Correction: only Device Group power-off creates virtual off; direct physical
  power-off retains normal `SetPower(False)` behavior.
- Apply the behavior to every physical LIFX entity; do not use Group-membership detection.
- Group power-off sends brightness zero; a direct physical whole-light `turn_off`
  sends `SetPower(False)`.
- Keep actual device power from every coordinator refresh; do not persist or trust a stale actual-power value.
- Retain existing physical retry behavior and Group per-stage ACK/retry/preemption behavior.
- A zoned `lifx.set_state` brightness-zero request is a normal segment operation, never whole-light virtual off.

---

### Task 1: Add shared virtual-power coordinator state and persistence

**Files:**
- Modify: `homeassistant/components/lifx/coordinator.py`
- Modify: `homeassistant/components/lifx/light.py`
- Test: `tests/components/lifx/test_light.py`

**Interfaces:**
- Produces `coordinator.virtual_power`: virtual-off marker, resume HSBK, actual-power property, and refresh reconciliation methods.
- Produces `LIFXVirtualPowerExtraStoredData` serialized by each physical light entity.

- [x] **Step 1: Write failing persistence and reconciliation tests**

```python
async def test_virtual_off_restores_after_restart_and_clears_on_external_change(
    hass: HomeAssistant,
) -> None:
    """A persisted virtual-off marker survives restart but external state wins."""
    # Restore virtual_off=True and a nonzero HSBK.
    # Verify b=0 + actual power on keeps it off.
    # Verify a refreshed visible color and real power off each clear virtual_off.
```

- [x] **Step 2: Run the focused test to verify it fails**

Run: `.venv/bin/python -m pytest tests/components/lifx/test_light.py::test_virtual_off_restores_after_restart_and_clears_on_external_change -q`

Expected: FAIL because no virtual-power state or restore data exists.

- [x] **Step 3: Add the coordinator state and physical RestoreEntity bridge**

```python
@dataclass(slots=True)
class LIFXVirtualPowerState:
    virtual_off: bool = False
    resume_hsbk: tuple[int, int, int, int] | None = None

    def reconcile(self, device: Light) -> None:
        if device.power_level == 0 or self._has_visible_color(device):
            self.virtual_off = False
```

Persist `virtual_off` and `resume_hsbk` from `LIFXLight.extra_restore_state_data`; restore them in `async_added_to_hass`. Call reconciliation after each successful coordinator refresh. Retain the resume HSBK when clearing the marker because a real power-off still requires it for the next HSBK → power-on sequence.

- [x] **Step 4: Run the focused test to verify it passes**

Run: `.venv/bin/python -m pytest tests/components/lifx/test_light.py::test_virtual_off_restores_after_restart_and_clears_on_external_change -q`

Expected: PASS.

### Task 2: Route physical whole-light power commands through virtual power

**Files:**
- Modify: `homeassistant/components/lifx/light.py`
- Test: `tests/components/lifx/test_light.py`

**Interfaces:**
- Consumes `coordinator.virtual_power`.
- Produces whole-light virtual-off, resume, and actual-off staging behavior.

- [x] **Step 1: Write failing physical-command tests**

```python
async def test_turn_off_uses_zero_brightness_and_reports_off(...) -> None: ...
async def test_turn_on_restores_hsbk_without_power_when_actually_on(...) -> None: ...
async def test_turn_on_restores_hsbk_then_power_when_actually_off(...) -> None: ...
async def test_zoned_zero_brightness_does_not_mark_whole_light_virtual_off(...) -> None: ...
```

- [x] **Step 2: Run those tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/components/lifx/test_light.py -k 'virtual_off or restores_hsbk or zoned_zero' -q`

Expected: FAIL because normal turn-off still sends `set_power(False)`.

- [x] **Step 3: Implement whole-light virtual-off and resume commands**

```python
if requested_whole_light_off:
    virtual_power.record_off(current_or_requested_hsbk)
    await self.set_color([None, None, 0, None], kwargs, duration=fade)
elif requested_turn_on:
    target = virtual_power.merge_resume_with_request(hsbk)
    await self.set_color(target, kwargs, duration=0 if actual_power_off else fade)
    if actual_power_off:
        await self.set_power(True, duration=fade)
```

Use the stored overall HSBK as the baseline for any command following virtual off. For multizone entities, retain existing zone command behavior; no zone snapshot is added. A bare resume may intentionally apply the stored overall HSBK to all zones after a whole-device virtual off. Do not infer virtual off from a zoned brightness-zero request.

- [x] **Step 4: Run the focused physical tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/components/lifx/test_light.py -k 'virtual_off or restores_hsbk or zoned_zero' -q`

Expected: PASS.

### Task 3: Use shared virtual state in Device Groups and retain staged ACK timing

**Files:**
- Modify: `homeassistant/components/lifx/parallel_group.py`
- Modify: `homeassistant/components/lifx/parallel.py`
- Test: `tests/components/lifx/test_parallel_group.py`
- Test: `tests/components/lifx/test_parallel.py`

**Interfaces:**
- Consumes each member coordinator's virtual state and actual-power property.
- Produces globally aligned Group stages with optional empty member slots.

- [x] **Step 1: Write failing Group tests**

```python
async def test_group_off_marks_members_virtual_off_without_power_packets(...) -> None: ...
async def test_group_turn_on_stages_hsbk_then_power_only_for_actually_off_members(...) -> None: ...
async def test_group_keeps_any_member_virtual_on_display_rule(...) -> None: ...
```

- [x] **Step 2: Run the focused Group tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/components/lifx/test_parallel_group.py -k 'virtual_off or actually_off_members' -q`

Expected: FAIL because Group projection is independent of physical virtual state.

- [x] **Step 3: Implement shared-state Group projection and padded stages**

```python
# Stage 0: only actually-off members receive hidden target HSBK.
# Stage 1: actually-off members receive power-on; actual-on members receive
#          the visible target HSBK at the same shared release deadline.
```

Replace the experimental Group-only brightness-zero helper with shared coordinator state updates. Keep Group `is_on` as `any(member.virtual_is_on)`. Represent an omitted member stage explicitly so the dispatcher waits for hidden-color ACKs before releasing all final visible work at the precise common deadline. Do not send a later stage after an ACK failure or a superseding command.

- [x] **Step 4: Run focused Group/parallel tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/components/lifx/test_parallel.py tests/components/lifx/test_parallel_group.py -q`

Expected: PASS.

### Task 4: Validate the complete integration

**Files:**
- Modify: `tests/components/lifx/test_light.py`
- Modify: `tests/components/lifx/test_parallel.py`
- Modify: `tests/components/lifx/test_parallel_group.py`

- [x] **Step 1: Run full validation on the HA host**

Run: `.venv/bin/python -m pytest tests/components/lifx -q`

Expected: all new virtual-power tests pass; report unrelated failures separately.

- [x] **Step 2: Run static validation**

Run: `.venv/bin/ruff check homeassistant/components/lifx tests/components/lifx && git diff --check`

Expected: no lint or whitespace errors.

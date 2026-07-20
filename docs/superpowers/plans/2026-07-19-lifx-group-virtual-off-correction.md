# LIFX Group-Only Virtual Off Correction Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore normal physical LIFX power-off packets while retaining virtual off exclusively for ACK-confirmed Device Group brightness-zero commands.

**Architecture:** The coordinator-owned virtual-off marker remains persisted because it lets a physical entity display a Group-darkened member as off and restore its saved HSBK. Direct physical `turn_off` never creates that marker: it uses the integration's original `SetPower(False)` path and clears a pre-existing Group marker. Device Groups remain the sole producer, recording virtual off only after the Group transport confirms its brightness-zero command.

**Tech Stack:** Home Assistant Python 3.14, aiolifx, LIFX LAN SetColor/SetLightPower, RestoreEntity, pytest.

## Global Constraints

- Direct physical `light.turn_off` and `lifx.set_state(power: false)` use physical `SetPower(False)`.
- Only Device Group power-off sends brightness zero and records virtual off.
- Retain physical retry behavior and existing Group ACK/retry/preemption timing.
- Retain virtual-off persistence and polling reconciliation for Group-created markers.
- Do not infer a whole-light virtual off from zoned brightness-zero commands.

---

### Task 1: Prove the corrected ownership boundary

**Files:**
- Modify: `tests/components/lifx/test_light.py`
- Modify: `tests/components/lifx/test_parallel_group.py`

**Interfaces:**
- Consumes physical light services and `LIFXParallelGroupRuntime` dispatch mocks.
- Produces regressions that distinguish direct physical off from Group virtual off.

- [ ] **Step 1: Write failing direct physical-off tests**

```python
async def test_direct_physical_power_off_does_not_create_virtual_off(...) -> None:
    await hass.services.async_call(LIGHT_DOMAIN, "turn_off", ..., blocking=True)
    assert bulb.set_power.calls[-1][0][0] is False
    assert not config_entry.runtime_data.virtual_off
```

Cover a direct physical off after a Group-created marker and assert that the marker is cleared while the saved resume HSBK remains available.

- [ ] **Step 2: Write failing Group virtual-off ownership test**

```python
async def test_group_power_off_marks_member_virtual_off_after_dispatch(...) -> None:
    await runtime.async_set_state(power=False)
    assert members[0].virtual_off
    assert members[0].async_record_virtual_off.call_count == 1
```

- [ ] **Step 3: Run focused tests and verify the direct physical assertion fails**

Run: `.venv/bin/python -m pytest tests/components/lifx/test_light.py -k 'direct_physical_power_off or virtual_power' tests/components/lifx/test_parallel_group.py -k virtual_off -q`

Expected: the direct physical test fails because current code emits a zero-brightness waveform and sets `virtual_off`.

### Task 2: Restore physical commands without removing Group virtual state

**Files:**
- Modify: `homeassistant/components/lifx/coordinator.py`
- Modify: `homeassistant/components/lifx/light.py`

**Interfaces:**
- Add `LIFXUpdateCoordinator.async_clear_virtual_off()` that clears only the marker and retains `resume_hsbk`.
- Keep `async_record_virtual_off()` callable only by Group runtime.

- [ ] **Step 1: Add the explicit marker-clear operation**

```python
@callback
def async_clear_virtual_off(self) -> None:
    """Clear a superseded Group-created virtual-off marker."""
    self.virtual_off = False
```

- [ ] **Step 2: Restore the original direct physical power-off branch**

Remove the current top-level `if power_off` brightness-zero branch from `LIFXLight.set_state`. Before taking the original direct physical off branch, call `async_clear_virtual_off()`. Preserve the original `SetPower(False)` duration behavior exactly.

- [ ] **Step 3: Preserve only consumer behavior for a Group marker**

Keep the `elif self.coordinator.virtual_off` turn-on/color branch: use saved HSBK when actual power is on, and HSBK followed by `SetPower(True)` when a poll says actual power is off. Do not create the marker from a direct physical command.

- [ ] **Step 4: Restore direct physical expectations in the broad light tests**

Revert the direct physical off/on assertions changed by the prior implementation in `tests/components/lifx/test_light.py`. Keep new regressions that are specifically Group-marker consumers; delete the test that asserts a direct physical power-off uses brightness zero.

- [ ] **Step 5: Run focused physical tests**

Run: `.venv/bin/python -m pytest tests/components/lifx/test_light.py -k 'physical_power_off or transitions or lifx_set_state' -q`

Expected: PASS.

### Task 3: Keep Group production and multizone semantics intact

**Files:**
- Modify: `homeassistant/components/lifx/parallel_group.py` only if test evidence shows it needs adjustment.
- Modify: `tests/components/lifx/test_parallel_group.py`
- Modify: `tests/components/lifx/test_light.py`

**Interfaces:**
- Group uses `async_record_virtual_off()` only after successful ACK-confirmed dispatch.
- Direct multizone services retain their physical power and zone behavior.

- [ ] **Step 1: Verify Group dispatch remains the sole marker producer**

Assert that Group power-off uses a color packet with brightness zero, does not build a `power=False` packet, and calls `async_record_virtual_off()` only after `async_dispatch()` succeeds.

- [ ] **Step 2: Verify direct multizone whole-light off uses SetPower(False)**

Cover both legacy and extended multizone entities. Retain the existing direct zoned-command behavior, including temporary power use when an actually-off legacy device needs zone data.

- [ ] **Step 3: Run Group and multizone tests**

Run: `.venv/bin/python -m pytest tests/components/lifx/test_parallel.py tests/components/lifx/test_parallel_group.py tests/components/lifx/test_light.py -k 'light_strip or extended_multizone or virtual_off' -q`

Expected: PASS.

### Task 4: Validate the corrected implementation

**Files:**
- Modify: `docs/superpowers/plans/2026-07-19-lifx-virtual-power-state.md`

- [ ] **Step 1: Mark the superseded physical-off assumption as incorrect**

Add a short correction note to the earlier plan: only Groups create virtual off; physical direct power-off remains physical.

- [ ] **Step 2: Run full HA-host validation**

Run: `.venv/bin/python -m pytest tests/components/lifx -q`

Expected: new ownership tests pass; report unrelated existing failures separately.

- [ ] **Step 3: Run static validation**

Run: `.venv/bin/ruff check homeassistant/components/lifx tests/components/lifx && git diff --check`

Expected: PASS.

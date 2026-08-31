# LIFX Revert Virtual Power Off — Design

**Goal:** Remove the "virtual power off" feature (setting brightness to 0 and
treating brightness 0 as off) from the LIFX integration, restore real power
commands for turn-off, and introduce a 3-stage no-flash group turn-on-from-off
sequence. All other logic (staged-ACK dispatch, cross-fade transitions,
keepalive, availability, preemption) continues to work as-is.

**Type:** Surgical revert + one refinement. This is **not** a `git revert`:
commit `44f6d8f7dcc` ("virtual power off") was followed by `b795374807c`
("bug fixes and improvements", the cross-fade work) which built `_default_transition_duration`
and `transition_cross_duration` on top of it. Those later changes must be
preserved while the virtual-off pieces are removed.

## Current state being removed

The virtual-off feature consists of these pieces, all to be removed:

- **`coordinator.py`** — `virtual_off` / `resume_hsbk` fields; `actual_power_on`
  and `display_color` properties; `async_record_virtual_off`, `async_record_virtual_on`,
  `async_clear_virtual_off`, `async_reconcile_virtual_power`; the reconcile call
  at the end of `_async_update_data`.
- **`light.py`** — `LIFXVirtualPowerStoredData` (and its `asdict`/`dataclass`
  /`ExtraStoredData`/`RestoreEntity` imports); `RestoreEntity` base on
  `LIFXLight`; `brightness` returning 0 while `virtual_off`; `is_on` gating on
  `virtual_off`; the `physical_power_off` / `virtual_off` branches and the
  resume-HSBK merge in `set_state`; the `physical_power_off` and `virtual_off`
  terms in `_default_transition_duration`; the restore block in
  `async_added_to_hass`; `extra_restore_state_data`.
- **`parallel_group.py`** — the virtual projection in `member_states`; the
  `virtual_off` parameter and `async_record_virtual_off/on` calls in
  `_async_dispatch_projected_states`.
- **`parallel.py`** — the `pad_before` field and the per-stage `None` skip in
  the dispatcher (added only for the mixed physically-on/off member case).

## Target behavior

### Turn off — real power-off (physical and group)

- **Physical light:** already sends `SetPower(False)`; unchanged once the
  virtual branches are removed.
- **Group:** the brightness-0 `color` packet is replaced by a single
  `ParallelCommand("power", (False, duration))` per member — one ACK'd
  `SetPower(False)`.

### Group turn-on-from-off — 3-stage, always

Applied uniformly to every member (once virtual-off is gone, "group off" means
all members are physically off, so there is no mixed-member case). Runs for
*every* turn-on-from-off, including a bare `turn_on` with no color/brightness.

```
Stage 1  SetColor(h, s, k, brightness=0)   duration 0   → ACK  (prime color while dark)
Stage 2  SetPower(True)                    duration 0   → ACK  (turn on dark)
Stage 3  SetColor(h, s, k, brightness=req) duration on  → ACK  (fade to actual values)
```

Built as a nested `ParallelCommand`:
`color(prime) → power(True) → color(apply)`, reusing the existing
`ParallelCommand.second` staged-ACK machinery — no new dispatcher work.

Duration mapping: stages 1 and 2 are immediate (`duration 0`) because the bulb
stays dark through them; the **visible** transition is stage 3 (apply), which
uses the on transition (`_transition_ms(member, "on", kwargs)` — Fade On Time,
or an explicit `transition` argument). This makes the whole wake-up a single
clean fade-in from black to the target color.

### Color baseline for a bare turn-on

`target_color` keeps the existing "precise double calls" baseline — the group's
aggregate `display_state.color` (mean across members, or the optimistic
projection). `target_color = color or display_state.color`, unchanged. For a
bare `turn_on`, `target_color = display_state.color` (the group's last known
color), so prime and apply both use it. No new per-member color source is
introduced.

### Physical light turn-on-from-off — unchanged (2-stage)

`set_color` then `set_power(True)` as today. The 3-stage sequence is group-only.

### Transition duration (cross-fade preserved)

`_default_transition_duration` drops the `physical_power_off` parameter (a
redundant alias of `power_off`) and the `virtual_off` term, keeping the
cross-fade classification. The `power_off` argument preserves the "turn off
with a color change → Fade Off Time" case that would otherwise be misread as
cross:

```python
def _default_transition_duration(self, power_on, power_off, hsbk):
    if power_off:
        return self.coordinator.transition_off_duration
    if power_on:
        return self.coordinator.transition_on_duration
    if hsbk:
        return self.coordinator.transition_cross_duration
    return self.coordinator.transition_on_duration  # no-op fallback
```

The `physical_power_off` local in `set_state` is removed along with its uses.

## Per-file change summary

| File | Change |
|---|---|
| `coordinator.py` | Delete all virtual-off state, properties, helpers, and the reconcile call. |
| `light.py` | Delete stored-data class + imports + `RestoreEntity`; revert `brightness`, `is_on`, `set_state`, `_default_transition_duration`, `async_added_to_hass`, `extra_restore_state_data`, and the multizone `transform` power check. |
| `parallel_group.py` | Revert `member_states` to physical projection; replace group turn-off with a `power` command; replace turn-on-from-off with the 3-stage nested command; drop the `virtual_off` param/record calls. |
| `parallel.py` | Remove `pad_before` and the per-stage `None` skip in `_command_summary` and the dispatcher. |

## Preserved as-is (non-goals)

- Staged-ACK dispatch (`ParallelCommand.second`, per-stage ACK, retries, preemption).
- Cross-fade transitions (`transition_cross_duration`).
- Optimistic group projection (`_begin_projection` / `_clear_projection` / `_optimistic_state`).
- Keepalive, availability, recovery, and member-listener logic.
- All non-power command paths (effects, themes, colorloop, identify, restart).

## Testing strategy

Update the existing virtual-off tests and add regressions for the new behavior:

- **Physical light** (`test_light.py`): direct `turn_off` sends `SetPower(False)`;
  `is_on`/`brightness` reflect real power only; no virtual-off state remains.
  Delete the virtual-off-specific tests (e.g. `test_direct_physical_power_off_does_not_create_virtual_off`).
- **Group** (`test_parallel_group.py`): turn-off builds a `power` command
  (not brightness-0 `color`); turn-on-from-off builds the 3-stage
  `color(prime b=0) → power → color(apply)` for every member, including a bare
  `turn_on`; `member_states` reflects physical state only. Delete/replace the
  virtual-off tests (`test_displayed_on_state_sends_one_virtual_off_color_for_every_member`,
  `test_group_power_off_sends_brightness_zero_but_displays_off`).
- **`parallel.py`** (`test_parallel.py`): remove `pad_before` coverage if any;
  confirm staged traversal still works for the 3-stage command.

Run `.venv/bin/python -m pytest tests/components/lifx -q` and
`.venv/bin/ruff check homeassistant/components/lifx tests/components/lifx`
(plus `git diff --check`) on the HA host.

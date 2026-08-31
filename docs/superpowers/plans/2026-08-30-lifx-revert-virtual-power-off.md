# LIFX Revert Virtual Power Off Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the "virtual power off" feature (brightness-0-as-off) from LIFX, restore real `SetPower(False)` for turn-off, and introduce a 3-stage no-flash group turn-on-from-off.

**Architecture:** Surgical removal of `virtual_off`/`resume_hsbk` state across `coordinator.py`, `light.py`, `parallel_group.py`, and `parallel.py`, preserving the staged-ACK dispatch (`ParallelCommand.second`) and cross-fade transition logic. Group turn-on-from-off becomes a nested 3-stage command (prime color with brightness 0 → power on → apply color).

**Tech Stack:** Home Assistant Python 3.14, aiolifx, LIFX LAN SetColor/SetLightPower, pytest.

## Global Constraints

- **No git commits** — the user explicitly requested "no commits" for this work. Omit every commit step; leave the working tree for the user to commit.
- Run tests with `uv run --no-sync pytest` (see `AGENTS.md`).
- Lint/format with `uv run --no-sync ruff check homeassistant/components/lifx tests/components/lifx` and `uv run --no-sync prek run --all-files` for the final pass.
- Preserve as-is: staged-ACK dispatch (`ParallelCommand.second`, per-stage ACK, retries, preemption), cross-fade `transition_cross_duration`, optimistic group projection, keepalive/availability/recovery, and all non-power command paths (effects, themes, colorloop, identify, restart).
- The `if not targets: continue` guard in the dispatcher stays (it also covers the failed-members path); only `pad_before` and the per-stage `None` check are removed.

---

### Task 1: Revert the group's virtual-off command surface

**Files:**
- Modify: `homeassistant/components/lifx/parallel_group.py:244-257,790-872`
- Test: `tests/components/lifx/test_parallel_group.py:31-51,409-592`

**Interfaces:**
- Consumes: `LIFXUpdateCoordinator` (its `device.color`, `device.power_level`, `async_set_updated_data`), `ParallelCommand(kind, payload, second=None)`.
- Produces: group turn-off as `ParallelCommand("power", (False, duration))`; group turn-on-from-off as a nested 3-stage `ParallelCommand("color", prime, ParallelCommand("power", (True, 0), ParallelCommand("color", apply)))`; `member_states` projects physical state only.

- [ ] **Step 1: Remove the virtual-off mock setup**

In `tests/components/lifx/test_parallel_group.py`, delete lines 40-49 from `_member` (the `virtual_off`, `resume_hsbk`, and `async_record_virtual_off`/`async_record_virtual_on` mocks). The function becomes:

```python
def _member(ip_address: str, *, last_update_success: bool = True) -> MagicMock:
    """Create a physical coordinator stand-in with an independent device cache."""
    coordinator = MagicMock(spec=LIFXUpdateCoordinator)
    coordinator.device = _mocked_bulb()
    coordinator.device.ip_addr = ip_address
    coordinator.last_update_success = last_update_success
    coordinator.transition_on_duration = 0
    coordinator.transition_off_duration = 0
    coordinator.transition_cross_duration = 0
    coordinator.async_schedule_post_command_refresh = AsyncMock()
    return coordinator
```

- [ ] **Step 2: Rewrite the group turn-on/turn-off tests to the new behavior**

In `tests/components/lifx/test_parallel_group.py`, rewrite the group turn-on/turn-off tests (four replacements and one deletion).

Replace `test_turning_on_an_off_member_with_color_uses_staged_commands` (lines 409-422) with:

```python
async def test_turning_on_an_off_member_with_color_uses_three_staged_commands(
    hass: HomeAssistant,
) -> None:
    """An off member primes a hidden color, powers on, then applies the color."""
    runtime, members = _runtime(hass)
    members[0].device.power_level = 0
    members[1].device.power_level = 0

    await runtime.async_set_state(power=True, brightness=255)

    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.kind == "color" for command in commands)
    assert all(command.payload[2] == 0 for command in commands)
    assert all(command.second is not None for command in commands)
    assert all(command.second.kind == "power" for command in commands)
    assert all(command.second.payload == (True, 0) for command in commands)
    assert all(command.second.second is not None for command in commands)
    assert all(command.second.second.kind == "color" for command in commands)
    assert all(command.second.second.payload[2] == 65535 for command in commands)
```

Replace `test_displayed_off_state_stages_color_and_power_for_every_member` (lines 425-449) with:

```python
async def test_displayed_off_state_stages_the_same_three_commands_for_every_member(
    hass: HomeAssistant,
) -> None:
    """A displayed-off target stages identically despite mixed member power caches."""
    runtime, members = _runtime(hass)
    members[0].device.power_level = 65535
    members[1].device.power_level = 0
    members[0].device.color = [1, 2, 3, 3500]
    members[1].device.color = [4, 5, 6, 4000]
    runtime._begin_projection(
        (
            _MemberCommandState((100, 200, 300, 3500), 0),
            _MemberCommandState((100, 200, 300, 3500), 0),
        )
    )

    await runtime.async_set_state(power=True, brightness=255)

    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.kind == "color" for command in commands)
    assert all(command.payload[2] == 0 for command in commands)
    assert all(command.second is not None for command in commands)
    assert all(command.second.kind == "power" for command in commands)
    assert all(command.second.second is not None for command in commands)
    assert all(command.second.second.kind == "color" for command in commands)
    assert commands[0].payload == commands[1].payload
```

Add a bare-`turn_on` coverage test (no color/brightness) after the replaced turn-on tests:

```python
async def test_bare_turn_on_from_off_uses_the_three_stage_sequence(
    hass: HomeAssistant,
) -> None:
    """A bare turn_on primes the last color, powers on, then applies it."""
    runtime, members = _runtime(hass)
    members[0].device.power_level = 0
    members[1].device.power_level = 0
    members[0].device.color = [100, 200, 300, 3500]
    members[1].device.color = [100, 200, 300, 3500]

    await runtime.async_set_state(power=True)

    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.kind == "color" for command in commands)
    assert all(command.payload[2] == 0 for command in commands)
    assert all(command.second is not None for command in commands)
    assert all(command.second.kind == "power" for command in commands)
    assert all(command.second.second is not None for command in commands)
    assert all(command.second.second.kind == "color" for command in commands)
    assert all(command.second.second.payload[2] == 300 for command in commands)
```

Delete `test_displayed_on_state_sends_one_virtual_off_color_for_every_member` (lines 469-488) entirely.

Replace `test_group_power_off_sends_brightness_zero_but_displays_off` (lines 507-525) with:

```python
async def test_group_power_off_sends_a_power_command_and_displays_off(
    hass: HomeAssistant,
) -> None:
    """A group turn-off sends SetPower(False) and the group displays off."""
    runtime, members = _runtime(hass)
    for member in members:
        member.device.color = [100, 200, 300, 3500]
        member.device.power_level = 65535

    await runtime.async_set_state(power=False)

    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.kind == "power" for command in commands)
    assert all(command.payload == (False, 0) for command in commands)
    assert all(command.second is None for command in commands)
    assert runtime.display_state.color == (100, 200, 300, 3500)
    assert runtime.display_state.is_on is False
```

Replace `test_turning_off_with_a_transition_keeps_the_requested_color_hidden` (lines 577-592) with:

```python
async def test_turning_off_with_a_transition_uses_the_power_transition(
    hass: HomeAssistant,
) -> None:
    """A color-plus-off request turns off with the transition duration."""
    runtime, members = _runtime(hass)
    for member in members:
        member.device.power_level = 0

    await runtime.async_set_state(power=False, brightness=255, transition=1)

    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.kind == "power" for command in commands)
    assert all(command.second is None for command in commands)
    assert all(command.payload == (False, 1000) for command in commands)
    assert runtime.display_state.is_on is False
```

- [ ] **Step 3: Run the group tests to verify they fail**

Run: `uv run --no-sync pytest tests/components/lifx/test_parallel_group.py -q`

Expected: FAIL — the rewritten tests assert a `power` command and a 3-stage turn-on, but the code still emits a brightness-0 `color` command and records virtual-off state; the mock no longer exposes `virtual_off`/`async_record_virtual_off`, so several tests error on the missing attributes.

- [ ] **Step 4: Revert `member_states` to physical projection**

In `parallel_group.py`, replace lines 244-257 with:

```python
    @property
    def member_states(self) -> tuple[_MemberCommandState, ...]:
        """Return physical state for each member."""
        return tuple(
            _MemberCommandState(tuple(member.device.color), member.device.power_level)
            for member in self.members
        )
```

- [ ] **Step 5: Rework `_async_set_state` turn-off and turn-on branches**

In `parallel_group.py`, replace the `if power is False:` / `elif power is True and not display_state.is_on:` branches inside the member loop (lines 814-833) with:

```python
        if power is False:
            commands.append(ParallelCommand("power", (False, duration)))
        elif power is True and not display_state.is_on:
            commands.append(
                ParallelCommand(
                    "color",
                    (*target_color[:2], 0, target_color[3], 0),
                    ParallelCommand(
                        "power",
                        (True, 0),
                        ParallelCommand("color", (*target_color, duration)),
                    ),
                )
            )
```

Leave the `elif color is not None:` / `elif power is not None:` / `else: raise` branches unchanged.

- [ ] **Step 6: Remove the `virtual_off` record path**

In `parallel_group.py`, change the `_async_set_state` dispatch call to drop `virtual_off` (match by symbol; line numbers here are relative to the pre-Step-5 file and shift after Step 5 shrinks the method):

```python
    await self._async_dispatch_projected_states(tuple(commands), tuple(states))
```

Then replace `_async_dispatch_projected_states` (match by method name) with:

```python
    async def _async_dispatch_projected_states(
        self,
        commands: tuple[ParallelCommand, ...],
        states: tuple[_MemberCommandState, ...],
    ) -> bool:
        """Dispatch a command and keep its aggregate projection until polling catches up."""
        generation = self._begin_projection(states)
        try:
            result = await self.parallel.async_dispatch(
                self._commands_for_available_members(commands)
            )
        except HomeAssistantError:
            result = ParallelDispatchResult(ParallelDispatchOutcome.FAILED, 0)
        if result.outcome is not ParallelDispatchOutcome.COMPLETED:
            self._clear_projection(generation)
            self._log_dispatch_outcome("state", result)
            return False
        self._reset_keepalive_health()
        for member in self.members:
            member.async_set_updated_data(None)
        return True
```

- [ ] **Step 7: Run the group tests to verify they pass**

Run: `uv run --no-sync pytest tests/components/lifx/test_parallel_group.py -q`

Expected: PASS (all rewritten group tests pass; unrelated group tests such as cross-fade, theme, effect, and availability are unaffected).

---

### Task 2: Revert the physical-light virtual-off surface and coordinator state

**Files:**
- Modify: `homeassistant/components/lifx/coordinator.py:115-116,175-221,483`
- Modify: `homeassistant/components/lifx/light.py:4,26,79-98,141,177-194,221-344,430-449,522`
- Test: `tests/components/lifx/test_light.py:493-520`

**Interfaces:**
- Consumes: `LIFXUpdateCoordinator` (its `device`, `async_set_power`, `async_set_color`).
- Produces: `LIFXLight` without `RestoreEntity`; `is_on` = `bool(self.bulb.power_level != 0)`; `brightness` with no virtual-off gate; `set_state` with no `physical_power_off`/`virtual_off` branches; `_default_transition_duration(self, power_on, power_off, hsbk)`.

- [ ] **Step 1: Replace the physical turn-off test**

In `tests/components/lifx/test_light.py`, replace `test_direct_physical_power_off_does_not_create_virtual_off` (lines 493-520) with:

```python
async def test_direct_physical_turn_off_sends_power_off(hass: HomeAssistant) -> None:
    """A direct physical turn-off sends SetPower(False) with no virtual-off state."""
    config_entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "127.0.0.1"}, unique_id=SERIAL
    )
    config_entry.add_to_hass(hass)
    bulb = _mocked_bulb_new_firmware()
    bulb.power_level = 65535
    bulb.color = [32000, 10000, 30000, 6000]
    with (
        _patch_discovery(device=bulb),
        _patch_config_flow_try_connect(device=bulb),
        _patch_device(device=bulb),
    ):
        await async_setup_component(hass, lifx.DOMAIN, {lifx.DOMAIN: {}})
        await hass.async_block_till_done()

    entity_id = "light.my_group_my_bulb"
    await hass.services.async_call(
        LIGHT_DOMAIN, "turn_off", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )

    assert bulb.set_power.calls[-1][0][0] is False
    assert not hasattr(config_entry.runtime_data, "virtual_off")
```

- [ ] **Step 2: Run the light test to verify it fails**

Run: `uv run --no-sync pytest tests/components/lifx/test_light.py::test_direct_physical_turn_off_sends_power_off -q`

Expected: FAIL — the `hasattr(config_entry.runtime_data, "virtual_off")` assertion fails against the current code (the coordinator still carries `virtual_off`), which is the distinguishing red for this revert. (Note: `bulb.set_power(False)` alone already passes on the current code, since the existing `physical_power_off` branch already sends `SetPower(False)`; the `hasattr` assertion is what makes the test revert-sensitive.)

- [ ] **Step 3: Remove coordinator virtual-off state and helpers**

In `coordinator.py`, delete:

- `self.virtual_off = False` and `self.resume_hsbk: tuple[int, int | None, int, int] | None = None` (lines 115-116).
- `actual_power_on` property (lines 175-178).
- `display_color` property (lines 180-183).
- `async_record_virtual_off`, `async_record_virtual_on`, `async_clear_virtual_off`, `async_reconcile_virtual_power` (lines 185-221).
- The `self.async_reconcile_virtual_power()` call at the end of `_async_update_data` (line 483).

- [ ] **Step 4: Revert `light.py` imports, stored data, and base class**

In `light.py`:

- Delete `from dataclasses import asdict, dataclass` (line 4). Leave `from typing import Any, override` (line 5) unchanged.
- Delete `from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity` (line 26).
- Delete the `LIFXVirtualPowerStoredData` class (lines 79-98).
- Change `class LIFXLight(LIFXEntity, LightEntity, RestoreEntity):` (line 141) to `class LIFXLight(LIFXEntity, LightEntity):`.

- [ ] **Step 5: Revert `brightness`, `is_on`, and `_default_transition_duration`**

In `light.py`, replace the `brightness` property including its decorators (lines 175-182) so it has no virtual-off gate:

```python
    @property
    @override
    def brightness(self) -> int:
        """Return the brightness of this light between 0..255."""
        fade = self.bulb.power_level / 65535
        return convert_16_to_8(int(fade * self.bulb.color[HSBK_BRIGHTNESS]))
```

Replace the `is_on` body (lines 190-194):

```python
    @property
    @override
    def is_on(self) -> bool:
        """Return true if light is on."""
        return bool(self.bulb.power_level != 0)
```

Replace `_default_transition_duration` (lines 221-234):

```python
    def _default_transition_duration(
        self,
        power_on: bool,
        power_off: bool,
        hsbk: list[float | int | None] | None,
    ) -> float:
        """Return the configured duration for a state request."""
        if power_off:
            return self.coordinator.transition_off_duration
        if power_on:
            return self.coordinator.transition_on_duration
        if hsbk:
            return self.coordinator.transition_cross_duration
        return self.coordinator.transition_on_duration
```

- [ ] **Step 6: Revert `set_state` to the pre-virtual-off control flow**

In `light.py`, within `set_state` (lines 275-344):

- Delete `physical_power_off = kwargs.get(ATTR_POWER) is False` (line 277).
- Delete the resume-HSBK merge block (lines 289-296).
- Update the transition call (lines 284-287) to pass `power_off` instead of `physical_power_off`:

```python
            fade = int(
                self._default_transition_duration(power_on, power_off, hsbk) * 1000
            )
```

- Delete the `if physical_power_off:` branch (lines 298-310) and the `elif self.coordinator.virtual_off:` branch (lines 311-320).
- Change the remaining `elif not self.is_on:` (line 321) to `if not self.is_on:`.

The resulting `set_state` control flow (after `hsbk = find_hsbk(...)` and `fade` computation) is:

```python
        if not self.is_on:
            if power_off:
                if has_transition:
                    await self.set_power(False, duration=0)
                else:
                    await self.set_power(False)
            # If fading on with color, set color immediately
            if hsbk and power_on:
                await self.set_color(hsbk, kwargs)
                await self.set_power(True, duration=fade)
            elif hsbk:
                await self.set_color(hsbk, kwargs, duration=fade)
            elif power_on:
                await self.set_power(True, duration=fade)
        else:
            if power_on:
                await self.set_power(True)
            if hsbk:
                await self.set_color(hsbk, kwargs, duration=fade)
            if power_off:
                await self.set_power(False, duration=fade)
```

- [ ] **Step 7: Revert persistence and the multizone power check**

In `light.py`:

- Replace the `async_added_to_hass` body (lines 430-441) so it no longer restores virtual state:

```python
    @override
    async def async_added_to_hass(self) -> None:
        """Register callbacks."""
        self.async_on_remove(
            self.manager.async_register_entity(self.entity_id, self.coordinator)
        )
        return await super().async_added_to_hass()
```

- Delete the `extra_restore_state_data` property (lines 443-449).
- In `LIFXMultiZone.transform`, change `if not self.coordinator.actual_power_on and hsbk[HSBK_BRIGHTNESS] is None:` (line 522) to `if not self.is_on and hsbk[HSBK_BRIGHTNESS] is None:`.

- [ ] **Step 8: Run the physical-light tests to verify they pass**

Run: `uv run --no-sync pytest tests/components/lifx/test_light.py -q`

Expected: PASS (the replacement test and all existing light tests pass; `test_color_bulb_is_actually_off` still passes because its physical turn-on path is unaffected by the revert).

---

### Task 3: Remove `pad_before` and the per-stage `None` skip from the dispatcher

**Files:**
- Modify: `homeassistant/components/lifx/parallel.py:84-98,1091-1099`

**Interfaces:**
- Consumes: `ParallelCommand` used by `parallel_group.py` (now only via `second`, never `pad_before`).
- Produces: `ParallelCommand.stages` returns `tuple[ParallelCommand, ...]` (no `None`); the dispatcher no longer filters `None` stages.

- [ ] **Step 1: Revert `ParallelCommand.stages`**

In `parallel.py`, replace lines 84-98:

```python
@dataclass(frozen=True, slots=True)
class ParallelCommand:
    """One already-resolved instruction for a single worker."""

    kind: str
    payload: tuple[Any, ...]
    second: ParallelCommand | None = None

    @property
    def stages(self) -> tuple[ParallelCommand, ...]:
        """Return this command's ordered dependency stages."""
        command = ParallelCommand(self.kind, self.payload)
        return (command,) if self.second is None else (command, *self.second.stages)
```

- [ ] **Step 2: Remove the per-stage `None` filter**

In `parallel.py`, replace the `targets` filter (lines 1091-1099) so it no longer checks `member_stages[stage] is not None`:

```python
                targets = tuple(
                    (worker, member_stages[stage])
                    for index, (worker, member_stages) in enumerate(
                        zip(self._workers, stages, strict=True)
                    )
                    if index not in failed_member_indexes
                    and stage < len(member_stages)
                )
```

Leave the `if not targets: continue` guard (lines 1100-1101) in place.

- [ ] **Step 3: Add a 3-stage traversal coverage test**

In `tests/components/lifx/test_parallel.py`, add a test documenting that a 3-deep nested command traverses in the order Task 1 relies on (this already passes; it guards the 3-stage command against future regressions):

```python
def test_command_stages_traverse_a_three_stage_chain() -> None:
    """A nested three-stage command yields color, power, color in order."""
    command = ParallelCommand(
        "color",
        (1, 2, 0, 3500, 0),
        ParallelCommand(
            "power",
            (True, 0),
            ParallelCommand("color", (1, 2, 65535, 3500, 0)),
        ),
    )
    assert tuple(stage.kind for stage in command.stages) == ("color", "power", "color")
```

- [ ] **Step 4: Run the parallel tests to verify they pass**

Run: `uv run --no-sync pytest tests/components/lifx/test_parallel.py -q`

Expected: PASS (`test_command_stages_preserve_member_dependency_order` still asserts `("power", "color")`; the new `test_command_stages_traverse_a_three_stage_chain` asserts `("color", "power", "color")`).

---

### Task 4: Full validation

**Files:**
- Modify: none (verification only)

**Interfaces:**
- Consumes: all prior tasks.

- [ ] **Step 1: Run the full LIFX suite**

Run: `uv run --no-sync pytest tests/components/lifx -q`

Expected: PASS, with no virtual-off references remaining. Any unrelated pre-existing failure should be reported separately.

- [ ] **Step 2: Run lint and whitespace checks**

Run: `uv run --no-sync ruff check homeassistant/components/lifx tests/components/lifx && git diff --check`

Expected: no lint or whitespace errors.

- [ ] **Step 3: Confirm no virtual-off references remain**

Run: `grep -rn "virtual_off\|resume_hsbk\|record_virtual\|reconcile_virtual\|pad_before\|actual_power_on\|display_color" homeassistant/components/lifx/ tests/components/lifx/`

Expected: only the intentional `assert not hasattr(config_entry.runtime_data, "virtual_off")` in `test_light.py` remains; every other reference is gone.

- [ ] **Step 4: Full formatting pass**

Run: `uv run --no-sync prek run --all-files`

Expected: no formatting or lint issues.

---

## Self-review notes

- **Spec coverage:** turn-off→power (Task 1/2), turn-on 3-stage (Task 1), member projection revert (Task 1), physical display/set_state/persistence revert (Task 2), coordinator state removal (Task 2), pad_before removal (Task 3), cross-fade preservation (`_default_transition_duration` keeps `transition_cross_duration`; group `transition_kind` untouched), staged-ACK preservation (Task 3 keeps `second` recursion). All spec sections map to a task.
- **Placeholder scan:** no TBD/TODO; all code steps include full code.
- **Type consistency:** `_default_transition_duration` is defined with `(power_on, power_off, hsbk)` and called with `(power_on, power_off, hsbk)` in the same task; `member_states` and `_async_dispatch_projected_states` signatures are consistent across Task 1.

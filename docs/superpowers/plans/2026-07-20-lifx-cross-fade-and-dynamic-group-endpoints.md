# LIFX Cross Fade and Dynamic Device Group Endpoints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persisted Cross Fade defaults for physical LIFX lights and Device Groups, and let a Device Group safely adopt a physical member's replacement coordinator and endpoint without reloading the whole group.

**Architecture:** Add one duration field and restored number entity beside Fade On and Fade Off. Resolve an explicit transition first; otherwise classify ordinary state writes as off, on, or cross from displayed state and request semantics. Refactor group member subscriptions into runtime-managed bindings so a physical-entry reload swaps only that member coordinator and reconnects its existing worker, whose `GetService` preflight remains the authoritative port discovery.

**Tech Stack:** Home Assistant Python 3.14, aiolifx, `DataUpdateCoordinator`, `RestoreNumber`, asyncio, multiprocessing-backed LIFX parallel workers, pytest, Ruff/prek.

## Global Constraints

- Restrict functional changes to `homeassistant/components/lifx` and `tests/components/lifx`; `lifx_cloud` is out of scope.
- Preserve the `lifx` domain and custom-integration identity.
- An explicit Home Assistant `transition`, including `0`, overrides every configured fade default.
- A Device Group Cross Fade value of `0` inherits each member's physical Cross Fade value, matching existing Fade On/Off behavior.
- Group virtual off remains a brightness-zero `SetColor` operation and retains current ACK, retry, preemption, and optimistic-state behavior.
- Physical discovery/DHCP remains the only endpoint discovery owner. The group reads its replacement physical coordinator's host and keeps worker `GetService` preflight as its service-port source.
- Run Git and all tests on `root@192.168.8.28` in `/config/workplace/ha-core`; do not commit unless the user requests it.

---

## File structure

- `homeassistant/components/lifx/const.py`: Cross Fade key and shared transition-kind values.
- `homeassistant/components/lifx/coordinator.py`: physical Cross Fade runtime value.
- `homeassistant/components/lifx/number.py`: physical restored Cross Fade number description and coordinator assignment.
- `homeassistant/components/lifx/light.py`: physical state-command transition classification and default duration lookup.
- `homeassistant/components/lifx/parallel_group.py`: group Cross Fade setting, group transition classification, and dynamic member coordinator bindings.
- `homeassistant/components/lifx/strings.json` and `homeassistant/components/lifx/translations/en.json`: Cross Fade label.
- `tests/components/lifx/test_number.py`: physical number creation/restoration regressions.
- `tests/components/lifx/test_light.py`: physical on/off/cross duration-selection regressions.
- `tests/components/lifx/test_parallel_group.py`: group duration fallback, displayed-state classification, member replacement, and stale-event regressions.
- `tests/components/lifx/test_parallel.py`: reconnect command generation/routing regression when the runtime interface changes.

### Task 1: Cross Fade configuration contract

**Files:**
- Modify: `homeassistant/components/lifx/const.py:68-69`
- Modify: `homeassistant/components/lifx/coordinator.py:108-112`
- Modify: `homeassistant/components/lifx/number.py:13-41,89-95`
- Modify: `homeassistant/components/lifx/parallel_group.py:163-168,870-883,996-1017`
- Modify: `homeassistant/components/lifx/strings.json:55-62`
- Modify: `homeassistant/components/lifx/translations/en.json:55-62`
- Test: `tests/components/lifx/test_number.py`
- Test: `tests/components/lifx/test_parallel_group.py`

**Interfaces:**
- Consumes: existing `TRANSITION_ON_DURATION` and `TRANSITION_OFF_DURATION` number configuration.
- Produces: `TRANSITION_CROSS_DURATION: Final[str]`, `LIFXUpdateCoordinator.transition_cross_duration: float`, and `LIFXParallelGroupRuntime.transition_cross_duration: float`.

- [ ] **Step 1: Write failing physical-number tests**

```python
async def test_cross_fade_number_updates_the_physical_coordinator(
    hass: HomeAssistant,
) -> None:
    entity_id = "number.my_group_my_bulb_cross_fade_time"

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: 1.5},
        blocking=True,
    )

    assert hass.states.get(entity_id).state == "1.5"
    assert config_entry.runtime_data.transition_cross_duration == 1.5


async def test_cross_fade_number_restores_value(hass: HomeAssistant) -> None:
    entity_id = "number.my_group_my_bulb_cross_fade_time"
    mock_restore_cache_with_extra_data(
        hass,
        ((State(entity_id, "2.5"), NumberExtraStoredData(300, 0, 0.1, "s", 2.5).as_dict()),),
    )

    # Set up the existing physical fixture.
    assert hass.states.get(entity_id).state == "2.5"
    assert config_entry.runtime_data.transition_cross_duration == 2.5
```

- [ ] **Step 2: Run the focused physical-number tests and verify RED**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m pytest tests/components/lifx/test_number.py -q'
```

Expected: FAIL because `number.my_group_my_bulb_cross_fade_time` does not exist.

- [ ] **Step 3: Write failing Device Group configuration tests**

```python
async def test_group_cross_fade_zero_inherits_each_member(
    hass: HomeAssistant,
) -> None:
    runtime, members = _runtime(hass)
    members[0].transition_cross_duration = 0.4
    members[1].transition_cross_duration = 0.8

    assert runtime._transition_ms(members[0], "cross", {}) == 400
    assert runtime._transition_ms(members[1], "cross", {}) == 800


async def test_group_cross_fade_overrides_each_member(hass: HomeAssistant) -> None:
    runtime, members = _runtime(hass)
    runtime.transition_cross_duration = 1.2
    members[0].transition_cross_duration = 0.4
    members[1].transition_cross_duration = 0.8

    assert runtime._transition_ms(members[0], "cross", {}) == 1200
    assert runtime._transition_ms(members[1], "cross", {}) == 1200
```

- [ ] **Step 4: Run the focused Group configuration tests and verify RED**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m pytest tests/components/lifx/test_parallel_group.py -q'
```

Expected: FAIL because Cross Fade state and duration selection do not exist.

- [ ] **Step 5: Implement the configuration surface**

```python
# const.py
TRANSITION_CROSS_DURATION = "transition_cross_duration"

# coordinator.py and parallel_group.py constructors
self.transition_cross_duration: float = 0.0

# number.py
TRANSITION_DURATION_ENTITIES = (
    ...,
    NumberEntityDescription(
        key=TRANSITION_CROSS_DURATION,
        translation_key=TRANSITION_CROSS_DURATION,
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=300,
        native_step=0.1,
        native_unit_of_measurement="s",
    ),
)

# Both number implementations
if self.entity_description.key == TRANSITION_ON_DURATION:
    self.coordinator.transition_on_duration = value
elif self.entity_description.key == TRANSITION_OFF_DURATION:
    self.coordinator.transition_off_duration = value
else:
    self.coordinator.transition_cross_duration = value
```

Add the equivalent Group assignment and description, then add `Cross Fade
Time` under the existing number translations and regenerate English
translations on the HA host.

- [ ] **Step 6: Run configuration tests and translation generation**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m script.translations develop --integration lifx && .venv/bin/python -m pytest tests/components/lifx/test_number.py tests/components/lifx/test_parallel_group.py -q'
```

Expected: PASS; physical and Group values restore, and Group zero fallback
remains member-specific.

### Task 2: Physical transition classification

**Files:**
- Modify: `homeassistant/components/lifx/light.py:221-332`
- Test: `tests/components/lifx/test_light.py:1964-2045`

**Interfaces:**
- Consumes: `LIFXUpdateCoordinator.transition_on_duration`,
  `transition_off_duration`, `transition_cross_duration`, `virtual_off`,
  `actual_power_on`, and explicit `ATTR_TRANSITION`.
- Produces: `LIFXLight._transition_ms(kwargs, hsbk) -> int`, selected before
  physical packets are built.

- [ ] **Step 1: Write failing physical transition tests**

```python
@pytest.mark.parametrize(
    ("initial_virtual_off", "power", "brightness", "expected_duration"),
    [
        pytest.param(False, False, None, 2500, id="actual-off"),
        pytest.param(True, True, 255, 1500, id="virtual-off-to-on"),
        pytest.param(False, None, 128, 900, id="on-to-on-brightness"),
    ],
)
async def test_default_transition_kind_selects_the_right_duration(
    hass: HomeAssistant,
    initial_virtual_off: bool,
    power: bool | None,
    brightness: int | None,
    expected_duration: int,
) -> None:
    # Configure Fade On=1.5, Fade Off=2.5, Cross Fade=0.9.
    # Use the existing physical fixture and issue one state request.
    assert bulb.set_waveform_optional.call_args.kwargs["value"]["period"] == expected_duration
```

Add a separate explicit-zero test that sets `transition=0` for an on-to-on
color change and asserts a zero packet duration.

- [ ] **Step 2: Run the physical transition tests and verify RED**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m pytest tests/components/lifx/test_light.py -k "transition_duration or cross_fade" -q'
```

Expected: FAIL because on-to-on color changes still select Fade On Time and
the Cross Fade number is absent.

- [ ] **Step 3: Implement physical classification and lookup**

```python
def _transition_kind(
    self, power: bool | None, hsbk: list[float | int | None] | None
) -> Literal["on", "off", "cross"]:
    if power is False:
        return "off"
    if power is True and not self.is_on:
        return "on"
    if self.coordinator.virtual_off and hsbk is not None:
        return "on"
    return "cross"

def _transition_ms(
    self, kwargs: dict[str, Any], kind: Literal["on", "off", "cross"]
) -> int:
    if ATTR_TRANSITION in kwargs:
        return round(kwargs[ATTR_TRANSITION] * 1000)
    return round(
        {
            "on": self.coordinator.transition_on_duration,
            "off": self.coordinator.transition_off_duration,
            "cross": self.coordinator.transition_cross_duration,
        }[kind]
        * 1000
    )
```

Use the selected `fade` in the existing packet branches; do not change their
ordering, virtual-power bookkeeping, direct physical power-off semantics, or
refresh scheduling.

- [ ] **Step 4: Run the focused physical tests and verify GREEN**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m pytest tests/components/lifx/test_light.py -k "transition_duration or cross_fade or virtual_off" -q'
```

Expected: PASS, including explicit `transition=0` precedence.

### Task 3: Group transition classification and staged duration selection

**Files:**
- Modify: `homeassistant/components/lifx/parallel_group.py:336-345,409-452,480-492`
- Test: `tests/components/lifx/test_parallel_group.py`

**Interfaces:**
- Consumes: captured `_OptimisticGroupState`, requested `power`, HSBK change,
  Group and physical member transition defaults.
- Produces: `LIFXParallelGroupRuntime._transition_ms(member, kind, kwargs) -> int`
  and one group-scoped kind per ordinary state request.

- [ ] **Step 1: Write failing Group state tests**

```python
async def test_displayed_on_color_change_uses_cross_fade_for_every_member(
    hass: HomeAssistant,
) -> None:
    runtime, members = _runtime(hass)
    runtime.transition_cross_duration = 0.9

    await runtime.async_set_state(brightness=128)

    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.payload[-1] == 900 for command in commands)


async def test_group_virtual_off_uses_fade_on_for_visible_wakeup(
    hass: HomeAssistant,
) -> None:
    runtime, members = _runtime(hass)
    runtime.transition_on_duration = 1.5
    runtime._begin_projection(
        tuple(_MemberCommandState(tuple(member.device.color), 0) for member in members)
    )

    await runtime.async_set_state(power=True, brightness=255)

    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.stages[-1].payload[-1] == 1500 for command in commands)


async def test_group_virtual_off_uses_fade_off(hass: HomeAssistant) -> None:
    runtime, members = _runtime(hass)
    runtime.transition_off_duration = 2.5

    await runtime.async_set_state(power=False)

    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.payload[-1] == 2500 for command in commands)
```

- [ ] **Step 2: Run the Group transition tests and verify RED**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m pytest tests/components/lifx/test_parallel_group.py -k "cross_fade or virtual_off" -q'
```

Expected: FAIL because a displayed-on color update uses Fade On Time.

- [ ] **Step 3: Implement group-scoped classification**

```python
def _transition_kind(
    self,
    display_state: _OptimisticGroupState,
    power: bool | None,
) -> Literal["on", "off", "cross"]:
    if power is False:
        return "off"
    if power is True and not display_state.is_on:
        return "on"
    return "cross"

def _transition_ms(
    self,
    member: LIFXUpdateCoordinator,
    kind: Literal["on", "off", "cross"],
    kwargs: dict[str, Any],
) -> int:
    if ATTR_TRANSITION in kwargs:
        return round(kwargs[ATTR_TRANSITION] * 1000)
    group_duration, member_duration = {
        "on": (self.transition_on_duration, member.transition_on_duration),
        "off": (self.transition_off_duration, member.transition_off_duration),
        "cross": (
            self.transition_cross_duration,
            member.transition_cross_duration,
        ),
    }[kind]
    return round((group_duration or member_duration) * 1000)
```

Compute `kind` once immediately after taking `display_state`, pass it to all
ordinary member command construction, and explicitly pass `"on"` to existing
effect power stages. Keep the preparation color stage duration at zero in the
already-staged actual-power-off wakeup path.

- [ ] **Step 4: Run Group transition tests and existing staging regressions**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m pytest tests/components/lifx/test_parallel_group.py -q'
```

Expected: PASS; Cross Fade changes only visible on-to-on color stages and
does not change ACK/staged topology.

### Task 4: In-place physical coordinator and endpoint handoff

**Files:**
- Modify: `homeassistant/components/lifx/parallel_group.py:150-321,910-960`
- Modify: `homeassistant/components/lifx/parallel.py:886-903` only if a
  member-binding generation needs to be carried to reconnect completion.
- Test: `tests/components/lifx/test_parallel_group.py`
- Test: `tests/components/lifx/test_parallel.py`

**Interfaces:**
- Consumes: `ConfigEntry.state`, `ConfigEntry.runtime_data`,
  `LIFXUpdateCoordinator.async_add_listener()`, coordinator
  `device.ip_addr`, and `LIFXParallelRuntime.async_request_reconnect(index, host)`.
- Produces: `LIFXParallelGroupRuntime.async_member_entry_state_changed(index)`,
  `async_member_updated(index)`, and generation-scoped member listener/reconnect
  handling.

- [ ] **Step 1: Write failing member-replacement tests**

```python
async def test_member_reload_replaces_only_its_coordinator_and_reconnects_worker(
    hass: HomeAssistant,
) -> None:
    runtime, members = _runtime(hass)
    replacement = _member("192.0.2.99")
    runtime.member_entries[0].runtime_data = replacement
    runtime.member_entries[0].state = ConfigEntryState.LOADED

    await runtime.async_member_entry_state_changed(0)

    assert runtime.members[0] is replacement
    runtime.parallel.async_request_reconnect.assert_awaited_once_with(0, "192.0.2.99")
    runtime.parallel.async_request_reconnect.assert_not_awaited_with(1, "192.0.2.2")


async def test_member_unload_makes_group_unavailable_without_stopping_workers(
    hass: HomeAssistant,
) -> None:
    runtime, _members = _runtime(hass)
    runtime.member_entries[0].state = ConfigEntryState.UNLOAD_IN_PROGRESS

    await runtime.async_member_entry_state_changed(0)

    assert not runtime.available
    runtime.parallel.async_stop.assert_not_awaited()


async def test_stale_reconnect_completion_does_not_update_a_replaced_member(
    hass: HomeAssistant,
) -> None:
    runtime, _members = _runtime(hass)
    first = runtime._member_binding_generation[0]
    runtime._member_binding_generation[0] += 1

    await runtime._async_request_reconnect(0, "192.0.2.1", first)

    assert runtime._member_hosts[0] != "192.0.2.1"
```

- [ ] **Step 2: Run member-replacement tests and verify RED**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m pytest tests/components/lifx/test_parallel_group.py -k "member_reload or member_unload or stale_reconnect" -q'
```

Expected: FAIL because the runtime has immutable member bindings and schedules
a full group recovery on unload.

- [ ] **Step 3: Implement runtime-owned mutable bindings**

```python
# Constructor
self.members = list(members)
self._member_listener_removers: list[CALLBACK_TYPE | None] = [None] * len(members)
self._member_binding_generation = [0] * len(members)

@callback
def async_member_entry_state_changed(self, index: int) -> None:
    member_entry = self.member_entries[index]
    if member_entry.state is not ConfigEntryState.LOADED:
        self._member_binding_generation[index] += 1
        self._member_ready[index] = False
        self.async_update_listeners()
        return
    coordinator = member_entry.runtime_data
    if not isinstance(coordinator, LIFXUpdateCoordinator):
        return
    self._async_replace_member(index, coordinator)

@callback
def _async_replace_member(
    self, index: int, coordinator: LIFXUpdateCoordinator
) -> None:
    if self.members[index] is coordinator:
        self.async_member_updated(index)
        return
    if remove_listener := self._member_listener_removers[index]:
        remove_listener()
    self._member_binding_generation[index] += 1
    self.members[index] = coordinator
    self._member_listener_removers[index] = coordinator.async_add_listener(
        lambda: self.async_member_updated(index)
    )
    self.async_member_updated(index)
```

Move all member entry and coordinator listener registration from setup into
runtime methods. On `async_stop`, remove every live coordinator listener.
Capture the binding generation before scheduling reconnect and check it before
and after the awaited reconnect. Keep worker port discovery unchanged:
`async_request_reconnect(index, coordinator.device.ip_addr)` continues to
send `GetService` in the worker.

- [ ] **Step 4: Add dispatch/handoff race coverage**

```python
async def test_replacement_during_ack_wait_keeps_only_the_latest_request(
    hass: HomeAssistant,
) -> None:
    runtime, _members = _runtime(hass)
    runtime.parallel.async_dispatch = AsyncMock(side_effect=HomeAssistantError("superseded"))

    # Replace one ready member while an old dispatch is active, then issue a
    # later state request through the existing preemption test seam.
    # Assert no old retry/later stage is dispatched after replacement and the
    # replacement worker receives only its reconnect request.
```

Use the existing parallel preemption tests to assert that adding a reconnect
does not consume the new request's worker control events. Add a direct
`parallel.py` test only if an explicit reconnect generation is introduced
there; otherwise retain the generation guard in `parallel_group.py` and test
that boundary directly.

- [ ] **Step 5: Run endpoint-handoff and transport regressions**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m pytest tests/components/lifx/test_parallel.py tests/components/lifx/test_parallel_group.py -q'
```

Expected: PASS; only the affected worker reconnects, the group stays loaded,
and a stale member/reconnect event cannot corrupt newer work.

### Task 5: Integration verification and plan audit

**Files:**
- Modify: `docs/superpowers/plans/2026-07-20-lifx-cross-fade-and-dynamic-group-endpoints.md` to check completed tasks and record exact results.
- Verify: `homeassistant/components/lifx/`
- Verify: `tests/components/lifx/`

**Interfaces:**
- Consumes: all completed tasks.
- Produces: fresh HA-host test, static-check, and diff evidence without a commit.

- [ ] **Step 1: Run focused behavior tests**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m pytest tests/components/lifx/test_number.py tests/components/lifx/test_light.py tests/components/lifx/test_parallel.py tests/components/lifx/test_parallel_group.py -q'
```

Expected: PASS, or report every unrelated pre-existing failure separately.

- [ ] **Step 2: Run the complete LIFX suite**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m pytest tests/components/lifx -q'
```

Expected: all new tests pass. If the known unrelated duration regressions
remain, list their names and output instead of attributing them to this work.

- [ ] **Step 3: Run static and diff checks**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m prek run --all-files && git diff --check && git status --short'
```

Expected: prek and diff check exit zero; status contains only the approved
LIFX source, tests, specification, and plan files.

- [ ] **Step 4: Reconcile the implementation with the specification**

Check every Cross Fade and dynamic endpoint requirement in
`docs/superpowers/specs/2026-07-20-lifx-cross-fade-and-dynamic-group-endpoints-design.md`
against the final diff and test output. Report any unmet requirement rather
than claiming completion.

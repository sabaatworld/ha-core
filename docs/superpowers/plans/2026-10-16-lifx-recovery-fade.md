# LIFX Recovery Fade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan inline, task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The initial no-commit instruction was subsequently superseded by the user's request to amend the previous commit and push.

**Goal:** Apply the selected fade duration when a physical LIFX light recovers from virtual off.

**Architecture:** Correct the duration argument in the existing powered-on recovery branch. Exercise real HA service routing and durable recovery state with device API doubles. Retain instant hidden preparation followed by a power fade for actually-off devices.

**Tech Stack:** Python 3.14, Home Assistant Core, lifx-async 6.5.1, pytest, uv, prek.

**Spec:** [Approved recovery fade design](../specs/2026-10-16-lifx-recovery-fade-design.md).

## Global Constraints

- Technical domain and component directory remain `lifx`.
- Python minimum is 3.14; dependencies and manifest version are unchanged.
- Execute inline in the current checkout. The initial no-commit instruction was superseded by the user's request to amend the previous commit and push.
- All task documentation lives in `ha-core/docs/superpowers/`.
- Initial implementation changes local source and tests. The user subsequently authorized syncing the component to HA after amendment and push, using the private-workspace deployment script.
- Use `uv run --no-sync pytest` for tests and run `uv run --no-sync prek run --all-files` after development.
- Preserve unrelated user files and record verification failures without disguising them as passes.

## File responsibilities

- `homeassistant/components/lifx/light.py`: select the visible recovery duration.
- `tests/components/lifx/test_light.py`: service-level transition and pattern regression coverage.
- `docs/superpowers/specs/2026-10-16-lifx-recovery-fade-design.md`: approved behaviour and known limitations.
- This plan: execution checklist, verification results, and rollback.

### Task 1: Reproduce and repair virtual-off turn-on duration

**Files:** Modify `tests/components/lifx/test_light.py` and `homeassistant/components/lifx/light.py`.

**Interfaces:** Exercise `light.turn_off`, `switch.turn_on`/`switch.turn_off`, and `light.turn_on`; observe `device.set_waveform_optional(color, period=seconds, ...)`. Production signature remains `_async_recover_virtual_off(self, kwargs: dict[str, Any], duration: float, reserved: VirtualPowerToken) -> None`.

- [x] Replace the overlapping bare/partial/explicit-brightness recovery tests with the following parameterized test. The break it catches is replacing the selected visible recovery duration with zero.

```python
@pytest.mark.parametrize("policy_service", [SERVICE_TURN_ON, SERVICE_TURN_OFF])
@pytest.mark.parametrize(
    ("default", "transition_data", "expected_duration"),
    [
        pytest.param(2.0, {}, 2.0, id="default"),
        pytest.param(2.0, {ATTR_TRANSITION: 5.0}, 5.0, id="override"),
        pytest.param(2.0, {ATTR_TRANSITION: 0.0}, 0.0, id="explicit-zero"),
        pytest.param(2.0, {ATTR_TRANSITION: 0.1}, 0.1, id="explicit-short"),
        pytest.param(0.0, {}, 0.0, id="default-zero"),
    ],
)
@pytest.mark.parametrize(
    ("color_data", "expected_color"),
    [
        pytest.param({}, HSBK(20.0, 0.25, 0.75, 3500), id="bare"),
        pytest.param(
            {ATTR_BRIGHTNESS: 128}, HSBK(20.0, 0.25, 128 / 255, 3500), id="brightness"
        ),
        pytest.param(
            {ATTR_COLOR_TEMP_KELVIN: 4500}, HSBK(20.0, 0.0, 0.75, 4500), id="temperature"
        ),
    ],
)
async def test_virtual_off_recovery_uses_selected_transition(
    hass: HomeAssistant,
    policy_service: str,
    default: float,
    transition_data: dict[str, float],
    expected_duration: float,
    color_data: dict[str, int],
    expected_color: HSBK,
) -> None:
    device = create_mock_light()
    device.state.power = 65535
    device.state.color = HSBK(20.0, 0.25, 0.75, 3500)
    entry = await async_setup_lifx_entry(hass, device)
    entry.runtime_data.transition_on_duration = default
    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )
    await hass.services.async_call(
        "switch", policy_service,
        {ATTR_ENTITY_ID: "switch.my_group_my_bulb_virtual_power_off"}, blocking=True,
    )
    device.state.color = device.state.color.replace(brightness=0)
    device.set_waveform_optional.reset_mock()
    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY_ID, **transition_data, **color_data}, blocking=True,
    )
    assert_color_written(device, expected_color, period=expected_duration)
    device.set_power.assert_not_awaited()
    assert hass.states.get(ENTITY_ID).state == STATE_ON
```

- [x] Run `uv run --no-sync pytest -q tests/components/lifx/test_light.py -k virtual_off_recovery_uses_selected_transition`. Nonzero default/override cases must fail on the observed `period=0.0`; zero-duration controls must pass.
- [x] Correct the powered-on branch and remove its misleading hidden-preparation comment:

```python
if self.coordinator.actual_power_on:
    await self.async_write_recovery_image(target, duration=duration)
else:
    await self.async_write_recovery_image(target, duration=0.0)
    if store.async_is_current(token):
        await self.set_power(True, duration=duration)
```

- [x] Rerun the focused command; all cases must pass.

### Task 2: Characterize adjacent recovery behaviour

**Files:** Modify `tests/components/lifx/test_light.py`.

**Interfaces:** Reuse public `light.turn_on`/`light.turn_off` routing, coordinator refresh, and existing typed light factories. Observe seconds at `set_all_color_zones` and milliseconds at `set_matrix_colors`.

- [x] Parameterize `test_actually_off_recovery_writes_hidden_then_powers_on` over `(default, transition_data, expected_duration)` using `(2.0, {}, 2.0)`, `(2.0, {ATTR_TRANSITION: 5.0}, 5.0)`, `(2.0, {ATTR_TRANSITION: 0.0}, 0.0)`, and `(2.0, {ATTR_TRANSITION: 0.1}, 0.1)`. Capture the setup entry, assign its on default, merge transition data into the final on call, and assert `set_power(True, duration=expected_duration)` plus zero-period image preparation. Turn the Virtual Power Off switch off before final recovery to exercise the actual-off policy.
- [x] Add these public-boundary pattern tests:

```python
@pytest.mark.parametrize("extended", [True, False], ids=["extended", "legacy"])
async def test_multizone_recovery_preserves_pattern_and_duration(
    hass: HomeAssistant, extended: bool
) -> None:
    device = create_mock_multizone_light(extended=extended)
    device.state.power = 65535
    colors = list(device.state.zones)
    await async_setup_lifx_entry(hass, device)
    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )
    device.set_all_color_zones.reset_mock()
    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_TRANSITION: 2.0}, blocking=True,
    )
    device.set_all_color_zones.assert_awaited_once_with(colors, duration=2.0)
    device.set_power.assert_not_awaited()


async def test_matrix_recovery_preserves_pattern_and_duration(
    hass: HomeAssistant,
) -> None:
    device = create_reference_matrix_light()
    colors = list(device.state.tile_colors)
    await async_setup_lifx_entry(hass, device)
    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )
    device.set_matrix_colors.reset_mock()
    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_TRANSITION: 2.0}, blocking=True,
    )
    assert device.set_matrix_colors.await_args_list == [
        call(tile.tile_index, colors[index * 64 : (index + 1) * 64], duration=2000)
        for index, tile in enumerate(device.state.chain)
    ]
    device.set_power.assert_not_awaited()
```

- [x] Add the interrupted-fade recovery regression:

```python
async def test_interrupted_recovery_retains_resume_target(
    hass: HomeAssistant,
) -> None:
    device = create_mock_light()
    device.state.power = 65535
    device.state.color = HSBK(20.0, 0.25, 0.75, 3500)
    entry = await async_setup_lifx_entry(hass, device)
    for service in (SERVICE_TURN_OFF, SERVICE_TURN_ON):
        await hass.services.async_call(
            LIGHT_DOMAIN, service,
            {ATTR_ENTITY_ID: ENTITY_ID, ATTR_TRANSITION: 5.0}, blocking=True,
        )
    device.state.color = device.state.color.replace(brightness=0.2)
    await entry.runtime_data.async_request_refresh()
    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_TRANSITION: 1.0}, blocking=True,
    )
    device.set_waveform_optional.reset_mock()
    await hass.services.async_call(
        LIGHT_DOMAIN, SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ENTITY_ID, ATTR_TRANSITION: 5.0}, blocking=True,
    )
    assert_color_written(device, HSBK(20.0, 0.25, 0.75, 3500), period=5.0)
    device.set_power.assert_not_awaited()
    assert hass.states.get(ENTITY_ID).state == STATE_ON
```

- [x] Run `uv run --no-sync pytest -q tests/components/lifx` and inspect all failures. Tests characterize integration API forwarding, not actual firmware interpolation or library frame-buffer correctness.

### Task 3: Review, required checks, and execution record

**Files:** Review the production/test diff and update this plan with actual results.

**Interfaces:** Git diff/status, pytest results, and prek output.

- [x] Run `uv run --no-sync prek run --all-files`; record complete success or concrete blockers. If hooks modify files, inspect changes and retain only intended task changes.
- [x] Run targeted hooks for the changed Python files if repository-wide checks have unrelated failures; do not call the full repository clean on the strength of targeted checks.
- [x] Run `git diff --check`, inspect `git diff --stat` and `git status --short`, and review the final source/test diff against the spec.
- [x] Record red/green counts, suite results, lint results, and remaining limitations below. Report the changed source and documentation paths. Make no commits during the initial implementation.

## Rollback

Apply the inverse of the reviewed duration correction and remove or restore the accompanying task tests/docs from the diff. Initial implementation did not modify live state or config. After deployment, a rollback requires syncing the preceding component source from `cd4b066c70f04b279fb5a1492553caac66c70f32` with the same private-workspace script, which restarts HA.

## Authorized post-publication deployment

The user requested syncing LIFX after amendment and push. From
`/Users/sabaata/Documents/Projects/ha-private-ws`, run:

```sh
scripts/ha_custom_component.sh sync --source-dir /Users/sabaata/Documents/Projects/ha-core/homeassistant/components/lifx
```

The script stages and replaces `/config/custom_components/lifx` and requests one
HA restart. Verify HA returns, all four LIFX config entries load, and startup logs
contain no LIFX setup failure. Do not blindly retry the restart if the API is
temporarily unavailable. Light actuation is not needed to verify deployment;
physical fade observation remains the user's hardware acceptance check.

## Execution record

- Plan and linked spec written before implementation in `ha-core`, following the user's documentation-location correction.
- Task 1 red: 18 failed, 12 passed; every failure showed selected nonzero duration replaced with `period=0.0`.
- Task 1 green: the same 30 parameter cases passed after the single duration correction.
- Task 2 mutation check: temporarily restoring the zero-duration bug produced 4 failures (legacy/extended strips, matrix, interrupted recovery) and 4 passing actual-power-off controls. The selected target colours matched; failures were duration mismatches.
- Full LIFX suite: `uv run --no-sync pytest -q tests/components/lifx` passed 428 tests and 10 snapshots in 28.09 seconds.
- Repository-wide `uv run --no-sync prek run --all-files` was attempted and reached the 120-second command limit. Ruff check, Ruff format, codespell, and JSON validation passed. Findings already present outside this change include workflow action pinning/credential warnings from zizmor and workflow YAML lint in `.github/workflows/publish-lifx-ultimate.yml`, the `dev` branch commit guard, and mypy's duplicate `homeassistant.util.signal_type` module. Pylint and later full-repository hooks did not report completion before timeout.
- Prettier reformatted two unrelated SmartThings washer JSON fixtures during the all-files run. Both were clean at task start; the hook output identified them as changed, and their formatter-only changes were restored.
- Targeted prek over both changed Python files and both new documents completed: Ruff check, Ruff format, codespell, Prettier, and pylint passed. The `dev` branch guard failed as expected. Mypy reported 61 errors in 12 files; the only report in `light.py` is the existing unreachable branch at line 125, outside the edited recovery statement. Other errors concern unchanged coordinator, group, transport, frontend, matter, stream, and USB code.
- Scope review: the production diff changes one duration argument and removes its incorrect comment. A shared switch entity-ID constant avoids repetition in the tests. The known large-matrix frame-buffer and multi-tile overlap limitations remain separate follow-ups documented in the spec.
- Final inline review found no additional issues in the patch. `git diff --check` passed; the initial execution ended with only the two intended Python modifications and the new spec and plan, uncommitted on `dev`. No live deployment was performed.
- The user subsequently requested amending the previous commit and pushing. A fresh fetch confirmed the previous Virtual Power Off commit was unpublished, and GitHub reported no open PR from `dev` in the fork or upstream repository. The authorized publication is an amendment preserving the existing message, followed by a normal push to `origin/dev`.
- While publication was in progress, the user additionally authorized the post-publication HA sync described above.
- The required pre-commit `uv run --no-sync script/setup` attempt could not build the unrelated `dtlssocket==0.1.19` dependency because `autoconf` is absent. It stopped before translation generation and hook installation. Verification uses the existing working virtual environment.

# LIFX Virtual Power Off Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The user explicitly prohibits commits; do not execute the skills' default commit steps.

**Goal:** Provide independent, persistent Virtual Power Off policies for physical LIFX lights and Device Groups, reliable shared recovery and pattern preservation, and the approved shared 250 ms ordinary color ramp.

**Architecture:** Physical coordinators share versioned virtual-power records through the integration manager, independently of typed hardware observations and switch entity lifecycle. Prepare and persist recoverable intent before destructive writes, execute through existing physical/group transports, and commit only current successful participants. Extend group packet stages for pattern recovery and add narrowly scoped effect ownership/exclusion so older producers cannot invalidate virtual off.

**Tech Stack:** Home Assistant Core, Python 3.14, pinned `lifx-async==6.5.1`, HA `Store` and config-entry options, pytest/Syrupy, existing spawned UDP workers, HA MCP, and the ha-private-ws deployment script.

**Spec:** [LIFX Virtual Power Off design](../specs/2026-10-16-lifx-virtual-power-off-design.md). Read the full active contract, including the operation table and Section 2.1; Appendix A is a deferred backlog except for explicitly identified overlaps.

**Status:** Execution started on the current `dev` branch by explicit user instruction; no commits. Independent spec review approved the corrected design on 2026-10-16. Baseline LIFX verification: 291 tests and 10 snapshots passed. Full `script/setup` was attempted but its unrelated dtlssocket build requires unavailable autoconf; the existing environment passes the full baseline suite. Detailed batch/review progress is recorded in `.superpowers/sdd/2026-10-16-lifx-virtual-power-off/progress.md` during execution.

## Global Constraints

- **No commits, amendments, pushes, PRs, or branch-history changes.** Leave the changes uncommitted for human review.
- Technical domain and directory remain `lifx`; manifest placeholder version remains `0.0.0`.
- Source, spec, plan, and integration notes live in `/Users/sabaata/Documents/Projects/ha-core` by explicit user direction. Deployment tooling remains in `/Users/sabaata/Documents/Projects/ha-private-ws`.
- Audited source HEAD: `4a6aab3bdc927d4050ae44e45b6659a44e330115`. Recheck current work before execution; preserve unfamiliar changes.
- Python 3.14 is the minimum. Run `python3` in the project environment and tests with `uv run --no-sync pytest`.
- Run `script/setup` when entering an execution environment/worktree. Do not create an isolation worktree or run setup merely to write this plan.
- Every physical light and Device Group gets **Virtual Power Off**, default **On** for new and existing entries. Store each independent policy in entry options.
- Policy changes send no light packets, clear no marker, and affect future off commands only.
- Group off follows group policy; direct physical off follows physical policy. Resume and recognition are policy-independent.
- Omitted `power` preserves each participant's logical power. Ordinary aggregate color merging and reporting changes remain deferred.
- Capture compatible per-zone/pixel images before destructive writes. Keep `resume_required` separate from `virtual_off`.
- Use the user-approved **100%** fallback only when virtual-derived recovery has no usable explicit, saved, or nonzero observed brightness. Do not infer virtual off from arbitrary brightness zero.
- Move **`LIFX_MIN_COLOR_RAMP = 0.25`** into `const.py`. Apply it to eligible implicit ordinary color writes on both paths; explicit incoming zero/sub-250 ms durations remain exact.
- Hidden preparation stays immediate. Actual/virtual On/Off, Identify, effects, and theme-specific durations receive no blanket ordinary-color floor.
- D26, general native-group parity, effect fidelity, group-startup availability redesign, membership editing, and unrelated audit findings remain parked.
- New packet serialization uses public `lifx.protocol` packet types and `pack()`; do not create another protocol implementation or patch library internals.
- Preserve synchronized group visible-release deadlines, existing ACK/retry/preemption behavior, and typed coordinator data. Do not introduce group-owned discovery or routine extra physical polls.
- Persist recovery candidates with an **awaited** save before destructive dispatch; a scheduled delayed save is insufficient at that boundary.
- Regenerate `translations/en.json` after `strings.json` changes and before tests/deployment. The sync script excludes `strings.json`.
- After the code session, run `uv run --no-sync prek run --all-files`; distinguish pre-existing failures from new failures.
- Deployment uses `ha-private-ws/scripts/ha_custom_component.sh`, then separate startup/readback/log verification. Restore all test settings and light states.

## File and responsibility map

Paths are relative to `ha-core` unless explicitly qualified. Short integration
filenames in the responsibility table refer to `homeassistant/components/lifx/`;
short test filenames refer to `tests/components/lifx/`. Task file lists below use
full repository-relative paths.

| File | Responsibility |
| --- | --- |
| `homeassistant/components/lifx/virtual_power.py` (new) | Immutable image/layout types, virtual-derived record state, serialization, intent/revision rules, supported HA recovery storage; no network writes. |
| `homeassistant/components/lifx/parallel_commands.py` (new) | Render required recovery images into public-library packet payloads and dependency/release stages; no discovery, polling, process ownership, or effect algorithms. |
| `homeassistant/components/lifx/virtual_power_effects.py` (new) | Narrow per-member virtual-derived ownership, producer exclusion, temporary prestate, and revision-safe cleanup; reuse existing effect implementations. |
| `homeassistant/components/lifx/switch.py` (new) | Physical/group local policy switches backed by config-entry options. |
| `const.py`, `util.py` | Policy/storage keys, shared ramp constant, small timing resolver. |
| `__init__.py`, `manager.py`, `coordinator.py` | Store startup, coordinator binding, legacy import, shared record access, lifecycle teardown/reconciliation. |
| `light.py`, `parallel_group.py` | Physical/group command adapters, scoped restore/prepare behavior, policy selection, successful-participant commits. |
| `parallel.py`, `_parallel_worker.py` only if the bootstrap interface requires it | Required payload batches, cancellable stage ordering, and participant outcomes; retain worker bootstrap fixes. |
| `select.py`, `button.py` | Route virtual-derived theme/Identify behavior through the shared interaction boundary. |
| `strings.json`, generated `translations/en.json`, `icons.json`, `README_FEATURES.md` | New control text and user-facing timing/policy documentation. |
| `tests/components/lifx/test_virtual_power.py` (new) | Image/record/persistence/uncertainty contracts. |
| `tests/components/lifx/test_switch.py` (new) | Switch defaults, options persistence, independent policies, disabled-entity behavior. |
| `tests/components/lifx/test_virtual_power_effects.py` (new) | Real producer exclusion and temporary-operation tests around mocked public device/transport boundaries. |
| Existing `test_light.py`, `test_parallel_group.py`, `test_parallel.py`, `test_parallel_spawn.py`, `test_init.py`, `test_button.py`, `test_select.py`, `test_services.py`, `test_hacs_export.py`, helpers/snapshots | Integration behavior, wire stages, lifecycle, compatibility, export. |
| `docs/integrations/lifx-virtual-power-off.md` (new) | Post-implementation behavior, deployment evidence, known limits, rollback source, all retained in ha-core. |
| `docs/integrations/lifx-device-groups.md` | Link the new canonical spec/plan/system note and summarize the shipped behavior. |

Keep existing large modules focused: move the new image/persistence and ownership responsibilities into the new modules above rather than restructuring unrelated code.

## Shared contracts used by the tasks

These names are the implementation seam, not permission to add a general device-state engine. All following tasks use these names consistently.

### Images and records: `virtual_power.py`

```python
@dataclass(frozen=True, slots=True)
class VirtualPowerTile:
    index: int
    width: int
    height: int
    frame_buffers: int
    colors: tuple[HSBK, ...]


@dataclass(frozen=True, slots=True)
class VirtualPowerImage:
    color: HSBK
    zones: tuple[HSBK, ...] = ()
    tiles: tuple[VirtualPowerTile, ...] = ()


@dataclass(frozen=True, slots=True)
class VirtualPowerToken:
    serial: str
    revision: int
```

- `capture_image(state: LIFXState) -> VirtualPowerImage`: copy complete public state; include all matrix/Ceiling pixels and addresses, not references to mutable lists.
- `darken_image(image: VirtualPowerImage) -> VirtualPowerImage`: change brightness only, in overall color and every zone/pixel.
- `merge_image(image: VirtualPowerImage, changes: HSBKChanges, *, zones: tuple[int, ...] | None = None) -> VirtualPowerImage`: use `replace_hsbk` semantics; preserve unspecified components and untargeted saved zones.
- `image_matches_layout(image: VirtualPowerImage, state: LIFXState) -> bool`: compare complete zone count/tile indices/dimensions/coverage before applying saved addresses.
- `resolve_recovery_image(record: VirtualPowerRecord, state: LIFXState, changes: HSBKChanges, *, zones: tuple[int, ...] | None = None) -> VirtualPowerImage`: explicit values override a compatible saved image; valid legacy overall HSBK can fill the current compatible layout; otherwise use valid observed layout/color and the approved last-resort brightness. A valid intentionally prepared zero target is not discarded merely because it is dark.
- `VirtualPowerRecord`: holds `virtual_off: bool`, `resume_required: bool`, `image: VirtualPowerImage | None`, `revision: int`, and `pending: VirtualPowerIntent | None`. Mere image presence does not activate restore.
- `VirtualPowerRequest`: frozen caller input with kind `Literal["virtual_off", "actual_off", "on", "prepare", "temporary"]`, `before_image: VirtualPowerImage | None`, `target_image: VirtualPowerImage | None`, `before_power_on: bool`, `before_virtual_off: bool`, `before_resume_required: bool`, and `duration: float`. It has no caller-assigned revision or token.
- `VirtualPowerIntent`: frozen persisted wrapper holding `request: VirtualPowerRequest`, `token: VirtualPowerToken`, delivery state `Literal["prepared", "uncertain"]`, and an optional wall-clock settle bound. Never restore a process's monotonic timestamp as a valid new-process deadline.
- `LIFXVirtualPowerStore(hass: HomeAssistant)`: owns `Store[dict[str, object]]` version `1`, key **`lifx.virtual_power`**, serial-keyed records, and one write lock. Pending group candidates are saved as one record-set update before dispatch.
- `await store.async_load() -> None`: load once and validate records independently; an invalid record/image does not invalidate unrelated entries.
- `store.async_get_record(serial: str) -> VirtualPowerRecord`: synchronous callback accessor, initialized after load.
- `await store.async_begin_many(requests: Mapping[str, VirtualPowerRequest]) -> dict[str, VirtualPowerToken]`: reserve current revisions before the first suspension; preserve valid previous recovery data; await a serialized save; return only still-current tokens. Do not hold this store lock across network I/O.
- `store.async_is_current(token: VirtualPowerToken) -> bool`: callback check used before dispatch/stages and after every relevant await.
- `await store.async_finish(token: VirtualPowerToken, outcome: Literal["completed", "uncertain", "not_sent"]) -> bool`: update only a current token, persist the result, and return whether it was accepted.
- `await store.async_reconcile(serial: str, state: LIFXState, *, now: datetime) -> None`: resolve current pending intent and observed visibility according to Spec Section 3; preserve a candidate through partial images.
- `await store.async_import_legacy(serial: str, extra: Mapping[str, object]) -> bool`: import only if no authoritative record exists; never overwrite a newer revision.

The implementation of `async_begin_many` wraps requests in token-bearing immutable intents; callers do not guess the next revision. A failed pre-dispatch save raises a translated HA error and sends no destructive packet. Noncritical clean observations may be coalesced; prepared/uncertain recovery barriers may not.

### Transport and ownership seams

- Add frozen `ParallelPacket(packet_type: int, payload: bytes)` in `parallel.py`.
- A required packet burst is `ParallelCommand("packet_batch", (packets,))`, where `packets` is `tuple[ParallelPacket, ...]`. Existing command kinds and constructor fields remain compatible.
- `compile_image_stages(image: VirtualPowerImage, state: LIFXState, *, duration_ms: int, power_on: bool | None) -> tuple[ParallelCommand, ...]` in `parallel_commands.py`: ordered per-member stages. Stages contain public-library payload bytes, so spawned children do not need pickled live devices/coordinators.
- `align_visible_stages(stages: tuple[tuple[ParallelCommand, ...], ...]) -> tuple[ParallelCommand | None, ...]`: pad preparatory stages before each member's final visible stage, chain with existing `second`, and keep absent members `None`.
- `await parallel.async_cancel_members(indexes: frozenset[int]) -> None`: capture the old involved request identities before its first suspension, invalidate their unsent mutation stages, and await dispatcher/worker acknowledgement of that cancellation boundary. Later requests must not be cancelled by an older barrier. Never claim to recall sent UDP packets or wait for an old full ACK timeout.
- `VirtualPowerEffectRegistry` in `virtual_power_effects.py`: manager-owned registry keyed by physical serial, with monotonically versioned ownership and runtime cleanup callbacks.
- `VirtualPowerFrameContext`: frozen internal context with `owner_id: int` and `tokens: Mapping[str, VirtualPowerToken]`. It is never accepted from service data.
- `registry.async_accepts_frame(context: VirtualPowerFrameContext, serial: str) -> bool`: callback returning whether the producer still owns the member and its token remains current.
- `await runtime._async_dispatch_owned_frame(context: VirtualPowerFrameContext, images: Mapping[str, VirtualPowerImage], *, duration: float) -> None`: internal group producer path that filters current owners and uses the existing synchronized transport without allocating a new user-command revision or superseding its own effect.
- `await registry.async_supersede(tokens: Mapping[str, VirtualPowerToken]) -> None`: revoke older affected producers and finish the cancellation boundary before a newer destructive write. Check current tokens before revocation and after each await so an old requester cannot revoke newer ownership; unaffected members may continue.
- `await registry.async_start_temporary(members: tuple[LIFXUpdateCoordinator, ...], *, operation: Literal["pulse", "identify"], power_on: bool) -> VirtualPowerTemporarySession`: capture only virtual-derived participants; skip logically-off participants when visibility is disallowed.
- `await session.async_restore() -> None`: restore only members whose ownership/token remains current; use their saved actual/virtual classification rather than the current policy switch.
- `await registry.async_stop(serials: frozenset[str], *, restore: bool) -> None`: explicit stop restores still-owned virtual-derived prestates; replacement uses `restore=False` and retains the image as the new request's merge/recovery base.
- `await registry.async_shutdown() -> None`: exclude producers and remove listeners/tasks before device/worker teardown; do not start visible restoration during HA shutdown.

Do not route group effects through an unsynchronized direct-device conductor merely to reuse its cleanup. Reuse rendering where public APIs permit it; retain group network ownership.

---

## Task 1: Build recoverable images and the persistent intent boundary

**Files:** Create `homeassistant/components/lifx/virtual_power.py` and `tests/components/lifx/test_virtual_power.py`; modify `homeassistant/components/lifx/const.py`, `homeassistant/components/lifx/manager.py`, `homeassistant/components/lifx/__init__.py`.

**Interfaces:** Produce the image/record/store contracts above. `manager.virtual_power` is the single `LIFXVirtualPowerStore`; `async_setup` awaits its load before exposing the manager to entry setup.

- [ ] **Step 1: Establish execution provenance and environment.** Read all applicable instructions; inspect `git status --short`, `git diff`, and the current source HEAD. Run `script/setup` in the chosen execution environment. Record baseline source/working changes in this plan. Do not commit or overwrite unrelated files.
- [ ] **Step 2: Add failing image/intent tests.** Use existing typed state factories and `hass_storage`. Include this behavior test with imports from the new module and `.helpers`:

```python
def test_darken_preserves_multizone_resume_image() -> None:
    device = create_mock_multizone_light()
    original = tuple(device.state.zones)
    image = capture_image(device.state)

    dark = darken_image(image)

    assert image.zones == original
    assert tuple(color.brightness for color in dark.zones) == (0.0, 0.0)
    assert tuple(color.hue for color in dark.zones) == (300.0, 255.0)
    assert tuple(device.state.zones) == original
```

Add parameterized cases for single/legacy strip/extended strip/multiple tiles/Ceiling 64- and 128-zone layouts; malformed HSBK, missing pixels, changed layout, explicit saved zero, and fallback to 100% only for virtual-derived recovery.

- [ ] **Step 3: Run the red tests.** `uv run --no-sync pytest tests/components/lifx/test_virtual_power.py -q`. Confirm failure is due to the missing contract, not environment/setup errors.
- [ ] **Step 4: Implement the types, image operations, and store.** Copy public state into immutable tuples; serialize HSBK with its public `as_dict`. Validate each restored numeric component/layout through the public HSBK constructor/current state. Use the awaited barrier pattern:

```python
async with self._write_lock:
    payload = self._serialize_records()
    await self._store.async_save(payload)
```

Build serial-keyed replacement records under this lock, reserve revisions before suspension, and recheck current tokens after saving. Keep pending candidates distinct from confirmed flags. Implement deterministic reconcile outcomes: known pending dark confirms off; actual off retains needed recovery; unchanged before-image resolves not-applied; partial images keep uncertainty and the original candidate.

- [ ] **Step 5: Test write ordering and failures.** Add an awaited-save Event gate: no packet callback may run before save release; save failure prevents dispatch. Test restart with prepared/uncertain records, repeated off retaining the first valid image, and stale token rejection. Use concrete parameter types and give each `pytest.param` a descriptive case ID.
- [ ] **Step 6: Run the task suite green and inspect the diff.** Re-run `test_virtual_power.py`; the independently testable deliverable is image preservation plus durable pending intent, without light-platform changes. Leave it uncommitted.

## Task 2: Add policy switches and restore authority before control

**Files:** Create `homeassistant/components/lifx/switch.py`, `tests/components/lifx/test_switch.py`; modify `homeassistant/components/lifx/__init__.py`, `homeassistant/components/lifx/coordinator.py`, `homeassistant/components/lifx/parallel_group.py`, `homeassistant/components/lifx/light.py`, `homeassistant/components/lifx/const.py`, `homeassistant/components/lifx/strings.json`, `homeassistant/components/lifx/icons.json`, generated `homeassistant/components/lifx/translations/en.json`, `tests/components/lifx/test_init.py`, `tests/components/lifx/__init__.py`.

**Interfaces:** Add `CONF_VIRTUAL_POWER_OFF = "virtual_power_off"`; physical and group runtimes expose read-only `virtual_power_off_enabled` from their entry options with default `True`. The physical coordinator exposes its serial-keyed record and compatibility read properties `virtual_off`, `resume_hsbk`, and `resume_required`.

- [ ] **Step 1: Add failing public switch tests.** Extend `async_setup_lifx_entry` with keyword `virtual_power_off: bool | None = None`; `None` creates no option, so default-on tests exercise the real upgrade behavior. Do not globally default legacy tests to Off. Seed explicit policies only where a legacy physical-power test needs them.

```python
async def test_virtual_power_switch_changes_policy_without_packets(
    hass: HomeAssistant,
) -> None:
    device = create_mock_light()
    entry = await async_setup_lifx_entry(hass, device)
    entity_id = "switch.my_group_my_bulb_virtual_power_off"
    assert hass.states.get(entity_id).state == "on"

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": entity_id}, blocking=True
    )

    assert entry.options[CONF_VIRTUAL_POWER_OFF] is False
    device.set_power.assert_not_awaited()
    device.set_waveform_optional.assert_not_awaited()
```

Add group/physical default-on, saved Off, independent mixed policies, marker unaffected by toggling, and disabled-switch reload cases. Patch only group transport/process startup, not the physical coordinators or shared records.

- [ ] **Step 2: Run the failing tests.** `uv run --no-sync pytest tests/components/lifx/test_switch.py tests/components/lifx/test_init.py -q`.
- [ ] **Step 3: Implement the two switch adapters in `switch.py`.** Physical adapter extends `LIFXEntity, SwitchEntity`; group adapter extends `LIFXParallelGroupEntity, SwitchEntity`. Both constructors retain their config entry as `self.entry`. Use config category, `translation_key="virtual_power_off"`, serial/group-ID suffixed unique IDs, and existing device metadata. Change policy only with:

```python
self.hass.config_entries.async_update_entry(
    self.entry,
    options={**self.entry.options, CONF_VIRTUAL_POWER_OFF: enabled},
)
self.async_write_ha_state()
```

The switch does not trigger a reload or restore a separate on/off cache. Add `Platform.SWITCH` to physical and group platform lists; the new switch setup handles its own group class rather than falling through the existing group number factory.

- [ ] **Step 4: Bind recovery authority before commands.** After the store loads, bind the coordinator's current serial/record before first refresh. Migrate legacy `virtual_off`/`resume_hsbk` once when no authoritative record exists, resolving the physical light's current registry ID rather than constructing it from its name. Read `restore_state.async_get(hass).last_states` and the stored extra-data object's `as_dict()` through the restore helper, not `.storage` files. Complete this before physical entry setup returns; group setup then sees authoritative physical records even if a light entity is disabled. Keep legacy extra-data export as a derived compatibility view for rollback, but remove its later unconditional runtime assignments.
- [ ] **Step 5: Add translation text and regenerate.** Entity name is exactly `Virtual Power Off`; describe packet-free setting changes and target-owned policy in the feature documentation later. Run `uv run --no-sync python3 -m script.translations develop --integration lifx` before the green tests.
- [ ] **Step 6: Verify green, including late entity addition.** Re-run the task tests and assert a re-enabled light cannot overwrite a newer record revision. The deliverable is persistent policy plus one recovery authority, with no changes to command behavior yet.

## Task 3: Share the minimum ordinary color ramp without fixing D26

**Files:** Modify `homeassistant/components/lifx/const.py`, `homeassistant/components/lifx/util.py`, `homeassistant/components/lifx/light.py`, `homeassistant/components/lifx/parallel_group.py`, `tests/components/lifx/test_light.py`, `tests/components/lifx/test_parallel_group.py`, `tests/components/lifx/helpers.py`; create `tests/components/lifx/test_util.py`.

**Interfaces:** `LIFX_MIN_COLOR_RAMP = 0.25` lives only in `const.py`. Add `resolve_color_ramp_duration(duration: float, *, explicit: bool, ordinary: bool) -> float` to `util.py`.

- [ ] **Step 1: Add the failing timing matrix.** Parameterize ordinary implicit defaults 0/0.1/0.8, explicit 0/0.1/0.8, and nonordinary hidden/On/Off writes. Include direct physical and group outgoing duration assertions.

```python
@pytest.mark.parametrize(
    ("duration", "explicit", "ordinary", "expected"),
    [
        pytest.param(0.0, False, True, 0.25, id="implicit-zero"),
        pytest.param(0.1, False, True, 0.25, id="implicit-short"),
        pytest.param(0.8, False, True, 0.8, id="implicit-long"),
        pytest.param(0.0, True, True, 0.0, id="explicit-zero"),
        pytest.param(0.1, True, True, 0.1, id="explicit-short"),
        pytest.param(0.0, False, False, 0.0, id="hidden-preparation"),
    ],
)
def test_color_ramp_duration(
    duration: float, explicit: bool, ordinary: bool, expected: float
) -> None:
    assert resolve_color_ramp_duration(
        duration, explicit=explicit, ordinary=ordinary
    ) == expected
```

- [ ] **Step 2: Run red.** `uv run --no-sync pytest tests/components/lifx/test_util.py tests/components/lifx/test_light.py tests/components/lifx/test_parallel_group.py -q`.
- [ ] **Step 3: Implement the resolver and update eligible call sites.** Use this complete rule:

```python
def resolve_color_ramp_duration(
    duration: float, *, explicit: bool, ordinary: bool
) -> float:
    if explicit or not ordinary:
        return duration
    return max(duration, LIFX_MIN_COLOR_RAMP)
```

Apply after existing duration/default selection, only where the ordinary physical color branch or its group analogue writes color. Convert seconds once at the raw group boundary. Pass `ordinary=False` for hidden preparations and actual/virtual power transitions. Leave `_default_transition_duration`'s separate D26 classification unchanged.

- [ ] **Step 4: Verify wire/SDK boundaries.** Physical explicit 0/0.1 periods must remain exact; group equivalents must serialize to 0/100 ms. Group zero/member0.8 still yields 800 ms, and a longer explicit transition wins. Check the final hidden-color/power stage pair separately from ordinary color tests.
- [ ] **Step 5: Run green and inspect constant usages.** Use dedicated content search for `LIFX_MIN_COLOR_RAMP` and duplicate `0.25` timing literals; update test imports rather than keeping two definitions. Do not change theme/effect timing algorithms.

## Task 4: Route physical virtual off, resume, and off-state preparation correctly

**Files:** Modify `homeassistant/components/lifx/light.py`, `homeassistant/components/lifx/coordinator.py`, `homeassistant/components/lifx/virtual_power.py`, `tests/components/lifx/test_light.py`, `tests/components/lifx/helpers.py`; add focused persistence errors to `homeassistant/components/lifx/strings.json` and regenerate `homeassistant/components/lifx/translations/en.json` before tests.

**Interfaces:** The physical adapter uses the store intent contract, current entry policy, and `resolve_recovery_image`. New `LIFXLight.async_write_recovery_image(image: VirtualPowerImage, *, duration: float) -> None` explicitly writes all required restore components through public device methods; it does not reuse caller-only component masks for a full resume.

- [ ] **Step 1: Reproduce the reported bug with faithful public-boundary state.** Add typed helper side effects that honor `set_waveform_optional` component flags and power writes. Keep specialized strip/tile helpers separate so tests contain no device-type branching.

```python
async def test_bare_on_restores_virtual_off_brightness(
    hass: HomeAssistant,
) -> None:
    device = create_mock_light()
    device.state.power = 65535
    device.state.color = HSBK(20.0, 0.25, 0.75, 3500)
    await async_setup_lifx_entry(hass, device)

    await hass.services.async_call(
        "light", "turn_off", {"entity_id": ENTITY_ID}, blocking=True
    )
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": ENTITY_ID}, blocking=True
    )

    assert device.state.color.brightness == 0.75
    assert hass.states.get(ENTITY_ID).state == "on"
    device.set_power.assert_not_awaited()
```

Use the new helper to apply public writes, so the test fails when a target is calculated but its brightness flag is false. Add temperature-only/color-only/explicit-brightness recovery and virtual-off→actual-off→on cases.

- [ ] **Step 2: Run red.** `uv run --no-sync pytest tests/components/lifx/test_light.py -k 'virtual or recovery or prepared or physical_power_off' -q`.
- [ ] **Step 3: Implement physical request routing.** Capture and persist the candidate request before destructive writes, then recheck its token. Retain the existing physical software-effect stop before applying a replacement; Task 7 wires cross-owner exclusion into these tokenized paths. Do not add a temporary no-op safety API. Virtual off sends brightness zero without power-off; actual off sends hardware power-off and retains needed virtual-derived image. Bare resume restores the image even when caller changes are empty. An actually-off resume writes its image immediately before power-on. Check current tokens around waits and use `async_finish` to distinguish success from uncertain delivery.
- [ ] **Step 4: Implement device-specific recovery writes.** Single light: explicit required waveform component flags. Strip: `set_all_color_zones` with a complete merged image, preserving untouched saved zones. Matrix: iterate addressed complete tile images using `set_matrix_colors` with milliseconds once; never use a device-wide shortcut that repaints other tiles. Preserve all Ceiling pixels/components. Do not globally change ordinary on-to-on partial writes.
- [ ] **Step 5: Implement off-state preparation and fallback.** A power-omitted request on virtual off updates the stored next-on image without positive visible writes. Repeated off preserves that image. Legacy HSBK-only records can fill current layout at known saved brightness, without claiming a pre-upgrade lost pixel pattern was recovered. Missing/invalid images use the approved compatible-layout/100% fallback only when no usable target exists.
- [ ] **Step 6: Verify green and legacy physical off explicitly.** Set policy Off in tests that require `set_power(False)`. Do not hide production default-On by changing a global fixture. Test toggling policy Off while virtually off produces no packets; the next actual-off/on still restores correctly.

## Task 5: Extend synchronized transport for pattern stages and cancellation barriers

**Files:** Create `homeassistant/components/lifx/parallel_commands.py`, `tests/components/lifx/test_parallel_commands.py`; modify `homeassistant/components/lifx/parallel.py`, `tests/components/lifx/test_parallel.py`, `tests/components/lifx/test_parallel_spawn.py`.

**Interfaces:** Produce `ParallelPacket`, `compile_image_stages`, `align_visible_stages`, and `async_cancel_members` as defined above. Existing `ParallelCommand` consumers continue working. Extend private requests to distinguish user completion from dispatcher quiescence; an early `done` result alone is not the cancellation barrier.

- [ ] **Step 1: Add red payload and stage tests.** For an extended strip over 82 zones, decode planned packets with public `packets.MultiZone.SetExtendedColorZones.unpack`; assert complete colors, 82-color chunk limits, indices, buffered preparation, and final apply. Legacy strips use public `SetColorZones` and `MultiZoneApplicationRequest`, including the uint8 addressing limit. Matrix/Ceiling fixtures cover ≤64 and >64 pixels, chains, and current layout mismatch rejection before network writes.

```python
def test_legacy_recovery_stages_preserve_color_and_duration() -> None:
    device = create_mock_multizone_light(extended=False)
    device.state.power = 65535
    image = capture_image(device.state)
    stages = compile_image_stages(
        image, device.state, duration_ms=800, power_on=None
    )
    preparation = stages[0].payload[0][0]
    release = stages[-1].payload[0][-1]
    prepared = packets.MultiZone.SetColorZones.unpack(preparation.payload)
    applied = packets.MultiZone.SetColorZones.unpack(release.payload)

    assert prepared.color == image.zones[0].to_protocol()
    assert prepared.apply == MultiZoneApplicationRequest.NO_APPLY
    assert applied.duration == 800
    assert applied.apply == MultiZoneApplicationRequest.APPLY_ONLY
```

Add a loopback runtime test that withholds a preparation ACK for one member; it must receive no final visible stage while acknowledged members retain a shared final deadline.

- [ ] **Step 2: Run red.** `uv run --no-sync pytest tests/components/lifx/test_parallel_commands.py tests/components/lifx/test_parallel.py -q`.
- [ ] **Step 3: Implement payload rendering with public packet classes.** Use `packet.PKT_TYPE` and `packet.pack()` for new forms; retain existing LIFX header generation. Strip preparations use NO_APPLY for the complete target image and a final APPLY_ONLY release; do not accidentally discard the last chunk's colors. For supported matrix frame buffers, write complete addressed pixels to a non-visible buffer and copy to display at release using `Tile.CopyFrameBuffer`. For devices without such buffering, place required Set64 packets in one final release burst, preserving per-tile addressing; describe alignment as network release, not atomic firmware rendering. Actual-off devices may prepare display colors under hardware-off before final power-on.
- [ ] **Step 4: Implement packet-batch ACK handling.** Each packet gets its own sequence and matching ACK; retain out-of-order ACKs for the whole batch rather than dropping later sequences while waiting for the first. A stage succeeds only after all required payloads acknowledge. Check cancellation before release and between sends/waits; preserve existing stage deadlines/retries. Do not mark a partly written image as fully completed.
- [ ] **Step 5: Implement per-member cancellation at the dispatcher boundary.** Capture old involved request IDs under `_dispatch_condition` before the public coroutine first suspends. Invalidate those mutations, signal worker CANCEL, and wake the dispatcher; never retarget cancellation to a later request that appeared during an await. Await a distinct quiescence acknowledgement for the old request/stage, not merely the superseded caller's completed future. Preserve a newer pending generation if one already exists. A current newer physical command may then dispatch. If an entire old group request is invalidated, unaffected effect participants may produce a subsequent eligible frame; do not restore/restart old ownership over the targeted member.
- [ ] **Step 6: Handle cancelled buffers explicitly.** Retain uncertainty when prepared device buffers may contain stale partial data. Every later recovery apply must first overwrite the complete compatible target buffer; do not flush an abandoned partial image. Add cancellation-before-apply and replacement-image tests.
- [ ] **Step 7: Verify green with real spawned loopback workers.** Run the two task suites plus `test_parallel_spawn.py`. Confirm built-in/custom/exported bootstrap still works, no live devices are contacted, and ACK barriers preserve visible durations. Do not solve general missing-slot startup D21 here.

## Task 6: Integrate group policy, per-member recovery, and participant commits

**Files:** Modify `homeassistant/components/lifx/parallel_group.py`, `homeassistant/components/lifx/parallel_commands.py`, `homeassistant/components/lifx/coordinator.py`, `tests/components/lifx/test_parallel_group.py`, `tests/components/lifx/helpers.py`.

**Interfaces:** Group off reads only `runtime.virtual_power_off_enabled`. Capture immutable eligible indexes and their tokens before dispatch. The success calculation is `eligible - result.failed_member_indexes` only when required stages completed; records of excluded members are not touched.

- [ ] **Step 1: Add the red policy and mixed-state matrix.** Cover all four group/physical policy combinations with real coordinators; vary which entity receives off. Group virtual-off/physical actual-off and its inverse must not consult the other target's policy. Add no-power mixed requests containing ordinary on, ordinary off, virtual off, and actual-off/resume-required members.
- [ ] **Step 2: Add a partial-success regression.** Configure transport to return:

```python
ParallelDispatchResult(
    ParallelDispatchOutcome.COMPLETED,
    request_id=1,
    failed_member_indexes=frozenset({1}),
)
```

Assert only eligible index zero commits, index one's candidate remains uncertain, and an excluded index receives neither success nor health reset. Preserve typed coordinator data identity. Add superseding requests that complete out of order and pattern recovery for a strip/tile member.

- [ ] **Step 3: Run red.** `uv run --no-sync pytest tests/components/lifx/test_parallel_group.py -k 'virtual or recovery or policy or participant or omitted' -q`.
- [ ] **Step 4: Implement scoped group routing.** Persist candidate images for participating off/recovery operations before any zero/pattern write. Use member-specific images/tokens for virtual-derived recovery, and compiler stages for complete patterns. Group policy Off builds actual power-off stages with preserved legacy fade behavior. Do not broaden the ordinary non-recovery aggregate color baseline under D01.
- [ ] **Step 5: Preserve omitted power for every participant.** Use each member's logical power in command-state/projection bookkeeping, not `display_power_level` copied to all. Off virtual-derived participants update next-on images while staying dark; ordinary actual-off members stay logically off. Preserve existing ordinary color merging for members not recovering.
- [ ] **Step 6: Commit only the successful current subset.** Mark excluded/failed/uncertain members appropriately, recheck tokens, reset health only for successful participants, and notify using `member.async_update_listeners()`. Keep group projection bounded and based on participating intended outcomes; do not overwrite `member.data` or trigger a compensating group-wide poll.
- [ ] **Step 7: Verify green.** Re-run the task suite and all group/transport tests. Check ordinary group color-merge expectations remain intact except timing/new logical-power requirements; record any unavoidable overlap explicitly rather than silently rewriting parked behavior.

## Task 7: Protect virtual-derived state from effects and temporary operations

**Files:** Create `homeassistant/components/lifx/virtual_power_effects.py`, `tests/components/lifx/test_virtual_power_effects.py`; modify `homeassistant/components/lifx/manager.py`, `homeassistant/components/lifx/coordinator.py`, `homeassistant/components/lifx/light.py`, `homeassistant/components/lifx/parallel_group.py`, `homeassistant/components/lifx/select.py`, `homeassistant/components/lifx/button.py`, `tests/components/lifx/test_button.py`, `tests/components/lifx/test_select.py`, `tests/components/lifx/test_services.py`.

**Interfaces:** Produce the registry/session contracts above as `manager.virtual_power_effects`. Each physical/group producer registers a serial-keyed exclusion callback; group callbacks remove member slots from future frames and invoke the Task 5 cancellation barrier for queued work.

- [ ] **Step 1: Add red interaction tests, using actual producer coroutines.** Start the existing group colorloop/pulse against a gated mock transport; turn the affected member virtually off via its physical entity or an overlapping group. Release the old frame gate and assert no stale frame/cleanup can relight it. Use `asyncio.Event`/bounded waits rather than long wall-clock sleeps. Do not use a mocked effect task whose cancellation counter is the only assertion.
- [ ] **Step 2: Add the operation-table matrix.** Test theme false prepares next-on state while dark; pulse false skips logically-off members; temporary pulse/Identify true reports temporary on then restores saved actual/virtual classification; switching policy during the temporary operation does not change the restore classification. Stop with no active owner stays dark. Ongoing effect stop restores only still-owned virtual-derived members. New commands suppress old restoration; unaffected members may continue.
- [ ] **Step 2a: Prove internal frames do not supersede their own owner.** A permitted multi-frame operation continues under one frame context until a real replacement arrives. Revoking one context prevents later/queued visible writes for that member; service callers cannot supply or forge the internal context.
- [ ] **Step 3: Run red.** `uv run --no-sync pytest tests/components/lifx/test_virtual_power_effects.py tests/components/lifx/test_button.py tests/components/lifx/test_select.py tests/components/lifx/test_services.py -q`.
- [ ] **Step 4: Implement ownership and producer exclusion.** Store per-member pre-operation image/classification and revision, not just a group-wide snapshot. Register existing producers when they start, including producers on ordinary on lights that may later receive virtual off; that registration alone does not change their ordinary rendering behavior. Before a new off or virtual-derived state request, use its reserved current tokens to revoke older ownership, stop/exclude its producer, and await the queued-write barrier. Recheck ownership after every await. Public physical conductor stop/restore must complete before the replacement target is applied; never allow it to restore after that target. Group tasks continue only over currently owned eligible slots. Use this restoration acceptance condition:

```python
if not self.store.async_is_current(token):
    return
if self.owners.get(token.serial) is not session:
    return
await session.async_restore_member(token.serial)
```

Define `VirtualPowerTemporarySession.async_restore_member(serial: str) -> None` to apply the saved image/classification through the appropriate adapter, then retire ownership; `async_restore` iterates this method over still-owned members. Never hold the persistence lock during the network operation.

- [ ] **Step 5: Adapt virtual-derived effects/themes only.** Mask off participants when `power_on: false`; capture/prep next-on images for explicit theme/parameter changes without exposing them. Quiesce known firmware rendering when necessary to maintain virtual darkness. For permitted temporary visibility, retain a separate temporary overlay/prestate so polling cannot discard it. Do not fix ordinary pulse modes/colorloop speed/Move direction/theme palettes as part of this task.

Route group producer frames through `_async_dispatch_owned_frame` using their captured internal context rather than recursively invoking the public user-command path. Filter via `async_accepts_frame` immediately before queueing and after relevant awaits; cancellation must cover already queued stale stages. Keep physical conductor writes under its public start/stop lifecycle plus registry ownership. This separates effect ticks from genuine superseding commands without changing the parked rendering algorithms.
- [ ] **Step 6: Bind clean teardown.** Remove registry callbacks on coordinator replacement and entry unload. At HA stop, invalidate owners and cancel producers before closing devices/workers, and avoid beginning visible restoration. Preserve the existing group HA-stop worker cleanup.
- [ ] **Step 7: Verify green and compare parked cases.** Run the interaction suite and group/physical effect suites. Confirm no generic group registration or direct unsynchronized group-control fallback was introduced.

## Task 8: Verify polling, restart, migration, and uncertain outcomes end to end

**Files:** Modify `homeassistant/components/lifx/coordinator.py`, `homeassistant/components/lifx/virtual_power.py`, `homeassistant/components/lifx/light.py`, `homeassistant/components/lifx/parallel_group.py`, `tests/components/lifx/test_virtual_power.py`, `tests/components/lifx/test_light.py`, `tests/components/lifx/test_init.py`, `tests/components/lifx/test_parallel_group.py`, `tests/components/lifx/test_parallel_spawn.py`.

**Interfaces:** `_async_update_data` calls store reconciliation with the newly read state before returning it to listeners. Runtime transition guards use monotonic time; persisted settle information is bounded and translated/expired on startup. An old `async_added_to_hass` cannot become a competing state authority.

- [ ] **Step 1: Add focused red integration tests.** Cover external visible state, actual power-off then bare recovery, any visible zone/pixel, intermediate fade samples, and failed refresh retaining data. Test valid/invalid legacy extras, store restart during uncertain off, stale physical-entry callbacks after group rebinding, and disabled entities.
- [ ] **Step 2: Exercise applied-write/lost-ACK and partial images.** Have the public device/loopback responder apply brightness zero but drop its ACK. Confirm the candidate survives, a later fresh dark observation resolves the issued intent, and bare on restores the original image. For partially applied zone writes, display observed visibility but retain the clean candidate and `resume_required`; a later successful command resolves uncertainty.
- [ ] **Step 3: Run red.** `uv run --no-sync pytest tests/components/lifx/test_virtual_power.py tests/components/lifx/test_init.py tests/components/lifx/test_light.py tests/components/lifx/test_parallel_group.py -q`.
- [ ] **Step 4: Finish reconciliation/lifecycle integration.** Use the freshly returned `device.state`, not stale `self.data`, for reconciliation. Bound fade protection to the effective duration/settle window and current revision; an ACK is not fade completion. Actual power-off may clear `virtual_off` while retaining `resume_required`; normal clean visible observations update the baseline only when not a partial/temporary/uncertain image. A fresh arbitrary zero read without intent creates no marker.
- [ ] **Step 5: Verify startup/late-restore ordering.** The authoritative load/import completes before entry setup returns; group dependency readiness must not observe an uninitialized record. Preserve policy through disabled switches. Re-enable a light with older RestoreEntity extras and confirm newer live records win. Restart during temporary visibility must retire the lost producer/overlay and restore the saved off classification through a current cleanup token, rather than granting new visibility or treating the temporary frame as external intent. Shutdown/reload invalidates prior tokens and cleans tasks/listeners symmetrically.
- [ ] **Step 6: Verify green.** Run the task suites and real-spawn shutdown test; review every error path for a preserved usable candidate and current-token checks. Do not fix general startup dependency D21 or On/Cross D26 while doing this work.

## Task 9: Document, export, and validate the complete local change

**Files:** Modify `homeassistant/components/lifx/README_FEATURES.md`, `homeassistant/components/lifx/strings.json`, generated `homeassistant/components/lifx/translations/en.json`, `docs/integrations/lifx-device-groups.md`, and `tests/components/lifx/test_hacs_export.py`; create `docs/integrations/lifx-virtual-power-off.md`; update relevant `tests/components/lifx/snapshots/` files only when their expected interfaces change.

- [ ] **Step 1: Document the actual policy and timing contract.** Describe independent default-On switches, packet-free toggles, group policy precedence, legacy actual-off selection, logical versus hardware power, pattern recovery/fallback, and the shared implicit 250 ms floor with explicit-duration exceptions. Keep the LIFX app zero-brightness/standby distinction accurate. Link the canonical spec/plan and parked backlog.
- [ ] **Step 2: Regenerate English translation and run focused suites.**

```sh
uv run --no-sync python3 -m script.translations develop --integration lifx
uv run --no-sync pytest tests/components/lifx/test_virtual_power.py tests/components/lifx/test_switch.py tests/components/lifx/test_virtual_power_effects.py tests/components/lifx/test_parallel_commands.py -q
```

- [ ] **Step 3: Run the full LIFX suite and export checks.**

```sh
uv run --no-sync pytest tests/components/lifx -q
```

Confirm `test_hacs_export.py` includes the new modules, switch platform, translations, and unchanged technical domain. Confirm custom/built-in/exported spawn loading remains covered. If snapshot changes are needed, inspect the semantic diff before updating them; do not blanket-accept snapshots.

- [ ] **Step 4: Run required static checks.**

```sh
uv run --no-sync ruff check homeassistant/components/lifx tests/components/lifx
uv run --no-sync ruff format --check homeassistant/components/lifx tests/components/lifx
uv run --no-sync prek run --all-files
git diff --check
```

Resolve new findings; record unrelated pre-existing failures precisely. Re-run only checks justified by subsequent changes. Review typed test parameters, no test-body branching, no device-ID identity changes, no `.storage` edits, and no silent scope expansion.

- [ ] **Step 5: Perform an independent implementation review before deployment.** Review actual code against the active spec and this plan; fix important findings with meaningful regressions. Update the plan/system note with what changed and verification evidence. Leave all changes uncommitted.

## Task 10: Deploy with the prescribed script and verify the private instance

**Files:** `ha-private-ws/scripts/ha_custom_component.sh` is used unchanged; update this plan and `docs/integrations/lifx-virtual-power-off.md` with actual deployment/readback/rollback evidence.

**Live targets to resolve first:** custom `light.test_group`, group entry `01M512FVNX1CMHK6AK40G1Z140`; members `light.living_room_tree_lamp_bottom`, `light.living_room_tree_lamp_middle`, `light.living_room_tree_lamp_top`. `light.living_room_tree_lamp` is an ordinary separate HA group helper, not the custom group. Do not assume the old IDs still exist without readback.

- [ ] **Step 1: Read deployment-time state and all interfering consumers.** Use HA MCP to resolve current entries, light/switch/Number states, Adaptive Lighting profiles, and exact-ID consumers. Follow ha-private-ws exhaustive search requirements before temporarily changing automations/settings, including scripts, scenes, helpers, dashboards, config-entry options, and AppDaemon references where relevant. Record every changed control and its inverse. Existing evidence showed AL switches may be toggled by automations; do not assume one switch write holds them disabled.
- [ ] **Step 2: Capture rollback source and starting states.** Read the local parent directory before creating an approved temporary backup directory. Preserve the pre-change custom `lifx` source using git archive/export tooling without checking out or overwriting the working tree. Record its actual path and revision. Snapshot test light states through HA's scene API, and separately record virtual/actual off classification, policies, Number values, AL/automation states, and any existing scene attributes a normal off-state scene cannot preserve. Use supported API/diagnostics, not direct storage access. Do not delete/recreate entries or rename entities.
- [ ] **Step 3: Inspect and perform the sync.** Run from `/Users/sabaata/Documents/Projects/ha-private-ws`:

```sh
scripts/ha_custom_component.sh sync --source-dir /Users/sabaata/Documents/Projects/ha-core/homeassistant/components/lifx --dry-run
scripts/ha_custom_component.sh sync --source-dir /Users/sabaata/Documents/Projects/ha-core/homeassistant/components/lifx
```

The real command stages the component and requests HA restart. Wait/read system health, entry setup and logs separately. Do not blindly repeat a slow restart. Confirm physical/group entries loaded, worker health, generated names/translations, and new switch identities before light tests.

- [ ] **Step 4: Isolate controlled transition/VPO tests.** Preserve and temporarily manage interfering AL/automation settings using supported configuration/services. Make explicit per-test transition calls where a known duration is required; for implicit-floor comparisons, verify no caller/profile/AL injected transition. Use actual bulb behavior/readbacks, not only the immediately updated group card.
- [ ] **Step 5: Execute the live acceptance table and record readbacks.**

| Case | Procedure | Required result |
| --- | --- | --- |
| Defaults and policy persistence | Read all new controls; save one Off, reload its entry, read again | Initial On; saved Off persists, including across HA restart later |
| Packet-free policy change | Switch a policy while its light is virtually off; read light/diagnostics | Darkness/classification/resume unchanged |
| Group On / physical Off policies | Group-off, then physical bare-on; physical-off, then group-on | Group uses virtual off; physical uses actual off; both recovery paths visible with saved brightness |
| Group Off / physical On policies | Group-off, then physical-on; physical-off, then group-on | Group uses actual off; physical uses virtual off |
| Both On / both Off | Target each entity in each setting | Target-owned behavior remains independent |
| Partial on after virtual off | Temperature-only and color-only on | Saved brightness restored; requested component overrides it |
| Off-state preparation | `lifx.set_state` without power sets 40%/3000 K while virtually off; then bare on | Remains dark until on; resumes at prepared settings |
| Repeated off and actual-off conversion | Virtual off twice; change policy Off; actual off; bare on | Original/prepared next-on image survives |
| Color ramp | With injected transitions excluded, compare direct/group ordinary color default0, explicit0, explicit0.1, explicit0.8 | 250 ms default floor; exact 0/100/800 ms explicit values; do not use this to test the deferred D26 selection difference |
| Nonzero inherited duration | Group matching Number0, physical matching Number0.8; ordinary color update | Member duration is 800 ms, not 250 ms or zero |
| Reload/restart recovery | Save virtual off and one Off policy, reload/restart, read state then bare-on | Policy and recovery persist; observed actual power reconciles correctly |
| Temporary/excluded operations | Pulse/Identify and theme false on a virtually-off participant | Permitted temporary visibility restores; disallowed visibility stays dark; saved next-on state intact |

Read back after the selected settle window and ordinary polling; inspect actual power/brightness where diagnostics/API exposes it. Logs should have no new virtual-state, pattern, worker, or serialization errors. Fault injection, missing-storage fallback, large patterns, and stale ACK races belong in local tests unless suitable live hardware/tooling exists. Explicitly identify which pattern layouts were not available live.

- [ ] **Step 6: Restore the starting environment and verify it.** Restore light colors/brightness and intended actual/virtual off classification through the new supported controls, then restore policy/Number settings and AL/automation state. Read the restored switches again after automation activity settles. Remove only temporary test scenes/helpers created by this session. Compare the final inventory with the initial record; do not declare restoration from service-call success alone.
- [ ] **Step 7: Roll back if required.** If implementation prevents safe restoration or startup is unhealthy, use the recorded pre-change source with the same sync script, then verify restart, entry/worker health and final light/AL state. Keep config entries and options; older code may ignore new option/storage fields. Do not delete the new store or edit `.storage` to roll back. Record any inability to preserve new-only pattern semantics under the old version.
- [ ] **Step 8: Final evidence handoff.** Update the spec/plan/system note with executed tests, deployment source, actual entity IDs, limitations, final restored states, and rollback path. Inspect git status/diff for only intended files. No commits, pushes, or releases.

## Spec coverage and completion checklist

| Active requirement | Tasks |
| --- | --- |
| Independent default-On switches, packet-free toggles, disabled-entity persistence | 2, 10 |
| Target-owned off policy and policy-independent recognition | 4, 6, 8, 10 |
| Exact HSBK/zone/pixel recovery, compatible-layout/100% fallback | 1, 4, 5, 6, 8 |
| `resume_required` after actual off, pending/uncertain durable intent | 1, 4, 6, 8 |
| Power-omitted mixed-member preservation and next-on preparation | 4, 6, 10 |
| Successful eligible subset, lost ACK, partial writes, stale completion | 1, 5, 6, 8 |
| Temporary/ongoing operation table and cross-owner producer exclusion | 5, 7, 8, 10 |
| Shared minimum constant, explicit-duration priority, hidden-stage timing | 3, 5, 9, 10 |
| Fresh polling, bounded transitions, legacy authority/startup/teardown | 1, 2, 7, 8 |
| Translation/export, full tests/static checks, unchanged domain | 2, 9 |
| Sync-script deploy, live readback, environment restoration, rollback | 10 |
| Deferred audit findings remain parked | Every task review; 9 final review |

- [ ] All active spec requirements have implementation and meaningful verification evidence.
- [ ] No deferred issue was silently promoted beyond the named necessary overlaps.
- [ ] Local and live evidence are distinguished; missing live hardware coverage is explicit.
- [ ] Test light/configuration restoration is confirmed by readback.
- [ ] Final diff is human-reviewable and uncommitted.

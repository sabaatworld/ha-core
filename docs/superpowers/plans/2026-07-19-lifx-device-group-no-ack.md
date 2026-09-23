# LIFX Device Group No-ACK Implementation Plan

> **Superseded 2026-07-19:** Use `2026-07-19-lifx-device-group-staged-ack-preemption.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make LIFX Device Groups send low-skew commands through permanent warmed workers without ACK waits, while physical lights alone determine group availability and ordinary polling remains the actual-state source.

**Architecture:** `parallel.py` is a transport supervisor: it owns one spawned UDP worker per member, a reusable dispatch gate, exact-slot replacement after confirmed death, reconnection of living workers, and a generation-aware latest-unsent dispatcher. `parallel_group.py` depends on physical coordinators for availability and actual state; it submits packets, retains one short-lived aggregate optimistic state, and requests reconnect/reinitialization only at lifecycle boundaries.

**Tech Stack:** Home Assistant Python 3.14, `DataUpdateCoordinator`, `asyncio`, spawned `multiprocessing`, connected UDP sockets, pytest.

## Global Constraints

- Preserve the `lifx` domain, manifest version, persisted `parallel_group` entry type, and internal parallel naming; user-visible copy remains **Device Group**.
- Run Git commands and every test through `ssh root@192.168.8.28` in `/config/workplace/ha-core`; use `.venv/bin/python -m pytest` because `uv` is unavailable on that host.
- Do not create commits during this execution unless the user later requests one. The per-task commit snippets are retained for a later handoff, not executed now.
- Group entity availability is false **only** when a configured physical entry is unloaded/replaced or its original coordinator has `last_update_success == False`.
- Group startup raises `ConfigEntryNotReady` until all physical dependencies are ready. Home Assistant owns indefinite retry scheduling; do not add a retry limit.
- Never set `ack_required`, wait for an ACK, mock an ACK timeout, query worker state, or schedule a physical coordinator refresh from a Device Group command.
- Do not kill a living worker for a transport error. Replace only a confirmed-dead process, EOF, or broken control pipe; do not disturb other worker slots.
- Transport errors fail only the affected command. They do not reload the group or alter group availability.
- Keep one group-wide optimistic state, never mutate a physical coordinator cache, and expire it at `PHYSICAL_LIGHT_POLL_INTERVAL * 1.5` from a shared constant.
- A newer command supersedes only work that has not crossed the common dispatch gate. Packets already sent are not cancellable.
- Physical entry unload/reload rebuilds the group runtime. A merely unavailable/recovered coordinator keeps its group worker and asks it to reconnect/re-probe.
- Every test parameter has a type annotation. Keep real worker coverage narrow: one loopback responder fixture and only the lifecycle/transport cases below.

## File Structure

- `homeassistant/components/lifx/const.py`: shared physical poll interval and derived Device Group optimistic expiry.
- `homeassistant/components/lifx/coordinator.py`, `light.py`: restore stable physical-light refresh behavior and its transition regression coverage; Device Group code will not call it.
- `homeassistant/components/lifx/parallel.py`: no-ACK packets, resilient receive/preflight, warmed worker lifecycle, reconnect, exact worker replacement, and latest-unsent dispatch.
- `homeassistant/components/lifx/parallel_group.py`: dependency lifecycle listeners, reconnect requests, no-ACK state dispatch, and aggregate optimistic state.
- `tests/components/lifx/test_parallel.py`: small loopback UDP responder and real spawned-worker transport tests.
- `tests/components/lifx/test_parallel_group.py`: Device Group availability, optimism, rapid command, and actual lifecycle tests.
- `tests/components/lifx/test_config_flow.py`: one Device Group config-flow success test.
- `docs/superpowers/plans/2026-07-18-lifx-device-group-reliability.md`: mark the ACK plan superseded.

---

### Task 1: Restore the physical-light regression baseline and share polling timing

**Files:**
- Modify: `homeassistant/components/lifx/const.py`
- Modify: `homeassistant/components/lifx/coordinator.py`
- Modify: `homeassistant/components/lifx/light.py`
- Modify: `tests/components/lifx/test_light.py`

**Interfaces:**
- Produces `PHYSICAL_LIGHT_POLL_INTERVAL: Final = 10` and `DEVICE_GROUP_OPTIMISTIC_STATE_EXPIRY: Final = PHYSICAL_LIGHT_POLL_INTERVAL * 1.5` in `const.py`.
- Restores `LIFXLight.update_during_transition(when: int) -> None` as the physical-light-owned refresh path. Device Groups do not invoke it.

- [ ] **Step 1: Reproduce and preserve the current physical regression.**

  Run on HA host:

  ```sh
  .venv/bin/python -m pytest tests/components/lifx/test_light.py::test_transition_duration_legacy_multizone tests/components/lifx/test_light.py::test_transitions_color_bulb tests/components/lifx/test_light.py::test_transition_duration_numbers -v
  ```

  Expected before the fix: the three existing transition assertions fail.

- [ ] **Step 2: Write the focused regression assertions before modifying implementation.** Keep the existing exact assertions: legacy multizone sends three zone commands with the configured duration; an already-off transition uses duration zero; a zero configured fade omits `duration`.

- [ ] **Step 3: Restore physical entity-local refresh ownership and correct the already-off duration branch.** Remove the unused coordinator post-command generation/helper introduced for ACK reconciliation and restore the prior physical-light flow:

  ```python
  async def update_during_transition(self, when: int) -> None:
      self._cancel_postponed_update()
      self.async_write_ha_state()
      await self.coordinator.async_request_refresh()
      if when > 0:
          self.postponed_update = async_call_later(
              self.hass, timedelta(milliseconds=when), _async_refresh
          )
  ```

  Retain the existing physical `LIFX_STATE_SETTLE_DELAY` before this method, restore `_cancel_postponed_update()` on removal, and remove coordinator-only imports, fields, methods, and tests that are no longer referenced. In `set_state`, an already-off request must call `set_power(False, duration=0)` only when an explicit transition was supplied; without an explicit transition it calls `set_power(False)` and omits `duration`. This preserves the test contract while an on light still uses its configured off-duration.

- [ ] **Step 4: Move the poll interval to the shared constant.** Replace coordinator-local `LIGHT_UPDATE_INTERVAL` with `PHYSICAL_LIGHT_POLL_INTERVAL`, and define the derived Device Group expiry from it without duplicating `10` or `15`.

- [ ] **Step 4a: Preserve the existing Fade entity IDs in the WIP test edits.** The number entity descriptions retain `transition_on_duration` and `transition_off_duration`, while the translated user-facing names determine the stable entity IDs `number.my_group_my_bulb_fade_on_time` and `number.my_group_my_bulb_fade_off_time`. Do not regenerate translations or rename the physical number UX as part of this task.

- [ ] **Step 5: Run the three regressions and the affected physical tests.**

  Run on HA host:

  ```sh
  .venv/bin/python -m pytest tests/components/lifx/test_light.py -k 'transition or duration' -v
  ```

  Expected: PASS.

- [ ] **Step 6: Commit the physical baseline repair.**

  ```sh
  git add homeassistant/components/lifx/const.py homeassistant/components/lifx/coordinator.py homeassistant/components/lifx/light.py tests/components/lifx/test_light.py
  git commit -m "Fix LIFX physical transition refresh regression"
  ```

### Task 2: Define the no-ACK worker protocol and resilient receive path

**Files:**
- Modify: `homeassistant/components/lifx/parallel.py`
- Create: `tests/components/lifx/test_parallel.py`

**Interfaces:**
- `LIFXParallelRuntime(hass: HomeAssistant, hosts: Iterable[str], *, port: int = DEFAULT_PORT)` retains the production default and permits an isolated loopback test port.
- `async_dispatch(commands: tuple[ParallelCommand, ...]) -> None` has no ACK callback.
- `async_request_reconnect(index: int, host: str) -> None` queues a best-effort reconnect for one living worker.

- [ ] **Step 1: Write a loopback UDP responder fixture and failing no-ACK test.** The responder must implement only startup `GetService`/`StateService`, record every Set datagram, and deliberately send no acknowledgements. Assert a real spawned worker starts, a `SetPower` arrives, and frame-address flags contain no ACK bit:

  ```python
  assert flags & 0b10 == 0
  await runtime.async_dispatch((ParallelCommand("power", (True, 0)),))
  assert responder.wait_for_packet(SET_POWER)
  ```

- [ ] **Step 2: Add malformed-datagram coverage before implementation.** Have the responder emit, before the valid `StateService`, a truncated datagram, declared-length mismatch, unknown packet type, and malformed payload. Assert startup succeeds and a later Set packet is received. This proves the worker survives parsing noise.

- [ ] **Step 3: Remove ACK and state-query protocol code.** Make every `_set_*` packet use `_header(..., ack_required=False)`; delete `ACKNOWLEDGEMENT`, `_wait_for_packet` uses after a Set, `async_stage_colors`, `async_query_states`, `_query_states`, `_handle_query_state`, `ParallelLightState`, and ACK/second-phase collection. Replace an ACK-gated pair with one worker command containing ordered packets sent immediately after the same gate.

- [ ] **Step 4: Centralize receive validation and preserve warm workers.** Implement a preflight receiver that catches `struct.error` and `ValueError`, rejects bad declared sizes/payloads/unmatched types, and continues until timeout. Keep exceptions inside the worker command loop. A startup preflight failure reports `PREFLIGHT_ERROR` for `ConfigEntryNotReady`; a later send/reprobe error reports `ERROR`, closes/recreates only its UDP socket, and returns to the command loop.

- [ ] **Step 5: Run real-worker no-ACK and malformed-packet tests.**

  Run on HA host:

  ```sh
  .venv/bin/python -m pytest tests/components/lifx/test_parallel.py -k 'no_ack or malformed' -v
  ```

  Expected: PASS without an ACK response or worker exit.

- [ ] **Step 6: Commit the no-ACK transport protocol.**

  ```sh
  git add homeassistant/components/lifx/parallel.py tests/components/lifx/test_parallel.py
  git commit -m "Refactor LIFX Device Group workers to no-ACK dispatch"
  ```

### Task 3: Supervise warmed workers and make unsent requests latest-wins

**Files:**
- Modify: `homeassistant/components/lifx/parallel.py`
- Modify: `tests/components/lifx/test_parallel.py`

**Interfaces:**
- Each `_Worker` remains in its original member index.
- Runtime has one supervisor-owned pending dispatch and a monotonic shared generation visible to workers at the dispatch gate.
- `_replace_dead_worker(index: int) -> None` only runs after `process.is_alive()` is false, EOF, or a broken control pipe is confirmed.

- [ ] **Step 1: Write the failing real-process replacement test.** Start two workers through the loopback responder, terminate exactly one process in the test, wait until `is_alive()` is false, submit a command, then assert only that slot has a new PID, both slots are alive, the responder sees the packet, and the group-level dependency predicate remains unaffected.

- [ ] **Step 2: Write the failing latest-unsent test.** Delay a worker before its common gate, submit an `on` request then an `off` request, and assert the old undisbursed generation reports superseded, the final observed Set packet is off, and no five-second ACK wait occurs. Do not assert that an already-sent first packet disappears.

- [ ] **Step 3: Implement the supervisor mailbox.** Start one runtime dispatcher thread during `_start`; it owns pipe writes/reads and a single pending request. `async_dispatch` submits a generation and waits only until it is sent, fails, or is superseded. A newer request overwrites pending work and updates the shared current generation. Workers check that generation after their `READY` signal; stale workers consume the released gate, report `CANCELLED`, and return to their warm loop without sending.

- [ ] **Step 4: Implement exact-slot replacement and reconnect.** Before dispatch, reap each worker and replace only confirmed-dead slots using the existing gates and a fresh pipe/process. For a living UDP failure, do not replace or stop it; report the command failure and queue `RECONNECT` using the updated host. A reconnect failure remains a command-level error and leaves the process alive for future retries.

- [ ] **Step 5: Remove five-second ACK-path locking.** The supervisor may bound only control-plane preparation/replacement. It must not hold a transport lock while waiting for device acknowledgement, because acknowledgements no longer exist.

- [ ] **Step 6: Run the real-worker lifecycle tests.**

  Run on HA host:

  ```sh
  .venv/bin/python -m pytest tests/components/lifx/test_parallel.py -k 'replacement or latest or reconnect' -v
  ```

  Expected: PASS; the replacement test observes a new PID only for the killed slot.

- [ ] **Step 7: Commit warmed-worker supervision.**

  ```sh
  git add homeassistant/components/lifx/parallel.py tests/components/lifx/test_parallel.py
  git commit -m "Keep LIFX Device Group workers warm and replace dead slots"
  ```

### Task 4: Rework Device Group command, availability, and reinitialization semantics

**Files:**
- Modify: `homeassistant/components/lifx/parallel_group.py`
- Modify: `tests/components/lifx/test_parallel_group.py`

**Interfaces:**
- `LIFXParallelGroupRuntime.available` remains `_members_are_ready(...)` only.
- `_begin_projection(states) -> int` installs one aggregate state and schedules `DEVICE_GROUP_OPTIMISTIC_STATE_EXPIRY`.
- `async_set_state(**kwargs) -> None` dispatches no-ACK commands and never invokes `LIFXUpdateCoordinator.async_schedule_post_command_refresh`.

- [ ] **Step 1: Replace the ACK-era mock tests with failing no-ACK tests.** Delete tests that assert individual ACK refreshes, ACK timeouts, and stale ACK callbacks. Keep dependency-only availability and cache-isolation tests. Add assertions that dispatch receives no callback, no physical refresh helper is awaited, and a command transport error clears only its current projection while `runtime.available` remains dependency-derived.

- [ ] **Step 2: Change the optimistic TTL to the shared derived constant.** Replace `asyncio.sleep(0.3)` with `asyncio.sleep(DEVICE_GROUP_OPTIMISTIC_STATE_EXPIRY)`. Assert the state remains group-wide, uses the latest command generation, expires after `PHYSICAL_LIGHT_POLL_INTERVAL * 1.5`, and leaves all member `device.color`/`power_level` values unchanged.

- [ ] **Step 3: Submit packets without ACK-stage flows.** Remove `_apply_acknowledged_state`, stage callbacks, timeout refresh tasks, and any direct-query recovery. Build ordered packets for off-to-on color/power operations through the new `ParallelCommand` shape and call only `parallel.async_dispatch(commands)`.

- [ ] **Step 4: Drive reconnect from physical lifecycle without changing availability.** Store each member's prior readiness/host. Coordinator listener updates entity state on every physical update; on false-to-true availability or IP change, create a background `parallel.async_request_reconnect(index, host)` task. On false, leave workers alive and make the group unavailable. Only entry state leaving `LOADED` schedules orderly full group reload.

- [ ] **Step 5: Add the actual unload/reload lifecycle test.** Use real worker processes via the loopback responder and a runtime factory with its test port. Unload one physical entry, assert group teardown/reload scheduling and old process cleanup; reload it with a replacement coordinator, assert fresh workers initialize. Also assert ordinary `last_update_success` false/true only toggles group availability and requests reconnect on recovery.

- [ ] **Step 6: Run Device Group behavior tests.**

  Run on HA host:

  ```sh
  .venv/bin/python -m pytest tests/components/lifx/test_parallel_group.py -v
  ```

  Expected: PASS with no ACK mocks or state-refresh assertions.

- [ ] **Step 7: Commit Device Group lifecycle behavior.**

  ```sh
  git add homeassistant/components/lifx/parallel_group.py tests/components/lifx/test_parallel_group.py
  git commit -m "Align LIFX Device Group state with physical dependencies"
  ```

### Task 5: Cover Device Group configuration and remove superseded documentation

**Files:**
- Modify: `tests/components/lifx/test_config_flow.py`
- Modify: `docs/superpowers/plans/2026-07-18-lifx-device-group-reliability.md`
- Modify: `docs/superpowers/plans/2026-07-19-lifx-device-group-no-ack.md`

**Interfaces:**
- The existing `parallel_group` config flow creates the persisted entry type while UX continues to say Device Group.

- [ ] **Step 1: Write the config-flow success test.** Add two physical Lifx entries and entity-registry light entities, navigate user menu → `parallel_group`, submit a group title and those entity IDs, then assert `CREATE_ENTRY`, `ENTRY_TYPE_PARALLEL_GROUP`, a generated group ID, and the sorted physical entry IDs. One happy path is sufficient because current validation tests cover invalid input.

- [ ] **Step 2: Run the failing config-flow test, then fix only the flow or translations if it exposes a gap.**

  Run on HA host:

  ```sh
  .venv/bin/python -m pytest tests/components/lifx/test_config_flow.py -k 'parallel_group' -v
  ```

  Expected: PASS after the focused test is added.

- [ ] **Step 3: Mark the previous plan superseded.** Add this exact notice below its title:

  ```markdown
  > **Superseded 2026-07-19:** ACK-based reconciliation is no longer desired. Use `2026-07-19-lifx-device-group-no-ack.md`.
  ```

- [ ] **Step 4: Mark completed checkboxes in this plan as each task clears review.** Never mark a task complete solely because code was edited; require its recorded test command and clean review.

- [ ] **Step 5: Commit configuration coverage and plan linkage.**

  ```sh
  git add tests/components/lifx/test_config_flow.py docs/superpowers/plans
  git commit -m "Test LIFX Device Group configuration flow"
  ```

### Task 6: Final review and HA-host verification

**Files:**
- Modify: `docs/superpowers/plans/2026-07-19-lifx-device-group-no-ack.md`

- [ ] **Step 1: Re-read the Global Constraints against the final diff.** Verify every item has either an executable test or an inspected direct code path. In particular, verify there is no `ACK`, `async_query_states`, `async_stage_colors`, worker state query, or Device Group coordinator-refresh call left.

- [ ] **Step 2: Run all focused transport, group, configuration, and physical regression tests.**

  Run on HA host:

  ```sh
  .venv/bin/python -m pytest tests/components/lifx/test_parallel.py tests/components/lifx/test_parallel_group.py tests/components/lifx/test_config_flow.py tests/components/lifx/test_light.py -v
  ```

  Expected: PASS.

- [ ] **Step 3: Run the complete Lifx suite.**

  Run on HA host:

  ```sh
  .venv/bin/python -m pytest tests/components/lifx -v
  ```

  Expected: PASS.

- [ ] **Step 4: Run formatting/linting and inspect the final change.**

  Run on HA host:

  ```sh
  .venv/bin/python -m prek run --all-files
  git diff --check HEAD~1..HEAD
  git status --short --untracked-files=no
  ```

  Expected: all commands exit zero; do not include unrelated metadata changes.

- [ ] **Step 5: Obtain final code review, resolve Critical/Important findings, and re-run the affected tests.**

- [ ] **Step 6: Mark every task checkbox and the progress ledger only after fresh verification, then commit the final plan status.**

  ```sh
  git add docs/superpowers/plans/2026-07-19-lifx-device-group-no-ack.md
  git commit -m "docs: record LIFX Device Group verification"
  ```

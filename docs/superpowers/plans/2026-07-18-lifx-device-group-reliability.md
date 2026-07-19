# LIFX Device Group Reliability Implementation Plan

> **Superseded 2026-07-19:** ACK-based reconciliation is no longer desired. Use `2026-07-19-lifx-device-group-no-ack.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make LIFX Device Groups retain warmed multiprocessing workers, accurately follow their physical LIFX members' availability and state, and never become unavailable because of command or polling timeouts.

**Architecture:** The Device Group remains a precise UDP transport layer with one spawned process and connected socket per member. Physical `LIFXUpdateCoordinator` instances remain the only authority for state, polling, retrying, and availability. A single, short-lived group-wide optimistic state presents the requested HA state between dispatch and the refreshed physical states; it is not copied into member coordinator caches.

**Tech Stack:** Home Assistant Python 3.14, `DataUpdateCoordinator`, `aiolifx`, asyncio, spawned `multiprocessing` workers, pytest with Home Assistant test fixtures.

## Implementation status (2026-07-18)

- Implemented the dependency-only availability predicate, startup retry behavior, warmed-worker timeout behavior, coordinator-owned 0.3-second refresh lifecycle, group-wide optimistic state, stale-ACK protection, and Device Group UX wording.
- Focused verification on the HA host: `13 passed` across Device Group lifecycle/timeout/rapid-command tests, physical coordinator refresh timing, and updated UX tests. Ruff, Ruff format, and `git diff --check` pass for the modified files.
- The full `tests/components/lifx` run currently has three unrelated physical-light expectation failures: legacy multizone command expectations and two already-off transition-duration expectations. They do not exercise `parallel_group.py` and must be resolved separately before claiming a fully green integration suite.

## Global Constraints

- Keep this a drop-in custom integration with the `lifx` domain and manifest version unchanged.
- Keep internal `parallel` naming and config-entry compatibility where useful; all user-visible language must say **Device Group**.
- Do not mutate `LIFXUpdateCoordinator.device` state from Device Group ACKs or direct UDP responses.
- Device Group entity availability is false only when a configured physical member is unloaded, replaced, or has `last_update_success == False`.
- Group setup must raise `ConfigEntryNotReady` until every member is ready; rely on Home Assistant's indefinite retry policy, whose delay caps at 600 seconds.
- ACK, worker, and direct-state-query timeouts may fail a command and trigger physical refresh attempts, but may not reload the group or change entity availability by themselves.
- Retain persistent spawned worker processes and UDP sockets across commands. Recreate them only for group unload, a physical member entry replacement/reload, or explicit internal worker replacement.
- For each individual member ACK, schedule the normal physical-coordinator refresh flow after `LIFX_STATE_SETTLE_DELAY` (0.3 seconds); never wait for ACKs from other members.
- Use one group-wide optimistic state with a bounded expiry. It represents the requested Device Group HA state, not individual member HSBK caches.
- Preserve the physical-light last-command-wins behavior: stale ACKs, delayed refreshes, and transition-end refreshes cannot overwrite a newer command.
- Tests must add type annotations to every test parameter and use `pytest.mark.usefixtures` for unused fixtures.

---

## File Structure

- `homeassistant/components/lifx/coordinator.py`: Own the reusable post-command physical state-refresh lifecycle and its stale-command cancellation.
- `homeassistant/components/lifx/light.py`: Delegate the existing physical-light transition refresh behavior to the coordinator helper.
- `homeassistant/components/lifx/parallel.py`: Keep workers warm, expose ACK/error events without converting transient transport faults into group teardown, and support ordered latest-command dispatch.
- `homeassistant/components/lifx/parallel_group.py`: Model dependency-only availability, group-wide optimistic state expiry, physical coordinator refresh dispatch, member reinitialization, and Device Group metadata.
- `homeassistant/components/lifx/strings.json` and `homeassistant/components/lifx/translations/en.json`: Remove user-visible “parallel” wording.
- `tests/components/lifx/test_parallel_group.py`: New focused behavior coverage for lifecycle, availability, synchronization, timeout, rapid-command, and worker-persistence rules.
- Existing LIFX test files: Extend only where their existing fixtures are the clearest assertion point.

## Task 1: Establish Device Group test scaffolding and dependency lifecycle contract

**Files:**
- Create: `tests/components/lifx/test_parallel_group.py`
- Modify: `homeassistant/components/lifx/parallel_group.py`

**Interfaces:**
- Produces a test helper that creates physical `LIFXUpdateCoordinator` members and a Device Group config entry.
- Produces `LIFXParallelGroupRuntime.dependencies_available: bool`, based on member entry identity/state and `last_update_success`.

- [ ] **Step 1: Write failing setup-retry tests.** Cover missing member runtime data, a member in `SETUP_RETRY`, and a loaded member with `last_update_success=False`. Each must cause `async_setup_parallel_group_entry` to raise `ConfigEntryNotReady` before worker startup.
- [ ] **Step 2: Run the focused tests and confirm they fail because readiness currently ignores coordinator availability or converts startup faults to a generic setup error.**

  Run: `uv run pytest tests/components/lifx/test_parallel_group.py -k setup_retry -v`

- [ ] **Step 3: Implement one readiness predicate used by startup and runtime availability.** It must check the original config entry, `ConfigEntryState.LOADED`, coordinator identity, and `last_update_success`; startup must map failed preflight cleanup to `ConfigEntryNotReady`.
- [ ] **Step 4: Write and run tests that a later member coordinator failure makes every Device Group entity unavailable, and recovery makes it available again without unloading the group or destroying the pool.**
- [ ] **Step 5: Commit the focused lifecycle contract.**

## Task 2: Move physical state refresh ownership into the coordinator

**Files:**
- Modify: `homeassistant/components/lifx/coordinator.py`
- Modify: `homeassistant/components/lifx/light.py`
- Test: `tests/components/lifx/test_light.py`
- Test: `tests/components/lifx/test_parallel_group.py`

**Interfaces:**
- Produces `LIFXUpdateCoordinator.async_schedule_post_command_refresh(duration_ms: int, generation: int | None = None) -> None` or an equivalently named coordinator-owned method.
- The helper waits 0.3 seconds, requests the coordinator's debounced refresh, and schedules the normal end-of-transition refresh while cancelling a stale scheduled callback.

- [ ] **Step 1: Write failing physical-light tests proving a newer command cancels the prior transition-end refresh and retains the 0.3-second debounced refresh behavior.**
- [ ] **Step 2: Run the targeted physical-light tests and confirm they fail against the entity-owned `postponed_update` implementation.**

  Run: `uv run pytest tests/components/lifx/test_light.py -k transition -v`

- [ ] **Step 3: Move the delayed-refresh state and scheduling logic from `LIFXLight` into `LIFXUpdateCoordinator`, then make physical lights invoke that shared helper after their command succeeds.** Preserve `async_set_updated_data(None)` only for physical commands issued through the physical entity.
- [ ] **Step 4: Write and run Device Group tests that one ACK calls the corresponding member's coordinator helper independently of other members.**
- [ ] **Step 5: Commit the shared physical refresh lifecycle.**

## Task 3: Replace direct Device Group state confirmation with group-wide optimism and coordinator refreshes

**Files:**
- Modify: `homeassistant/components/lifx/parallel_group.py`
- Test: `tests/components/lifx/test_parallel_group.py`

**Interfaces:**
- Produces one `_OptimisticGroupState` value containing the requested Device Group light attributes and an expiry/generation; it is never keyed by member.
- `LIFXParallelGroupLight` reads the optimistic value until it expires or a newer command supersedes it, then computes all attributes from physical coordinator state.

- [ ] **Step 1: Write failing tests for a group command that shows one optimistic requested group state, does not change any member `device.color` or `power_level`, and returns to aggregate physical state after expiry.**
- [ ] **Step 2: Run the focused test and confirm it fails because `_ProjectedMemberState`, `_apply_acknowledged_state`, and `_apply_confirmed_states` write member caches.**

  Run: `uv run pytest tests/components/lifx/test_parallel_group.py -k optimistic -v`

- [ ] **Step 3: Delete the worker-owned `async_query_states` confirmation path from Device Group state reconciliation. Replace per-member projection tracking with one requested group-state snapshot plus a cancellable expiry task.**
- [ ] **Step 4: On every ACK—including hidden color staging and both phases of a compound command—schedule only that physical coordinator’s shared post-command refresh helper. On timeout, clear group optimism for that generation and request physical refreshes without changing group availability.**
- [ ] **Step 5: Add and run tests for first-ACK refresh timing, independently delayed second member ACK, ACK timeout, and state-query timeout. Assert no config-entry reload and no availability change caused by those failures.**
- [ ] **Step 6: Commit Device Group state reconciliation.**

## Task 4: Preserve warmed workers and implement rapid-command semantics

**Files:**
- Modify: `homeassistant/components/lifx/parallel.py`
- Modify: `homeassistant/components/lifx/parallel_group.py`
- Test: `tests/components/lifx/test_parallel_group.py`

**Interfaces:**
- `LIFXParallelRuntime` reports transport failure separately from member availability and does not stop all workers for a transient prepare/ACK/query failure.
- Device Group commands use monotonically increasing generations; only the latest generation may alter optimism or schedule effective reconciliation.

- [ ] **Step 1: Write failing tests showing workers remain alive after a timed-out ACK, a direct query failure, and two rapid Device Group `turn_on`/`turn_off` calls.**
- [ ] **Step 2: Run these tests and confirm the current recovery/stop code fails them.**

  Run: `uv run pytest tests/components/lifx/test_parallel_group.py -k 'timeout or rapid' -v`

- [ ] **Step 3: Remove the `async_stop()`-on-`_ParallelPreparationError` behavior. Keep bounded waits, return a command failure, and arrange internal replacement only for a confirmed dead process or broken pipe.**
- [ ] **Step 4: Remove the group-wide lock from the ACK-wait critical path. Keep worker-pipe ordering and deadline dispatch, but permit a newer command to supersede old reconciliation so the final requested command wins.**
- [ ] **Step 5: Add and run tests proving processes/sockets are started once across repeated commands, precise dispatch gates are reused, and stale ACK/refresh events cannot replace the final request.**
- [ ] **Step 6: Commit warmed-worker and rapid-command behavior.**

## Task 5: Reinitialize only for real member-entry replacement and clean UX wording

**Files:**
- Modify: `homeassistant/components/lifx/parallel_group.py`
- Modify: `homeassistant/components/lifx/strings.json`
- Modify: `homeassistant/components/lifx/translations/en.json`
- Test: `tests/components/lifx/test_parallel_group.py`
- Test: `tests/components/lifx/test_config_flow.py`

**Interfaces:**
- A member config-entry state transition away from the original loaded coordinator initiates orderly Device Group reinitialization.
- User-visible metadata and copy use “Device Group”; internal config values such as `parallel_group` remain stable.

- [ ] **Step 1: Write failing tests for a physical member unload/reload: the group reinitializes its pool with the replacement coordinator, while an ordinary coordinator polling failure does not.**
- [ ] **Step 2: Implement entry-state listeners that distinguish a member entry replacement/reload from a coordinator availability update. Cancel superseded background work during unload.**
- [ ] **Step 3: Write failing assertions for config-flow description, generated translation, and device metadata model text.**
- [ ] **Step 4: Change all user-visible “parallel” wording to “Device Group,” then regenerate English translations with `.venv/bin/python3 -m script.translations develop --integration lifx`.**
- [ ] **Step 5: Run the config-flow and Device Group lifecycle tests, then commit reinitialization and UX work.**

## Task 6: Full regression and deployment-grade verification

**Files:**
- Modify: this plan, marking each verified task complete
- Test: `tests/components/lifx/`

- [ ] **Step 1: Re-read this plan against the implementation and test names. Confirm every Global Constraint has a focused assertion or direct code path.**
- [ ] **Step 2: Run the new focused suite.**

  Run: `uv run pytest tests/components/lifx/test_parallel_group.py -v`

- [ ] **Step 3: Run the complete LIFX suite.**

  Run: `uv run pytest tests/components/lifx -v`

- [ ] **Step 4: Run repository-required formatting and linting.**

  Run: `uv run prek run --all-files`

- [ ] **Step 5: Inspect `git diff --check`, the final diff, generated translations, and process-cleanup assertions. Do not claim completion if any command fails or the local Python/uv environment cannot execute the suite.**
- [ ] **Step 6: Commit only the intended LIFX implementation, tests, translations, and this plan; preserve unrelated worktree changes.**

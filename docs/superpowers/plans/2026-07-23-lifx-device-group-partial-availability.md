# LIFX Device Group Partial Availability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep a Device Group available and controllable whenever at least one configured physical LIFX light is usable, while recovering unavailable members independently and without group-side discovery.

**Architecture:** The group owns stable member slots, each backed by an optional warmed parallel worker. Physical coordinators remain the sole authority for discovery, polling, IP changes, and real device reachability. The group observes each coordinator, snapshots eligible slots for a command, synchronizes only that subset, and rate-limits its own worker reconnect work to once per member per minute.

**Tech Stack:** Home Assistant config entries and `DataUpdateCoordinator`; spawned Python multiprocessing workers; connected UDP LIFX packets; pytest with Home Assistant fixtures.

## Global Constraints

- Modify Device Group behavior only; do not change physical LIFX polling, discovery, config flow, or coordinator behavior.
- Do not send `GetService` or `StateService` from a Device Group worker.
- Preserve fixed configured-member indexes even when a coordinator or worker is temporarily absent.
- Use the latest `ParallelTransport` supplied by a loaded physical coordinator for every worker spawn or reconnect.
- Do not run more than one reconnect attempt per member per 60 seconds; coalesce all triggers while an attempt or delay is pending.
- A new user command must cancel idle health work immediately and must not wait for an Echo timeout or reconnect delay.
- For a two-stage command, wait at most five seconds for first-stage acknowledgements; if at least one member acknowledges, release stage two only to that acknowledged subset. If none acknowledge, do not release stage two.

---

### Task 1: Add stable optional worker slots

**Files:**
- Modify: `homeassistant/components/lifx/parallel.py`
- Test: `tests/components/lifx/test_parallel.py`

**Interfaces:**
- `LIFXParallelRuntime` accepts `Iterable[ParallelTransport | None]` and retains one worker slot per member.
- `async_request_reconnect(index, transport)` creates a missing worker or reconnects the exact existing slot.
- Dispatch accepts `tuple[ParallelCommand | None, ...]` and ignores `None` command/worker slots.

- [ ] Write tests showing that startup with one populated transport and one `None` slot starts only the populated worker, and that reconnecting the empty slot starts only that worker.
- [ ] Run the focused tests and verify they fail because the runtime currently requires every transport and worker.
- [ ] Store workers as `list[_Worker | None]`, start only non-`None` transports, and preserve the index in worker names, worker-health reporting, shutdown, replacement, keepalive, and dispatch.
- [ ] Make reconnect atomically replace only the requested slot, using the supplied transport; retain existing generation and stale-event safeguards.
- [ ] Run `tests/components/lifx/test_parallel.py -q` and verify the new slot tests pass.

### Task 2: Define per-member eligibility and group startup

**Files:**
- Modify: `homeassistant/components/lifx/parallel_group.py`
- Test: `tests/components/lifx/test_parallel_group.py`

**Interfaces:**
- A member slot is eligible only when its entry is loaded, its bound coordinator is current and has `last_update_success`, its transport handoff is complete, its worker is alive, and it is not health-suspect.
- `runtime.available` is `True` when at least one slot is eligible.
- Group setup raises `ConfigEntryNotReady` only when no configured member is initially usable.

- [ ] Write tests for loading a two-member group when one coordinator is unavailable, remaining available when one member later unloads, and becoming unavailable only after the final eligible member is lost.
- [ ] Run those tests and verify they fail under the current all-members-ready gate.
- [ ] Replace all-members readiness checks with slot binding that tolerates a missing/unloaded/unhealthy coordinator, while still rejecting malformed group membership with no usable slot.
- [ ] Register config-entry state listeners for every configured entry, including unavailable slots, so a recovered physical entry can bind and create/reconnect its exact worker.
- [ ] Make display-state aggregation and command capability calculations use eligible bound coordinators; preserve group optimistic state until its existing expiry.
- [ ] Run `tests/components/lifx/test_parallel_group.py -q` and verify the partial-availability tests pass.

### Task 3: Dispatch only the healthy subset

**Files:**
- Modify: `homeassistant/components/lifx/parallel.py`
- Modify: `homeassistant/components/lifx/parallel_group.py`
- Test: `tests/components/lifx/test_parallel.py`
- Test: `tests/components/lifx/test_parallel_group.py`

**Interfaces:**
- `ParallelDispatchResult.failed_member_indexes` contains members excluded after an Echo or stage acknowledgement failure.
- Stage one returns the acknowledged subset after its five-second group deadline.
- Later stages are prepared and released only for that subset.

- [ ] Write tests that a known unavailable slot receives no command and that two available workers receive the same release deadline.
- [ ] Write a two-stage transport test where one first-stage ACK arrives and one times out: only the acknowledged member receives stage two; a zero-ACK stage sends no stage two.
- [ ] Run those tests and verify they fail under the current all-worker command length and all-ACK rules.
- [ ] Allow `None` commands for unavailable slots, and have group command construction insert `None` outside one immutable eligibility snapshot.
- [ ] Change first-stage ACK collection to a five-second decision window. Mark missing ACK slots failed, stop later stages for them, and retain synchronized later-stage release for the acknowledged subset.
- [ ] Treat a successful partial command as a group success, update virtual/optimistic state only for members that received the command, and schedule recovery for failed members without emitting a frontend error.
- [ ] Run both focused parallel suites and verify command exclusion, partial staged dispatch, supersession, and existing timing tests pass.

### Task 4: Coalesce group-owned recovery and idle health checks

**Files:**
- Modify: `homeassistant/components/lifx/const.py`
- Modify: `homeassistant/components/lifx/parallel_group.py`
- Test: `tests/components/lifx/test_parallel_group.py`

**Interfaces:**
- `DEVICE_GROUP_MEMBER_RECONNECT_INTERVAL = 60` is a code-only rate limit.
- Each member has at most one delayed retry or active reconnect task and one newest pending transport.
- Idle Echo probes only the eligible subset after the existing 120-second idle interval.

- [ ] Write tests that repeated poll/listener/Echo-failure events schedule one reconnect, attempts cannot occur before the one-minute deadline, and the latest transport replaces older pending addresses.
- [ ] Write tests that a scheduled or active Echo is cancelled by a user command, a first missed Echo excludes only that member from new commands, and a second missed Echo logs confirmed unavailability without making a different healthy member unavailable.
- [ ] Run those tests and verify they fail because reconnects are currently started directly and keepalive targets every worker.
- [ ] Add member-local retry state: pending transport, next deadline, task, active generation, and cancellation on recovery, replacement, stop, and reload.
- [ ] Route physical coordinator updates, worker failures, and Echo failures through the coalescing scheduler. At execution time rebuild the transport from the current loaded coordinator; do not probe an old endpoint or invoke discovery.
- [ ] Keep idle Echo response/failure accounting per member. On the first miss mark only that slot suspect and request a coalesced reconnect; on the second consecutive miss mark it confirmed unavailable. Reset both states after a successful physical-coordinator handoff or Echo.
- [ ] Run focused group tests and verify no retry loop starts more than one task or attempt per slot.

### Task 5: Diagnostics and HA-host validation

**Files:**
- Modify: `homeassistant/components/lifx/parallel_group.py`
- Test: `tests/components/lifx/test_parallel_group.py`

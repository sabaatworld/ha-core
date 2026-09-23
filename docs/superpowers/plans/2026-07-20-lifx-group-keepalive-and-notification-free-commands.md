# LIFX Device Group Keepalive and Notification-Free Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Device Group worker endpoints warm with cancellable idle Echo requests and make Group operational failures notification-free while preserving truthful Group availability.

**Architecture:** Extend the existing generation-aware parallel dispatcher with a typed request outcome and a packet-58/59 Echo operation. The Group runtime schedules its own idle health work, keeps independent per-member health counters, and maps every operational transport outcome to a normal entity return plus one safe log summary. Physical coordinators continue to own physical availability and endpoint discovery.

**Tech Stack:** Home Assistant Python 3.14, asyncio, multiprocessing spawned workers, connected UDP LIFX LAN protocol, pytest, Ruff/prek.

## Global Constraints

- Modify only `homeassistant/components/lifx`, `tests/components/lifx`, and the plan/spec documentation for this feature.
- Keep the LIFX integration domain and custom override identity unchanged; do not modify `lifx_cloud`.
- Define code-only constants `DEVICE_GROUP_KEEPALIVE_INTERVAL = 120` and `DEVICE_GROUP_KEEPALIVE_MAX_CONSECUTIVE_FAILURES = 2`; add no configuration or UI surface.
- Echo uses LIFX packet 58 and expects packet 59 with the exact 64-byte token; it is one three-second health attempt, not the five-attempt write retry policy.
- A new user command must preempt active Echo and health reconnect immediately; workers remain alive and warm.
- No operational Group outcome may escape an entity service as `HomeAssistantError`; config-entry startup failures remain lifecycle errors.
- New diagnostics must omit IPs, serials, light names, HSBK/payload values, and duplicate outcome summaries.
- Run Git and every test on `root@192.168.8.28` in `/config/workplace/ha-core`; do not commit unless the user requests it.

---

## File structure

- `homeassistant/components/lifx/const.py`: keepalive constants.
- `homeassistant/components/lifx/parallel.py`: Echo packet support, cancellable reconnect control, typed dispatch results, and outcome-safe dispatcher behavior.
- `homeassistant/components/lifx/parallel_group.py`: idle timer, member health state, availability gate, notification-free entity command wrapper, and single-owner logging.
- `tests/components/lifx/test_parallel.py`: packet encoding/response matching, typed result, stale events, Echo/reconnect cancellation, and worker warmth.
- `tests/components/lifx/test_parallel_group.py`: idle scheduling, threshold availability, recovery, projection rollback, notification-free actions, and concise logging.

### Task 1: Establish the transport outcome contract

**Files:**
- Modify: `homeassistant/components/lifx/parallel.py:82-100,787-884`
- Test: `tests/components/lifx/test_parallel.py`

**Interfaces:**
- Produces `ParallelDispatchOutcome` and `ParallelDispatchResult` with `outcome`, `request_id`, and `failed_member_indexes`.
- Changes `LIFXParallelRuntime.async_dispatch(commands)` to return `ParallelDispatchResult`, never an operational `HomeAssistantError`.

- [ ] **Step 1: Write failing dispatch-result tests**

```python
def test_superseded_request_returns_a_result_without_raising() -> None:
    runtime = _runtime_with_one_worker()
    old = _DispatchRequest(1, (), threading.Event())
    runtime._active_dispatch = old

    result = runtime._queue_dispatch((ParallelCommand("power", (False, 0)),))

    assert old.result.outcome is ParallelDispatchOutcome.SUPERSEDED
    assert result.outcome is ParallelDispatchOutcome.COMPLETED


def test_dispatch_maps_ack_timeout_to_failed_result() -> None:
    runtime = _runtime_with_one_worker()
    with patch.object(runtime, "_dispatch", side_effect=_ParallelAckTimeout()):
        result = runtime._run_dispatch(_request(1))

    assert result.outcome is ParallelDispatchOutcome.FAILED
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m pytest tests/components/lifx/test_parallel.py -k "superseded or ack_timeout" -q'
```

Expected: FAIL because `_DispatchRequest` has only an exception field and the public path raises it.

- [ ] **Step 3: Add typed result storage and supervisor mapping**

```python
class ParallelDispatchOutcome(Enum):
    COMPLETED = auto()
    SUPERSEDED = auto()
    UNAVAILABLE = auto()
    FAILED = auto()


@dataclass(frozen=True, slots=True)
class ParallelDispatchResult:
    outcome: ParallelDispatchOutcome
    request_id: int
    failed_member_indexes: frozenset[int] = frozenset()
```

Replace `_DispatchRequest.error` with a result assigned by the dispatcher.
When `_queue_dispatch` replaces pending/active work, assign
`SUPERSEDED`, set that request's event, and return normally. Have the
dispatcher catch operational `_ParallelPreempted`, ACK timeout, preparation,
pipe, and worker failures and map them to `SUPERSEDED` or `FAILED`. Preserve
unexpected exception traceback logging once in the dispatcher, then return
`FAILED` rather than exposing it through the entity action.

- [ ] **Step 4: Remove duplicated supersession outcome logging**

Keep detailed protocol trace logging only at DEBUG. Delete or consolidate the
multiple queue/stage/supervisor summary messages so the caller can emit one
logical Group outcome later. Do not include host strings or command payloads
in new summary messages.

- [ ] **Step 5: Run the focused transport suite and verify GREEN**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m pytest tests/components/lifx/test_parallel.py -q'
```

Expected: PASS with supersession, ACK timeout, stale-event, and worker-warmth coverage retained.

### Task 2: Add cancellable Echo and reconnect transport operations

**Files:**
- Modify: `homeassistant/components/lifx/parallel.py:31-41,345-562,568-653,886-903,905-1135`
- Test: `tests/components/lifx/test_parallel.py`

**Interfaces:**
- Produces `LIFXParallelRuntime.async_keepalive() -> ParallelDispatchResult`.
- Produces `LIFXParallelRuntime.async_cancel_health() -> None`.
- `ParallelDispatchResult.failed_member_indexes` identifies only unresolved Echo members.

- [ ] **Step 1: Write failing Echo wire tests**

```python
def test_echo_packet_has_a_64_byte_token_and_no_ack_flag() -> None:
    token = bytes(range(64))
    packet = _echo_request(1, 2, bytes(8), token)
    *_header_fields, packet_type, _reserved = HEADER.unpack_from(packet)

    assert packet_type == ECHO_REQUEST
    assert packet[HEADER_SIZE:] == token


def test_wait_for_echo_requires_the_matching_response_payload() -> None:
    token = bytes(range(64))
    udp.recv.side_effect = (
        _header(ECHO_RESPONSE, 1, 2, bytes(8), 64) + bytes(64),
        _header(ECHO_RESPONSE, 1, 2, bytes(8), 64) + token,
    )

    assert _wait_for_echo(udp, pipe, 1, bytes(8), 2, token, request_id=3)
```

- [ ] **Step 2: Run Echo wire tests and verify RED**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m pytest tests/components/lifx/test_parallel.py -k echo -q'
```

Expected: FAIL because Echo constants, packet builder, and wait helper do not exist.

- [ ] **Step 3: Implement Echo preparation, send, and exact response matching**

```python
ECHO_REQUEST = 58
ECHO_RESPONSE = 59
ECHO_PAYLOAD_SIZE = 64


def _echo_request(source: int, sequence: int, target: bytes, token: bytes) -> bytes:
    assert len(token) == ECHO_PAYLOAD_SIZE
    return _header(ECHO_REQUEST, source, sequence, target, len(token)) + token
```

Add `kind == "echo"` to `_build_packet`. Make `_dispatch_prepared` select
`_wait_for_echo` for an echo command and send `ECHOED`, `ECHO_TIMEOUT`, or
`CANCELLED` with request/stage/attempt identity. Use a request-specific token
generated in the supervisor, do not log it, and retain the worker's existing
generation and control-pipe checks throughout the three-second wait.

- [ ] **Step 4: Write failing cancellation and reconnect tests**

```python
def test_normal_dispatch_preempts_active_echo_without_waiting() -> None:
    runtime = _runtime_with_one_worker()
    runtime._active_dispatch = _health_request(1)
    runtime._active_stage = (1, 0, (runtime._workers[0],))

    runtime._queue_dispatch((ParallelCommand("power", (True, 0)),))

    runtime._workers[0].pipe.send.assert_called_with(("CANCEL", 1, 0))


def test_cancelled_reconnect_does_not_delay_the_replacement_dispatch() -> None:
    runtime = _runtime_with_one_worker()
    worker = runtime._workers[0]
    health = _DispatchRequest(1, (), threading.Event(), health=True)
    runtime._active_dispatch = health
    runtime._active_stage = (1, 0, (worker,))
    runtime._current_generation.value = 1

    with patch.object(runtime, "_dispatch", return_value=_completed_result()) as dispatch:
        result = runtime._queue_dispatch((ParallelCommand("power", (True, 0)),))

    assert health.result.outcome is ParallelDispatchOutcome.SUPERSEDED
    assert result.outcome is ParallelDispatchOutcome.COMPLETED
    worker.pipe.send.assert_called_with(("CANCEL", 1, 0))
    dispatch.assert_called_once()
```

- [ ] **Step 5: Implement health request and cancellable reconnect routing**

Route `async_keepalive()` through the same dispatcher but mark its request as
`health=True`. Its one stage sends Echo to every worker at one deadline and
returns every unresolved index in `failed_member_indexes`; it does not retry.
After an Echo timeout, issue per-member reconnect work under that same health
generation rather than through the lock-held standalone reconnect method.
Refactor `_preflight` to poll the control pipe and generation between short
socket waits, returning a cancelled result without waiting for the one-second
service deadline. A normal queue request must set the health request result to
`SUPERSEDED`, signal each worker, and proceed through `PREPARE` immediately.

Keep the existing public endpoint-replacement reconnect API working, but
route it through the cancellable control path too. It still returns `FAILED`
to the Group runtime instead of a service exception.

- [ ] **Step 6: Run the focused transport suite and verify GREEN**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m pytest tests/components/lifx/test_parallel.py -q'
```

Expected: PASS; malformed/stale frames cannot satisfy Echo, cancellation is immediate, and workers remain alive.

### Task 3: Add Group idle scheduling and worker-health availability

**Files:**
- Modify: `homeassistant/components/lifx/const.py:31-33`
- Modify: `homeassistant/components/lifx/parallel_group.py:1-20,150-260,370-430,498-750`
- Test: `tests/components/lifx/test_parallel_group.py`

**Interfaces:**
- Produces `LIFXParallelGroupRuntime.async_note_user_mutation()` and an
  internal `_async_run_keepalive(generation)`.
- Extends `LIFXParallelGroupRuntime.available` with per-member
  `keepalive_healthy`.

- [ ] **Step 1: Write failing health and availability tests**

```python
async def test_first_idle_echo_failure_reconnects_but_keeps_group_available(
    hass: HomeAssistant,
) -> None:
    runtime, _members = _runtime(hass)
    runtime.parallel.async_keepalive = AsyncMock(
        return_value=_failed_health_result({1})
    )
    runtime.parallel.async_request_reconnect = AsyncMock(
        return_value=_completed_result()
    )

    await runtime._async_run_keepalive(runtime._keepalive_generation)

    assert runtime.available
    assert runtime._keepalive_failures == [0, 1]


async def test_second_consecutive_idle_echo_failure_makes_group_unavailable(
    hass: HomeAssistant,
) -> None:
    runtime, _members = _runtime(hass)
    await _fail_member_echo_twice(runtime, index=0)

    assert not runtime.available
    assert runtime._keepalive_healthy == [False, True]
```

Add tests that a valid Echo, a completed normal Group dispatch, and a physical
endpoint replacement reset a member's counter and restore availability. Keep
the existing physical-coordinator-unavailable regression and update its name
to state that availability now depends on both physical and Group-worker
health.

- [ ] **Step 2: Run the focused Group health tests and verify RED**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m pytest tests/components/lifx/test_parallel_group.py -k "keepalive or availability" -q'
```

Expected: FAIL because Group runtime has no idle health state or timer.

- [ ] **Step 3: Implement code constants and runtime state**

```python
# const.py
DEVICE_GROUP_KEEPALIVE_INTERVAL = 120
DEVICE_GROUP_KEEPALIVE_MAX_CONSECUTIVE_FAILURES = 2

# parallel_group.py constructor
self._keepalive_failures = [0] * len(members)
self._keepalive_healthy = [True] * len(members)
self._keepalive_generation = 0
self._cancel_keepalive: CALLBACK_TYPE | None = None
self._keepalive_task: asyncio.Task[None] | None = None
```

Make `available` require `all(self._keepalive_healthy)` in addition to the
existing physical readiness checks. Cancel scheduled/active health work on
runtime stop and invalidate its generation. Reset the affected health values
after a successful physical coordinator replacement.

- [ ] **Step 4: Implement idle re-arm and health outcome handling**

Use `async_call_later` with `timedelta(seconds=DEVICE_GROUP_KEEPALIVE_INTERVAL)`
to schedule a callback. `async_note_user_mutation()` increments the generation,
cancels the delayed callback, asks the parallel runtime to cancel active health
work, and re-arms after the normal command outcome is handled. The callback
must not start when the runtime is stopped, a Group member is not physically
ready, or a software effect is actively dispatching visual changes.

For each failed member index, increment only that counter and request the
cancellable endpoint reconnect. On threshold crossing set health false and
call `async_update_listeners()`. On a health success reset all responding
members. Do not mutate coordinator caches, optimistic state, or virtual power.

- [ ] **Step 5: Add immediate-preemption timing regression**

```python
async def test_user_mutation_cancels_active_keepalive_before_dispatch(
    hass: HomeAssistant,
) -> None:
    runtime, _members = _runtime(hass)
    health_started = asyncio.Event()
    release_health = asyncio.Event()
    runtime.parallel.async_keepalive = _blocking_health(health_started, release_health)

    runtime._start_keepalive_for_test()
    await health_started.wait()
    await runtime.async_set_state(brightness=128)

    runtime.parallel.async_dispatch.assert_awaited_once()
    release_health.set()
```

Assert the normal dispatch is awaited before the held health task completes;
the test must not use a sleep to establish ordering.

- [ ] **Step 6: Run Group health tests and verify GREEN**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m pytest tests/components/lifx/test_parallel_group.py -k "keepalive or availability or reconnect" -q'
```

Expected: PASS with first-failure, threshold, recovery, endpoint replacement, and user-preemption coverage.

### Task 4: Make every Group user action notification-free

**Files:**
- Modify: `homeassistant/components/lifx/parallel_group.py:498-840,936-1037`
- Test: `tests/components/lifx/test_parallel_group.py`

**Interfaces:**
- Produces `_async_handle_dispatch_result(operation: str, result:
  ParallelDispatchResult, projection_generation: int | None) -> bool` and
  `_log_operation_outcome(operation: str, outcome: str, request_id: int | None,
  member_index: int | None, failures: int | None) -> None`.
- All Group public action methods return normally for operational outcomes.

- [ ] **Step 1: Write failing entity-boundary tests**

```python
@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        pytest.param("async_turn_on", {"brightness": 128}, id="turn-on"),
        pytest.param("async_turn_off", {}, id="turn-off"),
        pytest.param("set_state", {"brightness": 128}, id="set-state"),
    ],
)
async def test_group_actions_do_not_raise_for_operational_dispatch_failure(
    hass: HomeAssistant,
    method: str,
    kwargs: dict[str, Any],
) -> None:
    entity, runtime = _group_entity(hass)
    runtime.parallel.async_dispatch.return_value = _failed_result()

    await getattr(entity, method)(**kwargs)

    assert runtime._optimistic_state is None


async def test_group_unavailable_action_is_a_silent_noop(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    entity, runtime = _group_entity(hass)
    runtime._keepalive_healthy[0] = False

    await entity.async_turn_on()

    runtime.parallel.async_dispatch.assert_not_awaited()
    assert caplog.messages.count("LIFX Device Group command skipped: unavailable") == 1
```

Add corresponding identify, restart, effect-start, effect-stop, and
background software-effect tests. For each operational outcome assert no
`HomeAssistantError` reaches the service/entity caller.

- [ ] **Step 2: Run notification-free tests and verify RED**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m pytest tests/components/lifx/test_parallel_group.py -k "does_not_raise or silent_noop or superseded" -q'
```

Expected: FAIL because Group methods currently raise on unavailable/recovering state and re-raise dispatch errors.

- [ ] **Step 3: Replace raising action guards with outcome handling**

```python
async def _async_can_accept_user_command(self, operation: str) -> bool:
    self.async_note_user_mutation()
    if self._recovery_task is not None or not self.available:
        self._log_operation_outcome(operation, "skipped")
        return False
    return True


async def _async_handle_dispatch_result(
    self,
    operation: str,
    result: ParallelDispatchResult,
    projection_generation: int | None = None,
) -> bool:
    if result.outcome is ParallelDispatchOutcome.COMPLETED:
        self._reset_keepalive_health_after_command()
        return True
    if projection_generation is not None:
        self._clear_projection(projection_generation)
    self._log_operation_outcome(operation, result.outcome.name.lower())
    return False
```

Use this handler from state writes, identify, restart, every firmware effect,
and the software effect loops. Retain `ConfigEntryNotReady` handling in setup.
Catch unexpected exceptions once at the entity boundary with `_LOGGER.exception`
and return normally after clearing only the current projection.

- [ ] **Step 4: Implement single-owner safe logging**

Emit one DEBUG line for superseded, cancelled health, skipped unavailable, and
below-threshold health failure. Emit one WARNING only when a member crosses
the failure threshold or later recovers and Group availability changes. The
logger receives only operation category, request id, member index, and count;
do not pass hosts, names, payloads, serials, or colors. Remove duplicate
outcome summaries from the worker/supervisor layers introduced by prior
preemption code.

- [ ] **Step 5: Run Group entity tests and verify GREEN**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m pytest tests/components/lifx/test_parallel_group.py -q'
```

Expected: PASS; all Group actions return normally for operational transport outcomes and retain correct optimistic rollback.

### Task 5: Validate integration behavior and diagnostics

**Files:**
- Modify only if needed by failures: `tests/components/lifx/test_light.py`, `tests/components/lifx/test_config_flow.py`
- Verify: `homeassistant/components/lifx/parallel.py`, `homeassistant/components/lifx/parallel_group.py`, focused test modules

- [ ] **Step 1: Add cross-module regressions only where an existing contract requires them**

Run the existing physical-light and config-flow tests first. If a Group API
call is mocked by another LIFX test, update its mock to return
`ParallelDispatchResult(COMPLETED, request_id)` and assert no physical-light
behavior, discovery reload, virtual-power persistence, or fade behavior has
changed.

- [ ] **Step 2: Run focused transport and Group validation**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m pytest tests/components/lifx/test_parallel.py tests/components/lifx/test_parallel_group.py -q'
```

Expected: PASS.

- [ ] **Step 3: Run the complete LIFX suite**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/python -m pytest tests/components/lifx -q'
```

Expected: PASS, or report each unrelated pre-existing failure with its test
name and output separately from this feature.

- [ ] **Step 4: Run static checks and inspect the diff**

Run:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && .venv/bin/ruff check homeassistant/components/lifx tests/components/lifx && git diff --check && .venv/bin/python -m prek run --all-files'
```

Expected: all available checks pass. If `prek` is unavailable in the HA host
environment, report that exact tooling limitation and do not claim it passed.

- [ ] **Step 5: Review against the design before handoff**

Verify each of these facts in the final diff:

```text
one Echo attempt after 120 seconds idle
two consecutive missed Echos gate Group availability
first missed Echo reconnects without availability loss
normal command cancels active Echo/reconnect immediately
no Group operational HomeAssistantError reaches entity services
one safe log owner; no payload, IP, serial, or light name in new logs
workers survive Echo, reconnect, ACK, and command failures
```

Do not commit, restart Home Assistant, or change live configuration unless the
user separately asks for those actions.

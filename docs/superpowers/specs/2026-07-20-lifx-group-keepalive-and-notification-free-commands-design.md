# LIFX Device Group Keepalive and Notification-Free Commands

## Purpose

Keep each Device Group worker and its UDP endpoint warm without interrupting
normal lighting commands. After a group has been idle for two minutes, the
group will synchronously send a LIFX `EchoRequest` to every member worker.
The response is a health signal for the worker's current endpoint, not a
physical-light state refresh.

At the same time, make all ordinary Device Group user commands notification
free. A transient transport outcome, a superseded request, an unavailable
member, or an idle health-check failure must not escape an entity service as a
`HomeAssistantError`, since Home Assistant can present that error as a toast.
The Group state and concise integration logs remain the feedback mechanisms.

## Scope

### In scope

- Code constants for Device Group keepalive interval and consecutive-failure
  threshold: 120 seconds and 2 by default.
- A low-priority, cancellable, synchronized `EchoRequest`/`EchoResponse`
  operation across the existing warmed member-worker pool.
- A per-member consecutive Echo-failure counter, quiet reconnect after every
  missed Echo, and Group unavailability after the configured threshold.
- Immediate preemption of an Echo wait or health reconnect when a real Group
  command arrives.
- Typed internal dispatch outcomes so all Device Group entity actions return
  normally instead of producing frontend toasts for operational failures.
- Concise, single-owner DEBUG/WARNING/ERROR logging for Group command and
  health outcomes.
- Regression tests for packet validation, health state, availability,
  cancellation, and notification-free entity behavior.

### Out of scope

- Any Home Assistant configuration-flow, Options-flow, number entity, or UI
  control for the two keepalive constants.
- Changes to physical-light command behavior, physical-light polling cadence,
  discovery ownership, or aiolifx retry policy.
- A Group-owned state query, state refresh, or a separate discovery listener.
- Killing warmed workers after ordinary command, Echo, ACK, or reconnect
  failure.
- Persistent notifications, Repairs issues, or any new user-facing
  diagnostics.

## Existing facts and constraints

- `LIFXParallelRuntime` owns a persistent spawned worker and connected UDP
  socket per Group member. It already synchronizes normal staged packets with
  `PREPARE`, `READY`, and a shared release deadline.
- A newer normal request increments the shared generation, signals `CANCEL`
  to active workers, and replaces pending work. The old caller currently gets
  `HomeAssistantError("LIFX Device Group command superseded")`, which is the
  direct source of a frontend toast.
- All normal Group stages currently wait for ACK and retry using five attempts
  with a three-second per-attempt wait and a fifteen-second stage budget.
- The current one-member reconnect path acquires the transport lock and may
  wait up to 1.5 seconds. It is therefore not suitable as a side-channel
  keepalive recovery path: a user command must be able to supersede it.
- Physical coordinators poll independently every ten seconds. Their
  `last_update_success`, config-entry state, and endpoint replacement remain
  authoritative physical-dependency facts.
- Today Group availability follows only the physical coordinators. This design
  deliberately adds a second, Group-worker endpoint health gate.
- LIFX LAN `EchoRequest` is packet type 58 with a 64-byte payload. The device
  returns packet type 59, `EchoResponse`, containing exactly that payload.

## Keepalive design

### Constants and state

Add these code-only constants in `const.py`:

```python
DEVICE_GROUP_KEEPALIVE_INTERVAL = 120
DEVICE_GROUP_KEEPALIVE_MAX_CONSECUTIVE_FAILURES = 2
```

Each `LIFXParallelGroupRuntime` stores, per member index:

- `keepalive_failures`: starts at zero and increases only for a completed idle
  Echo that received no valid response from that member.
- `keepalive_healthy`: starts true; becomes false only when failures reaches
  the threshold.
- `keepalive_generation` and one cancellable delayed task/callback so an old
  timer or health result cannot re-arm after a later user command, reload, or
  unload.

No host, serial, light name, packet payload, or device state is retained for
logging.

### Scheduling

The Group arms one delayed keepalive when it has no accepted user mutation
request for `DEVICE_GROUP_KEEPALIVE_INTERVAL` seconds. A mutation request is
any ordinary Group state write, effect start/stop, identify, or restart; the
timer is reset when the request reaches the Group runtime, not when its fade
visually finishes.

The delayed callback:

1. Captures the current keepalive generation and verifies the runtime is
   loaded, not stopped, and has no active software effect dispatching changes.
2. Runs one low-priority all-member Echo operation through the same dispatcher
   and shared send deadline used by normal packets.
3. Does not alter Group color, virtual-power state, optimistic projection, or
   physical coordinator data.
4. Rearms the next idle check only if its captured generation is still current
   and no user mutation arrived.

A real user command first invalidates/cancels this delayed or active health
operation, then queues its own normal request. It never waits for the Echo
response timeout, retry period, reconnect preflight, or a worker shutdown.

### Echo wire and worker behavior

Add `ECHO_REQUEST = 58` and `ECHO_RESPONSE = 59` to `parallel.py`.

- An `echo` `ParallelCommand` builds a packet 58 with a request-specific,
  opaque 64-byte token. It does **not** request an ACK.
- Each worker reports `ECHOED` only after receiving a packet 59 whose source,
  sequence, target, packet type, length, and 64-byte payload match its own
  request.
- Malformed, wrong-sequence, wrong-target, wrong-type, and wrong-payload UDP
  frames are ignored. They cannot complete the health request or leak into a
  later request.
- The worker checks `CANCEL` and the shared generation while waiting exactly as
  it does for an ACK. A cancellation sends the normal request-scoped cancelled
  event and promptly returns the worker to its warm command loop.

Echo is a single health attempt, not a five-attempt user-write retry policy.
It has one three-second response deadline. A missed response identifies only
the unresolved member indexes; it is not a Group command failure and never
raises to an entity service.

### Reconnect after a missed Echo

For each unresolved member, the health request immediately asks its existing
worker to reconnect using the currently bound physical coordinator's
`device.ip_addr`. The worker closes and recreates its socket, sends
`GetService`, and reconnects to the reported service port exactly like the
existing endpoint-replacement flow.

This reconnect belongs to the same request generation as the failed Echo. Its
preflight wait must observe `CANCEL`/generation changes, so a later normal
command can stop it before its timeout and proceed without a reconnect grace
wait. Reconnect results do not reset `keepalive_failures`: only a valid future
Echo response or a completed normal Group command proves the endpoint health
check succeeded.

A successful physical coordinator replacement resets the matching member's
health counter and health flag because the worker is now bound to a new,
preflight-verified endpoint.

### Availability

The Group is available only when all of these are true for every member:

1. its physical config entry is loaded and exposes the expected coordinator;
2. the coordinator's `last_update_success` is true and any endpoint handoff
   has completed; and
3. its `keepalive_healthy` is true.

One missed Echo leaves `keepalive_healthy` true and the Group available. At
two consecutive missed idle Echos, the member becomes unhealthy and the
whole Group becomes unavailable. A valid later Echo, a successfully completed
normal Group dispatch, or a successful physical endpoint replacement restores
that health immediately. The physical coordinator is never modified to
reflect a Group-worker health outcome.

## Notification-free command design

### Typed transport outcomes

Replace the public dispatch path's `HomeAssistantError` result for operational
outcomes with a typed result, for example:

```python
class ParallelDispatchOutcome(Enum):
    COMPLETED = auto()
    SUPERSEDED = auto()
    UNAVAILABLE = auto()
    FAILED = auto()
```

The result can carry safe internal detail: request id, operation category,
and member indexes that failed. `HomeAssistantError` subclasses remain valid
inside the supervisor and worker implementation, but the public Group command
path maps them to an outcome before they reach an entity method.

When a newer request supersedes an older one, the old caller receives
`SUPERSEDED` immediately and returns successfully. The newest request remains
the only pending command. Background Echo/reconnect cancellation receives the
same result and completes silently.

### Entity boundary and state rollback

Every Group user action must use one common outcome handler:

- `async_turn_on`, `async_turn_off`, integration `set_state`, identify,
  restart, and effect starts/stops return normally for `SUPERSEDED`,
  `UNAVAILABLE`, and `FAILED`.
- A rejected unavailable/recovering request sends no packet and makes no
  optimistic-state change. The existing unavailable entity state is the
  user-visible feedback.
- A failed or superseded projected state request clears only its own
  still-current optimistic projection. It cannot clear a newer request's
  projection.
- A completed projected state request keeps the current optimistic/virtual
  power bookkeeping unchanged.
- Background software-effect loops consume their own operational outcomes so
  they never produce an unhandled task exception or a service toast.
- Setup and unload are not entity commands. Configuration-entry startup may
  still raise `ConfigEntryNotReady`; an unexpected programming bug is logged
  with a traceback once, then contained at the entity boundary.

This intentionally makes transient Group failures best-effort from an
automation caller's perspective. Health is reflected through entity
availability after the configured threshold, rather than a service exception.

### Logging policy

One Group-runtime outcome function is the only owner of operational outcome
logs. Lower-level worker/supervisor logs retain protocol detail only where it
helps diagnostic debug logging; they must not repeat the same logical
supersession/failure summary.

| Event | Level | One log event |
| --- | --- | --- |
| Normal completion | DEBUG only when useful for protocol tracing | request category and id |
| Superseded request / cancelled Echo / skipped unavailable command | DEBUG | request category, id, reason |
| Missed Echo / reconnect failure below threshold | DEBUG | member index and failure count |
| Member reaches threshold or later recovers, changing Group availability | WARNING | member index and availability transition |
| Unexpected invariant/lifecycle bug | ERROR with traceback | one boundary log |

New logs omit IP addresses, serials, light names, packet payloads, and HSBK
values. No persistent notification, Repairs issue, or service exception is
used for these outcomes.

## Validation requirements

- Verify Echo packet 58 encoding, exact 64-byte response validation, and
  malformed/stale response rejection.
- Verify all workers receive one synchronized Echo dispatch and a normal
  request preempts its wait immediately without an Echo retry or reconnect
  delay.
- Verify a first missed Echo reconnects the affected warm worker while Group
  availability stays true; a second missed Echo makes it unavailable; a later
  valid response, normal dispatch, or endpoint replacement restores it.
- Verify physical coordinator unavailability still makes the Group unavailable
  independently of keepalive state.
- Verify every Group action, including background effect tasks, returns
  without `HomeAssistantError` for supersession, health failure, transport
  failure, recovering, and unavailable outcomes.
- Verify only one safe DEBUG/WARNING log summary is emitted per operational
  outcome and that it contains no sensitive endpoint or payload detail.
- Run focused parallel/Group tests, the complete LIFX suite, translation/lint
checks as applicable, and required validation commands on the Home Assistant
host. Report pre-existing failures separately.

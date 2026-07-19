# LIFX Device Group Staged ACK Preemption Implementation Plan

**Goal:** Make every dependent multi-command Device Group operation send each
stage at a common low-skew deadline, require acknowledgements before advancing
to its next stage, and immediately preempt stale ACK waits for newer commands.

## Global constraints

- Run Git commands and every test through `ssh root@192.168.8.28` in
  `/config/workplace/ha-core`; use `.venv/bin/python -m pytest` and
  `.venv/bin/python -m prek` there.
- Keep one spawned warm worker process and connected UDP socket per member.
  An ACK timeout, malformed datagram, cancellation, or ordinary UDP failure
  must not kill, replace, or unload a living worker.
- A non-final command stage sets the LIFX ACK-required flag. Every targeted
  member must return a correlated ACK before the next stage is prepared.
- Each ACK stage has five total sends, one 3-second ACK window per send, and a
  15-second maximum. On incomplete ACK collection, send no later stage.
- A newer command immediately invalidates the active request. Do not queue it
  behind an ACK wait, do not retry the old request, and do not let any delayed
  ACK authorize an old later stage.
- Every stage has a distinct shared absolute send deadline. A single-command
  request retains the existing fire-and-forget low-skew dispatch behavior.
- Device Group availability remains derived exclusively from its physical
  coordinators; command failure is local to the command.

## Files

- `homeassistant/components/lifx/parallel.py`: staged packet representation,
  ACK construction/parsing, cancellation-aware worker loop, and preemptive
  dispatcher supervision.
- `homeassistant/components/lifx/parallel_group.py`: translate every dependent
  multi-command operation into ordered stages instead of independent dispatches.
- `tests/components/lifx/test_parallel.py`: packet, ACK, retry, preemption,
  warm-worker, and real UDP worker tests.
- `tests/components/lifx/test_parallel_group.py`: staged command construction
  and group failure/availability regression tests.
- `homeassistant/components/lifx/AGENTS.md`: HA-host-only Git and test rule.

## Tasks

### 1. Define staged packet and ACK protocol

- Add failing tests that require a non-final `SetColor` or `SetPower` packet to
  have the ACK flag, and that reject ACKs with the wrong source, target,
  sequence, packet type, size, or request generation.
- Extend `ParallelCommand.second` into an ordered staged traversal that allows
  a member to skip a stage while the group remains synchronized, without a
  broad compatibility-breaking representation change.
- Add builders and parsers for correlated LIFX acknowledgement messages. Keep
  final stages fire-and-forget unless they precede another dependent stage.

### 2. Implement worker-stage ACK and cancellation behavior

- Add failing real-worker tests for: all ACKs advancing to the next stage;
  missing ACKs causing five sends with no next stage; malformed or stale ACKs
  being ignored; and a timeout retaining original worker PIDs.
- Make each worker prebuild, report ready, send at a common gate, and wait for
  only its matching ACK. Multiplex its UDP socket and control pipe so a
  cancellation wakes it immediately rather than waiting up to three seconds.
- On ACK success, timeout, parse error, or cancellation, report request and
  stage identity to the supervisor and return to the warm command loop.

### 3. Implement preemptive supervisor-stage orchestration

- Add failing tests for a new command arriving during every ACK attempt. Assert
  the old command sends no later stage, its delayed ACK is ignored, the latest
  command is dispatched without waiting for the old retry window, and workers
  retain their PIDs.
- Refactor the dispatcher so it owns staged worker-pipe I/O and supports an
  active request plus an immediate replacement signal, not a queue behind ACK
  collection.
- On preemption, invalidate the active generation, send worker cancellation,
  drain only cancellation reports, and begin preparing the newest command as
  soon as every worker is idle. Do not replay intermediate requests.
- Retry unresolved ACK targets at a fresh shared deadline, up to five total
  attempts. Stop the stage immediately on preemption or after the fifth
  unsuccessful attempt.

### 4. Convert all dependent Device Group operations to stages

- Add failing construction tests for `SetColor -> SetPower`, `SetPower ->
  SetColor`, and mixed member state paths; assert the prior stage is ACK-gated
  and the later stage cannot be sent before it succeeds.
- Convert the state builder’s paired commands to ordered stages.
- Convert power-before-effect, power-before-theme, power-before-pulse, and
  power-before-colorloop flows from independent dispatches into one staged
  operation whenever a later command depends on a successful power command.
- Leave unrelated one-packet actions on the original fire-and-forget path.

### 5. Verify behavior and review

- Run the newly added focused tests first on the HA host and record the red
  failure before each production change; rerun them after each implementation
  step.
- Run the LIFX focused tests, full LIFX suite, formatting/linting, and Git
  diff checks on the HA host only.
- Perform up to three independent review/fix cycles. Each reviewer checks this
  plan and the complete diff; apply all Critical and Important findings from a
  cycle in one change set, rerun the affected HA-host tests, then re-review.

# LIFX Ultimate Device Groups

Updated: 2026-10-15

Design: [Worker bootstrap repair](../superpowers/specs/2026-10-15-lifx-custom-worker-bootstrap-design.md)
Plan and detailed execution evidence: [Implementation plan](../superpowers/plans/2026-10-15-lifx-custom-worker-bootstrap.md)

## Current integration and group

- Source: sibling `ha-core`, branch `dev`, base revision
  `dac92701113fbdb418695dd3b53371bcf84dc6a5` plus the uncommitted repair.
- Deployment: `/config/custom_components/lifx` through
  `scripts/ha_custom_component.sh`; technical domain remains `lifx`.
- Instance: HA 2026.10.0, Python 3.14.6. Local tests use Python 3.14.5.
- Custom LIFX Device Group: **Test Group**, entry
  `01M512FVNX1CMHK6AK40G1Z140`, light **`light.test_group`**.
- Members: `light.living_room_tree_lamp_bottom`,
  `light.living_room_tree_lamp_middle`, `light.living_room_tree_lamp_top`.
- **`light.living_room_tree_lamp` is a separate ordinary HA group helper**,
  not the custom integration's Test Group.

## Defects repaired

### Worker initialization

HA temporarily mounts the config directory to import `custom_components`.
Fresh multiprocessing `spawn` children could not import that namespace
while unpickling their target and `ParallelTransport` argument. They exited
with `ModuleNotFoundError: No module named 'custom_components'`; the parent
reported a closed worker against whichever host's pipe failed first.

The repair uses a standard-library `runpy.run_path` target and primitive
transport fields. An adjacent `_parallel_worker.py` establishes the import
root in the child before importing the original integration module and
reconstructing its transport. Parent import paths and built-in loading are
unchanged. This applies to initial and replacement workers.

The failure happened before bulb communication. Earlier explanations based
on UDP loss or a startup `get_color` exchange were not supported.

### Typed member state overwritten after commands

Once workers could start, live control exposed a second defect: the group
called `member.async_set_updated_data(None)` after successful dispatch.
Physical coordinators now hold typed `lifx-async` state, so listeners failed
accessing `.power` and `.capabilities` on `None`.

The group now calls `member.async_update_listeners()`, preserving physical
state and its polling schedule while notifying virtual-power changes.
Real-coordinator on/off regressions catch this; the older mocked
coordinators did not.

## Verification

- Full LIFX suite: **290 passed**, including **10 snapshots**.
- Real-spawn tests cover built-in/custom/exported package loading, loopback
  command/response IPC, dead-worker replacement, bootstrap failure cleanup,
  and unchanged parent environment/import path.
- Both defects reproduced in failing tests before their repairs.
- Independent review found no critical/important blockers for either repair.
- Final deployment loads all four LIFX entries, including Test Group.
- Three targeted reloads of the final repair each returned `loaded` with
  `worker_health: [true, true, true]`, `available: true`, and no failure reason.
- Actual commands on `light.test_group` were verified after polling:

  | Operation | All three member readbacks |
  |---|---|
  | On: 20%, 3500 K | On, brightness 51, 3500 K |
  | Change: 40%, 4500 K | On, brightness 102, 4500 K |
  | Off | All off |
  | On after group-off: 25%, 3000 K | On, brightness 64, 3000 K |

- Restored all bulbs to their pre-test off state and Adaptive Lighting to
  grouped on / standalone off. The force-on automation can alter adaptive
  selection during restoration; final explicit readback confirmed the saved
  switch states. Removed the temporary snapshot scene afterward.
- Final structured system-log search returned no LIFX errors/warnings.
- Ruff/formatting pass. Changed-file Pylint passes for bootstrap/tests;
  `parallel_group.py` reports four existing entity-class placement findings.
  Repository-wide hooks also have existing publishing-workflow, typing, and
  protected-branch findings; this is not a claim of a clean global hook run.

### Missing HA-stop cleanup (third defect)

The first post-fix restart took ~283.6 seconds, mostly in shutdown. Source
and lifecycle analysis, plus a local reproduction, showed group setup
registered no `EVENT_HOMEASSISTANT_STOP` handler. Persistent `spawn` workers
are non-daemon and block in `pipe.recv()`, so CPython's `atexit` joins them
until the supervisor's 240-second service grace time elapses.

Group setup now registers a one-shot `EVENT_HOMEASSISTANT_STOP` listener that
awaits `runtime.async_stop()`, registered through `entry.async_on_unload`.
A real-runtime lifecycle test (`hass.async_stop()` terminates workers and the
dispatcher thread) was red before the fix and green after. Full suite
**291 passed**. Confirmed live: shutdown fell from ~244s to ~44s.

## Restart benchmark

Metric: Supervisor's `Restarting homeassistant` timestamp to
`Detect a running Home Assistant instance`, both from Supervisor logs.
This measures restart-to-HA-RUNNING, not every integration's eventual
external-device reconnection. All timestamps below are 2026-10-15 PDT
(`America/Los_Angeles`, UTC−07:00). Durations use the millisecond timestamps,
not rounded clock displays or tool-response latency.

At least 60 seconds of rest follows each observed completed startup before
the next measured restart. Some historical gaps were longer, including a
user pause between baseline runs 4 and 5. The deployment restarts are excluded.

Historical measurements retained from the earlier tool results:

| Run | Configuration | Restart | RUNNING detected | Seconds |
|---|---|---|---|---:|
| 1 | Built-in LIFX | 16:50:43.167 | 16:51:47.349 | 64.182 |
| 2 | Built-in LIFX | 16:53:14.765 | 16:54:15.510 | 60.745 |
| 3 | Built-in LIFX | 16:55:48.180 | 16:57:06.590 | 78.410 |
| 4 | Built-in LIFX | 16:59:22.308 | 17:00:32.801 | 70.493 |
| 5 | Built-in LIFX | 17:19:45.347 | 17:20:58.774 | 73.427 |
| 6 | Original custom LIFX | 17:27:08.537 | 17:28:21.874 | 73.337 |
| 7 | Original custom LIFX | 17:30:47.054 | 17:32:10.887 | 83.833 |
| 8 | Original custom LIFX | 17:33:24.977 | 17:34:48.526 | 83.549 |
| 9 | Original custom LIFX | 17:36:02.758 | 17:37:21.806 | 79.048 |
| 10 | Original custom LIFX | 17:38:41.044 | 17:39:54.663 | 73.619 |

The five repaired-custom measurements will be appended during the authorized
follow-up benchmark. That configuration includes the now-working Test Group
and its three workers; Test Group was added after the original custom series.
The series therefore differ in active group workload as well as code.

Repaired-custom measurements (final code: worker bootstrap, typed-state
preservation, and HA-stop worker cleanup; Test Group loaded with three
persistent workers):

| Run | Configuration | Restart | RUNNING detected | Seconds |
|---|---|---|---|---:|
| 11 | Repaired custom LIFX | 19:26:51.010 | 19:28:10.322 | 79.312 |
| 12 | Repaired custom LIFX | 19:30:16.308 | 19:31:39.933 | 83.625 |
| 13 | Repaired custom LIFX | 19:35:02.473 | 19:36:26.047 | 83.574 |
| 14 | Repaired custom LIFX | 19:37:52.290 | 19:39:21.032 | 88.742 |
| 15 | Repaired custom LIFX | 19:40:38.725 | 19:41:43.168 | 64.443 |

Diagnostic (not part of either series): restart 18:59:58.552 → RUNNING
19:04:42.178 = 283.626 s while running the typed-state fix but **before** the
HA-stop cleanup; that is the shutdown hang the third defect caused.

### Summary (seconds)

| Series | Mean | Median | Min | Max |
|---|---|---|---|---:|
| Built-in LIFX (1–5) | 69.451 | 70.493 | 60.745 | 78.410 |
| Original custom LIFX (6–10) | 78.677 | 79.048 | 73.337 | 83.833 |
| Repaired custom LIFX (11–15) | 79.939 | 83.574 | 64.443 | 88.742 |

The repaired series includes a functioning Test Group with three persistent
worker processes; the original custom series could not start that group, so
its three worker processes did not exist. The repaired mean is ~1.3 s higher
than the original custom mean and within the run-to-run spread; the shutdown
hang (283.6 s) is eliminated. These absolute numbers remain noisy (18–24 s
ranges) and are not evidence that the group or its workers add meaningful
boot cost beyond the extra processes.

## Rollback source

Pre-fix source is retained locally at
`/var/folders/c2/gr38mmlx25dcmsjbpl2vc1z00000gn/T/opencode/lifx-pre-fix-20261015/homeassistant/components/lifx`.
Restore it with the approved sync script if needed. It restores the original
custom code, including the original Test Group defects; it is not the
built-in integration. No config entries were deleted or recreated.

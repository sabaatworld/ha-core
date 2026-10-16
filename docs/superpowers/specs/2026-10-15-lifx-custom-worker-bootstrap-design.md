# LIFX custom-component worker bootstrap repair

Date: 2026-10-15

Status: Approved for execution; user requires a working integration, no new branches, and no commits.

Plan: [Implementation plan](../plans/2026-10-15-lifx-custom-worker-bootstrap.md)

## Problem and evidence

The private HA instance runs Home Assistant 2026.10.0 / Python 3.14 with
`homeassistant/components/lifx` from the sibling `ha-core` repository deployed
as `/config/custom_components/lifx`. Source branch: `dev`; inspected revision:
`dac92701113fbdb418695dd3b53371bcf84dc6a5`.

The three physical Living Room Tree Lamp lights load and accept commands.
The separately added LIFX Device Group **Test Group** does not create its
entities because its entry stays in `setup_retry`:

- Entry ID: `01M512FVNX1CMHK6AK40G1Z140`
- Unique ID: `parallel-group-6efc73bb5a034dc3b6fdc3799728a1bc`
- Reported reason: `LIFX worker for 192.168.15.155 closed unexpectedly`,
  sometimes naming `.169` instead.
- The existing `light.living_room_tree_lamp` is an ordinary HA light-group
  helper, not this LIFX Device Group. Its successful operation does not test
  the failing group runtime.

The diagnostic agent found repeated child-process tracebacks in Core logs:

```text
File "/usr/local/lib/python3.14/multiprocessing/spawn.py", line 132
    self = reduction.pickle.load(from_parent)
ModuleNotFoundError: No module named 'custom_components'
```

The agent also reproduced the failure using the actual integration source,
Python 3.14.5, and loopback only. Built-in imports worked; a custom namespace
with HA-style temporary import-path mounting failed; retaining the import
root allowed startup. The throwaway reproducer was
`/var/folders/c2/gr38mmlx25dcmsjbpl2vc1z00000gn/T/opencode/lifx_spawn_repro.py`.
The implementation must turn that temporary diagnostic into a durable test.

## Root cause

`homeassistant/loader.py:_async_mount_config_dir` temporarily puts the config
directory on `sys.path`, imports `custom_components`, then removes that path.
The HA process retains the package in `sys.modules`, so physical-light setup
works in that process.

`lifx/parallel.py:LIFXParallelRuntime._spawn_worker` passes a function and a
`ParallelTransport` instance from `custom_components.lifx.parallel` to a
`spawn` child. That fresh interpreter must import the package while
unpickling the process object, before calling the target. The config root is
no longer on its import path, so it exits before `_worker` begins.

The parent's pipe EOF becomes the host-specific error, which group setup
converts to `ConfigEntryNotReady`. Different hosts identify the first failed
pipe observed, not a different network failure. `_worker` normally opens a
UDP socket and sends the IPC `STARTED` message without sending a bulb packet
or waiting for a bulb response. Earlier claims that these failures proved
UDP loss or an initial `get_color` timeout were incorrect.

## Requirements

1. Python 3.14 is the minimum supported Python version.
2. Keep the integration domain `lifx`, existing config entries, unique IDs,
   group membership, and entity IDs unchanged.
3. Preserve the `spawn` start method and the persistent per-member worker model.
4. Repair custom-component imports before the worker function and its custom
   argument types need to be imported in the child.
5. Do not mutate the parent HA process's `sys.path`, `PYTHONPATH`, global
   multiprocessing configuration, or HA loader implementation.
6. Preserve packet encoding, ACK/retry policy, stage scheduling, transition
   semantics, and physical-light setup.
7. Cover real process startup, subsequent command deserialization, worker
   replacement, and cleanup with loopback-only regression tests.
8. Include the bootstrap in direct-sync and HACS-export artifacts.
9. No commits, pushes, or PR creation.

## Options and recommendation

### A. Temporarily or permanently expose the config root in parent `sys.path`

Smallest diff, and the diagnostic demonstrated that an importable root fixes
startup. However, it changes shared interpreter state; temporary mutations
can overlap other imports or worker starts. Permanent mutation broadens HA's
import behavior. Not selected.

### B. Bootstrap only the child through a standard-library target — recommended

For the custom namespace, spawn `runpy.run_path` targeting a small adjacent
`_parallel_worker.py` script. Its initial arguments contain only built-in
values and standard multiprocessing objects. The script adds the correct
import root to **its own** path, imports the original module by its exact
custom name, reconstructs `ParallelTransport`, then calls `_worker`.

This addresses both early imports: the target function and the dataclass
argument. Subsequent `ParallelCommand` objects can then unpickle under the
same module identity in the child. Initial startup and dead-worker recovery
both use `_spawn_worker`, so one change covers both paths. Built-in loading
retains the existing direct worker target.

### C. Replace multiprocessing with an executable/subprocess transport

Can also control child imports explicitly, but would replace process/IPC
management and expand the change well beyond this defect. Not selected.

## Selected design

Create `homeassistant/components/lifx/_parallel_worker.py` with only
standard-library imports before its explicit integration import. Invoke it
by absolute path through `runpy.run_path(..., run_name="__main__")`.

The initial payload is a tuple containing:

1. The import root derived from the deployed package location, not `/config`
   or the current working directory hardcoded into the implementation.
2. The original `parallel` module name (`__name__` in the parent).
3. Plain transport fields `(host, port, target)`.
4. Source number, child `Connection`, shared generation value, stop event.

For this package layout, `Path(__file__).parent.parent.parent` is the parent
of `custom_components`. Use the lexical deployment path so an integration
directory symlink does not accidentally redirect the import root away from
the package namespace. Test with a non-default temporary config directory.

Do not catch an import failure and pretend startup succeeded. Preserve child
stderr tracebacks and parent setup failure/cleanup. Keep the existing worker
protocol and startup deadline. A fix to import/bootstrap does not justify
changing timing or bulb retry policy.

## Acceptance criteria

- A real custom worker starts after its config root has been removed from
  parent `sys.path`; it reports `STARTED` without bulb traffic.
- The parent path/environment remains unchanged before and after spawning.
- A command containing the custom module's dataclass crosses the IPC boundary
  and receives a valid loopback LIFX response.
- A dead custom worker can be replaced, and shutdown leaves no child or
  dispatcher thread alive.
- Built-in worker startup and existing LIFX tests continue to pass.
- Direct-sync and HACS export contain the bootstrap file.
- After deployment, Test Group is `loaded`, its actual LIFX group entities
  exist, and three targeted reload cycles plus a cold HA startup succeed.
- Functional tests target the LIFX entry's group light, not the helper
  `light.living_room_tree_lamp`; member readbacks confirm command propagation.

## Deployment and rollback

Implementation follows review of this design and its plan. Use only
`ha-private-ws/scripts/ha_custom_component.sh` for deployment; it restarts HA.
Save a local copy of the pre-fix component and verify it is a complete
rollback source before syncing. Restore it with the same sync script if
the repaired override fails verification. Do not delete/recreate the group
entry or restore a full HA backup to undo this code-only change.

Record implementation/test output and observed deployment results in the
linked plan. Keep code, bootstrap rationale, and integration documentation
consistent when implementation occurs.

## Execution addendum: typed state preservation

The bootstrap repair passed cold startup and three live group reloads.
The first group control test then exposed a second defect:
`_async_dispatch_projected_states` overwrites typed physical coordinator
state with `None` after a successful dispatch, triggering `.power` and
`.capabilities` access errors. The user authorized fixing the integration
end-to-end. Extend the repair to notify member listeners without replacing
their physical state; retain polling and group-local optimism. Add real
coordinator regressions for on/off and repeat the live acceptance checks.

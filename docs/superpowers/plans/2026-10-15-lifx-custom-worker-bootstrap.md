# LIFX Custom Worker Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Delegation requires user authorization or applicable instructions.

**Goal:** Fix the deployed LIFX integration so the existing Test Group starts, reloads reliably, and controls its physical member bulbs successfully.

**Architecture:** Keep multiprocessing `spawn` and the existing worker protocol. For custom-component loading only, launch a standard-library `runpy.run_path` target with primitive transport data; a child-local script establishes the package import root before importing the worker module and reconstructing its dataclass. Preserve the built-in target and physical-light path.

**Tech Stack:** Python 3.14, multiprocessing spawn/Connection/shared synchronization, runpy/importlib, Home Assistant, pytest, loopback UDP.

**Spec:** [Worker bootstrap design](../specs/2026-10-15-lifx-custom-worker-bootstrap-design.md)

**Status:** Execution authorized by the user: "Include in the plan to fix the integration. Execute". Work in place on `dev`; no branches or commits.

**Current outcome:** Three integration defects repaired, deployed, and
verified: worker bootstrap, typed-state preservation, and HA-stop worker
cleanup. Task 5 benchmark complete (five runs). Current-state documentation:
[LIFX Device Groups](../../integrations/lifx-device-groups.md).

## Global Constraints

- Python 3.14 is the minimum supported Python version.
- Keep the integration domain `lifx`, existing config entries, unique IDs, group membership, and entity IDs unchanged.
- Preserve the `spawn` start method and the persistent per-member worker model.
- Do not mutate the parent HA process's `sys.path`, `PYTHONPATH`, global multiprocessing configuration, or HA loader implementation.
- Preserve packet encoding, ACK/retry policy, stage scheduling, transition semantics, and physical-light setup.
- No new branches, commits, pushes, or PR creation.
- Work from `ha-core` branch `dev` as requested. Inspect status before editing and preserve unrelated user work.
- Completion requires a working integration, not merely a successful worker import. If live acceptance exposes an additional integration defect, capture evidence, add a regression and revise this plan with the necessary repair before continuing. Do not declare success while Test Group remains broken.

## Repository and file map

Source root: `/Users/sabaata/Documents/Projects/ha-core`.
Documentation/deployment root: `/Users/sabaata/Documents/Projects/ha-private-ws`.
Inspected source revision: `dac92701113fbdb418695dd3b53371bcf84dc6a5`.

| Action | File relative to source root | Responsibility |
|---|---|---|
| Modify | `homeassistant/components/lifx/parallel.py` | Select a child-import-safe target/payload in `_spawn_worker` |
| Create | `homeassistant/components/lifx/_parallel_worker.py` | Child-local standard-library bootstrap |
| Create | `tests/components/lifx/test_parallel_spawn.py` | Fresh-interpreter custom/built-in spawn regressions |
| Create | `tests/components/lifx/fixtures/parallel_spawn_probe.py` | Subprocess driver for real runtime and loopback responder |
| Modify | `tests/components/lifx/test_hacs_export.py` | Assert exported bootstrap presence |
| Modify | `homeassistant/components/lifx/README_FEATURES.md` | Brief custom-worker startup explanation |
| Update | This plan in `ha-private-ws` | Actual test/deployment evidence and rollback outcome |

Do not modify `homeassistant/loader.py`. Reference `_async_mount_config_dir`
there to reproduce the loading conditions in tests. Do not alter
`parallel_group.py` merely to mask bootstrap failures with extra retries.

### Task 1: Reproduce the custom import failure in a durable regression test

**Files:** Create the spawn test and probe files listed above.

**Interfaces:** Tests invoke the probe using `sys.executable`. The probe imports
the real integration, calls `LIFXParallelRuntime._start()` / `_stop()`, and
reports JSON evidence to stdout; stderr retains child tracebacks.

- [ ] Read the source repository's `AGENTS.md`, applicable nested instructions,
  `parallel.py:_spawn_worker/_worker/_replace_dead_worker`, and the design.
  Inspect `git status --short`, `git branch --show-current`, and the current
  revision in both repositories. Do not change branches away from `dev`.
- [ ] Before production edits, save a complete pre-fix component directory
  under the approved temporary work area with basename `lifx`. Record the
  deployed source revision and verify the rollback copy's manifest and
  contents. Use the clean pre-edit source or an archive of that exact
  revision; do not copy the repaired source later and label it pre-fix.
- [ ] Inspect the existing throwaway reproducer at
  `/var/folders/c2/gr38mmlx25dcmsjbpl2vc1z00000gn/T/opencode/lifx_spawn_repro.py`.
  Port its behavior into the durable probe; remove hardcoded developer paths.
- [ ] Copy the integration into a pytest temporary directory at
  `<tmp>/config-with-spaces/custom_components/lifx`, excluding caches. In the
  probe's custom mode, reproduce HA's namespace mount exactly:

  ```python
  sys.path.insert(0, str(config_root))
  importlib.import_module("custom_components")
  sys.path.remove(str(config_root))
  sys.path_importer_cache.pop(str(config_root), None)
  parallel = importlib.import_module("custom_components.lifx.parallel")
  assert str(config_root) not in sys.path
  ```

  Ensure cwd and `PYTHONPATH` do not accidentally expose the config root.
  Run each namespace mode in a separate interpreter; pytest's imported
  module cache must not make the child succeed accidentally.
- [ ] In built-in mode, import
  `homeassistant.components.lifx.parallel` normally. Parameterize tests across
  `builtin` and `custom-mounted` with explicit IDs and typed test arguments.
- [ ] Bind a UDP receiver to `127.0.0.1:0`, derive its assigned port, and
  start the actual runtime with that loopback transport. Record parent
  `sys.path` and `PYTHONPATH` before/after. Startup must report availability
  and leave the receiver without any datagram. No physical bulb IP is used.
- [ ] Always stop the runtime in `finally`; retain process handles before
  cleanup so the probe can assert no surviving children/dispatcher thread.
  Bound pipe/socket waits and the outer subprocess timeout (30 seconds).
- [ ] Add a wrapper test following this subprocess/assertion shape (fixture
  paths are computed from `__file__` and pytest `tmp_path`):

  ```python
  completed = subprocess.run(
      [sys.executable, str(probe), mode, str(config_root)],
      cwd=source_root,
      capture_output=True,
      text=True,
      timeout=30,
      check=False,
  )
  assert completed.returncode == 0, completed.stdout + completed.stderr
  evidence = json.loads(completed.stdout)
  assert evidence["available"] is True
  assert evidence["startup_datagrams"] == 0
  assert evidence["parent_path_unchanged"] is True
  assert evidence["parent_environment_unchanged"] is True
  assert evidence["children_stopped"] is True
  ```

- [ ] Run from the source root:

  ```sh
  uv run --no-sync pytest -q tests/components/lifx/test_parallel_spawn.py
  ```

  Expected before the fix: built-in passes; custom-mounted fails with the
  missing `custom_components` traceback / closed worker error. A fixture,
  dependency, or test-runner error is not the required red result. Permit
  real sockets only in these loopback tests using the project's pytest
  socket-fixture conventions; do not disable network guards globally.

### Task 2: Bootstrap custom workers before importing integration objects

**Files:** Create `_parallel_worker.py`; modify `parallel.py`;
extend `test_parallel_spawn.py` and its probe.

**Interfaces:** `_spawn_worker(index, transport) -> _Worker` is unchanged.
The custom spawn target is standard-library `runpy.run_path`; all its
initial arguments are primitive values or multiprocessing objects.
The existing `_worker` signature and all later IPC messages remain unchanged.

- [ ] Add a standard-library-only bootstrap script with this entry sequence:

  ```python
  """Establish custom-component imports before starting a spawned LIFX worker."""

  from importlib import import_module
  import sys

  if __name__ == "__main__":
      (
          import_root,
          module_name,
          transport_fields,
          source,
          pipe,
          current_generation,
          stop_event,
      ) = globals()["lifx_worker_args"]
      sys.path.insert(0, import_root)
      parallel = import_module(module_name)
      parallel._worker(
          parallel.ParallelTransport(*transport_fields),
          source,
          pipe,
          current_generation,
          stop_event,
      )
  ```

  This path mutation occurs only in the disposable worker interpreter.
  Keep bootstrap import exceptions visible on stderr. Do not import
  `custom_components`, `parallel`, or an integration type at script top
  level before the path insertion.
- [ ] Import `Path` and `runpy` in `parallel.py`. In `_spawn_worker`, preserve
  the built-in direct target; use the following custom target/payload when
  `__name__.startswith("custom_components.")`:

  ```python
  worker_args = (
      str(Path(__file__).parent.parent.parent),
      __name__,
      (transport.host, transport.port, transport.target),
      self._source,
      child,
      self._current_generation,
      self._stop_event,
  )
  process = self._context.Process(
      target=runpy.run_path,
      args=(
          str(Path(__file__).with_name("_parallel_worker.py")),
          {"lifx_worker_args": worker_args},
          "__main__",
      ),
      name=f"lifx-parallel-{index}-{transport.host}",
  )
  ```

  Retain the common `process.start()`, child-end closure, and `_Worker`
  return. Explain why the initial payload cannot contain a custom
  dataclass. Keep the code small; no loader or global start-method patch.
- [ ] Re-run Task 1's command. Both actual spawn modes must pass, including
  unchanged parent path/environment and no startup datagrams.
- [ ] Extend the probe's loopback receiver into an EchoRequest responder.
  Parse `HEADER`, require packet type `ECHO_REQUEST` (58), return
  `ECHO_RESPONSE` (59) with the same source, target, sequence, and 64-byte
  token. Respond only on loopback. After `_start()`, call the existing
  `_queue_dispatch((parallel.ParallelCommand("echo", (token,)),), True)`
  and require `ParallelDispatchOutcome.COMPLETED` **and an empty
  `failed_member_indexes`**. Assert the receiver observed the request and
  sent its response. This verifies later custom `ParallelCommand`
  unpickling, not merely the initial `STARTED`.
- [ ] Add a recovery mode: save the first worker process, terminate/join it,
  call `_replace_dead_worker(0)`, and require a different live process plus
  a successful loopback echo dispatch. The same custom bootstrap must be
  used for replacement. Stop/join all workers and the responder in `finally`.
- [ ] Add a failure mode by making only the copied bootstrap script raise
  `RuntimeError("bootstrap regression sentinel")` before startup. Require
  startup failure, the sentinel on captured stderr, no dispatcher, and no
  surviving child. This tests cleanup without adding production fault hooks.
- [ ] Run the focused regression tests and existing transport/group tests:

  ```sh
  uv run --no-sync pytest -q tests/components/lifx/test_parallel_spawn.py tests/components/lifx/test_parallel.py tests/components/lifx/test_parallel_group.py
  ```

  Do not increase startup deadlines to make these pass. If this exposes a
  distinct cleanup defect, record it and revise the plan before expanding
  the production change.

### Task 2b: Preserve typed physical coordinator data after group dispatch

Live testing after the bootstrap repair exposed a second defect in
`parallel_group.py:_async_dispatch_projected_states`: successful commands call
`member.async_set_updated_data(None)`. Physical coordinators now store typed
`lifx-async` state objects, so that call destroys their data. Subsequent
entity and group listeners raise `AttributeError` accessing `.power` and
`.capabilities`, and the group command returns HTTP 500. This was hidden by
the mocked coordinators in the existing group tests.

**Files:** Modify `homeassistant/components/lifx/parallel_group.py` and
`tests/components/lifx/test_parallel_group.py`.

- [ ] Add parameterized on/off regressions with a real
  `LIFXUpdateCoordinator` and typed mock-device state; mock only the external
  packet dispatcher. Require the physical data object and its color/power
  to survive, listeners to receive typed state, the virtual-off marker to
  reflect the command, and a subsequent group command to succeed.
- [ ] Run the new regression before the repair; require failure because the
  coordinator data becomes `None`.
- [ ] Replace the data-overwriting call with
  `member.async_update_listeners()`: notify virtual-power changes without
  replacing physical state, marking failed members successful, or deferring
  the existing polling schedule. Preserve group-local optimism and normal
  physical polling as the source of actual bulb state.
- [ ] Run the group and full LIFX suites and lint on the touched files.
- [ ] Redeploy using the approved sync script; repeat cold-start/reload and
  actual group-control acceptance below. Restore the saved member/Adaptive
  Lighting state before restarting; recreate the transient snapshot after
  restart because HA discards runtime scenes.

### Task 2c: Stop persistent group workers on Home Assistant shutdown

The first post-fix restart took ~283 seconds, mostly in shutdown. Source and
lifecycle analysis plus a local reproduction confirmed a third defect: group
setup registered no `EVENT_HOMEASSISTANT_STOP` handler, so normal HA shutdown
never stopped the persistent `spawn` workers. They are non-daemon and blocked
in `pipe.recv()`, so CPython's `atexit` multiprocessing finalizer joins them
until the supervisor's ~240-second service grace time elapses.

**Files:** Modify `homeassistant/components/lifx/parallel_group.py` and
`tests/components/lifx/test_parallel_group.py`.

- [ ] Add a red lifecycle test that sets up a real group runtime, asserts its
  workers and dispatcher thread are alive, then calls `hass.async_stop()` and
  requires `runtime.stopped`, all processes terminated, and the dispatcher
  thread exited. Reset the synthetic physical entry state in `finally`.
- [ ] Confirm the red result: `runtime.stopped` is False after `async_stop`.
- [ ] Register a one-shot `EVENT_HOMEASSISTANT_STOP` listener during group
  setup that awaits `runtime.async_stop()`, and register its removal through
  `entry.async_on_unload`. Follow the `wiz` integration pattern.
- [ ] Confirm the lifecycle test passes; run the full LIFX suite and lint.
- [ ] Redeploy and confirm the group reloads, then run the Task 5 benchmark.

### Task 3: Verify packaging, compatibility, and documentation

**Files:** Modify `test_hacs_export.py`, `README_FEATURES.md`, and this plan.

**Interfaces:** Existing export copy rules and the private sync script must
include `_parallel_worker.py` beside `parallel.py` without special deployment
steps or a new requirement.

- [ ] Extend
  `test_export_creates_hacs_layout_without_core_only_files` in
  `test_hacs_export.py`: its source tree is synthetic, so add a bootstrap
  fixture file before export, then assert the exported
  `custom_components/lifx/_parallel_worker.py` exists with identical content.
- [ ] Exercise the custom-mounted probe against the exported package as
  well as the direct source copy, proving the distributable bootstraps.
  For this spawn test, invoke `script/lifx_ultimate_hacs_export.py` with the
  actual `homeassistant/components/lifx` source, a temporary destination,
  and `--source-revision` set to the recorded base revision plus `-working`.
  Point the probe's config root at the generated destination; do not use
  the synthetic minimal package for a real worker import.
- [ ] Update `README_FEATURES.md` with a short technical note: Device Groups
  use persistent workers; custom installation bootstraps their package path
  in each child. Keep the existing user-facing light behavior documented.
- [ ] Run the integration suite, then required repository checks:

  ```sh
  uv run --no-sync pytest -q tests/components/lifx
  uv run --no-sync prek run --all-files
  ```

  Record any unrelated existing failures by test/check name and error.
  Do not label the suite passing if failures remain.
- [ ] Run the deployment script dry-run from `ha-private-ws` and confirm both
  `parallel.py` and `_parallel_worker.py` are listed:

  ```sh
  scripts/ha_custom_component.sh sync --source-dir /Users/sabaata/Documents/Projects/ha-core/homeassistant/components/lifx --dry-run
  ```

- [ ] Inspect `git diff --check`, `git diff`, and `git status --short` in both
  repositories. Record checks and the reviewed file list here. No commits.

### Task 4: Deploy and verify the actual LIFX Device Group

**Objects:** Existing Test Group entry
`01M512FVNX1CMHK6AK40G1Z140`, its selected physical members, and its newly
registered LIFX group entities. Do not infer the group entity ID from its
name; resolve it from the entry registry after successful setup.

**Interfaces:** Use HA MCP for reads, config-entry reloads, logs, and light
actions. Use only `scripts/ha_custom_component.sh` for code deployment and
rollback. No direct SSH diagnostics or `.storage` access.

- [ ] Before live changes, re-read the HA best-practices guide and references
  for device-control and scene snapshot/restore. Inspect the Test Group
  entry, selected-member registry metadata, current states, and its stored
  failure reason through discovered read tools. Record actual member IDs.
- [ ] Search all consumers of the physical members and group entry/entities
  across automations, scripts, all helpers, scenes, dashboards, integration
  config-entry data/options (including HomeKit), blueprints, and AppDaemon
  Python/apps.yaml; include other execution systems if present. Follow
  `ha-private-ws/AGENTS.md` exhaustive search requirements. Identify any
  automation that could interfere with a short light-control test. Preserve
  config and membership; this code fix requires no renames or consumer edits.
- [ ] Recheck the pre-fix rollback copy saved in Task 1: confirm its path
  ends in `/lifx`, its recorded provenance matches the deployed baseline,
  and its manifest and files remain intact. Record its exact path here
  before syncing the repaired code.
- [ ] After review authorizes deployment, sync from `ha-private-ws`:

  ```sh
  scripts/ha_custom_component.sh sync --source-dir /Users/sabaata/Documents/Projects/ha-core/homeassistant/components/lifx
  ```

  The script requests HA restart. Wait at least 90 seconds before the first
  MCP request, per the observed embedded-server restart behavior. If not
  ready, use spaced retries; never blindly request another restart.
- [ ] Verify HA `RUNNING`, the custom `lifx` loader message, Test Group
  `loaded`, and its actual light/number/diagnostic entities registered to
  that entry. Inspect raw Core log lines with literal searches such as
  `custom_components`, `Traceback`, and `worker` separately; log search is
  substring filtering, not a regex alternation facility. Page windows as
  needed and distinguish historical failures from the new boot interval.
- [ ] Reload only the Test Group entry three times sequentially. After each,
  require `loaded`, available group entities, and all physical entries still
  loaded. Verify cleanup/replacement does not accumulate old worker pools
  using the integration's available diagnostics and fresh error logs.
- [ ] Snapshot the members' current states with a temporary HA scene through
  supported APIs. Test on/off plus brightness and `color_temp_kelvin` on the
  **actual LIFX group light** resolved from its entry, not
  `light.living_room_tree_lamp`. Read back each member after polling settles;
  service success alone is insufficient. Restore the snapshot afterward.
  If automation overrides a requested value, inspect its trace before
  treating the difference as a transport regression.
- [ ] Record boot/reload outcomes and latest relevant logs here. A cold
  startup (the deployment restart), three reloads, and verified group
  control are required for the live acceptance result. No additional ten-run
  startup benchmark is required for this import-only repair.

## Rollback

If boot, physical lights, or group behavior regress after deployment:

1. Use the same sync script with `--source-dir` pointing to the verified
   pre-fix local directory whose basename is `lifx`.
2. Wait for HA to restart, verify `RUNNING` and physical lights, and restore
   any temporary scene/log-level/automation changes made during testing.
3. Preserve Test Group and its membership. It may return to its original
   `setup_retry` symptom under the pre-fix code; document that honestly.
4. Record rollback source revision, observed reason, and resulting state.
   Do not remove the override as a substitute for restoring the previous
   custom implementation, and do not restore a full HA snapshot.

### Task 5: Append five repaired-integration restart benchmarks

Authorized follow-up: "Once fix is finalized and you are done - Now benchmark
HA restart 5 times and add to prior gathered results".

- [x] Use the same metric as the original ten runs: Supervisor
  `Restarting homeassistant` → `Detect a running Home Assistant instance`.
- [x] Run five HA restarts sequentially. After each, wait 90 seconds before
  calling the embedded MCP server; if unavailable, wait between retries.
  Read the exact restart/RUNNING log pair and verify all four LIFX entries
  loaded. Keep at least a 60-second break after confirmed startup before
  the next restart. Do not retry restart because a client call timed out.
- [x] Append runs 11–15 and raw timestamp-derived durations to
  `docs/integrations/lifx-device-groups.md`. Compute mean, median, minimum,
  maximum and differences using unrounded durations for all three series.
- [x] Explicitly note that the repaired series includes a functioning Test
  Group/three workers, whereas that group was added after the prior custom
  series. Do not attribute all timing differences solely to the patch.
- [x] Verify final HA RUNNING, Test Group loaded/three healthy workers, and
  original member states; record results and leave all changes uncommitted.

Benchmark results (final code, seconds): 79.312, 83.625, 83.574, 88.742,
64.443. Mean 79.939, median 83.574, min 64.443, max 88.742. Final state:
HA RUNNING, all four LIFX entries loaded, Test Group `worker_health
[true,true,true]`, all three member bulbs off. A diagnostic run (before the
shutdown-cleanup fix) measured 283.626 s; that hang is eliminated.

## Completion evidence to record during execution

- Regression red result before the fix and green result afterward.
- Focused tests, integration suite, packaging check, and lint outcomes.
- Source revision plus uncommitted diff/file list deployed.
- Actual LIFX group entity ID and selected member IDs.
- Cold startup and each of the three reload results.
- Group action/member readback and successful snapshot restoration.
- Any rollback or unresolved failure, with current instance state.

## Execution log

### Local repair and verification

- Working in place on `ha-core:dev` at the inspected base revision; no new
  branches or commits. Source checkout was clean before implementation.
- Verified rollback source:
  `/var/folders/c2/gr38mmlx25dcmsjbpl2vc1z00000gn/T/opencode/lifx-pre-fix-20261015/homeassistant/components/lifx`.
  Created with `git archive HEAD homeassistant/components/lifx` before edits.
- Baseline existing transport tests: **17 passed**.
- Durable red regression: built-in passed; custom-mounted failed with
  `ModuleNotFoundError: No module named 'custom_components'` during spawn
  unpickling, followed by `LIFX worker for 127.0.0.1 closed unexpectedly`.
- Bootstrap implemented as designed. Initial startup regression became
  **2 passed**; expanded spawn/transport/group tests **55 passed**.
- Full LIFX suite after packaging/review refinements: **288 passed**,
  including **10 snapshots**, Python **3.14.5**. New real-spawn cases cover
  built-in, custom-mounted and exported layouts; EchoResponse, replacement,
  bootstrap-failure cleanup, parent path/environment, and thread cleanup.
- Independent review found no critical/important blockers. Addressed both
  minor test findings: preserve EchoResponse sequence (clear flags instead),
  and retain the dispatcher handle to prove the thread actually stopped.
- Changed-file Ruff, formatting and Pylint pass. The all-files hook command
  was attempted but exceeded its 120-second budget after reporting existing
  publishing-workflow Zizmor/YAML issues, the expected `dev` commit guard,
  and duplicate `event_type.py`/`.pyi` Mypy input. Its formatter touched two
  unrelated SmartThings fixtures; those exact hook-only changes were undone.
- Targeted Mypy reports **54 errors** in existing code/imported modules.
  Re-ran using `--shadow-file` against the pre-fix `parallel.py`: the same
  **54 errors** reproduce. No bootstrap-related type error was introduced.
- Deployment dry-run includes both `parallel.py` and `_parallel_worker.py`.

### Pre-deployment dependency observations

- Confirmed physical light registry platform `lifx`; ordinary
  `light.living_room_tree_lamp` platform `group`. Existing Test Group remains
  `setup_retry` before deployment.
- Exact member-reference searches (each of bottom/middle/top) find:
  `automation.home_away_turn_off_and_restore`,
  `automation.office_desk_height_controls_living_room_tree_lamp`,
  `script.goodnight`, and HA helper group entry `01JA7AJP9DNS7T27CGFE12A3QR`.
- Inspected all 82 helpers; the ordinary light group contains these members.
  HomeKit `HASS Primary Devices` exposes the three physical lights.
  Dashboard content search found no tree-lamp card matches; it also reports
  an unrelated malformed card container in `home-watch`.
- AppDaemon recursive source/argument search finds the ordinary group and
  member IDs only in `apps.yaml` (including its symlink target), around
  lines 1064–1125. No Test Group entry/unique-ID reference was found there.
- The helper group's force-on automation only selects Adaptive Lighting
  switches through `script.living_room_tree_lamp_adaptive_state`; account for
  adaptive overrides when evaluating bulb color readback. No consumer IDs or
  stored membership values are changed by this repair.

### Live repair outcomes

- First deployment: custom loader log at **18:27:46.502 PDT**, Test Group
  loaded with all three workers alive. Three targeted reloads passed.
- First functional command exposed the typed-data defect documented in
  Task 2b. Forced physical refresh and restored the pre-test scene; after
  polling, all members were confirmed off before the next deployment.
- Task 2b red: both real-coordinator on/off cases failed with `member.data`
  becoming `None`. Replaced the overwriting call with listener notification.
  Both cases green; full suite **290 passed**, **10 snapshots**.
- Second independent review found no blockers in the state repair. Ruff and
  formatting pass. Pylint of the newly touched `parallel_group.py` reports
  four pre-existing entity-class module-placement findings, not new logic
  errors. No unrelated class restructuring was performed.
- Final deployment: custom loader log at **18:49:27.333 PDT**. This restart
  took longer to return; spaced MCP checks initially failed, then confirmed
  RUNNING. No duplicate restart was requested.
- Final entry is `loaded`, reason `null`; actual registry platform `lifx`
  group entity **`light.test_group`**, unique ID
  `6efc73bb5a034dc3b6fdc3799728a1bc_light`. All three physical entries loaded.
- Three final targeted group reloads each verified `loaded`, three healthy
  workers and availability. Actual group-control readbacks for all three
  bulbs: 20%/3500 K → brightness 51/3500 K; 40%/4500 K → 102/4500 K; off →
  all off; on after virtual off at 25%/3000 K → 64/3000 K.
- Snapshot restoration was verified after polling: all bulbs off, Adaptive
  Lighting grouped on / standalone off (the final pre-test snapshot). The
  force-on automation briefly altered adaptive selection; restored that
  selection explicitly and verified it. Temporary scene deleted.
- Final structured LIFX system-log search returned zero entries.

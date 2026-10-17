# LIFX physical-light recovery fade

## Problem and evidence

Physical LIFX lights ignore Fade On Time and explicit transition overrides when
recovering from virtual off. Device Groups and physical-light turn-off retain
their selected durations. The live A21 bulbs have Virtual Power Off enabled;
diagnostics showed hardware power on with brightness zero while HA reported off.

`LIFXLight._async_recover_virtual_off()` resolves the correct recovery image and
receives the selected duration, but writes that image with `duration=0.0` when
hardware power is already on. Commit `cd4b066c70f` introduced this regression.
A command-boundary probe reproduced zero-second recovery for requested durations
of 0.25, 2, and 5 seconds. The initial 160 relevant tests passed because recovery
tests did not assert a nonzero duration.

## Approved design

Pass `duration` to `async_write_recovery_image()` when hardware power is on.
Remove the misleading comment describing that visible restoration as hidden
preparation. Hardware-off recovery prepares its image at zero duration before
fading power on. Use the already-selected duration directly, including explicit
zero and sub-250 ms values; do not apply the ordinary colour-change floor.

Recovery restores saved brightness and patterns, merging explicit brightness
and temperature overrides. The current Virtual Power Off switch controls future
off commands, not whether a previously virtual-off light can recover with a fade.
The existing durable intent, transition reconciliation bound, and refresh
scheduling continue to own state recovery during transitions.

## Acceptance criteria

- A physical virtually-off bulb receives its Fade On Time or explicit transition
  override, including zero and 0.1 seconds, at the public device API boundary.
- Recovery works with the Virtual Power Off switch currently on or off.
- Bare, brightness-only, and temperature-only on requests restore the correct target.
- Actually-off recovery prepares colour immediately and fades power on once.
- Legacy and extended multizone recovery preserve patterns and seconds; matrix
  recovery preserves tile patterns and converts seconds to milliseconds.
- Polling an intermediate fade value and interrupting recovery with off/on does
  not replace the saved target with intermediate brightness.
- The LIFX test suite passes and required repository-wide checks are attempted;
  any pre-existing or environment failures are recorded accurately.

## Scope and constraints

- Technical domain and component directory remain `lifx`.
- Python minimum is 3.14; dependencies and manifest version are unchanged.
- Execute inline in the current checkout. The initial no-commit instruction was
  superseded by the user's request to amend the previous commit and push.
- All task documentation lives in `ha-core/docs/superpowers/`.
- Initial implementation changes local source and tests. The user subsequently
  authorized syncing the component to HA after amendment and push, using the
  private-workspace deployment script.

## Known follow-ups

Source inspection found that the installed lifx-async matrix path for nonuniform
tiles larger than 64 pixels copies a temporary frame to the display without
forwarding duration. Physical multi-tile recovery also lacks ownership checks
between tile writes. These require separate investigation/fixes; duration
forwarding tests do not establish hardware timing or concurrent-write safety.

## Implementation and rollback

Plan: [LIFX recovery fade implementation](../plans/2026-10-16-lifx-recovery-fade.md).

Local rollback is the inverse duration change in the powered-on recovery branch,
with the accompanying regression tests and task documentation removed or restored
from the reviewed diff. There are no entity renames, configuration migrations,
live config API writes, or AppDaemon changes in this execution. The subsequently
authorized deployment uses the private-workspace sync script and restarts HA.

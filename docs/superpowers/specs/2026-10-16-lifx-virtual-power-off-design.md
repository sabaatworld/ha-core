# LIFX Virtual Power Off for Physical Lights and Device Groups

Date: 2026-10-16

Status: Independently reviewed and approved for implementation planning on
2026-10-16 after the four Important findings were corrected. The user approved
architecture, configuration, the shared 250 ms color ramp in Section 2.1, and
100% brightness as the last-resort missing-snapshot fallback in Section 3. The
review corrections below clarify the required engineering behavior. The user
requested proceeding to planning once findings were resolved and subsequently
authorized execution in batches on the current branch. No commits are allowed.

Implementation source: this `ha-core` repository, integration domain `lifx`; audited
revision `4a6aab3bdc927d4050ae44e45b6659a44e330115`.

Implementation plan: [LIFX Virtual Power Off implementation plan](../plans/2026-10-16-lifx-virtual-power-off.md).
The user authorized planning after review corrections and then execution on the
current branch. Specs and plans live in `ha-core` by explicit user
direction; only the deployment script comes from `ha-private-ws`.

## Scope decision

The user explicitly chose to keep the original Virtual Power Off scope and
park the broader group discrepancies discovered during research. Appendix A
records those findings, recommended fixes, verification ideas, and source
references for later work. They are not acceptance requirements for this change.

The user subsequently promoted one additional requirement into active scope:
physical lights and Device Groups share the existing 250 ms minimum for ordinary
color/brightness writes, with one common constant and explicit-transition
priority. Section 2.1 defines its boundaries; this supersedes the deferred-only
status of D27's color-ramp policy.

Changes needed to execute, recognize, resume, and reconcile virtual off safely
are in scope, including interactions with other commands. Reproducing and fixing
an unrelated defect with all virtual-off markers cleared is otherwise deferred.
A shared fix may incidentally improve both paths, but that does not authorize a
general group-compatibility or effects rewrite.

### Active goals

- Repair physical turn-on after group-created virtual off.
- Add an independent persistent **Virtual Power Off** configuration switch to
  every physical light and custom Device Group.
- Support virtual off initiated directly through a physical light.
- Choose virtual versus actual off from the targeted entity's setting.
- Preserve logical off/on behavior across ordinary state commands, effects,
  themes, Identify, polling, reloads, and restarts where virtual off is involved.
- Restore per-zone/pixel state rather than flattening a saved pattern during
  virtual-off recovery.
- Share the existing 250 ms ordinary color-ramp minimum between physical and
  group commands, using a common constant and honoring explicit transitions.
- Retain the existing synchronized group transport, fade configuration,
  technical domain, config-entry IDs, and entity identities.
- Deploy using the ha-private-ws sync script and verify on the private HA
  instance after implementation and local checks.

### Deferred scope

General native-group parity, group display mathematics, effect parameter and
rendering fidelity, unrelated startup/config-flow defects, and membership
management/metadata improvements are parked in Appendix A. Narrow active overlaps
are explicitly defined below: per-member logical power preservation, virtual-
derived effect producer exclusion, participant-safe state commits, and the shared
ordinary color ramp. The proposed full shared command planner and general effects
engine remain follow-up architecture, not mandatory new subsystems for this task.

## Context and evidence

The existing custom integration already lets group-off send brightness zero and
record a physical-coordinator-owned `virtual_off` marker and `resume_hsbk`.
Physical light entities use that marker to report off while hardware power stays
on. Direct physical off currently uses actual power-off.

The main physical resume defect is a mismatch between the calculated target and
the fields actually written. `LIFXLight.set_state` computes saved resume HSBK,
but `set_color` selects waveform components solely from the original service
arguments. A bare on selects no components; a temperature-only on does not
select brightness. The marker is nevertheless cleared, leaving HA reporting on
while the bulb remains dark. Multizone and matrix writers similarly merge only
explicit components, ignoring the calculated resume brightness.

The coordinator's normal refresh path also never calls its existing virtual
power reconciliation method; the only call is during physical light addition.
External changes therefore do not clear the marker during normal polling.

Research evidence:

- A network-free probe exercised 11 scenarios using current runtime code and
  device doubles honoring the outgoing component masks. It reproduced bare and
  temperature-only resume failures, actual-off-after-virtual-off resume failure,
  multizone/matrix resume failure, absent polling reconciliation, mixed-group
  power staging, and state updates to skipped unavailable members.
- Existing focused tests passed: `uv run --no-sync pytest
  tests/components/lifx/test_light.py tests/components/lifx/test_parallel_group.py
  -q` reported **131 passed in 4.28 s**. Passing tests do not cover the missing
  behavior demonstrated by the probe.
- Broader Appendix A findings were independently cross-checked against native
  group source, physical LIFX handling, and installed `lifx-async==6.5.1` source.
  These checks are not claims of live-hardware reproduction.
- No integration implementation or live-light changes have been made for this
  design session.

## 1. Approved architecture and configuration

### Independent target-owned policy

Each physical device and Device Group exposes a configuration switch named
**Virtual Power Off**. It defaults to **On** for both existing devices on first
upgrade and newly added devices. A saved user choice survives reload/restart.

Changing the switch affects future off commands only. It sends no light packets
and does not reinterpret or clear an existing virtual-off marker.

| Off target | Target's switch | Required result |
| --- | --- | --- |
| Physical light | On | Virtual off for that physical light |
| Physical light | Off | Actual power-off for that physical light |
| Device Group | On | Virtual off for participating members, regardless of their physical switches |
| Device Group | Off | Actual power-off for participating members, regardless of their physical switches |

A physical switch set Off does not prevent recognition of group-created virtual
off, and does not disable resume support. The policy controls how off is sent,
not whether shared logical state is understood.

### Shared physical state ownership

Each physical coordinator owns actual hardware state, logical virtual-off state,
saved resume state, and the operation/transition information required for safe
reconciliation. Every group containing the physical light observes that shared
state. Resume data remains available after a subsequent actual power-off.

Distinguish the active virtual-off marker from `resume_required`: an actual
power-off can clear the marker while the device still needs its virtual-derived
brightness/pattern restored. The presence of an old snapshot alone does not mean
it should be reapplied; recovery eligibility must be represented explicitly.

Persist each target's policy in its config entry's options, independently of
whether its switch entity is enabled. Load policy/recovery authority before
accepting commands. Resume images, pending recovery candidates, and revisions
must have a supported integration-owned persistence authority; the implementation
plan will specify its versioned storage boundary. Existing physical-light extra
restore data is a legacy migration source, not a second writer that may overwrite
newer runtime state when an entity is added late or re-enabled.

Use the existing physical and group transport paths, extending their supported
packet/stage forms only where needed for virtual-derived pattern recovery.
Centralize the shared virtual-power semantics required to prevent contradictory
implementations. Do not overwrite typed physical coordinator data with a logical
projection. A pattern snapshot includes all relevant zone/tile/component colors
and addressing/layout metadata; validate compatibility with current hardware
before restoring it.

## 2. Virtual-power command requirements

These requirements reflect the user's reviewed choices. Broader native-group
parity proposals discussed during Section 2 review remain deferred by the later
scope decision above.

- Virtual off saves the intended next-on state and writes zero brightness with
  the selected off transition. It does not power on an actually-off device.
- Actual off uses the legacy hardware power-off path. Clearing a virtual marker
  must not discard resume information still needed for the next on.
- Repeated off must not replace a valid resume snapshot with a zero-brightness
  image or change an actually-off device into a powered-on one.
- On after virtual off restores required components even when they were omitted
  from the service call. Explicitly requested components override saved ones.
- Actually-on virtual-off members receive a visible restore; actually-off
  members need hidden state preparation followed by hardware power-on. Group
  recovery must make that decision per participating member.
- A bare recovery restores each affected member's saved state, including its
  zone/pixel pattern. Ordinary already-on group command merging is not being
  redesigned as part of this scope.
- `lifx.set_state` with `power` omitted preserves logical on/off state. For a
  virtually-off light, requested changes are retained for the next on while the
  light remains dark. Follow the existing physical partial-write semantics and
  the documented LIFX service contract.
- In a mixed group, omitted power preserves each participating member's logical
  state, including ordinary on, ordinary actual-off, virtual-off, and actual-off
  with virtual-derived recovery pending. Fix this narrow logical-power field for
  all participants rather than copying aggregate power to ordinary members.
  Existing aggregate color merging for ordinary non-recovery members remains
  deferred under D01, as do general display-aggregation changes under D04.
- Zone-only zero-brightness writes are not whole-light virtual-off operations.
- Effects/themes with `power_on: false` must not expose virtually-off lights.
  Temporary operations must not destroy their logical off state or saved resume
  information. Independent effect modes/timing/rendering defects are deferred.
- Preserve explicit-transition priority and existing fade overrides/fallbacks.
- Preserve group synchronized visible-release behavior and avoid extra
  group-triggered state polling merely to compensate for bookkeeping errors.

The brightness-zero `light.turn_on` case is excluded from the LIFX entity-level
design table: HA handles it before invoking the entity's turn-on method.

### Native-group research baseline

Native HA Light Groups forward requested attributes to every member; they do
not fill unspecified attributes from a displayed average. Their default state
is on when any known member is on. These facts informed the discussion but do
not silently expand the active task into all-member native-group parity.

The group-specific `lifx.set_state`/effect service comparisons in Appendix A are
against physical LIFX handlers. A native `light` group is not automatically a
direct target of every LIFX-specific service in this checkout.

### Virtual-derived effect and temporary-operation boundary

The following contract applies to virtual-off/resume-required members and
operations that started from that state. It does not require correcting ordinary
non-virtual effect mode, speed, palette, or end-state fidelity. A temporary
operation retains an exact pre-operation recovery image and logical/actual power
classification separately from its visible working state.

| Operation | Visibility and snapshot treatment | Completion, stop, or replacement |
| --- | --- | --- |
| Theme with `power_on: false` | Keep each off member dark and merge the rendered requested result into its next-on snapshot. Do not write positive visible brightness to an actually-on virtual-off member. | The prepared next-on state persists; no temporary restore is scheduled. Broader theme-rendering fidelity remains D14. |
| Theme with `power_on: true` | Resume and apply the requested result as an ordinary permanent on operation; retain recovery protection until the required writes succeed. | Commit the resulting on state/snapshot only for successful participants. |
| Finite pulse with `power_on: false` | Exclude logically-off members; leave their recovery images untouched. | No temporary on or cleanup writes to excluded members. |
| Finite pulse with `power_on: true`, or Identify | Temporarily permit visibility. HA reports the participant on during the visible operation, while its pre-operation off classification and resume image remain protected separately. | On completion or explicit stop, restore the pre-operation actual/virtual off classification and saved image. A newer command replaces that restoration obligation. |
| Ongoing software or firmware effect with `power_on: false` | May prepare supported effect parameters while an off member remains dark, but its producers/rendering must not emit visible output for that member. Preserve the recovery image except for explicit prepared color/pattern changes. | Stop alone must not illuminate it or replace its recovery image with an aggregate color. |
| Ongoing effect with `power_on: true` | Permit resume/visibility and retain the pre-effect virtual-derived recovery image as an effect-owned restoration obligation. | Explicit effect stop restores that starting off classification/image for still-owned members. A newer state command instead consumes/supersedes the obligation and applies the new target. |
| Off, new state request, or a replacing operation while an effect owns a virtual-derived member | Revoke the old producer's eligibility for the affected member before dispatching the replacement. Preserve the pre-effect image as a merge/recovery base where omitted fields require it. | No old queued stage, later frame, or cleanup may relight the member or overwrite the newer target. Other still-owned members may continue their existing effect. |

Producer exclusion is an active correctness requirement, not just a guard around
completion callbacks. Coordinate group-owned tasks, physical conductor activity,
and overlapping group owners at the shared physical member boundary. Stop or
exclude producers that could violate an off request, cancel unsent stale stages,
and establish ordering before the newer writes. Packets already sent cannot be
recalled; do not claim otherwise. If active firmware rendering would defeat
virtual darkness, quiesce it for that operation. Ordinary non-VPO firmware-effect
auto-stop policy remains unchanged.

Restore the saved classification rather than consulting a policy switch that
changed during a temporary operation. Do not restart another owner or a previous
animation as a side effect of restoring static color/pattern and power state.
All temporary ownership and exclusion callbacks must be cleaned up on completion,
stop, entry unload, and HA shutdown.

### 2.1. Approved addition: shared minimum color ramp

The user explicitly requested adopting the physical light's 250 ms color-ramp
behavior for Device Groups and sharing its constant. This is an active
implementation requirement, rather than a parked compatibility suggestion.

Move the existing `LIFX_MIN_COLOR_RAMP = 0.25` from `light.py` into the integration's
`const.py`. Both physical and group command construction must import that one
seconds-based constant. Derive milliseconds at the group protocol boundary;
do not introduce a separate hard-coded 250 ms value or duplicate the default in
another transport/entity class. Update affected code/test imports when moving it.

Apply the minimum to ordinary color/brightness writes at the same semantic
points where the physical implementation uses its ordinary color-ramp branch.
Keep On/Off and temporary/effect operations distinct from ordinary color writes.
Do not indiscriminately clamp every packet carrying a color payload.

For an eligible ordinary color write:

1. If a transition was supplied to the entity, use it exactly, including `0` and
   positive values below 0.25 s. This preserves the documented explicit-transition
   priority and corrects the physical branch's existing unconditional clamp.
2. Otherwise resolve the existing matching group/member defaults, then use
   `max(resolved_duration, LIFX_MIN_COLOR_RAMP)` on both paths.

| Ordinary color/brightness request | Required duration |
| --- | --- |
| No incoming transition; group and matching member Numbers both 0 | 250 ms |
| No incoming transition; selected configured default 0.1 s | 250 ms |
| No incoming transition; selected configured default 0.8 s | 800 ms |
| Incoming `transition: 0`, regardless of Number defaults | 0 ms |
| Incoming `transition: 0.1`, regardless of Number defaults | 100 ms |

An HA profile or another integration may supply a transition before LIFX entity
dispatch; that incoming value has the same precedence as the entity's other
transition arguments. The new minimum is not an additional clamp on such values.

Hidden color/pattern preparation before an actual power-on remains immediate.
Actual power-on/off transitions and virtual on/off transitions retain their
selected On/Off duration; a virtual-off brightness-zero packet does not acquire
the ordinary color minimum merely because it uses a color command. Effect,
Identify, and theme-specific timing likewise does not acquire a blanket floor.

The common constant changes timing only. It does not authorize the deferred
physical On-versus-Cross classification fix (D26), general group command-baseline
changes, or general effect rendering/lifecycle work. Group dispatch must retain
its common visible-release deadline and existing per-member duration inheritance.

Update feature documentation when implementing this requirement: a zero Number
continues to mean inheritance/no configured override, but the terminal behavior
for eligible ordinary color writes is now the shared 250 ms minimum. An explicit
incoming zero remains immediate. Preserve historical specs as research context
and identify this section as the newer policy decision.

## 3. Reconciliation, persistence, and failures

The review corrections below clarify the virtual-power requirements. The user
explicitly selected 100% brightness as the last-resort recovery fallback.

### Hardware observations and transitions

- Reconcile after each successful refresh using the newly observed typed state
  before notifying listeners. A failed refresh retains last-known logical state
  and resume data and exposes normal coordinator availability.
- Hardware power-off clears the active virtual-off marker but retains any
  virtual-derived resume data still needed for a future on.
- While hardware remains on and every relevant zone/pixel is dark, retain a
  confirmed virtual-off marker. A zero-brightness observation alone does not
  create a marker; unrelated external zero-brightness states keep their existing
  semantics.
- A confirmed externally visible state supersedes virtual off and becomes the
  resume baseline. Check the complete device's zones/pixels, including relevant
  Ceiling components, rather than only its overall color.
- An in-flight fade-to-zero or temporary Identify/pulse may be visibly nonzero
  before it finishes. Do not misclassify those expected intermediate samples as
  external changes. Bound that protection to the operation's lifetime and
  re-evaluate from a fresh observation afterward.

### Persistence

- Persist the policy independently from the current virtual-off marker and
  resume snapshot. A saved Off policy is not equivalent to missing data.
- Restore the policy and virtual-derived snapshot on physical/group reload and
  HA restart; reconcile them against fresh hardware observations.
- Save HSBK and per-zone/pixel resume information through HA's supported
  persistence mechanisms. Do not edit `.storage` directly or assume cached
  hardware power is authoritative after restart.
- Treat malformed/incompatible saved state as unusable instead of allowing an
  exception to block setup. A usable requested/saved/observed recovery target is
  always preferred. If there is no usable snapshot, no explicit brightness, and
  no valid nonzero observed brightness, use valid current color components at
  100% brightness as the user-approved last resort. Preserve explicit brightness
  and do not reinterpret unrelated intentional zero-brightness writes. This
  condition is exceptional, not once-only: loss/invalidation of recovery data
  can recur. A successful recovery establishes a new usable baseline; a newly
  added light without virtual-derived off history does not enter this fallback
  merely because it has no snapshot yet. If a pattern is incompatible, use the
  current compatible layout with valid observed colors rather than writing old
  addresses or claiming the unavailable old pattern was restored.

### Completion, cancellation, and participants

- Before any destructive zero-brightness/pattern write, capture the candidate
  pre-off recovery image and persist it as pending operation data. Do not discard
  an existing valid recovery image on repeated off. Capturing this candidate is
  distinct from confirming a successful virtual-off state.
- Commit confirmed virtual-state changes only after their required stages
  complete. For a group result, the successful set is the captured eligible
  participants whose required stages completed, excluding every reported
  `failed_member_indexes` entry. `COMPLETED` with failed members is partial
  success, not permission to update all submitted members. Overall failure or
  supersession cannot manufacture a confirmed-success commit.
- If an ACK is lost after a command may have applied, retain the candidate,
  operation kind/revision, and uncertain status across reload/restart. Never let
  a zeroed or partially overwritten device image replace its pre-off recovery
  data just because dispatch did not report success.
- A subsequent successful observation can resolve a known pending off intent:
  powered-on and fully dark confirms its virtual-off result; actual power-off
  clears the active marker while retaining recovery-needed state. This is
  reconciliation of an issued pending intent, not inference from arbitrary zero
  brightness. An unchanged valid pre-write image can establish that off did not
  apply. Partial pattern changes retain the recovery candidate and uncertainty;
  report observed visibility without treating the partial image as a new clean
  resume baseline. The next successful explicit command can resolve that state.
- If persisting the only candidate fails, fail before the destructive write.
  Skipped/unavailable members retain their own state; another member's success
  must not clear their recovery data or falsely confirm their endpoint health.
- Associate pending virtual-state updates and temporary-operation cleanup with
  command revisions. Stale completions must not clear or restore over a newer
  physical/group request.

## 4. Verification and deployment requirements

The linked implementation plan turns these requirements into concrete tasks,
tests, live readbacks, restoration, and rollback. The independent reviewer
confirmed that no further product decision is needed before planning.

### Local verification matrix

- New and existing physical/group switches default On; saved Off choices restore.
- All four combinations of physical and group policies, targeting each in turn.
- Physical/group off followed by bare on and temperature/color-only on.
- Virtual off followed by actual off and then on, including an intervening
  policy change, reload, or HA restart.
- Repeated off retains the saved brightness/pattern.
- Off-state partial writes preserve darkness and update the next-on target.
- Multizone/matrix recovery restores exact saved patterns; partial updates to a
  virtually-off saved pattern affect only their intended parts and do not create
  a separate whole-light virtual-off operation.
- External visible changes and actual power-off reconcile after successful polls.
- Intermediate fade observations do not prematurely clear virtual off.
- Failed/superseded writes do not commit false success; skipped group members
  do not receive virtual-state updates.
- An applied off with lost ACK retains its pre-off image and reconciles from a
  pending intent after polling/restart. Partial pattern writes cannot destroy
  the candidate. A `COMPLETED` dispatch with failed member indexes commits only
  its successful subset; failed persistence prevents destructive writes.
- Effects, themes, and Identify preserve virtual-off state and resume data when
  required by their power/temporary-operation contracts.
- A producer started before virtual off, through a physical entity or overlapping
  group, cannot send later visible frames or stale cleanup over that off/newer
  command. Test active production/queued stages, not only completion bookkeeping.
- `power`-omitted mixed groups preserve each member's logical state and update
  virtual-derived next-on images without adopting aggregate power for ordinary
  actual-off members. General color-merging/display fixes remain deferred.
- Re-enabling a light/switch entity does not overwrite newer authoritative
  recovery/policy state. Policy survives a reload with the switch disabled.
- Missing/invalid recovery images fall back to 100% only after explicit and valid
  saved/observed brightness options are exhausted; subsequent successful off/on
  uses the newly established image. Unrelated intentional zero-brightness states
  and newly added devices without virtual-derived history retain their semantics.
- Physical and group ordinary color writes use the same shared 250 ms floor when
  defaults resolve to zero or a positive value below that floor. Larger defaults
  remain unchanged; group zero still inherits nonzero member values.
- Explicit incoming 0, 0.1, and 0.8 s transitions reach the physical/group write
  unchanged. Assert wire duration/component semantics rather than only UI state.
- Hidden preparation stages remain zero-duration; power and virtual on/off
  stages keep their selected fade durations. No blanket floor is added to effect,
  Identify, or theme-specific commands, and synchronized group release is retained.

Use meaningful regressions that exercise real coordinators and outgoing write
semantics, including selective component masks and actual device-type writers.
Existing mock-only tests did not catch the reported resume defect.

Run focused tests, the full LIFX suite, required formatting/lint checks, and
`uv run --no-sync prek run --all-files`. Regenerate English translations after
changing `strings.json`, before tests and deployment; the sync script excludes
`strings.json`. Report existing unrelated hook failures accurately.

### Deployment and live verification

The user explicitly requested deployment and live testing using the sync script
from this repository. After implementation/local verification, run from
`ha-private-ws`:

```sh
scripts/ha_custom_component.sh sync --source-dir /Users/sabaata/Documents/Projects/ha-core/homeassistant/components/lifx --dry-run
scripts/ha_custom_component.sh sync --source-dir /Users/sabaata/Documents/Projects/ha-core/homeassistant/components/lifx
```

The real sync restarts HA. It does not wait for startup or inspect logs, so
verify integration setup, worker health, entity availability, and logs afterward.
Confirm restart completion rather than blindly repeating a slow restart.

Before testing, capture the selected physical/group lights' states and policy
settings, and inspect automation/Adaptive Lighting consumers that could interfere
with the exercises. Restore the starting light states, policies, and any changed
automation/integration settings afterward. Use existing custom Test Group and
its physical members only after resolving their current identities and state.

Verify the physical/group policy combinations, bare resume, explicit partial
on, prepared off-state changes, and restore across reload/restart with readbacks
after settling and normal polling. Inspect the actual hardware state where
available, not only HA's logical off display. Test zone/pixel behavior locally
with suitable fixtures/emulation if that hardware is not available live; do not
claim live coverage for unsupported test hardware.

Retain the pre-change custom-component source as the rollback point. Rollback
uses the same sync script with that source, then startup/readback verification.

## Appendix A. Deferred discrepancies and researched fixes

Items in this appendix are parked except for the narrow active overlaps stated
in Sections 1-3 and individual rows. These include per-member logical power,
virtual-derived effect exclusion/restoration, pattern recovery, participant-safe
commits, and D27's shared color ramp. They do not activate each affected row's
general non-VPO parity work. The IDs are references, not an approved execution
order. Source links refer to the audited implementation checkout; later edits can move
line numbers.

### A1. Ordinary commands and group reporting

| ID | Finding and evidence | Recommended later fix | Verification idea |
| --- | --- | --- | --- |
| D01 | Partial commands use one aggregate HSBK baseline: brightness-only overwrites member colors, color-only overwrites member brightness, and bare on can homogenize already-on members. This was an explicit older design choice, not an accidental regression. [Group command builder](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L828-L877), [older design](../../../../ha-core/docs/superpowers/specs/2026-07-20-lifx-cross-fade-and-dynamic-group-endpoints-design.md#L60-L61). | Replace the general aggregate command baseline with per-member command planning; share only requested components and preserve each member's other values. Explicitly supersede the older baseline policy. | Two already-on members, red/20% and blue/80%: brightness-only preserves colors, color-only preserves brightness, and bare on preserves both. |
| D02 | General group turn-on can omit actual power-on for an off member whenever the group already displays on. This also occurs with every virtual marker false. [Power staging](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L859-L877). | Decide power stages per member rather than from aggregate group on/off. Keep synchronized visible release. The subset required for virtual recovery remains an active requirement. | With one actually-on and one actually-off bulb, group turn-on reaches both. Include a currently-on optimistic projection. |
| D03 | Omitted `power` assigns the aggregate logical target to every member's projected command state. It does not itself physically power all members on. [Target construction](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L840-L851), [completion bookkeeping](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L887-L912). | **Narrow active overlap:** preserve each participating member's logical power when power is omitted, including ordinary actual-off members in mixed requests. This does not activate D01's general aggregate color-merging changes or D04's reporting rewrite. | Mixed ordinary on/off and virtual-derived members retain individual logical power; dark members' next-on images update without illumination. |
| D04 | Display aggregation includes actually-off and unavailable members, including stale power-on data. Native groups aggregate attributes from on members; stale unavailable members cannot keep a native group on. [Custom aggregation](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L277-L304), [native](../../../../ha-core/homeassistant/components/group/light.py#L203-L249). | Filter eligible members for state and logically-on members for displayed light attributes; keep bounded optimism separate. | Healthy off member plus unavailable cached-on member reports off; a bright on member is not diluted by an off member's stored brightness. |
| D05 | Raw hue is averaged arithmetically: 350 degrees and 10 degrees yield approximately 180 degrees instead of red. [Custom mean](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L299-L302). | Reuse native circular hue averaging instead of averaging raw hue integers. | Two available, powered-on saturated reds at 350/10 degrees report near 0/360, not cyan. |
| D06 | Group color mode follows nonzero averaged saturation, whereas native groups choose the predominant on-member mode. [Custom mode](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L1219-L1242), [native mode](../../../../ha-core/homeassistant/components/group/light.py#L297-L315). | Adopt native predominant-mode reporting if native display parity is selected; account for the physical LIFX entity's intentional saturation-based own mode. | Two color-temperature members plus one HS member report color-temperature mode. Treat as compatibility/design concern rather than an independently proven accident. |

### A2. Effect lifecycle and parameter fidelity

| ID | Finding and evidence | Recommended later fix | Verification idea |
| --- | --- | --- | --- |
| D07 | Ordinary group state requests do not cancel the group-owned software effect task, so a later pulse/colorloop tick overwrites a newer color/brightness command. [State entry](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L747-L752), [effect loop](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L1103-L1157). | Introduce explicit shared effect ownership/cancellation at the physical-light boundary, covering physical/group commands and overlapping groups. Stop producers before issuing the replacement target. | Start a group colorloop, issue warm-white/low-brightness, and confirm later effect deadlines cannot overwrite it. |
| D08 | Group pulse ends its final cycle at brightness zero without restoring the original color/power. [Pulse](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L1103-L1118). | Use proper transient pulse waveforms plus per-member pre-effect state restoration, preserving device-specific patterns. | An ordinary powered-on warm-white/40% bulb returns to that state after a red pulse. |
| D09 | Group effect-stop cancels a task but does not restore pre-effect per-member state; ordinary bulbs receive the current aggregate color instead. [Stop](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L1046-L1066). | Capture per-member prestate, restore on completion/explicit stop, and guard restoration with command revisions so it cannot overwrite newer work. | Stop during a pulse's bright phase; heterogeneous original member states are restored. |
| D10 | Pulse ignores mode and always asks for `power=True`, including `power_on: false`. Omitted strobe defaults are 1 s/1 cycle instead of the physical library's 0.1 s/10 cycles. [Pulse](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L1103-L1118), [physical dispatch](../../../../ha-core/homeassistant/components/lifx/manager.py#L274-L299). | Reuse library mode/default interpretation and waveform shape; respect the power flag and preserve exact permitted period/cycle values. Virtual-off power-flag safety remains active scope. | Test each mode; an entirely actually-off group with `power_on: false` receives no power-on; omitted strobe parameters match physical behavior. |
| D11 | Colorloop ignores dedicated-service `brightness_pct` and `spread`. [Colorloop](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L1120-L1157), [physical normalization](../../../../ha-core/homeassistant/components/lifx/manager.py#L301-L328). | Share normalization and apply member-index spread while dispatching synchronized frames. | `brightness_pct: 10` produces 10%; a three-member spread of 120 degrees produces corresponding hue offsets. |
| D12 | Colorloop advances `change` degrees once per `period`, while pinned library 6.5.1 completes a revolution per period. At period 60/change 20 the group takes about 18 minutes, versus 60 s physically. Group saturation alternates bounds instead of the physical renderer's midpoint. Local strings still describe older per-step timing. [Loop](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L1120-L1157). | Align rendering with the pinned library/public APIs, calculate motion from elapsed time, drop obsolete frames rather than queueing them, and update inconsistent descriptions. | Compare hues at controlled elapsed times and saturation against the physical library; test under delayed dispatch. |
| D13 | Active group effects are never reported through `effect`; the light inherits `None` despite advertising effects. [Group light](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L1190-L1298). | Track group-owned software effect identity and otherwise aggregate exposed on-member firmware effects appropriately. | Read the group effect during pulse/colorloop/firmware effect and after stop. Merely aggregating member conductor state will not identify group-owned tasks. |

### A3. Themes, device-specific writes, and Identify

| ID | Finding and evidence | Recommended later fix | Verification idea |
| --- | --- | --- | --- |
| D14 | Paint theme uses only palette color zero for every member, unlike random per-bulb selection and spatial multizone/matrix rendering on the physical path. [Theme](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L1068-L1101), [physical handler](../../../../ha-core/homeassistant/components/lifx/manager.py#L207-L223). | Reuse public library theme rendering/conversion and dispatch full device-specific results through the group transport. Extend the library's public rendering boundary if necessary rather than patching private internals. | Multicolor themes retain multiple zone/pixel colors; do not assert ordinary random bulbs must all choose different colors. |
| D15 | Move ignores a supplied theme, although the physical path applies it first. [Group Move](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L978-L1004), [physical Move](../../../../ha-core/homeassistant/components/lifx/manager.py#L242-L272). | Apply and confirm the requested theme before starting Move, preserving staged dispatch. | A supplied theme reaches zones before the effect command; omitting it preserves existing colors. |
| D16 | Move right/left values are reversed between group and physical paths. Group right sends 1; physical right maps to `Direction.REVERSED`, value 0. No physical library remapping compensates. [Group mapping](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L991-L996), [physical mapping](../../../../ha-core/homeassistant/components/lifx/manager.py#L73-L78), [raw packing](../../../../ha-core/homeassistant/components/lifx/parallel.py#L253-L265). | Use the same library direction enum mapping on both paths. | Both explicit directions and omitted default produce matching protocol parameter values. Visual orientation still depends on the installed strip. |
| D17 | Group `lifx.set_state` ignores accepted `zones`; a zone-limited color request becomes a whole-light write. [Group handler](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L828-L885), [physical writer](../../../../ha-core/homeassistant/components/lifx/light.py#L524-L566). | Add device-specific zone/pixel transport writes and preserve untouched parts using the physical semantics. The minimum needed to restore virtual-off patterns remains active scope. | A request for zone zero leaves all other zones unchanged, including on ordinary already-on devices. |
| D18 | Group `lifx.set_state` ignores deprecated `infrared` in combined requests; infrared-only requests instead fail as containing no state change. [Schema](../../../../ha-core/homeassistant/components/lifx/services.py#L69-L74), [physical behavior](../../../../ha-core/homeassistant/components/lifx/light.py#L330-L353). | Prefer a clear pre-dispatch unsupported-action error for this deprecated group feature unless compatibility is deliberately implemented. Never silently succeed after applying only other fields. | Combined requests either execute the advertised infrared behavior or reject before modifying the lights. |
| D19 | Explicit palette saturation/brightness at or below 1 use different units. Physical 0.5 means 50%; group means 0.5%. Named themes are unaffected. [Group conversion](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L153-L163), [physical conversion](../../../../ha-core/homeassistant/components/lifx/util.py#L156-L160). | Share physical palette normalization, then convert normalized HSBK to raw protocol units. | Compare 0, 0.5, 1, and values above 1 for theme/Morph/Sky explicit palettes. |
| D20 | Group Identify never temporarily powers on an actually-off member. Its waveform also packs raw brightness 1 rather than full-scale 65535. [Identify](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L930-L942), [raw packet builder](../../../../ha-core/homeassistant/components/lifx/parallel.py#L195-L243), [physical Identify](../../../../ha-core/homeassistant/components/lifx/coordinator.py#L196-L217). | Use the normal HSBK-to-wire conversion, temporarily power on permitted off members, then restore their prior state with revision-safe cleanup. Virtual-off preservation is active; unrelated general Identify parity is deferred. | Verify the encoded endpoint is 65535 and an actually-off participant receives the needed power stages. Do not equate wire-scale brightness with an exact perceptual output ratio. |

### A4. Availability, configuration, and metadata

| ID | Finding and evidence | Recommended later fix | Verification idea |
| --- | --- | --- | --- |
| D21 | Startup requires every member entry loaded before applying an any-ready check, so one physical `SETUP_RETRY` blocks healthy members. Running availability is already partial. [Startup](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L1362-L1385), [July 23 partial-availability plan](../../../../ha-core/docs/superpowers/plans/2026-07-23-lifx-device-group-partial-availability.md#L40-L56). | Keep stable optional coordinator/worker slots; start when any member is eligible; listen for returning entries and bind only their slots. Retain appropriate capability information without inventing support for unknown members. | Restart/reload with one healthy member and one retrying entry; the healthy subset controls successfully and the returning member rejoins. |
| D22 | Group Reconfigure is UI-reachable because the shared flow class advertises the method, but its initial form accesses `entry.data['host']`, absent from group entries, and raises `KeyError`. [Handler](../../../../ha-core/homeassistant/components/lifx/config_flow.py#L193-L217), [group entry data](../../../../ha-core/homeassistant/components/lifx/config_flow.py#L318-L325). | Branch reconfiguration by entry type before reading host; keep physical host behavior and show a group-specific form for group entries. | Start the actual group reconfigure flow without user input; it displays a valid form rather than throwing. |
| D23 | There is no group membership editor/options flow, unlike native group management. [Custom group creation](../../../../ha-core/homeassistant/components/lifx/config_flow.py#L264-L340). | Prefer a group-aware Reconfigure membership editor for setup-defining membership data. Preserve entry/group/entity IDs and independent settings, update only membership, and perform a controlled runtime reload. | Add/remove members without creating a new group or losing settings; validate duplicates and unsupported nested members. |
| D24 | The custom light exposes neither standard `entity_id` member metadata nor a registered generic group. Template expansion and membership introspection therefore differ. Custom LIFX effect targeting still works through explicit manager dispatch. [Entity](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L1160-L1204), [manager dispatch](../../../../ha-core/homeassistant/components/lifx/manager.py#L397-L425). | Expose descriptive current member IDs where useful; deliberately preserve virtual-device dispatch. Do not blindly register a generic expandable group, which could replace the virtual target with physical IDs and bypass synchronized dispatch/off policy. | Membership attributes resolve current renamed member IDs while targeting the virtual light still reaches its custom runtime. Adding an attribute alone does not make template `expand()` recurse into arbitrary integration lights. |
| D25 | Successful subset dispatch resets keepalive failure/health state for every member, including excluded unhealthy members. [Health reset](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L805-L815). | Carry the eligible/successful participant set through completion and reset only members whose communication actually succeeded. Apply the same participant discipline to virtual-state commits. | One healthy member's successful command does not clear an excluded member's failure count or make its endpoint eligible without recovery evidence. |

### A5. Transition discrepancies found in the follow-up investigation

These record the physical/group consistency research. D26 remains deferred.
D27's shared ordinary color-ramp policy and explicit-transition handling have
been promoted to Section 2.1; that does not introduce a universal on/off fade.

| ID | Finding and evidence | Recommended later fix | Verification idea |
| --- | --- | --- | --- |
| D26 | Physical `light.turn_on` always supplies `power=True`; `_default_transition_duration` therefore selects Fade On for ordinary already-on color/brightness updates. Groups correctly select Cross Fade from displayed on-state. With distinct On/Cross settings, physical/group behavior differs from the shared classification spec. [Physical classifier](../../../../ha-core/homeassistant/components/lifx/light.py#L224-L247), [group classifier](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L828-L846). | Classify physical off-to-on separately from already-on updates, retaining virtual-off recovery handling. Avoid changing group zero inheritance to compensate. | With On=1.5 s and Cross=0.9 s, an already-on color request uses Cross on both paths. With all Numbers zero this discrepancy alone does not explain different durations. |
| D27 | Ordinary physical color writes impose `max(duration, 0.25)`, including explicit `transition: 0` or `0.1`. Group raw color writes preserve zero/sub-250 ms values. The physical floor contradicts explicit-transition priority and explains short direct color smoothing with all Numbers zero. It does not apply to every power/virtual-recovery branch. [Physical writes](../../../../ha-core/homeassistant/components/lifx/light.py#L290-L322). | **Promoted into active scope by user request: Section 2.1.** Share `LIFX_MIN_COLOR_RAMP` from `const.py`, apply its 250 ms minimum to eligible implicit physical/group color writes, and honor explicit incoming durations exactly. Other On/Off/effect timing remains distinct. | Inspect actual outgoing durations for absent transition, explicit 0, 0.1, and 0.8; distinguish ordinary color writes, hidden preparation, actual power on/off, and virtual recovery. |

### A6. Deliberate differences and qualified/non-findings

- Common capability/effect **intersection** and overlapping Kelvin range differ
  from native groups' capability union and outer Kelvin range. Preserve the
  conservative policy unless a future design explicitly chooses per-member
  feature filtering/conversion. [Custom capabilities](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L306-L314),
  [Kelvin bounds](../../../../ha-core/homeassistant/components/lifx/parallel_group.py#L1244-L1270).
- Group fade defaults, synchronized dispatch, and bounded 15-second optimistic
  reporting are intentional custom features, not automatically defects.
- Physical LIFX-only membership, no nested Device Groups, and no native "All
  entities" mode are narrower configuration scope, not approved parity work.
- `light.turn_on(effect=..., brightness=..., transition=...)` discards the extra
  settings in both physical and group effect shortcuts. This is a shared
  limitation, not a group-specific regression; current integration docs describe
  this shortcut as starting an effect with defaults. Dedicated effect actions
  are the baseline for parameter fidelity research.
- Unlike pulse, group colorloop does read `power_on: false` and suppresses its
  physical power-on stage. Both physical and group paths may still write hidden
  colors to actually-off devices. Do not report this as a proven group-only
  actual-power-on bug. Virtual-off visibility is a distinct active requirement.
- Ordinary physical state commands do not automatically stop firmware effects;
  the software-task cancellation finding must not be generalized into a claim
  that native LIFX behavior stops every firmware effect automatically.
- Native `LightGroup` exposes `entity_id`, but does not set up automatic
  `group_entities`/`GenericGroup` support in this checkout. Avoid claiming that
  blindly adding generic group registration restores parity without side effects.

### A7. Recommended follow-up architecture, not approved scope

The researched long-term approach keeps synchronized transport and introduces
per-member command plans shared by physical/group control. Plans distinguish
requested components from required restore components, actual power stages,
logical power, eligible participants, and transition policy. Display aggregation
never fills unspecified request fields.

A shared per-physical-light effect ownership boundary captures prestate,
cancels superseded producers, restores on explicit stop/completion, and prevents
late cleanup from undoing newer work. Device-specific writes serve ordinary
partial updates, themes, virtual recovery, and temporary-effect restoration.
Prefer public library renderers and conversions; if necessary, add an appropriate
public library boundary instead of patching private internals or bypassing the
synchronized dispatcher.

The follow-up verification matrix should include heterogeneous on-member
colors/brightness, mixed actual power and availability, hue wraparound,
physical/group interruption of effects, effect-option fidelity, exact pattern
restoration, startup with retrying entries, configuration editing without
identity changes, and protocol-level direction/brightness assertions.

## Appendix B. Test Group transition-default investigation

Date: 2026-10-16. The user requested an independent agent to investigate why
Test Group commands look immediate with transition Numbers at zero, while direct
physical control seems to fade. The agent traced historical requirements,
physical/group command building, HA profile processing, Number restoration,
pinned library defaults, packet serialization, and ACK staging. The parent read
live HA settings, diagnostics, Adaptive Lighting configuration/state, and recent
history. No lights/settings were changed and no commits were made.

### Intended meaning of zero

The July Cross Fade spec and subsequent release spec/README agree on the
historical policy below. This investigation describes the code before the
user-requested Section 2.1 addition; its new ordinary color floor is the intended
implementation change, not a claim about current deployed behavior.

1. A transition supplied to the entity wins, including explicit zero.
2. A nonzero matching group Number wins.
3. Group Number zero inherits the corresponding physical member Number.
4. If no setting supplies a nonzero duration, the command is immediate.

On inherits On, Off inherits Off, and Cross inherits Cross. Zero Cross does not
inherit Fade On. There is no documented universal nonzero LIFX fade at the end
of the chain. Configuration Number zero and a service argument `transition: 0`
have different meanings: the latter bypasses integration defaults.

HA default light profiles can add a transition before entity dispatch. Such a
value arrives as an ordinary transition argument and shadows integration Number
defaults. A physical entity-specific profile is not inherited by the custom
group. Live light-profile CSV contents were not read by the available tools;
profile involvement in the user's observed operations is unproven.

### Confirmed source and network-free results

These are results for the audited implementation before Section 2.1 is built.

| Inputs, no incoming transition unless stated | Selected group duration |
| --- | --- |
| Group 0; matching member 0 | 0 ms |
| Group 0; matching member 0.8 s | 800 ms |
| Group 1.2 s; matching member 0.8 s | 1200 ms |
| Explicit `transition: 0`; nonzero Numbers | 0 ms |

The agent reproduced inheritance for On, Off, and Cross. Normal missing/zero/
nonzero Number restoration correctly synchronized both physical and group
runtime fields. A disabled Number that never loads cannot restore its runtime
value, but no normal stale-restoration failure was reproduced.

Pinned library 6.5.1 has zero default color/power duration. Seconds-to-milliseconds
conversion and representative group packet payloads match the library; no
factor-of-1000 error was found. ACK stage expansion preserves command durations.
An ACK or service return is not confirmation that a visual fade has finished.

The group immediately displays its optimistic target instead of interpolating
HA attributes through the fade. Therefore a card that updates immediately is
not evidence of zero-duration hardware behavior.

Physical ordinary color writes have the branch-specific 250 ms minimum in D27.
Actual-off wake prepares color immediately and uses the selected duration for
power-on; direct power-off uses its selected off duration. Those actual power
operations do not receive a universal hidden 250 ms fade. D26 separately explains
different On/Cross selection when configured values differ.

### Read-only live evidence

- `light.test_group` is the custom LIFX group, entry
  `01M512FVNX1CMHK6AK40G1Z140`; it is loaded, available, and all three workers
  report healthy.
- Its Fade On, Fade Off, and Cross Fade Numbers all read `0.0`.
- All nine matching physical Numbers for
  `light.living_room_tree_lamp_bottom`, `light.living_room_tree_lamp_middle`, and
  `light.living_room_tree_lamp_top` also read `0.0`.
- Adaptive Lighting **Living Tree Standalone** is currently On, color adaptation
  On, brightness adaptation Off, with no currently listed manual-control members.
  Its targets are the three physical entities, not `light.test_group`.
- That profile has interception enabled, multi-light interception enabled, and
  initial/regular/sleep transition settings of 90 s. **Living Tree Grouped** is
  currently Off and also lists physical targets rather than the custom group.
- Recent history shows the Adaptive Lighting switches changed repeatedly around
  Test Group activity. Current switch state must not be treated as proof of its
  state during each historical command. No post-interception service payload or
  live outgoing packet for an affected command has been captured.

### Adaptive Lighting correlation

The public Adaptive Lighting v1.32.0 source, matching the reported installed
version, intercepts eligible off-to-on physical `light.turn_on` calls. When no
transition was supplied it can merge `initial_transition`, here 90 s, with its
adaptive color request. Brightness adaptation being Off does not prevent this.

The original Test Group request bypasses that physical-target interception:
the virtual entity is not an AL target, exposes no recognized membership, and
dispatches raw member packets rather than member `light.turn_on` service calls.
Later periodic/state-triggered AL writes remain possible but are separate
operations, not inherited transition defaults for the original group request.

AL v1.32.0 excludes already-on lights from this interception. Thus its 90 s
initial transition does not automatically explain an already-on explicit color
change; the physical 250 ms floor is an independently established explanation
for short direct color smoothing. AL also does not inject a transition into
`light.turn_off`; its 90 s settings do not establish a 90 s off fade.

### Root-cause confidence and remaining discriminator

- **Established:** all current group/member Numbers zero resolve to an immediate
  group operation absent another supplied transition. The group fallback resolver
  is not broken in the audited source.
- **Established:** direct ordinary physical color changes can still take 250 ms;
  groups have no equivalent minimum.
- **Strong, source-supported explanation for direct power-on:** AL can inject
  90 s into direct physical calls while the original custom group call bypasses
  it. A particular historical call has not been event-confirmed.
- **Unresolved if observed:** a meaningfully fading direct actual power-off with
  zero off Number still needs the caller/profile transition or hardware behavior
  to be identified. AL does not provide that default.

The smallest later controlled experiment is to capture a direct physical bare
turn-on from actual off with AL enabled and disabled, compare it with the custom
group under the same zero Numbers, and inspect post-interception parameters or
wire duration. Restore AL/light state afterward. Separately compare already-on
color and direct off; do not infer hardware fade timing from optimistic cards.

These results did not show a broken group inheritance resolver. After reviewing
them, the user explicitly chose the new shared 250 ms ordinary color-ramp policy
in Section 2.1. Matching member inheritance remains intact, explicit transitions
retain priority, and actual/virtual On/Off timing does not gain a universal fade.

## References

- [HA Group behavior](https://www.home-assistant.io/integrations/group/#binary-sensor-light-and-switch-groups)
- [HA LIFX integration](https://www.home-assistant.io/integrations/lifx/)
- [LIFX set_state contract](https://www.home-assistant.io/actions/lifx.set_state/)
- [LIFX pulse contract](https://www.home-assistant.io/actions/lifx.effect_pulse/)
- [LIFX colorloop contract](https://www.home-assistant.io/actions/lifx.effect_colorloop/)
- [LIFX effects architecture](https://github.com/djelibeybi/lifx-async/blob/main/docs/architecture/effects-architecture.md)
- [LIFX LAN device commands](https://lan.developer.lifx.com/docs/changing-a-device)
- [LIFX waveforms](https://lan.developer.lifx.com/docs/waveforms)
- [HA config-flow reconfiguration](https://developers.home-assistant.io/docs/config_entries_config_flow_handler/#reconfigure)
- [Prior virtual-power implementation plan](../plans/2026-07-19-lifx-virtual-power-state.md)
- [Prior group-only ownership correction](../plans/2026-07-19-lifx-group-virtual-off-correction.md)
- [Current custom-worker integration notes](../../integrations/lifx-device-groups.md)
- [Cross Fade and endpoint design](2026-07-20-lifx-cross-fade-and-dynamic-group-endpoints-design.md#L58-L121)
- [Release/readme design: transition priority](2026-07-21-lifx-ultimate-release-and-readme-design.md#L61-L77)
- [Current feature README: zero and transition priority](../../../homeassistant/components/lifx/README_FEATURES.md#L43-L69)
- [Adaptive Lighting v1.32.0 interception eligibility](https://github.com/basnijholt/adaptive-lighting/blob/v1.32.0/custom_components/adaptive_lighting/switch.py#L1980-L2042)
- [Adaptive Lighting v1.32.0 transition injection](https://github.com/basnijholt/adaptive-lighting/blob/v1.32.0/custom_components/adaptive_lighting/switch.py#L2170-L2273)
- [Adaptive Lighting v1.32.0 service interception registration](https://github.com/basnijholt/adaptive-lighting/blob/v1.32.0/custom_components/adaptive_lighting/switch.py#L1895-L1912)

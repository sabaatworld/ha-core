# LIFX Cross Fade and Dynamic Device Group Endpoints

## Purpose

Add a third persistent transition default, **Cross Fade Time**, to physical
LIFX lights and Device Groups. It controls color-only changes that leave the
displayed light state on. Preserve the existing Fade On and Fade Off defaults
for displayed state transitions to on and off, including the group's virtual
off behavior.

Also make Device Groups follow a physical member's current endpoint after
that member reloads because discovery or DHCP changed its host. The physical
LIFX config entry remains the sole discovery owner; the group must not start
or subscribe to discovery itself.

## Scope

### In scope

- A restored `Cross Fade Time` number for physical LIFX lights and Device
  Groups, with the same range, unit, and naming conventions as the two
  existing fade number entities.
- Explicit classification of each ordinary light state request as an on,
  off, or cross transition.
- Physical and Device Group default-duration selection using that same
  classification.
- In-place replacement of a Device Group member coordinator after its
  physical config entry reloads, followed by a reconnect of only that
  member's parallel worker.
- Tests for transition selection, fallback persistence, endpoint handoff,
  and preemption safety.

### Out of scope

- Changes to `lifx_cloud`, discovery timing, DHCP handling, or the physical
  integration's existing host-update flow.
- New endpoint discovery, endpoint persistence, or a group-owned port
  setting.
- Changes to ACK retry counts, retry deadlines, staged-command behavior,
  virtual power semantics, or worker process lifetime except where required
  to make one-member endpoint replacement safe.
- Firmware effects whose service arguments already define their own timing.

## Existing facts and constraints

- Physical config entries already receive DHCP and integration-discovery
  updates. Their config flow writes the new `CONF_HOST` and reloads the
  physical entry.
- A normal `LIFXConnection` starts its UDP connection at the standard LIFX
  service port, 56700. It does not expose a separately maintained,
  device-specific port value.
- The parallel worker already sends `GetService` during worker preflight and
  reconnect. Its `StateService` response supplies the member's current
  service port before it targets later commands.
- A Device Group currently stores a fixed tuple of member coordinators and
  reacts to a physical member unload by stopping and scheduling a reload of
  the entire group. It cannot adopt the replacement coordinator in place.
- An explicit Home Assistant `transition` argument must take precedence over
  every configured fade default, including a value of zero.
- A Device Group command uses one displayed-state snapshot to build the
  group request. That remains its decision baseline.

## Cross Fade design

### Persistent configuration

Add `transition_cross_duration` beside `transition_on_duration` and
`transition_off_duration`.

- Entity name: `Cross Fade Time`.
- Range: 0 through 300 seconds, step 0.1 seconds, unit `s`.
- It is a `RestoreNumber` and restores after Home Assistant restart exactly
  like Fade On Time and Fade Off Time.
- A physical coordinator holds `transition_cross_duration` in memory.
- A Device Group runtime holds `transition_cross_duration` in memory.
- For a group, a non-zero group value takes precedence. A zero group value
  inherits the corresponding physical member value, just as the present
  group Fade On and Fade Off values do.

### Transition classification

The integration determines a transition kind before it resolves a default
duration. The classifier considers displayed state, requested target state,
and whether the request changes HSBK.

| Requested result | Transition kind | Default |
| --- | --- | --- |
| Displayed state becomes off, including Device Group virtual off | off | Fade Off Time |
| Displayed state changes from off to on, including restoration from virtual off | on | Fade On Time |
| Displayed state remains on and HSBK changes | cross | Cross Fade Time |
| No visible state or HSBK change | none | No duration-dependent packet |
| A color-only request while an actually powered-off light stays off | cross | Cross Fade Time |

An explicit `transition` bypasses the default table. A brightness-zero target
that is the group's virtual-off operation is classified as off even though it
uses a `SetColor` packet instead of `SetPower(False)`.

The physical light's virtual-off marker is part of displayed state. A command
that makes a virtually off light visible is consequently an on transition;
the equivalent Group operation makes the Group's displayed state visible and
is also an on transition.

For Device Groups, classify once using the captured `display_state`, not each
member's cached power or color. The selected kind is shared, while inherited
member defaults may intentionally produce distinct durations if the group
Cross Fade Time remains zero.

### Packet behavior

- Physical color changes retain the existing physical aiolifx transport and
  post-command refresh policy; only the selected duration changes.
- Group color-only work retains its existing raw `SetColor` packet shape.
- Group virtual off retains its brightness-zero packet and ACK-confirmed
  optimistic projection.
- A staged Group wakeup applies Fade On Time to the visible power-on stage.
  Its preparatory color stage remains immediate, as it is not the visible
  transition.
- A Group on-to-on color command uses Cross Fade Time on its visible color
  stage.
- Explicit transition durations are passed to every relevant stage exactly as
  they are today.

## Dynamic Device Group endpoint design

### Ownership and endpoint source

The physical config entry owns endpoint discovery and the active
`LIFXUpdateCoordinator`. The Device Group stores member entry IDs and binds
to the coordinator currently found in each loaded member entry.

The coordinator's current `device.ip_addr` is the Group's host source. The
worker's preflight remains the port source: it connects to UDP 56700, sends
`GetService`, and reconnects to the returned member service port. This avoids
duplicating or persisting endpoint state that the physical connection does not
own.

### Member replacement lifecycle

1. A physical entry updates its host through its existing discovery/DHCP
   flow and begins its normal reload.
2. The Group observes that member entry's unload, marks that index not ready,
   and refuses commands requiring the stale coordinator or endpoint.
3. When the physical entry reaches `LOADED` with a ready replacement
   coordinator, the Group atomically replaces only that member binding:
   remove the old coordinator listener, install the new listener, and update
   that member's cached readiness and host.
4. The Group asks only the matching live parallel worker to reconnect to the
   replacement coordinator's `device.ip_addr`.
5. The worker repeats `GetService`, learns the active service port, and
   resumes use without killing or recreating unrelated workers.
6. A failed replacement binding or reconnect leaves that member unavailable.
   A later physical coordinator update may retry the handoff; no command is
   sent to the old endpoint.

### Concurrency and failure rules

- Coordinator replacement, worker reconnect, dispatch preparation, and
  recovery use one member-binding synchronization boundary so a command never
  snapshots a stale coordinator while that member is being replaced.
- A new state request still supersedes an outstanding group ACK wait. Endpoint
  handoff must not turn this into a FIFO queue.
- A reconnect event is associated with its member-binding generation. Late
  acknowledgements, reconnect completions, or old-coordinator listener events
  are ignored when their generation is no longer current.
- A member unload is a dependency availability condition, not a worker crash.
  Do not stop the whole group or discard healthy workers merely because one
  physical member receives a new IP.
- Group availability continues to require every configured physical member to
  be loaded and ready.

## Validation requirements

- Verify physical and Group Cross Fade number entity creation, naming,
  persistence, and zero fallback.
- Verify explicit `transition` overrides all three defaults.
- Verify physical and Group on, off, cross, virtual-off-to-on, and color-only
  while actually off classifications.
- Verify Group duration selection is based on one displayed-state snapshot.
- Simulate a physical config-entry replacement with a new coordinator and
  host; verify only the affected worker receives reconnect, uses preflight,
  and the Group remains loaded once all members are ready.
- Exercise replacement during a standalone ACK wait and a staged ACK wait;
  verify no stale event corrupts the newest dispatch and unaffected workers
  remain alive.
- Run focused LIFX tests, the entire LIFX test suite, and `prek` on the Home
  Assistant host. Report pre-existing failures separately.

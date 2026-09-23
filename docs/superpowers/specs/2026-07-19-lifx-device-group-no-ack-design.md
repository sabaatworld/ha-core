# LIFX Device Group No-ACK Design

**Status:** Approved 2026-07-19

**Problem:** The current Device Group transport waits for LIFX acknowledgements while holding its dispatcher lock. A missed acknowledgement can delay later commands, workers can exit on malformed packets, and a dead worker has no replacement path. The group must follow only its physical members' availability.

## Decisions

- Device Group availability is the conjunction of the configured physical entries being loaded with their original `LIFXUpdateCoordinator` and `last_update_success == True`. Transport faults, command failures, worker restarts, and reconnect failures never alter it.
- Initial setup raises `ConfigEntryNotReady` until all physical members are ready. Home Assistant retains ownership of retries. A physical entry unload/reload is the only normal reason to stop and rebuild the complete group runtime.
- The group owns one spawned warm worker process and connected UDP socket per configured member. A confirmed dead process, EOF, or broken control pipe replaces only that worker slot. A living worker is never killed because a UDP send or re-probe failed.
- A living worker reports a transport error for the affected command, resets/reconnects its own socket, and remains available for future work. Physical availability recovery and a member IP change also request that worker reconnect/re-probe. These operations are best-effort and do not change group availability.
- Packet builders set `ack_required = 0`. Workers report readiness and send completion, but do not wait for or consume acknowledgement packets after dispatch. The direct worker state-query API, ACK callbacks, ACK timeouts, and Device Group-triggered physical coordinator refreshes are removed.
- Workers retain the shared absolute-time dispatch gate. A command that has reached that gate is sent; newer commands replace only older work that has not crossed it. This is latest-unsent-command-wins, because UDP packets already sent cannot safely be recalled.
- Operations that require two packets retain packet ordering in the same worker immediately after their common gate. They do not use a second ACK-gated phase.
- Datagram parsing is centralized in a receive loop. Short frames, declared-length mismatches, `struct.error`, unexpected payload sizes, unmatched responses, and unknown packet types are discarded (with optional debug accounting) rather than escaping the worker loop.
- The Device Group shows one optimistic aggregate state without mutating physical coordinator caches. Its expiry is `PHYSICAL_LIGHT_POLL_INTERVAL * 1.5`; with the current 10-second physical interval this is 15 seconds. A later command replaces the earlier projection. No Device Group command schedules an immediate physical poll; the ordinary physical coordinator polling cadence remains authoritative.

## Boundaries

`parallel.py` owns worker process creation, socket recovery, packet building/parsing, gate dispatch, dead-worker replacement, and latest-unsent dispatch. `parallel_group.py` owns physical dependency availability, group reinitialization on entry replacement, optimistic display state, and translating group commands into worker packets. Physical `LIFXUpdateCoordinator` objects remain the source of actual state and availability.

## Verification

- Replace ACK/mock-only tests with a loopback UDP responder and real spawned workers/gates.
- Prove no-ACK headers and dispatch without replies; malformed datagrams do not kill a worker; a confirmed-dead worker is replaced in place; a living transport error keeps the worker and group availability intact; and recovery reconnects it.
- Prove latest-unsent-command-wins under a deliberately delayed preparation, a single optimistic group state expires at the shared 15-second-derived interval, and no group command schedules a coordinator refresh.
- Prove physical unavailable/recovered behavior, physical member unload/reload reinitialization, and Device Group config-flow creation.
- Restore the current three physical-light transition test regressions and finish with the full Lifx suite and formatting checks on the HA host.

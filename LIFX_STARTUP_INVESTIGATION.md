# LIFX Integration — Startup & Group Investigation

Date range: 2026-08-30 → 2026-08-31. This file is a working record of the
investigation into (a) reverting the LIFX "virtual power off" feature and
(b) why the LIFX custom-component fork appears to make Home Assistant start
much slower than the built-in integration.

---

## TL;DR / conclusions

1. **Virtual power off is reverted** on branch `lifx-revert-virtual-power-off`.
   Group turn-off now sends a real `SetPower(False)`; group turn-on-from-off
   uses a 3-stage `prime(b=0) → power → apply` sequence with a neutral 4000K
   prime, best-effort prime (members that miss the prime ACK still power on),
   and a 5s ACK wait.

2. **The slow startup is NOT caused by the Device Group feature.** Five
   independent code-analysis agents and live log inspection all converge on the
   same root cause: the physical bulbs on `192.168.15.x` intermittently drop
   `get_color` responses, and each drop costs one 18s timeout
   (`MESSAGE_TIMEOUT = 18`). The group adds only ~2–5s (worker-process spawn).

3. **A/B test (fork vs built-in) re-run correctly on 2026-08-31:** custom
   component (A) = 263.7s, built-in (B) = 273.6s to full startup. No meaningful
   difference — lifx is not the startup bottleneck. See §6.

4. **Proposed next fix (not yet implemented):** make the physical light's first
   refresh non-blocking (background the `get_color`, keep the serial/DHCP check
   synchronous via a fast `get_version`). See §7.

---

## 1. Environment

- HA host: `root@192.168.8.28`, Home Assistant `2026.8.3` (qemux86-64 VM).
- The fork overrides the built-in `lifx` via `/config/custom_components/lifx`
  (same `lifx` domain). Deploy is
  `script/sync_custom_component.sh 192.168.8.28 lifx` (excludes `strings.json`,
  `__pycache__`, `.pytest_cache`).
- Bulbs: `Tree Lamp` (`.118/.119/.150`) and `Living Room Tree Lamp`
  (`.155/.169/.176`) — 6 physical bulbs + at least one Device Group.

### Key constants (`homeassistant/components/lifx/const.py`)

| Constant | Value | Meaning |
|---|---|---|
| `MESSAGE_TIMEOUT` | 18 | per-request aiolifx timeout (s) |
| `MESSAGE_RETRIES` | 1 | retry count |
| `MAX_UPDATE_TIME` | 90 | coordinator update ceiling (s) |
| `PHYSICAL_LIGHT_POLL_INTERVAL` | 10 | physical `get_color` poll (s) |
| `DEVICE_GROUP_KEEPALIVE_INTERVAL` | 120 | group EchoRequest keepalive (s) |
| `DEVICE_GROUP_MEMBER_RECONNECT_INTERVAL` | 60 | member reconnect (s) |

---

## 2. Work completed: virtual power off revert

The "virtual power off" feature (setting brightness to 0 and treating
brightness 0 as off) was removed, and the group turn-on was reworked.

### Commits / branches

- `lifx-revert-virtual-power-off` — commit `f27dc95993e`
  "revert(lifx): remove virtual power off and fix group turn-on"
- `lifx-startup-logging` — commit `ded74bfbf26`
  "feat(lifx): log slow first refresh and device updates" (on top of `dev`)

### Behavior changes

- **Turn off (physical + group):** real `SetPower(False)` (group previously sent
  a brightness-0 `color` packet).
- **Group turn-on-from-off:** 3-stage `color(0,0,0,4000,0) → power(True,0) →
  color(target)`. The prime uses a fixed 4000K neutral white at brightness 0 so
  every member powers on dark simultaneously.
- **Best-effort prime:** a member that misses the prime ACK still advances to
  power-on/apply (was previously excluded via `partial_first_stage`).
- **ACK wait raised 3s → 5s** per stage (worker `_wait_for_ack` and dispatcher
  `ack_timeout`); stage deadline 15s → 25s.
- **Debug logging:** per-stage `command`/`payload` and per-attempt
  `ack result` (acked/unresolved/elapsed).
- Removed virtual-off state: `virtual_off`/`resume_hsbk`, `actual_power_on`,
  `display_color`, `async_record_virtual_off/on`, `async_clear_virtual_off`,
  `async_reconcile_virtual_power`, `LIFXVirtualPowerStoredData`, `RestoreEntity`,
  `pad_before`.

### Tests

`tests/components/lifx/test_parallel.py` + `test_parallel_group.py`: 50 passing.
`test_light.py`: 26 passing (2 pre-existing failures —
`test_transition_duration_numbers`, `test_transition_duration_legacy_multizone` —
and ~55 pre-existing `check_translations` teardown errors, all unrelated).

---

## 3. The slow-startup question

User observation: startup is **significantly** slower with the fork (custom
component + group) than with the built-in `lifx`.

### The blocking point (upstream, NOT the fork)

`homeassistant/components/lifx/__init__.py` `async_setup_entry` (line ~233):

```python
await coordinator.async_config_entry_first_refresh()  # get_color
```

`git blame` shows this line is upstream, written by J. Nick Koston in commit
`8c41d0d3d712` (2022-08-29). It predates the fork. Each physical bulb blocks on
its `get_color` first refresh. `MESSAGE_TIMEOUT = 18` and 5 retry attempts mean
a stalled bulb blocks its entry for up to 90s.

### Live log evidence

`LIFX update … took 18.0/18.1/18.2/18.3/18.4 seconds` warnings fire frequently
(roughly every 1–10 minutes) across random bulbs — e.g. `.119`, `.169`, `.176`,
`.118`, `.155`, `.150`. These are **ongoing polls** (and `first refresh`),
each ~18s = exactly one `MESSAGE_TIMEOUT`, then a retry succeeds. This is the
signature of a single dropped/lost `get_color` response, not a slow bulb.

The `LIFX first refresh … took …` warning only fired once in the observed
window (`.169` at 08:52:18, 18.0s) — startup is usually fast; the slowness is
intermittent and hits whichever bulb is mid-flake at boot.

---

## 4. Five-agent parallel analysis (all read-only)

Dispatched 5 `general-purpose` agents. Every one concluded the group does **not**
add significant startup cost:

| # | Aspect | Verdict |
|---|---|---|
| 1 | UDP traffic / contention | Group sends **0 packets at startup**; ~1 EchoRequest/bulb/**120s** steady-state; dwarfed by the bulb's own 10s `get_color`. |
| 2 | Group setup blocking | `async_start()` only spawns processes in an **executor thread** (≤5s timeout), zero network I/O; `_ensure_members_ready` is a pure flag read. |
| 3 | Physical refresh amplification | Group's only `async_set_updated_data(None)` **suppresses** pending refreshes — never increases polling. |
| 4 | Config entry ordering / retry | Physical + group entries set up **concurrently** (`asyncio.gather`); group `ConfigEntryNotReady` retries are **deferred until after `EVENT_HOMEASSISTANT_STARTED`**. |
| 5 | Custom-override overhead | **Negligible** — ~28ms pyc recompile; requirement checks read `.dist-info` metadata (never import numpy); built-in vs custom load path is identical. |

### The only real group cost

The worker-process spawn: `runtime.async_start()` spawns one Python subprocess
per member (`mp.get_context("spawn")`), each re-importing `homeassistant.core`,
bounded by a 5s `_collect("STARTED", 5.0)` timeout, off the event loop. So the
group adds ~2–5s wall-clock, not "much longer."

---

## 5. Root cause (conclusion)

**Intermittent UDP packet loss on the `192.168.15.x` segment.** The bulbs drop
`get_color` responses; each drop costs one 18s timeout (`MESSAGE_TIMEOUT`), then
a retry succeeds. When it happens to several bulbs at boot, their blocking
`async_config_entry_first_refresh()` calls stack up and make HA start slowly.
This is identical for the built-in integration and the fork — the group neither
causes nor amplifies it.

---

## 6. The A/B test

Attempted comparison of restart→ready time, fork (A) vs built-in (B).

- **A (fork):** ~4:33 total restart (all integrations).
- **B (built-in):** done by `mv /config/custom_components/lifx
  /config/custom_components/lifx.disabled`, then restart.

### The flaw

Renaming the folder to `lifx.disabled` **but keeping it inside
`custom_components/`** made HA still treat it as the `lifx` custom component
(its `manifest.json` still says `domain: lifx`) and then fail to import it:

```
ERROR [homeassistant.setup] Setup failed for custom integration 'lifx':
       Unable to import component: No module named 'custom_components.lifx'
ERROR [custom_components.lifx.disabled] Error occurred loading flow for
       integration lifx: No module named 'custom_components.lifx'  (×6)
```

So B was **not** "built-in lifx" — it was "lifx integration completely broken,"
and its ~4:28 "restart" measured HA booting *without* a working lifx. The A/B
comparison is therefore invalid, and any "A ≈ B" conclusion drawn from it is
withdrawn.

### Re-run (valid, 2026-08-31)

B was redone correctly: `mv /config/custom_components/lifx /config/lifx.bak`
(out of `custom_components`), so HA genuinely fell back to built-in. Timing =
wall-clock from `ha core restart` (which blocks until the core reports ready)
to HTTP 200 on `http://192.168.8.28:8123/` (polled from the dev machine — the
container port is *not* published on the host loopback, so `localhost:8123`
from the host never returns 200 and must be avoided).

| Run | Trigger | Ready (200) | Duration |
|---|---|---|---|
| A (custom component) | 09:54:50 | 09:59:14 | **263.7s** |
| B (built-in) | 10:00:12 | 10:04:46 | **273.6s** |

**Conclusion:** B is ~10s *slower* than A — well within run-to-run noise. There
is no evidence the custom component (or the Device Group) adds meaningful
startup time. The dominant cost is HACS (`HacsBase.startup_tasks()` → 60s
GitHub timeouts) plus ~40 other integrations and the intermittent lifx 18s
`get_color` drops. Single run each, so the absolute numbers are noisy; the
direction (lifx not the bottleneck) is robust.

After the test, the custom component was deleted from the host
(`rm -rf /config/lifx.bak`), leaving HA on the built-in `lifx`.

---

## 7. Proposed fix: non-blocking physical startup

Background from a brainstorm (not yet implemented). The physical
`async_setup_entry` blocks on the slow `get_color`. Split the first refresh:

**Synchronous (fast, keeps boot non-blocking):**
1. `connection.async_setup()` — handshake.
2. Populate device info (`get_hostfirmware`/`get_version`/`get_group`/`get_label`
   — fast queries, unlike `get_color`).
3. Serial/DHCP check (`serial != entry.unique_id → ConfigEntryNotReady`).

**Background (slow, non-blocking):**
4. `entry.async_create_background_task(hass, coordinator.async_config_entry_first_refresh(), name="lifx-first-refresh")`

Entities are `CoordinatorEntity`s (`available == coordinator.last_update_success`),
so they appear "unavailable" for a few seconds then flip available when the
background refresh lands. This is the standard HA pattern (cf. BSB-LAN commit
`29c4629cd0f`, and arcam_fmj/blue_current/ecovacs/flux_led/miele).

The group falls in line for free: it already raises `ConfigEntryNotReady` until
members report `last_update_success`, and its retries are deferred until after
startup.

---

## 8. Current state

- Git branch: `lifx-startup-logging` (HEAD `ded74bfbf26`).
- Branches:
  - `dev` — original, clean.
  - `lifx-revert-virtual-power-off` (`f27dc95993e`) — the revert + turn-on fixes.
  - `lifx-startup-logging` (`ded74bfbf26`) — `dev` + slow-startup WARNING logs.
- HA host: custom component **deleted** (no `lifx` under `/config` or
  `/config/custom_components`) — HA is running the **built-in** `lifx`.

### WARNING logs added (on `lifx-startup-logging`)

- `__init__.py`: `LIFX first refresh of {host} took {:.1f}s` (>10s)
- `coordinator.py`: `LIFX update of {name} took {:.1f}s` (>10s)

---

## 9. Open items / next steps

1. ~~Re-run the A/B correctly~~ — done: A=263.7s, B=273.6s (§6). lifx is not the
   startup bottleneck.
2. Implement the non-blocking physical startup (§7) — spec/plan not yet written.
3. Investigate the `192.168.15.x` network (AP/VLAN, single vs dual interface,
   the bulbs' Wi-Fi) — the true source of the 18s `get_color` drops.

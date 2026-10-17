"""Test LIFX Device Group behavior."""

import asyncio
from collections.abc import Callable
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.components.lifx.const import (
    CONF_GROUP_ID,
    CONF_MEMBERS,
    DATA_LIFX_MANAGER,
    DOMAIN,
)
from homeassistant.components.lifx.coordinator import LIFXUpdateCoordinator
from homeassistant.components.lifx.manager import SERVICE_EFFECT_MOVE, LIFXManager
from homeassistant.components.lifx.parallel import (
    ParallelCommand,
    ParallelDispatchOutcome,
    ParallelDispatchOwner,
    ParallelDispatchResult,
    ParallelTransport,
)
from homeassistant.components.lifx.parallel_group import (
    LIFXParallelGroupRuntime,
    _hsbk_to_raw16,
    _MemberCommandState,
    _members_are_ready,
    _raw16_to_hsbk,
    async_setup_parallel_group_entry,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .helpers import create_mock_light

from tests.common import MockConfigEntry


def _member(
    hass: HomeAssistant, ip_address: str, *, last_update_success: bool = True
) -> LIFXUpdateCoordinator:
    """Keep physical state and recovery authority real; mock only the device."""
    if DATA_LIFX_MANAGER not in hass.data:
        hass.data[DATA_LIFX_MANAGER] = LIFXManager(hass)
    serial = f"aabbccddee{int(ip_address.rsplit('.', 1)[1]):02x}"
    device = create_mock_light(ip=ip_address, serial=serial)
    device.port = 56700
    coordinator = LIFXUpdateCoordinator(hass, MockConfigEntry(domain=DOMAIN), device)
    coordinator.async_set_updated_data(device.state)
    coordinator.last_update_success = last_update_success
    return coordinator


def _transport(host: str) -> ParallelTransport:
    """Return the resolved physical transport used by a mock member."""
    target = bytes.fromhex(f"aabbccddee{int(host.rsplit('.', 1)[1]):02x}")
    return ParallelTransport(host, 56700, target.ljust(8, b"\0"))


@pytest.mark.parametrize(
    ("group_default", "member_default", "explicit", "expected"),
    [
        pytest.param(0, 0, None, 250, id="implicit-zero"),
        pytest.param(0.1, 0, None, 250, id="implicit-short"),
        pytest.param(0.8, 0, None, 800, id="implicit-long"),
        pytest.param(0, 0.8, None, 800, id="member-inherited"),
        pytest.param(0.8, 0.8, 0.0, 0, id="explicit-zero"),
        pytest.param(0.8, 0.8, 0.1, 100, id="explicit-short"),
        pytest.param(0.8, 0.8, 1.5, 1500, id="explicit-long"),
    ],
)
async def test_group_ordinary_color_duration(
    hass: HomeAssistant,
    group_default: float,
    member_default: float,
    explicit: float | None,
    expected: int,
) -> None:
    """Wire durations inherit first, then floor only implicit ordinary writes."""
    runtime, members = _runtime(hass)
    runtime.transition_cross_duration = group_default
    for member in members:
        member.data.power = 65535
        member.transition_cross_duration = member_default
    kwargs = {} if explicit is None else {"transition": explicit}
    await runtime.async_set_state(brightness=128, **kwargs)
    assert all(
        command.payload[-1] == expected
        for command in runtime.parallel.async_dispatch.await_args.args[0]
    )


async def test_group_hidden_color_and_power_stage_timing(hass: HomeAssistant) -> None:
    """Hidden color is immediate and the final power stage keeps its short fade."""
    runtime, members = _runtime(hass)
    runtime.transition_on_duration = 0.1
    await runtime.async_set_state(power=True, brightness=128)
    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.payload[-1] == 0 for command in commands)
    assert all(command.second.payload == (True, 100) for command in commands)
    for member in members:
        member.data.power = 65535
    runtime.transition_off_duration = 0.1
    await runtime.async_set_state(power=False)
    assert all(
        command.payload[-1] == 100
        for command in runtime.parallel.async_dispatch.await_args.args[0]
    )


def _runtime(
    hass: HomeAssistant,
    *,
    group_policy: bool = True,
) -> tuple[LIFXParallelGroupRuntime, tuple[LIFXUpdateCoordinator, ...]]:
    """Create a Device Group runtime without starting its worker processes."""
    members = (_member(hass, "192.0.2.1"), _member(hass, "192.0.2.2"))
    member_entries = tuple(
        MagicMock(state=ConfigEntryState.LOADED, runtime_data=member)
        for member in members
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_GROUP_ID: "test-device-group"},
        options={"virtual_power_off": group_policy},
    )
    runtime = LIFXParallelGroupRuntime(hass, entry, member_entries, members)
    runtime.parallel = MagicMock()
    runtime.parallel.async_dispatch = AsyncMock(
        return_value=ParallelDispatchResult(ParallelDispatchOutcome.COMPLETED, 1)
    )
    runtime.parallel.async_cancel_health = AsyncMock()
    runtime.parallel.async_cancel_members = AsyncMock()
    runtime.parallel.async_request_reconnect = AsyncMock(
        return_value=ParallelDispatchResult(ParallelDispatchOutcome.COMPLETED, 1)
    )
    return runtime, members


async def test_group_actual_off_policy_ignores_member_policy(
    hass: HomeAssistant,
) -> None:
    """A group's actual-off request must bypass default-On member policies."""
    runtime, members = _runtime(hass, group_policy=False)
    for member in members:
        member.data.power = 65535
    await runtime.async_set_state(power=False)
    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(
        command.kind == "power" and command.payload == (False, 0)
        for command in commands
    )
    assert all(not member.virtual_off for member in members)


async def test_group_prepares_virtual_members_without_visible_packets(
    hass: HomeAssistant,
) -> None:
    """Omitting power changes next-on values without illuminating dark members."""
    runtime, members = _runtime(hass)
    for member in members:
        member.data.power = 65535
    await runtime.async_set_state(power=False)
    for member in members:
        member.data.color = member.data.color.replace(brightness=0)
    runtime.parallel.async_dispatch.reset_mock()
    await runtime.async_set_state(brightness=128)
    runtime.parallel.async_dispatch.assert_not_awaited()
    assert all(member.virtual_off for member in members)
    assert all(member.resume_hsbk.brightness == 128 / 255 for member in members)


async def test_group_partial_completion_commits_only_success(
    hass: HomeAssistant,
) -> None:
    """COMPLETED may still contain members whose preparation failed."""
    runtime, members = _runtime(hass)
    for member in members:
        member.data.power = 65535
    runtime.parallel.async_dispatch.return_value = ParallelDispatchResult(
        ParallelDispatchOutcome.COMPLETED, 1, frozenset({1})
    )
    await runtime.async_set_state(power=False)
    assert members[0].virtual_off
    assert not members[1].virtual_off
    assert members[1].virtual_power_record.pending.delivery == "uncertain"


async def test_group_recovery_preserves_individual_saved_brightness(
    hass: HomeAssistant,
) -> None:
    """Bare group resume must not restore the average into every dark bulb."""
    runtime, members = _runtime(hass)
    members[0].data.color = members[0].data.color.replace(brightness=0.2)
    members[1].data.color = members[1].data.color.replace(brightness=0.8)
    for member in members:
        member.data.power = 65535
    await runtime.async_set_state(power=False)
    await runtime.async_set_state(power=True)
    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert commands[0].payload[2] == 13107
    assert commands[1].payload[2] == 52428
    assert runtime.display_state.is_on


def _exclude_preparing_member(
    _hass: HomeAssistant,
    _runtime: LIFXParallelGroupRuntime,
    members: tuple[LIFXUpdateCoordinator, ...],
) -> None:
    members[1].last_update_success = False


def _replace_preparing_member(
    hass: HomeAssistant,
    runtime: LIFXParallelGroupRuntime,
    _members: tuple[LIFXUpdateCoordinator, ...],
) -> None:
    runtime.members[1] = _member(hass, "192.0.2.99")
    runtime.member_entries[1].runtime_data = runtime.members[1]


@pytest.mark.parametrize(
    "change", [_exclude_preparing_member, _replace_preparing_member]
)
@pytest.mark.parametrize(
    "gate_save", [1, 2], ids=["candidate-save", "first-completion-save"]
)
async def test_packet_free_prepare_rechecks_participating_binding(
    hass: HomeAssistant,
    change: Callable[
        [HomeAssistant, LIFXParallelGroupRuntime, tuple[LIFXUpdateCoordinator, ...]],
        None,
    ],
    gate_save: int,
) -> None:
    """A skipped old binding must not acquire the prepared next-on image."""
    runtime, members = _runtime(hass)
    for member in members:
        member.data.power = 65535
    await runtime.async_set_state(power=False)
    entered = asyncio.Event()
    release = asyncio.Event()
    real_save = Store.async_save
    calls = 0

    async def save(
        storage: Store[dict[str, object]], payload: dict[str, object]
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == gate_save:
            entered.set()
            await release.wait()
        await real_save(storage, payload)

    with patch(
        "homeassistant.components.lifx.virtual_power.Store.async_save",
        side_effect=save,
        autospec=True,
    ):
        prepared = asyncio.create_task(runtime.async_set_state(brightness=128))
        await entered.wait()
        change(hass, runtime, members)
        release.set()
        await prepared
    record = runtime._virtual_power_store.async_get_record(
        members[1].virtual_power_serial
    )
    assert record.image.color.brightness == 1
    assert record.virtual_off
    assert record.pending is None


async def test_cancelled_group_dispatch_releases_polling_guard(
    hass: HomeAssistant,
) -> None:
    """A cancelled caller cannot suppress fresh reconciliation indefinitely."""
    runtime, members = _runtime(hass)
    for member in members:
        member.data.power = 65535
    entered = asyncio.Event()
    never = asyncio.Event()

    async def dispatch(
        _commands: tuple[ParallelCommand | None, ...],
        *,
        owner: ParallelDispatchOwner | None = None,
    ) -> ParallelDispatchResult:
        entered.set()
        await never.wait()
        return ParallelDispatchResult(ParallelDispatchOutcome.COMPLETED, 1)

    runtime.parallel.async_dispatch.side_effect = dispatch
    task = asyncio.create_task(runtime.async_set_state(power=False))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    members[0].data.color = members[0].data.color.replace(brightness=0)
    await runtime._virtual_power_store.async_reconcile(
        members[0].virtual_power_serial,
        members[0].data,
        now=dt_util.utcnow() + timedelta(seconds=1),
    )
    assert members[0].virtual_off
    assert members[0].virtual_power_record.pending is None


async def test_cancelled_group_save_releases_reservation(hass: HomeAssistant) -> None:
    """Cancelling before dispatch leaves no active or issued destructive intent."""
    runtime, members = _runtime(hass)
    entered = asyncio.Event()
    release = asyncio.Event()
    real_save = Store.async_save

    async def save(
        storage: Store[dict[str, object]], payload: dict[str, object]
    ) -> None:
        entered.set()
        await release.wait()
        await real_save(storage, payload)

    with patch(
        "homeassistant.components.lifx.virtual_power.Store.async_save",
        side_effect=save,
        autospec=True,
    ):
        task = asyncio.create_task(runtime.async_set_state(power=False))
        await entered.wait()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert all(member.virtual_power_record.pending is None for member in members)
    runtime.parallel.async_dispatch.assert_not_awaited()


def test_members_are_not_ready_when_a_loaded_member_is_unavailable() -> None:
    """A Device Group must wait for an unavailable physical coordinator."""
    coordinator = MagicMock(last_update_success=False)
    entry = MagicMock(
        state=ConfigEntryState.LOADED,
        runtime_data=coordinator,
    )

    assert not _members_are_ready([entry], [coordinator])


@pytest.mark.parametrize(
    ("state", "last_update_success"),
    [
        pytest.param(None, True, id="missing-runtime"),
        pytest.param(ConfigEntryState.SETUP_RETRY, True, id="member-retrying"),
        pytest.param(ConfigEntryState.LOADED, False, id="member-unavailable"),
    ],
)
async def test_setup_retries_until_every_physical_member_is_ready(
    hass: HomeAssistant,
    state: ConfigEntryState | None,
    last_update_success: bool,
) -> None:
    """A Device Group defers setup before it starts any worker process."""
    entry = MagicMock(data={"members": ("physical-entry",)})
    member_entry = (
        None
        if state is None
        else MagicMock(
            state=state,
            runtime_data=_member(
                hass, "192.0.2.1", last_update_success=last_update_success
            ),
        )
    )
    with (
        patch.object(
            hass.config_entries,
            "async_get_entry",
            return_value=member_entry,
        ),
        patch(
            "homeassistant.components.lifx.parallel_group.LIFXParallelGroupRuntime.async_start",
            new_callable=AsyncMock,
        ) as async_start,
        pytest.raises(Exception, match="not loaded"),
    ):
        await async_setup_parallel_group_entry(hass, entry)

    async_start.assert_not_awaited()


async def test_group_availability_requires_only_one_healthy_member(
    hass: HomeAssistant,
) -> None:
    """One unavailable member must not make a healthy Device Group unavailable."""
    runtime, members = _runtime(hass)
    runtime.parallel.available = False

    assert runtime.available

    members[1].last_update_success = False
    assert runtime.available

    members[1].last_update_success = True
    assert runtime.available

    runtime._keepalive_healthy[0] = False
    assert runtime.available


async def test_group_command_excludes_an_unavailable_member(
    hass: HomeAssistant,
) -> None:
    """Group commands must not prepare packets for an unavailable member."""
    runtime, members = _runtime(hass)
    members[1].last_update_success = False

    await runtime.async_set_state(power=True, brightness=255)

    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert commands[0] is not None
    assert commands[1] is None


async def test_first_idle_echo_failure_reconnects_but_keeps_group_available(
    hass: HomeAssistant,
) -> None:
    """One missed Echo repairs the worker without changing Group availability."""
    runtime, _members = _runtime(hass)
    runtime.parallel.async_keepalive = AsyncMock(
        return_value=ParallelDispatchResult(
            ParallelDispatchOutcome.COMPLETED, 1, frozenset({1})
        )
    )
    runtime.parallel.async_request_reconnect = AsyncMock(
        return_value=ParallelDispatchResult(ParallelDispatchOutcome.COMPLETED, 1)
    )

    await runtime._async_run_keepalive(runtime._keepalive_generation)

    assert runtime.available
    assert runtime._keepalive_failures == [0, 0]
    runtime.parallel.async_request_reconnect.assert_awaited_once_with(
        1, _transport("192.0.2.2")
    )


async def test_second_idle_echo_failure_excludes_only_that_member(
    hass: HomeAssistant,
) -> None:
    """Two missed Echoes exclude one member but retain the healthy group."""
    runtime, _members = _runtime(hass)
    runtime.parallel.async_keepalive = AsyncMock(
        return_value=ParallelDispatchResult(
            ParallelDispatchOutcome.COMPLETED, 1, frozenset({0})
        )
    )
    runtime.parallel.async_request_reconnect = AsyncMock(
        return_value=ParallelDispatchResult(ParallelDispatchOutcome.FAILED, 1)
    )

    await runtime._async_run_keepalive(runtime._keepalive_generation)
    await runtime._async_run_keepalive(runtime._keepalive_generation)

    assert runtime.available
    assert runtime._keepalive_healthy == [False, True]


async def test_member_reload_replaces_only_its_coordinator_and_reconnects_worker(
    hass: HomeAssistant,
) -> None:
    """A physical reload replaces only its Device Group member binding."""
    runtime, _members = _runtime(hass)
    replacement = _member(hass, "192.0.2.99")
    runtime.parallel.async_request_reconnect = AsyncMock(
        return_value=ParallelDispatchResult(ParallelDispatchOutcome.COMPLETED, 1)
    )
    runtime.member_entries[0].runtime_data = replacement
    runtime.member_entries[0].state = ConfigEntryState.LOADED

    runtime.async_member_entry_state_changed(0)
    await hass.async_block_till_done()

    assert runtime.members[0] is replacement
    runtime.parallel.async_request_reconnect.assert_awaited_once_with(
        0, _transport("192.0.2.99")
    )


async def test_member_reload_stays_unavailable_until_worker_reconnects(
    hass: HomeAssistant,
) -> None:
    """A Group must not dispatch to the previous endpoint during handoff."""
    runtime, _members = _runtime(hass)
    replacement = _member(hass, "192.0.2.99")
    reconnect_started = asyncio.Event()
    release_reconnect = asyncio.Event()

    async def _async_request_reconnect(
        index: int, transport: ParallelTransport
    ) -> ParallelDispatchResult:
        assert (index, transport) == (0, _transport("192.0.2.99"))
        reconnect_started.set()
        await release_reconnect.wait()
        return ParallelDispatchResult(ParallelDispatchOutcome.COMPLETED, 1)

    runtime.parallel.async_request_reconnect = AsyncMock(
        side_effect=_async_request_reconnect
    )
    runtime.member_entries[0].runtime_data = replacement
    runtime.member_entries[0].state = ConfigEntryState.LOADED

    runtime.async_member_entry_state_changed(0)
    await reconnect_started.wait()

    assert runtime.available

    release_reconnect.set()
    await hass.async_block_till_done()

    assert runtime.available


async def test_member_unload_makes_group_unavailable_without_stopping_workers(
    hass: HomeAssistant,
) -> None:
    """A physical reload must not stop unaffected Group workers."""
    runtime, _members = _runtime(hass)
    runtime.member_entries[0].state = ConfigEntryState.UNLOAD_IN_PROGRESS

    runtime.async_member_entry_state_changed(0)

    assert runtime.available
    runtime.parallel.async_stop.assert_not_called()


async def test_group_cross_fade_zero_inherits_each_member(
    hass: HomeAssistant,
) -> None:
    """A zero Group Cross Fade setting inherits the member settings."""
    runtime, members = _runtime(hass)
    members[0].transition_cross_duration = 0.4
    members[1].transition_cross_duration = 0.8

    assert runtime._transition_ms(members[0], "cross", {}) == 400
    assert runtime._transition_ms(members[1], "cross", {}) == 800


async def test_group_cross_fade_overrides_each_member(hass: HomeAssistant) -> None:
    """A non-zero Group Cross Fade setting overrides member settings."""
    runtime, members = _runtime(hass)
    runtime.transition_cross_duration = 1.2
    members[0].transition_cross_duration = 0.4
    members[1].transition_cross_duration = 0.8

    assert runtime._transition_ms(members[0], "cross", {}) == 1200
    assert runtime._transition_ms(members[1], "cross", {}) == 1200


async def test_dispatch_does_not_schedule_a_physical_refresh(
    hass: HomeAssistant,
) -> None:
    """A Device Group command does not schedule a physical refresh."""
    runtime, members = _runtime(hass)
    await runtime._async_dispatch_projected_states(
        tuple(ParallelCommand("power", (False, 0)) for _member in members),
        tuple(
            _MemberCommandState(_hsbk_to_raw16(member.data.color), member.data.power)
            for member in members
        ),
    )
    runtime.parallel.async_dispatch.assert_awaited_once()
    for member in members:
        member.device.refresh_state.assert_not_awaited()


@pytest.mark.parametrize("power", [True, False])
async def test_group_dispatch_preserves_real_coordinator_data(
    hass: HomeAssistant, power: bool
) -> None:
    """Group notifications must not replace a physical member's typed state."""
    manager = hass.data[DATA_LIFX_MANAGER] = LIFXManager(hass)
    await manager.virtual_power.async_load()
    physical_entry = MockConfigEntry(domain=DOMAIN, title="Physical member")
    device = create_mock_light()
    device.port = 56700
    device.state.power = 65535
    member = LIFXUpdateCoordinator(hass, physical_entry, device)
    member.async_set_updated_data(device.state)
    original_data = member.data
    original_color = member.data.color
    observed_states = []
    remove_listener = member.async_add_listener(
        lambda: observed_states.append(member.data)
    )
    group_entry = MockConfigEntry(domain=DOMAIN, data={CONF_GROUP_ID: "typed-state"})
    runtime = LIFXParallelGroupRuntime(
        hass,
        group_entry,
        (MagicMock(state=ConfigEntryState.LOADED, runtime_data=member),),
        (member,),
    )
    runtime.parallel = MagicMock()
    runtime.parallel.async_cancel_health = AsyncMock()
    runtime.parallel.async_dispatch = AsyncMock(
        return_value=ParallelDispatchResult(ParallelDispatchOutcome.COMPLETED, 1)
    )
    try:
        await runtime.async_set_state(power=power, brightness=51)

        assert member.data is original_data
        assert member.data.color == original_color
        assert member.data.power == 65535
        assert member.virtual_off is not power
        assert observed_states == [original_data]

        await runtime.async_set_state(power=True, brightness=102)
        assert member.data is original_data
        assert not member.virtual_off
        assert observed_states == [original_data, original_data]
    finally:
        remove_listener()
        runtime._cancel_optimistic_expiry()
        runtime._async_cancel_keepalive()


@pytest.mark.enable_socket
async def test_home_assistant_shutdown_stops_group_workers(hass: HomeAssistant) -> None:
    """Normal HA shutdown must release real workers before Python joins children."""
    member = _member(hass, "127.0.0.1")
    physical_entry = MockConfigEntry(domain=DOMAIN, title="Shutdown member")
    physical_entry.add_to_hass(hass)
    physical_entry.runtime_data = member
    physical_entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_GROUP_ID: "shutdown-group", CONF_MEMBERS: [physical_entry.entry_id]},
    )
    entry.add_to_hass(hass)
    with patch.object(hass.config_entries, "async_forward_entry_setups"):
        assert await async_setup_parallel_group_entry(hass, entry)
    runtime = entry.runtime_data
    processes = [worker.process for worker in runtime.parallel._workers]
    dispatcher = runtime.parallel._dispatcher
    assert runtime.parallel.available
    assert dispatcher is not None
    try:
        await hass.async_stop()

        assert runtime.stopped
        assert not any(process.is_alive() for process in processes)
        assert not dispatcher.is_alive()
    finally:
        await runtime.async_stop()
        physical_entry._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)


async def test_optimistic_group_state_expires_without_mutating_members(
    hass: HomeAssistant,
) -> None:
    """Device Group optimism is one bounded group snapshot, never a member cache."""
    runtime, members = _runtime(hass)
    member_colors = tuple(member.data.color for member in members)
    member_power_levels = tuple(member.data.power for member in members)

    generation = runtime._begin_projection(
        (
            _MemberCommandState((100, 200, 300, 4000), 65535),
            _MemberCommandState((100, 200, 300, 4000), 65535),
        )
    )

    assert runtime.display_state.color == (100, 200, 300, 4000)
    assert runtime.display_state.is_on
    assert tuple(member.data.color for member in members) == member_colors
    assert tuple(member.data.power for member in members) == member_power_levels

    runtime._cancel_optimistic_expiry()
    with patch(
        "homeassistant.components.lifx.parallel_group.asyncio.sleep",
        new_callable=AsyncMock,
    ):
        await runtime._async_expire_optimistic_state(generation)

    assert runtime.display_state.is_on is False
    assert tuple(member.data.color for member in members) == member_colors
    assert tuple(member.data.power for member in members) == member_power_levels


async def test_dispatch_failure_is_silent_and_clears_the_projection(
    hass: HomeAssistant,
) -> None:
    """A failed Group request clears optimism without raising a service error."""
    runtime, members = _runtime(hass)
    runtime.parallel.async_dispatch = AsyncMock(
        return_value=ParallelDispatchResult(ParallelDispatchOutcome.FAILED, 1)
    )
    states = tuple(
        _MemberCommandState(_hsbk_to_raw16(member.data.color), member.data.power)
        for member in members
    )

    await runtime._async_dispatch_projected_states(
        tuple(ParallelCommand("power", (False, 0)) for _member in members),
        states,
    )
    await hass.async_block_till_done()

    assert runtime.available
    assert runtime._optimistic_state is None
    assert runtime._recovery_task is None
    for member in members:
        member.device.refresh_state.assert_not_awaited()


async def test_unavailable_group_state_request_is_a_silent_noop(
    hass: HomeAssistant,
) -> None:
    """The unavailable entity state replaces a frontend command-error toast."""
    runtime, _members = _runtime(hass)
    runtime._keepalive_healthy = [False, False]

    await runtime.async_set_state(power=True)

    runtime.parallel.async_dispatch.assert_not_awaited()


async def test_latest_projection_wins_after_a_newer_group_request(
    hass: HomeAssistant,
) -> None:
    """A later command replaces the group's earlier optimistic projection."""
    runtime, members = _runtime(hass)
    on_states = tuple(
        _MemberCommandState(_hsbk_to_raw16(member.data.color), 65535)
        for member in members
    )
    off_states = tuple(
        _MemberCommandState(_hsbk_to_raw16(member.data.color), 0) for member in members
    )
    commands = tuple(ParallelCommand("power", (False, 0)) for _member in members)

    await runtime._async_dispatch_projected_states(commands, on_states)
    await runtime._async_dispatch_projected_states(commands, off_states)

    assert runtime.display_state.is_on is False


async def test_turning_on_an_off_member_with_color_uses_staged_commands(
    hass: HomeAssistant,
) -> None:
    """An off member must acknowledge its hidden color before power is sent."""
    runtime, members = _runtime(hass)
    members[0].data.power = 0
    members[1].data.power = 0

    await runtime.async_set_state(power=True, brightness=255)

    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.kind == "color" for command in commands)
    assert all(command.second is not None for command in commands)
    assert all(command.second.kind == "power" for command in commands)


async def test_displayed_off_state_stages_color_and_power_for_every_member(
    hass: HomeAssistant,
) -> None:
    """A group target, rather than mixed member caches, controls power staging."""
    runtime, members = _runtime(hass)
    members[0].data.power = 65535
    members[1].data.power = 0
    members[0].data.color = _raw16_to_hsbk((1, 2, 3, 3500))
    members[1].data.color = _raw16_to_hsbk((4, 5, 6, 4000))
    runtime._begin_projection(
        (
            _MemberCommandState((100, 200, 300, 3500), 0),
            _MemberCommandState((100, 200, 300, 3500), 0),
        )
    )

    await runtime.async_set_state(power=True, brightness=255)

    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.kind == "color" for command in commands)
    assert all(command.payload[2] == 65535 for command in commands)
    assert commands[0].second is None
    assert commands[0].pad_before == 1
    assert commands[1].second is not None
    assert commands[1].second.kind == "power"


async def test_expired_display_state_uses_one_aggregate_baseline_for_all_members(
    hass: HomeAssistant,
) -> None:
    """After expiry, command construction uses display aggregation, not member caches."""
    runtime, members = _runtime(hass)
    members[0].data.color = _raw16_to_hsbk((100, 200, 300, 3500))
    members[1].data.color = _raw16_to_hsbk((500, 600, 700, 4500))
    members[0].data.power = 0
    members[1].data.power = 65535

    await runtime.async_set_state(brightness=255)

    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.kind == "color" for command in commands)
    assert all(command.payload == commands[0].payload for command in commands)


async def test_displayed_on_state_sends_one_virtual_off_color_for_every_member(
    hass: HomeAssistant,
) -> None:
    """A displayed on target controls turn-off staging despite off member caches."""
    runtime, members = _runtime(hass)
    for member in members:
        member.data.power = 0
    runtime._begin_projection(
        (
            _MemberCommandState((100, 200, 300, 3500), 65535),
            _MemberCommandState((100, 200, 300, 3500), 65535),
        )
    )

    await runtime.async_set_state(power=False, brightness=255)

    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.kind == "color" for command in commands)
    assert all(command.second is None for command in commands)
    assert all(command.payload[2] == 0 for command in commands)


async def test_displayed_on_color_change_uses_cross_fade_for_every_member(
    hass: HomeAssistant,
) -> None:
    """One displayed-on color update uses the Group Cross Fade setting."""
    runtime, members = _runtime(hass)
    runtime.transition_on_duration = 1.5
    runtime.transition_cross_duration = 0.9
    for member in members:
        member.data.power = 65535

    await runtime.async_set_state(brightness=128)

    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.payload[-1] == 900 for command in commands)


async def test_group_power_off_sends_brightness_zero_but_displays_off(
    hass: HomeAssistant,
) -> None:
    """A group turn-off is visually black but remains logically off in HA."""
    runtime, members = _runtime(hass)
    for member in members:
        member.data.color = _raw16_to_hsbk((100, 200, 300, 3500))
        member.data.power = 65535

    await runtime.async_set_state(power=False)

    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.kind == "color" for command in commands)
    assert all(command.second is None for command in commands)
    assert all(command.payload == (100, 200, 0, 3500, 0) for command in commands)
    assert runtime.display_state.color == (100, 200, 300, 3500)
    assert runtime.display_state.is_on is False
    assert all(member.virtual_off for member in members)
    assert all(member.resume_required for member in members)


async def test_colorloop_uses_one_displayed_state_baseline_for_every_member(
    hass: HomeAssistant,
) -> None:
    """Colorloop must not derive independent hues from physical member caches."""
    runtime, members = _runtime(hass)
    members[0].data.color = _raw16_to_hsbk((100, 200, 300, 3500))
    members[1].data.color = _raw16_to_hsbk((500, 600, 700, 4500))
    runtime._begin_projection(
        (
            _MemberCommandState((1000, 2000, 3000, 3500), 65535),
            _MemberCommandState((1000, 2000, 3000, 3500), 65535),
        )
    )

    task = asyncio.create_task(
        runtime._async_colorloop(period=60, change=0, power_on=False)
    )
    await asyncio.sleep(0)

    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.kind == "color" for command in commands)
    assert all(command.payload == commands[0].payload for command in commands)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_paint_theme_stages_power_before_color(
    hass: HomeAssistant,
) -> None:
    """Theme application must not dispatch power and color independently."""
    runtime, _members = _runtime(hass)

    await runtime._async_paint_theme(
        power_on=True,
        palette=[(0, 100, 100, 3500), (240, 50, 25, 4000)],
        transition=0,
    )

    runtime.parallel.async_dispatch.assert_awaited_once()
    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.kind == "power" for command in commands)
    assert all(command.second is not None for command in commands)
    assert all(command.second.kind == "color" for command in commands)
    assert all(
        command.second.payload == commands[0].second.payload for command in commands
    )
    assert commands[0].second.payload == (0, 65535, 65535, 3500, 0)


async def test_turning_off_with_a_transition_keeps_the_requested_color_hidden(
    hass: HomeAssistant,
) -> None:
    """A color-plus-off request ends with an invisible brightness-zero stage."""
    runtime, members = _runtime(hass)
    for member in members:
        member.data.power = 0

    await runtime.async_set_state(power=False, brightness=255, transition=1)

    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.kind == "color" for command in commands)
    assert all(command.second is None for command in commands)
    assert all(command.payload[2] == 0 for command in commands)
    assert runtime.display_state.color[2] == 65535
    assert runtime.display_state.is_on is False


async def test_move_effect_stages_power_before_effect(
    hass: HomeAssistant,
) -> None:
    """Effect activation must not use a separate uncoordinated power request."""
    runtime, _members = _runtime(hass)

    await runtime.async_start_effect(SERVICE_EFFECT_MOVE, power_on=True)

    runtime.parallel.async_dispatch.assert_awaited_once()
    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.kind == "power" for command in commands)
    assert all(command.second is not None for command in commands)
    assert all(command.second.kind == "multizone_effect" for command in commands)

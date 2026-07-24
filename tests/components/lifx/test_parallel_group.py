"""Test LIFX Device Group behavior."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.components.lifx.const import CONF_GROUP_ID, DOMAIN
from homeassistant.components.lifx.coordinator import LIFXUpdateCoordinator
from homeassistant.components.lifx.manager import SERVICE_EFFECT_MOVE
from homeassistant.components.lifx.parallel import (
    ParallelCommand,
    ParallelDispatchOutcome,
    ParallelDispatchResult,
    ParallelTransport,
)
from homeassistant.components.lifx.parallel_group import (
    LIFXParallelGroupRuntime,
    _MemberCommandState,
    _members_are_ready,
    async_setup_parallel_group_entry,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from . import _mocked_bulb

from tests.common import MockConfigEntry


def _member(ip_address: str, *, last_update_success: bool = True) -> MagicMock:
    """Create a physical coordinator stand-in with an independent device cache."""
    coordinator = MagicMock(spec=LIFXUpdateCoordinator)
    coordinator.device = _mocked_bulb()
    coordinator.device.ip_addr = ip_address
    coordinator.last_update_success = last_update_success
    coordinator.transition_on_duration = 0
    coordinator.transition_off_duration = 0
    coordinator.transition_cross_duration = 0
    coordinator.virtual_off = False
    coordinator.resume_hsbk = None
    coordinator.async_record_virtual_off = MagicMock(
        side_effect=lambda color: setattr(coordinator, "virtual_off", True)
        or setattr(coordinator, "resume_hsbk", color)
    )
    coordinator.async_record_virtual_on = MagicMock(
        side_effect=lambda color: setattr(coordinator, "virtual_off", False)
        or setattr(coordinator, "resume_hsbk", color)
    )
    coordinator.async_schedule_post_command_refresh = AsyncMock()
    return coordinator


def _transport(host: str) -> ParallelTransport:
    """Return the resolved physical transport used by a mock member."""
    return ParallelTransport(host, 56700, b"\xaa\xbb\xcc\xdd\xee\xcc\x00\x00")


def _runtime(
    hass: HomeAssistant,
) -> tuple[LIFXParallelGroupRuntime, tuple[MagicMock, ...]]:
    """Create a Device Group runtime without starting its worker processes."""
    members = (_member("192.0.2.1"), _member("192.0.2.2"))
    member_entries = tuple(
        MagicMock(state=ConfigEntryState.LOADED, runtime_data=member)
        for member in members
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_GROUP_ID: "test-device-group"},
    )
    runtime = LIFXParallelGroupRuntime(hass, entry, member_entries, members)
    runtime.parallel = MagicMock()
    runtime.parallel.async_dispatch = AsyncMock(
        return_value=ParallelDispatchResult(ParallelDispatchOutcome.COMPLETED, 1)
    )
    runtime.parallel.async_cancel_health = AsyncMock()
    runtime.parallel.async_request_reconnect = AsyncMock(
        return_value=ParallelDispatchResult(ParallelDispatchOutcome.COMPLETED, 1)
    )
    return runtime, members


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
            runtime_data=_member("192.0.2.1", last_update_success=last_update_success),
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
    replacement = _member("192.0.2.99")
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
    replacement = _member("192.0.2.99")
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
            _MemberCommandState(tuple(member.device.color), member.device.power_level)
            for member in members
        ),
    )
    runtime.parallel.async_dispatch.assert_awaited_once()
    for member in members:
        member.async_schedule_post_command_refresh.assert_not_awaited()


async def test_optimistic_group_state_expires_without_mutating_members(
    hass: HomeAssistant,
) -> None:
    """Device Group optimism is one bounded group snapshot, never a member cache."""
    runtime, members = _runtime(hass)
    member_colors = tuple(tuple(member.device.color) for member in members)
    member_power_levels = tuple(member.device.power_level for member in members)

    generation = runtime._begin_projection(
        (
            _MemberCommandState((100, 200, 300, 4000), 65535),
            _MemberCommandState((100, 200, 300, 4000), 65535),
        )
    )

    assert runtime.display_state.color == (100, 200, 300, 4000)
    assert runtime.display_state.is_on
    assert tuple(tuple(member.device.color) for member in members) == member_colors
    assert tuple(member.device.power_level for member in members) == member_power_levels

    runtime._cancel_optimistic_expiry()
    with patch(
        "homeassistant.components.lifx.parallel_group.asyncio.sleep",
        new_callable=AsyncMock,
    ):
        await runtime._async_expire_optimistic_state(generation)

    assert runtime.display_state.is_on is False
    assert tuple(tuple(member.device.color) for member in members) == member_colors
    assert tuple(member.device.power_level for member in members) == member_power_levels


async def test_dispatch_failure_is_silent_and_clears_the_projection(
    hass: HomeAssistant,
) -> None:
    """A failed Group request clears optimism without raising a service error."""
    runtime, members = _runtime(hass)
    runtime.parallel.async_dispatch = AsyncMock(
        return_value=ParallelDispatchResult(ParallelDispatchOutcome.FAILED, 1)
    )
    states = tuple(
        _MemberCommandState(tuple(member.device.color), member.device.power_level)
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
        member.async_schedule_post_command_refresh.assert_not_awaited()


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
        _MemberCommandState(tuple(member.device.color), 65535) for member in members
    )
    off_states = tuple(
        _MemberCommandState(tuple(member.device.color), 0) for member in members
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
    members[0].device.power_level = 0
    members[1].device.power_level = 0

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
    members[0].device.power_level = 65535
    members[1].device.power_level = 0
    members[0].device.color = [1, 2, 3, 3500]
    members[1].device.color = [4, 5, 6, 4000]
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
    members[0].device.color = [100, 200, 300, 3500]
    members[1].device.color = [500, 600, 700, 4500]
    members[0].device.power_level = 0
    members[1].device.power_level = 65535

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
        member.device.power_level = 0
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
        member.device.power_level = 65535

    await runtime.async_set_state(brightness=128)

    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.payload[-1] == 900 for command in commands)


async def test_group_power_off_sends_brightness_zero_but_displays_off(
    hass: HomeAssistant,
) -> None:
    """A group turn-off is visually black but remains logically off in HA."""
    runtime, members = _runtime(hass)
    for member in members:
        member.device.color = [100, 200, 300, 3500]
        member.device.power_level = 65535

    await runtime.async_set_state(power=False)

    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.kind == "color" for command in commands)
    assert all(command.second is None for command in commands)
    assert all(command.payload == (100, 200, 0, 3500, 0) for command in commands)
    assert runtime.display_state.color == (100, 200, 300, 3500)
    assert runtime.display_state.is_on is False
    assert all(member.virtual_off for member in members)
    assert all(member.async_record_virtual_off.call_count == 1 for member in members)


async def test_colorloop_uses_one_displayed_state_baseline_for_every_member(
    hass: HomeAssistant,
) -> None:
    """Colorloop must not derive independent hues from physical member caches."""
    runtime, members = _runtime(hass)
    members[0].device.color = [100, 200, 300, 3500]
    members[1].device.color = [500, 600, 700, 4500]
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
    assert all(command.second.payload == commands[0].second.payload for command in commands)
    assert commands[0].second.payload == (0, 65535, 65535, 3500, 0)


async def test_turning_off_with_a_transition_keeps_the_requested_color_hidden(
    hass: HomeAssistant,
) -> None:
    """A color-plus-off request ends with an invisible brightness-zero stage."""
    runtime, members = _runtime(hass)
    for member in members:
        member.device.power_level = 0

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

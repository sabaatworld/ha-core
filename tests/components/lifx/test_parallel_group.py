"""Test LIFX Device Group behavior."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.components.lifx.const import CONF_GROUP_ID, DOMAIN
from homeassistant.components.lifx.coordinator import LIFXUpdateCoordinator
from homeassistant.components.lifx.manager import SERVICE_EFFECT_MOVE
from homeassistant.components.lifx.parallel import ParallelCommand
from homeassistant.components.lifx.parallel_group import (
    LIFXParallelGroupRuntime,
    _MemberCommandState,
    _members_are_ready,
    async_setup_parallel_group_entry,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

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
    coordinator.async_schedule_post_command_refresh = AsyncMock()
    return coordinator


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
    runtime.parallel.async_dispatch = AsyncMock()
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


async def test_group_availability_tracks_only_physical_dependencies(
    hass: HomeAssistant,
) -> None:
    """A worker fault cannot make an otherwise healthy Device Group unavailable."""
    runtime, members = _runtime(hass)
    runtime.parallel.available = False

    assert runtime.available

    members[1].last_update_success = False
    assert not runtime.available

    members[1].last_update_success = True
    assert runtime.available


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


async def test_dispatch_timeout_does_not_change_dependency_availability(
    hass: HomeAssistant,
) -> None:
    """A worker timeout fails only the command and leaves the group retryable."""
    runtime, members = _runtime(hass)
    runtime.parallel.async_dispatch = AsyncMock(
        side_effect=HomeAssistantError("timeout")
    )
    states = tuple(
        _MemberCommandState(tuple(member.device.color), member.device.power_level)
        for member in members
    )

    with pytest.raises(HomeAssistantError, match="timeout"):
        await runtime._async_dispatch_projected_states(
            tuple(ParallelCommand("power", (False, 0)) for _member in members),
            states,
        )
    await hass.async_block_till_done()

    assert runtime.available
    assert runtime._recovery_task is None
    for member in members:
        member.async_schedule_post_command_refresh.assert_not_awaited()


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
    assert all(command.payload == commands[0].payload for command in commands)
    assert all(command.second is not None for command in commands)
    assert all(command.second.kind == "power" for command in commands)


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


async def test_displayed_on_state_stages_color_before_power_off_for_every_member(
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
    assert all(command.second is not None for command in commands)
    assert all(command.second.kind == "power" for command in commands)


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


async def test_turning_off_with_a_transition_stages_power_before_color(
    hass: HomeAssistant,
) -> None:
    """An already-off member must power first before its final color is sent."""
    runtime, members = _runtime(hass)
    for member in members:
        member.device.power_level = 0

    await runtime.async_set_state(power=False, brightness=255, transition=1)

    commands = runtime.parallel.async_dispatch.await_args.args[0]
    assert all(command.kind == "power" for command in commands)
    assert all(command.second is not None for command in commands)
    assert all(command.second.kind == "color" for command in commands)


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

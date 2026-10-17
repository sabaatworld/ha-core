"""Local, persistent physical and Device Group virtual-power policy."""

from unittest.mock import patch

import pytest

from homeassistant.components.lifx.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import async_setup_lifx_entry
from .helpers import create_mock_light

from tests.common import MockConfigEntry

ENTITY_ID = "switch.my_group_my_bulb_virtual_power_off"


@pytest.mark.parametrize(
    "policy",
    [
        pytest.param(None, id="upgrade-default"),
        pytest.param(False, id="saved-off"),
        pytest.param(True, id="saved-on"),
    ],
)
async def test_virtual_power_switch_changes_policy_without_packets(
    hass: HomeAssistant, policy: bool | None
) -> None:
    """Policy uses options, sends no packets, and preserves recovery markers."""
    device = create_mock_light()
    entry = await async_setup_lifx_entry(hass, device, virtual_power_off=policy)
    assert hass.states.get(ENTITY_ID).state == ("off" if policy is False else "on")
    entry.runtime_data.async_record_virtual_off(device.state.color)
    before = entry.runtime_data.virtual_off
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": ENTITY_ID}, blocking=True
    )
    assert entry.options["virtual_power_off"] is False
    assert entry.runtime_data.virtual_off == before
    assert not entry.runtime_data.virtual_power_off_enabled
    device.set_power.assert_not_awaited()
    device.set_waveform_optional.assert_not_awaited()
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": ENTITY_ID}, blocking=True
    )
    assert entry.runtime_data.virtual_power_off_enabled


async def test_disabled_switch_policy_survives_reload(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """The policy remains authoritative without an instantiated switch."""
    device = create_mock_light()
    entry = await async_setup_lifx_entry(hass, device, virtual_power_off=False)
    entity_registry.async_update_entity(
        ENTITY_ID, disabled_by=er.RegistryEntryDisabler.USER
    )
    with patch("homeassistant.components.lifx.Device.connect", return_value=device):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID) is None
    assert not entry.runtime_data.virtual_power_off_enabled
    device.set_power.assert_not_awaited()


@pytest.mark.parametrize(
    "group_policy",
    [pytest.param(None, id="group-default"), pytest.param(False, id="group-saved-off")],
)
async def test_group_and_physical_policies_are_independent(
    hass: HomeAssistant, group_policy: bool | None
) -> None:
    """A real physical coordinator and group expose independently stored choices."""
    device = create_mock_light()
    device.port = 56700
    physical = await async_setup_lifx_entry(hass, device, virtual_power_off=False)
    group = MockConfigEntry(
        domain=DOMAIN,
        title="Room",
        version=2,
        unique_id="parallel-group-room",
        data={
            "entry_type": "parallel_group",
            "group_id": "room",
            "members": [physical.entry_id],
        },
        options={} if group_policy is None else {"virtual_power_off": group_policy},
    )
    group.add_to_hass(hass)
    with (
        patch("homeassistant.components.lifx.parallel.LIFXParallelRuntime.async_start"),
        patch("homeassistant.components.lifx.parallel.LIFXParallelRuntime.async_stop"),
    ):
        assert await hass.config_entries.async_setup(group.entry_id)
        await hass.async_block_till_done()
        group_id = "switch.room_virtual_power_off"
        assert hass.states.get(group_id).state == (
            "off" if group_policy is False else "on"
        )
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": group_id}, blocking=True
        )
        assert group.runtime_data.virtual_power_off_enabled
        assert not physical.runtime_data.virtual_power_off_enabled
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": ENTITY_ID}, blocking=True
        )
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": group_id}, blocking=True
        )
        assert physical.runtime_data.virtual_power_off_enabled
        assert not group.runtime_data.virtual_power_off_enabled
        assert await hass.config_entries.async_unload(group.entry_id)
    device.set_power.assert_not_awaited()
    device.set_waveform_optional.assert_not_awaited()

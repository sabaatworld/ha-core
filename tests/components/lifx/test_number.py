"""Tests for the lifx number platform."""

from homeassistant.components import lifx
from homeassistant.components.lifx import DOMAIN
from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
    NumberExtraStoredData,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_FRIENDLY_NAME, CONF_HOST
from homeassistant.core import HomeAssistant, State
from homeassistant.setup import async_setup_component

from . import (
    SERIAL,
    _mocked_bulb,
    _patch_config_flow_try_connect,
    _patch_device,
    _patch_discovery,
)

from tests.common import MockConfigEntry, mock_restore_cache_with_extra_data


async def test_transition_duration_numbers(hass: HomeAssistant) -> None:
    """Test transition duration number entities update the coordinator."""
    config_entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "127.0.0.1"}, unique_id=SERIAL
    )
    config_entry.add_to_hass(hass)
    bulb = _mocked_bulb()
    with (
        _patch_discovery(device=bulb),
        _patch_config_flow_try_connect(device=bulb),
        _patch_device(device=bulb),
    ):
        await async_setup_component(hass, lifx.DOMAIN, {lifx.DOMAIN: {}})
        await hass.async_block_till_done()

    entity_id = "number.my_group_my_bulb_fade_on_time"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "0.0"
    assert state.attributes["min"] == 0
    assert state.attributes["max"] == 300
    assert state.attributes["step"] == 0.1
    assert state.attributes["unit_of_measurement"] == "s"
    assert state.attributes[ATTR_FRIENDLY_NAME] == "My Bulb Fade On Time"
    assert hass.states.get("number.my_group_my_bulb_fade_off_time").state == "0.0"
    assert (
        hass.states.get("number.my_group_my_bulb_fade_off_time").attributes[
            ATTR_FRIENDLY_NAME
        ]
        == "My Bulb Fade Off Time"
    )

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: 1.5},
        blocking=True,
    )

    assert hass.states.get(entity_id).state == "1.5"
    assert config_entry.runtime_data.transition_on_duration == 1.5
    assert not bulb.set_power.calls


async def test_transition_duration_number_restores_value(hass: HomeAssistant) -> None:
    """Test a transition duration number restores its native value."""
    entity_id = "number.my_group_my_bulb_fade_off_time"
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State(entity_id, "2.5"),
                NumberExtraStoredData(300, 0, 0.1, "s", 2.5).as_dict(),
            ),
        ),
    )
    config_entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "127.0.0.1"}, unique_id=SERIAL
    )
    config_entry.add_to_hass(hass)
    bulb = _mocked_bulb()
    with (
        _patch_discovery(device=bulb),
        _patch_config_flow_try_connect(device=bulb),
        _patch_device(device=bulb),
    ):
        await async_setup_component(hass, lifx.DOMAIN, {lifx.DOMAIN: {}})
        await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "2.5"
    assert config_entry.runtime_data.transition_off_duration == 2.5

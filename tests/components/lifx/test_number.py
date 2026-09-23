"""Tests for the lifx number platform."""

from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
    NumberExtraStoredData,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_FRIENDLY_NAME
from homeassistant.core import HomeAssistant, State

from . import async_setup_lifx_entry
from .helpers import create_mock_light

from tests.common import mock_restore_cache_with_extra_data


async def test_transition_duration_numbers(hass: HomeAssistant) -> None:
    """Test transition duration number entities update the coordinator."""
    entry = await async_setup_lifx_entry(hass, create_mock_light())

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
    assert hass.states.get("number.my_group_my_bulb_cross_fade_time").state == "0.0"
    assert (
        hass.states.get("number.my_group_my_bulb_cross_fade_time").attributes[
            ATTR_FRIENDLY_NAME
        ]
        == "My Bulb Cross Fade Time"
    )

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: 1.5},
        blocking=True,
    )

    assert hass.states.get(entity_id).state == "1.5"
    assert entry.runtime_data.transition_on_duration == 1.5

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {
            ATTR_ENTITY_ID: "number.my_group_my_bulb_cross_fade_time",
            ATTR_VALUE: 0.9,
        },
        blocking=True,
    )

    assert hass.states.get("number.my_group_my_bulb_cross_fade_time").state == "0.9"
    assert entry.runtime_data.transition_cross_duration == 0.9


async def test_transition_duration_number_restores_value(hass: HomeAssistant) -> None:
    """Test a transition duration number restores its native value."""
    entity_id = "number.my_group_my_bulb_cross_fade_time"
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State(entity_id, "2.5"),
                NumberExtraStoredData(300, 0, 0.1, "s", 2.5).as_dict(),
            ),
        ),
    )
    entry = await async_setup_lifx_entry(hass, create_mock_light())

    assert hass.states.get(entity_id).state == "2.5"
    assert entry.runtime_data.transition_cross_duration == 2.5

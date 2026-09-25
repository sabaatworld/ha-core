"""Test for the SmartThings select platform."""

from unittest.mock import AsyncMock, call

from pysmartthings import Attribute, Capability, Command
from pysmartthings.models import HealthStatus
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.select import (
    ATTR_OPTION,
    ATTR_OPTIONS,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.smartthings import MAIN
from homeassistant.components.smartthings.select import (
    SmartThingsWasherCycleSelectEntity,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from . import (
    set_attribute_value,
    setup_integration,
    snapshot_smartthings_entities,
    trigger_health_update,
    trigger_update,
)

from tests.common import MockConfigEntry


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_all_entities(
    hass: HomeAssistant,
    snapshot: SnapshotAssertion,
    devices: AsyncMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test all entities."""
    await setup_integration(hass, mock_config_entry)

    snapshot_smartthings_entities(hass, entity_registry, snapshot, Platform.SELECT)


@pytest.mark.parametrize("device_fixture", ["da_wm_wd_000001"])
async def test_state_update(
    hass: HomeAssistant,
    devices: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test state update."""
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get("select.theater_dryer").state == "stop"

    await trigger_update(
        hass,
        devices,
        "02f7256e-8353-5bdd-547f-bd5b1647e01b",
        Capability.DRYER_OPERATING_STATE,
        Attribute.MACHINE_STATE,
        "run",
    )

    assert hass.states.get("select.theater_dryer").state == "run"


@pytest.mark.parametrize("device_fixture", ["da_wm_wd_000001"])
async def test_select_option(
    hass: HomeAssistant,
    devices: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test state update."""
    set_attribute_value(
        devices,
        Capability.REMOTE_CONTROL_STATUS,
        Attribute.REMOTE_CONTROL_ENABLED,
        "true",
    )
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: "select.theater_dryer", ATTR_OPTION: "run"},
        blocking=True,
    )
    devices.execute_device_command.assert_called_once_with(
        "02f7256e-8353-5bdd-547f-bd5b1647e01b",
        Capability.DRYER_OPERATING_STATE,
        Command.SET_MACHINE_STATE,
        MAIN,
        argument="run",
    )


@pytest.mark.parametrize("device_fixture", ["da_ks_range_0101x"])
async def test_select_option_map(
    hass: HomeAssistant,
    devices: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test state update."""
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get("select.vulcan_lamp")
    assert state
    assert state.state == "extra_high"
    assert state.attributes[ATTR_OPTIONS] == [
        "off",
        "extra_high",
    ]

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: "select.vulcan_lamp", ATTR_OPTION: "extra_high"},
        blocking=True,
    )
    devices.execute_device_command.assert_called_once_with(
        "2c3cbaa0-1899-5ddc-7b58-9d657bd48f18",
        Capability.SAMSUNG_CE_LAMP,
        Command.SET_BRIGHTNESS_LEVEL,
        MAIN,
        argument="extraHigh",
    )


@pytest.mark.parametrize("device_fixture", ["da_wm_wd_000001"])
async def test_select_option_without_remote_control(
    hass: HomeAssistant,
    devices: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test state update."""
    set_attribute_value(
        devices,
        Capability.REMOTE_CONTROL_STATUS,
        Attribute.REMOTE_CONTROL_ENABLED,
        "false",
    )
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(
        ServiceValidationError,
        match="Can only be updated when remote control is enabled",
    ):
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: "select.theater_dryer", ATTR_OPTION: "run"},
            blocking=True,
        )
    devices.execute_device_command.assert_not_called()


@pytest.mark.parametrize("device_fixture", ["da_wm_wd_000001"])
async def test_availability(
    hass: HomeAssistant,
    devices: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test availability."""
    await setup_integration(hass, mock_config_entry)

    assert hass.states.get("select.theater_dryer").state == "stop"

    await trigger_health_update(
        hass, devices, "02f7256e-8353-5bdd-547f-bd5b1647e01b", HealthStatus.OFFLINE
    )

    assert hass.states.get("select.theater_dryer").state == STATE_UNAVAILABLE

    await trigger_health_update(
        hass, devices, "02f7256e-8353-5bdd-547f-bd5b1647e01b", HealthStatus.ONLINE
    )

    assert hass.states.get("select.theater_dryer").state == "stop"


@pytest.mark.parametrize("device_fixture", ["da_wm_wd_000001"])
async def test_availability_at_start(
    hass: HomeAssistant,
    unavailable_device: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test unavailable at boot."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get("select.theater_dryer").state == STATE_UNAVAILABLE


@pytest.mark.parametrize("device_fixture", ["da_ac_rac_000003"])
async def test_select_option_as_integer(
    hass: HomeAssistant,
    devices: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test selecting an option represented as an integer."""
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get("select.clim_salon_dust_filter_alarm_threshold")
    assert state.state == "500"
    assert all(isinstance(option, str) for option in state.attributes[ATTR_OPTIONS])

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {
            ATTR_ENTITY_ID: "select.clim_salon_dust_filter_alarm_threshold",
            ATTR_OPTION: "300",
        },
        blocking=True,
    )
    devices.execute_device_command.assert_called_once_with(
        "1e3f7ca2-e005-e1a4-f6d7-bc231e3f7977",
        Capability.SAMSUNG_CE_DUST_FILTER_ALARM,
        Command.SET_ALARM_THRESHOLD,
        MAIN,
        argument=300,
    )


@pytest.mark.parametrize("device_fixture", ["da_wm_dw_01011"])
async def test_select_dishwasher_washing_course_with_wrong_machine_state(
    hass: HomeAssistant,
    devices: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test state update."""
    set_attribute_value(
        devices,
        Capability.REMOTE_CONTROL_STATUS,
        Attribute.REMOTE_CONTROL_ENABLED,
        "true",
    )
    set_attribute_value(
        devices,
        Capability.DISHWASHER_OPERATING_STATE,
        Attribute.MACHINE_STATE,
        "run",
    )
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(
        ServiceValidationError,
        match="Can only be updated when dishwasher machine state is stop",
    ):
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: "select.dishwasher_1_cycle", ATTR_OPTION: "eco"},
            blocking=True,
        )
    devices.execute_device_command.assert_not_called()


@pytest.mark.parametrize("device_fixture", ["da_wm_dw_01011"])
async def test_select_dishwasher_washing_course(
    hass: HomeAssistant,
    devices: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test state update."""
    set_attribute_value(
        devices,
        Capability.REMOTE_CONTROL_STATUS,
        Attribute.REMOTE_CONTROL_ENABLED,
        "true",
    )
    await setup_integration(hass, mock_config_entry)
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: "select.dishwasher_1_cycle", ATTR_OPTION: "eco"},
        blocking=True,
    )
    devices.execute_device_command.assert_called_once_with(
        "7ff318f3-3772-524d-3c9f-72fcd26413ed",
        Capability.SAMSUNG_CE_DISHWASHER_WASHING_COURSE,
        Command.SET_WASHING_COURSE,
        MAIN,
        argument="eco",
    )


@pytest.mark.parametrize("device_fixture", ["da_wm_dw_01011"])
async def test_select_dishwasher_washing_option_with_wrong_machine_state(
    hass: HomeAssistant,
    devices: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test state update."""
    set_attribute_value(
        devices,
        Capability.REMOTE_CONTROL_STATUS,
        Attribute.REMOTE_CONTROL_ENABLED,
        "true",
    )
    set_attribute_value(
        devices,
        Capability.DISHWASHER_OPERATING_STATE,
        Attribute.MACHINE_STATE,
        "run",
    )
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(
        ServiceValidationError,
        match="Can only be updated when dishwasher machine state is stop",
    ):
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: "select.dishwasher_1_selected_zone", ATTR_OPTION: "lower"},
            blocking=True,
        )
    devices.execute_device_command.assert_not_called()


@pytest.mark.parametrize("device_fixture", ["washer_us_table02"])
async def test_washer_cycle_select_sends_both_commands(
    hass: HomeAssistant, devices: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Selecting Heavy Duty sends setCourse(53) then setWasherCycle(<table>_Course_53)."""
    await setup_integration(hass, mock_config_entry)
    entity_id = "select.theater_washer_cycle"
    assert hass.states.get(entity_id) is not None
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity_id, "option": "Heavy Duty"},
        blocking=True,
    )
    calls = devices.execute_device_command.await_args_list
    assert calls[0].args[1] == Capability.CUSTOM_SUPPORTED_OPTIONS
    assert calls[0].args[2] == Command.SET_COURSE
    assert calls[0].args[3] == MAIN
    assert calls[0].kwargs["argument"] == "53"
    assert calls[1].args[1] == Capability.SAMSUNG_CE_WASHER_CYCLE
    assert calls[1].args[2] == Command.SET_WASHER_CYCLE
    assert calls[1].kwargs["argument"].endswith("_Course_53")


@pytest.mark.parametrize("device_fixture", ["washer_us_table02"])
async def test_washer_cycle_select_accepts_raw_code(
    hass: HomeAssistant, devices: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Raw codes without a friendly label remain selectable as-is."""
    await setup_integration(hass, mock_config_entry)
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": "select.theater_washer_cycle", "option": "F2"},
        blocking=True,
    )
    first = devices.execute_device_command.await_args_list[0]
    assert first.args[2] == Command.SET_COURSE
    assert first.kwargs["argument"] == "F2"


@pytest.mark.parametrize("device_fixture", ["washer_us_table02"])
async def test_washer_cycle_current_option_caches_transient_none(
    hass: HomeAssistant, devices: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A transient cleared course falls back to the last known value."""
    await setup_integration(hass, mock_config_entry)
    entry_data = mock_config_entry.runtime_data
    device = next(iter(entry_data.devices.values()))
    entity = SmartThingsWasherCycleSelectEntity(entry_data.client, device)
    assert entity.current_option == "Normal"
    entity._internal_state[Capability.CUSTOM_SUPPORTED_OPTIONS][
        Attribute.COURSE
    ].value = None
    entity._internal_state[Capability.SAMSUNG_CE_WASHER_CYCLE][
        Attribute.WASHER_CYCLE
    ].value = None
    assert entity.current_option == "Normal"


@pytest.mark.parametrize("device_fixture", ["washer_us_table02"])
async def test_dispenser_selects_exist(
    hass: HomeAssistant, devices: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Verify dispenser selects exist."""
    await setup_integration(hass, mock_config_entry)
    assert (
        hass.states.get("select.theater_washer_detergent_dispense_density") is not None
    )
    assert hass.states.get("select.theater_washer_softener_dispense_amount") is not None
    assert (
        hass.states.get("select.theater_washer_softener_dispense_density") is not None
    )


@pytest.mark.parametrize(
    ("option", "token"),
    [("off", "ExtraRinse_Off"), ("on", "ExtraRinse_On")],
)
@pytest.mark.parametrize("device_fixture", ["washer_us_table02"])
async def test_extra_rinse_select_sends_hidden_execute_command(
    hass: HomeAssistant,
    devices: AsyncMock,
    mock_config_entry: MockConfigEntry,
    option: str,
    token: str,
) -> None:
    """Extra rinse uses the private course option carried by execute."""
    await setup_integration(hass, mock_config_entry)
    entity_id = "select.theater_washer_extra_rinse"

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "not_reported"
    assert state.attributes[ATTR_OPTIONS] == ["not_reported", "off", "on"]

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: option},
        blocking=True,
    )

    devices.execute_device_command.assert_called_once_with(
        "12345678-abcd-ef01-2345-6789abcdef01",
        Capability.EXECUTE,
        Command.EXECUTE,
        MAIN,
        argument=[
            "course/vs/0",
            {"x.com.samsung.da.options": [token]},
        ],
    )
    assert hass.states.get(entity_id).state == "not_reported"


@pytest.mark.parametrize("device_fixture", ["washer_us_table02"])
async def test_extra_rinse_select_can_clear_last_command(
    hass: HomeAssistant, devices: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Not Reported clears the optimistic value without sending a command."""
    await setup_integration(hass, mock_config_entry)
    entity_id = "select.theater_washer_extra_rinse"

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: "not_reported"},
        blocking=True,
    )

    assert hass.states.get(entity_id).state == "not_reported"
    devices.execute_device_command.assert_not_called()


@pytest.mark.parametrize("device_fixture", ["washer_us_table02"])
async def test_delay_end_requires_remote(
    hass: HomeAssistant, devices: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Verify delay end requires remote control."""
    await setup_integration(hass, mock_config_entry)
    with pytest.raises(ServiceValidationError, match="remote control"):
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": "select.theater_washer_delay_end", "option": "60"},
            blocking=True,
        )


@pytest.mark.parametrize("device_fixture", ["da_wm_dw_01011"])
async def test_select_dishwasher_washing_option(
    hass: HomeAssistant,
    devices: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test state update."""
    set_attribute_value(
        devices,
        Capability.REMOTE_CONTROL_STATUS,
        Attribute.REMOTE_CONTROL_ENABLED,
        "true",
    )
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: "select.dishwasher_1_selected_zone", ATTR_OPTION: "lower"},
        blocking=True,
    )
    device_id = "7ff318f3-3772-524d-3c9f-72fcd26413ed"
    devices.execute_device_command.assert_has_calls(
        [
            call(
                device_id,
                Capability.SAMSUNG_CE_DISHWASHER_OPERATION,
                Command.CANCEL,
                MAIN,
                argument=False,
            ),
            call(
                device_id,
                Capability.SAMSUNG_CE_DISHWASHER_WASHING_COURSE,
                Command.SET_WASHING_COURSE,
                MAIN,
                argument="eco",
            ),
            call(
                device_id,
                Capability.SAMSUNG_CE_DISHWASHER_WASHING_OPTIONS,
                Command.SET_OPTIONS,
                MAIN,
                argument={
                    "selectedZone": "lower",
                    "speedBooster": False,
                    "sanitize": False,
                },
            ),
        ]
    )

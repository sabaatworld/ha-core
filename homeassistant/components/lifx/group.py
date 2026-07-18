"""Virtual, low-skew LIFX parallel group entities."""

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from statistics import fmean
from typing import Any, override

from aiolifx_themes.themes import Theme, ThemeLibrary

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.components.light import (
    ATTR_EFFECT,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.components.number import NumberEntityDescription, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_GROUP_ID,
    CONF_MEMBERS,
    DATA_LIFX_MANAGER,
    DOMAIN,
    LIFX_CEILING_PRODUCT_IDS,
    LIFX_STATE_SETTLE_DELAY,
)
from .coordinator import LIFXUpdateCoordinator
from .manager import (
    ATTR_CHANGE,
    ATTR_CYCLES,
    ATTR_DIRECTION,
    ATTR_PALETTE,
    ATTR_PERIOD,
    ATTR_POWER_ON,
    ATTR_SATURATION_MAX,
    ATTR_SATURATION_MIN,
    ATTR_SKY_TYPE,
    ATTR_SPEED,
    ATTR_SPREAD,
    ATTR_THEME,
    EFFECT_FLAME_DEFAULT_SPEED,
    EFFECT_MORPH_DEFAULT_SPEED,
    EFFECT_MOVE_DEFAULT_DIRECTION,
    EFFECT_MOVE_DEFAULT_SPEED,
    EFFECT_SKY_DEFAULT_CLOUD_SATURATION_MAX,
    EFFECT_SKY_DEFAULT_CLOUD_SATURATION_MIN,
    EFFECT_SKY_DEFAULT_SKY_TYPE,
    EFFECT_SKY_DEFAULT_SPEED,
    PAINT_THEME_DEFAULT_TRANSITION,
    SERVICE_EFFECT_COLORLOOP,
    SERVICE_EFFECT_FLAME,
    SERVICE_EFFECT_MORPH,
    SERVICE_EFFECT_MOVE,
    SERVICE_EFFECT_PULSE,
    SERVICE_EFFECT_SKY,
    SERVICE_EFFECT_STOP,
    SERVICE_PAINT_THEME,
)
from .parallel import LIFXParallelRuntime, ParallelCommand, ParallelLightState
from .util import convert_16_to_8, find_hsbk, lifx_features, merge_hsbk

GROUP_PLATFORMS = [Platform.BUTTON, Platform.LIGHT, Platform.NUMBER]


def _color_modes(coordinator: LIFXUpdateCoordinator) -> set[ColorMode]:
    features = lifx_features(coordinator.device)
    if features["color"]:
        return {ColorMode.COLOR_TEMP, ColorMode.HS}
    if features["min_kelvin"] != features["max_kelvin"]:
        return {ColorMode.COLOR_TEMP}
    return {ColorMode.BRIGHTNESS}


def _effects(coordinator: LIFXUpdateCoordinator) -> set[str]:
    features = lifx_features(coordinator.device)
    effects = {"effect_pulse", "effect_stop"}
    if features["color"]:
        effects.add("effect_colorloop")
    if features["multizone"]:
        effects.add("effect_move")
    if features["matrix"]:
        effects.update({"effect_flame", "effect_morph"})
    if coordinator.device.product in LIFX_CEILING_PRODUCT_IDS:
        effects.add("effect_sky")
    return effects


def _raw_hsbk(
    color: tuple[float | int, float | int, float | int, int],
) -> tuple[int, int, int, int]:
    """Convert a theme HSBK value into the LIFX packet representation."""
    hue, saturation, brightness, kelvin = color
    return (
        round(hue / 360 * 65535) if hue <= 360 else round(hue),
        round(saturation / 100 * 65535) if saturation <= 100 else round(saturation),
        round(brightness / 100 * 65535) if brightness <= 100 else round(brightness),
        round(kelvin),
    )


@dataclass(frozen=True, slots=True)
class _ProjectedMemberState:
    """The requested visual state for one group member."""

    color: tuple[int, int, int, int]
    power_level: int


class LIFXParallelGroupRuntime:
    """Runtime state and dispatch policy for one virtual group config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        members: tuple[LIFXUpdateCoordinator, ...],
    ) -> None:
        """Initialize the virtual group runtime."""
        self.hass = hass
        self.entry = entry
        self.members = members
        self.group_id = entry.data[CONF_GROUP_ID]
        self.parallel = LIFXParallelRuntime(
            hass, (member.device.ip_addr for member in members)
        )
        self.transition_on_duration = 0.0
        self.transition_off_duration = 0.0
        self._command_lock = asyncio.Lock()
        self._availability_listeners: list[Callable[[], None]] = []
        self._software_effect: asyncio.Task[None] | None = None
        self._stopped = False
        self._command_generation = 0
        self._projected_states: tuple[_ProjectedMemberState, ...] | None = None
        self._pending_acknowledgements: set[tuple[int, int]] = set()
        self._confirmation_task: asyncio.Task[None] | None = None

    @property
    def available(self) -> bool:
        """Return whether every worker and member is available."""
        return self.parallel.available and all(
            member.last_update_success for member in self.members
        )

    @property
    def stopped(self) -> bool:
        """Return whether the group has released its worker pool."""
        return self._stopped

    @property
    def member_states(self) -> tuple[_ProjectedMemberState, ...]:
        """Return projected state while an acknowledged command settles."""
        if self._projected_states is not None:
            return self._projected_states
        return tuple(
            _ProjectedMemberState(tuple(member.device.color), member.device.power_level)
            for member in self.members
        )

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        """Return the modes available on every member."""
        return set.intersection(*(_color_modes(member) for member in self.members))

    @property
    def effect_list(self) -> list[str]:
        """Return the effects available on every member."""
        return sorted(set.intersection(*(_effects(member) for member in self.members)))

    def supports_effect(self, service: str) -> bool:
        """Return whether the group can safely apply an effect service."""
        return service in self.effect_list or service == SERVICE_PAINT_THEME

    async def async_start(self) -> None:
        """Warm workers before adding the group entities."""
        await self.parallel.async_start()

    async def async_stop(self) -> None:
        """Release all process resources."""
        if self._stopped:
            return
        self._stopped = True
        self._cancel_confirmation()
        self._projected_states = None
        self._pending_acknowledgements.clear()
        await self._async_stop_software_effect()
        await self.parallel.async_stop()
        self.async_update_listeners()

    @callback
    def async_update_listeners(self) -> None:
        """Write virtual state after a member's coordinator changes."""
        for listener in tuple(self._availability_listeners):
            listener()

    @callback
    def async_add_availability_listener(
        self, listener: Callable[[], None]
    ) -> Callable[[], None]:
        """Add a listener that reflects a member-unload worker shutdown."""
        self._availability_listeners.append(listener)

        @callback
        def _remove_listener() -> None:
            self._availability_listeners.remove(listener)

        return _remove_listener

    def _transition_ms(
        self, member: LIFXUpdateCoordinator, power: bool | None, kwargs: dict[str, Any]
    ) -> int:
        if ATTR_TRANSITION in kwargs:
            return round(kwargs[ATTR_TRANSITION] * 1000)
        if power is False:
            duration = self.transition_off_duration or member.transition_off_duration
        else:
            duration = self.transition_on_duration or member.transition_on_duration
        return round(duration * 1000)

    def _begin_projection(
        self,
        states: tuple[_ProjectedMemberState, ...],
        acknowledgements: set[tuple[int, int]],
    ) -> int:
        """Make a command target visible to the virtual light immediately."""
        self._command_generation += 1
        self._cancel_confirmation()
        self._projected_states = states
        self._pending_acknowledgements = acknowledgements
        self.async_update_listeners()
        return self._command_generation

    def _clear_projection(self, generation: int) -> None:
        """Drop a failed command's projection if it is still current."""
        if generation == self._command_generation:
            self._projected_states = None
            self._pending_acknowledgements.clear()
            self.async_update_listeners()

    def _apply_projected_states(
        self,
        generation: int,
        states: tuple[_ProjectedMemberState, ...],
    ) -> None:
        """Apply acknowledged target state to the physical coordinators."""
        if generation != self._command_generation:
            return
        for member, state in zip(self.members, states, strict=True):
            member.device.color = list(state.color)
            member.device.power_level = state.power_level
            member.async_set_updated_data(None)
        self._projected_states = None
        self._pending_acknowledgements.clear()
        self.async_update_listeners()

    @callback
    def _apply_acknowledged_state(
        self,
        generation: int,
        index: int,
        phase: int,
        state: _ProjectedMemberState,
    ) -> None:
        """Reflect one acknowledged command on its physical coordinator."""
        if generation != self._command_generation:
            return
        member = self.members[index]
        member.device.color = list(state.color)
        member.device.power_level = state.power_level
        member.async_set_updated_data(None)
        self._pending_acknowledgements.discard((index, phase))
        if not self._pending_acknowledgements:
            self._projected_states = None
        self.async_update_listeners()

    def _apply_confirmed_states(
        self, generation: int, states: tuple[ParallelLightState, ...]
    ) -> None:
        """Apply direct worker state queries if no newer command superseded them."""
        if generation != self._command_generation:
            return
        for member, state in zip(self.members, states, strict=True):
            member.device.color = list(state.color)
            member.device.power_level = state.power_level
            if state.label:
                member.device.label = state.label
            member.async_set_updated_data(None)
        self._projected_states = None
        self._pending_acknowledgements.clear()
        self.async_update_listeners()

    def _cancel_confirmation(self) -> None:
        """Prevent an older delayed query from overwriting a newer command."""
        if self._confirmation_task is not None:
            if self._confirmation_task is not asyncio.current_task():
                self._confirmation_task.cancel()
            self._confirmation_task = None

    def _schedule_confirmation(
        self, generation: int, delay_ms: int, *, settle: bool = True
    ) -> None:
        """Confirm the current command through the worker-owned UDP sockets."""
        self._cancel_confirmation()
        self._confirmation_task = self.hass.async_create_background_task(
            self._async_confirm_states(generation, delay_ms, settle),
            f"lifx-parallel-confirm-{self.group_id}-{generation}",
        )

    async def _async_confirm_states(
        self, generation: int, delay_ms: int, settle: bool
    ) -> None:
        """Fetch the device state after the command transition settles."""
        try:
            await asyncio.sleep(
                delay_ms / 1000 + (LIFX_STATE_SETTLE_DELAY if settle else 0)
            )
            if generation != self._command_generation or self._stopped:
                return
            states = await self.parallel.async_query_states()
        except asyncio.CancelledError:
            raise
        except HomeAssistantError:
            if generation == self._command_generation and not self._stopped:
                self.hass.async_create_background_task(
                    self.async_stop(), "lifx-parallel-confirmation-failure"
                )
            return
        self._apply_confirmed_states(generation, states)

    async def async_set_state(self, **kwargs: Any) -> None:
        """Stage member-specific packets and release all visible work together."""
        async with self._command_lock:
            await self._async_set_state(**kwargs)

    async def _async_set_state(self, **kwargs: Any) -> None:
        """Dispatch a state change while holding the group command lock."""
        power = kwargs.get("power")
        hsbk = find_hsbk(self.hass, **kwargs)
        staged: dict[int, tuple[Any, ...]] = {}
        commands: list[ParallelCommand] = []
        acknowledged_states: dict[tuple[int, int], _ProjectedMemberState] = {}
        states: list[_ProjectedMemberState] = []
        durations: list[int] = []

        for index, member in enumerate(self.members):
            device = member.device
            color = tuple(merge_hsbk(device.color, hsbk)) if hsbk else None
            duration = self._transition_ms(member, power, kwargs)
            target_color = color or tuple(device.color)
            target_power = (
                65535 if power is True else 0 if power is False else device.power_level
            )
            states.append(_ProjectedMemberState(target_color, target_power))
            durations.append(duration)
            if power is False and color is not None:
                color_command = ParallelCommand("color", (*color, duration))
                power_command = ParallelCommand("power", (False, duration))
                if device.power_level:
                    commands.append(
                        ParallelCommand(
                            color_command.kind,
                            color_command.payload,
                            power_command,
                        )
                    )
                    acknowledged_states[index, 0] = _ProjectedMemberState(
                        target_color, device.power_level
                    )
                    acknowledged_states[index, 1] = states[index]
                elif ATTR_TRANSITION in kwargs or duration:
                    commands.append(
                        ParallelCommand(
                            power_command.kind,
                            power_command.payload,
                            color_command,
                        )
                    )
                    acknowledged_states[index, 0] = _ProjectedMemberState(
                        tuple(device.color), 0
                    )
                    acknowledged_states[index, 1] = states[index]
                else:
                    commands.append(color_command)
                    acknowledged_states[index, 0] = states[index]
            elif power is True and color is not None and device.power_level == 0:
                staged[index] = (*color, 0)
                commands.append(ParallelCommand("power", (True, duration)))
                acknowledged_states[index, 0] = states[index]
            elif color is not None:
                commands.append(ParallelCommand("color", (*color, duration)))
                acknowledged_states[index, 0] = states[index]
            elif power is not None:
                commands.append(ParallelCommand("power", (power, duration)))
                acknowledged_states[index, 0] = states[index]
            else:
                raise ValueError("LIFX group action did not contain a state change")

        await self._async_dispatch_projected_states(
            tuple(commands),
            tuple(states),
            acknowledged_states,
            max(durations),
            staged,
        )

    async def _async_dispatch_projected_states(
        self,
        commands: tuple[ParallelCommand, ...],
        states: tuple[_ProjectedMemberState, ...],
        acknowledged_states: dict[tuple[int, int], _ProjectedMemberState],
        duration_ms: int,
        staged: dict[int, tuple[Any, ...]] | None = None,
    ) -> None:
        """Synchronize a command, then make physical cache updates after ACKs."""
        generation = self._begin_projection(states, set(acknowledged_states))
        try:
            if staged:
                staged_states = {
                    index: _ProjectedMemberState(
                        states[index].color, self.members[index].device.power_level
                    )
                    for index in staged
                }

                def on_stage_ack(index: int) -> None:
                    self.hass.loop.call_soon_threadsafe(
                        self._apply_acknowledged_state,
                        generation,
                        index,
                        -1,
                        staged_states[index],
                    )

                await self.parallel.async_stage_colors(staged, on_stage_ack)

            def on_ack(index: int, phase: int) -> None:
                self.hass.loop.call_soon_threadsafe(
                    self._apply_acknowledged_state,
                    generation,
                    index,
                    phase,
                    acknowledged_states[index, phase],
                )

            await self.parallel.async_dispatch(commands, on_ack)
        except HomeAssistantError:
            self._schedule_confirmation(generation, 0, settle=False)
            raise
        await asyncio.sleep(0)
        self._schedule_confirmation(generation, duration_ms)

    async def async_identify(self) -> None:
        """Start the LIFX identify waveform from every worker gate."""
        async with self._command_lock:
            commands = tuple(
                ParallelCommand(
                    "waveform_optional",
                    (1, 0, 0, 1, 3500, 1000, 3.0, 0, 1, 1, 1, 1, 1),
                )
                for _member in self.members
            )
            await self.parallel.async_dispatch(commands)

    async def async_restart(self) -> None:
        """Reboot every member from the shared dispatch gate."""
        async with self._command_lock:
            await self.parallel.async_dispatch(
                tuple(ParallelCommand("reboot", ()) for _member in self.members)
            )

    async def async_start_effect(self, service: str, **kwargs: Any) -> None:
        """Apply an effect without falling back to member aiolifx connections."""
        async with self._command_lock:
            await self._async_stop_software_effect()
            if service == SERVICE_EFFECT_STOP:
                await self._async_stop_firmware_effects()
                return
            if service == SERVICE_PAINT_THEME:
                await self._async_paint_theme(**kwargs)
                return
            if service == SERVICE_EFFECT_PULSE:
                self._software_effect = self.hass.async_create_background_task(
                    self._async_pulse(**kwargs),
                    f"lifx-parallel-pulse-{self.group_id}",
                )
                return
            if service == SERVICE_EFFECT_COLORLOOP:
                self._software_effect = self.hass.async_create_background_task(
                    self._async_colorloop(**kwargs),
                    f"lifx-parallel-colorloop-{self.group_id}",
                )
                return
            if kwargs.get(ATTR_POWER_ON, True):
                await self._async_set_state(power=True)
            if service == SERVICE_EFFECT_MOVE:
                await self.parallel.async_dispatch(
                    tuple(
                        ParallelCommand(
                            "multizone_effect",
                            (
                                1,
                                round(
                                    kwargs.get(ATTR_SPEED, EFFECT_MOVE_DEFAULT_SPEED)
                                    * 1000
                                ),
                                1
                                if kwargs.get(
                                    ATTR_DIRECTION, EFFECT_MOVE_DEFAULT_DIRECTION
                                )
                                == "right"
                                else 0,
                            ),
                        )
                        for _member in self.members
                    )
                )
                return
            effect, speed, sky_type, saturation_min, saturation_max = {
                SERVICE_EFFECT_FLAME: (3, EFFECT_FLAME_DEFAULT_SPEED, 0, 0, 0),
                SERVICE_EFFECT_MORPH: (2, EFFECT_MORPH_DEFAULT_SPEED, 0, 0, 0),
                SERVICE_EFFECT_SKY: (
                    5,
                    EFFECT_SKY_DEFAULT_SPEED,
                    {"Sunrise": 0, "Sunset": 1, "Clouds": 2}[
                        kwargs.get(ATTR_SKY_TYPE, EFFECT_SKY_DEFAULT_SKY_TYPE)
                    ],
                    kwargs.get(
                        "cloud_saturation_min",
                        EFFECT_SKY_DEFAULT_CLOUD_SATURATION_MIN,
                    ),
                    kwargs.get(
                        "cloud_saturation_max",
                        EFFECT_SKY_DEFAULT_CLOUD_SATURATION_MAX,
                    ),
                ),
            }[service]
            palette = self._theme_colors(**kwargs)
            await self.parallel.async_dispatch(
                tuple(
                    ParallelCommand(
                        "tile_effect",
                        (
                            effect,
                            round(kwargs.get(ATTR_SPEED, speed) * 1000),
                            sky_type,
                            saturation_min,
                            saturation_max,
                            palette,
                        ),
                    )
                    for _member in self.members
                )
            )

    async def _async_stop_software_effect(self) -> None:
        """Cancel the prior software effect before changing group state."""
        if self._software_effect is None:
            return
        self._software_effect.cancel()
        with suppress(asyncio.CancelledError):
            await self._software_effect
        self._software_effect = None

    async def _async_stop_firmware_effects(self) -> None:
        """Stop any firmware effect active from this group."""
        commands = []
        for member in self.members:
            features = lifx_features(member.device)
            if features["matrix"]:
                commands.append(ParallelCommand("tile_effect", (0, 0, 0, 0, 0, ())))
            elif features["multizone"]:
                commands.append(ParallelCommand("multizone_effect", (0, 0, 0)))
            else:
                commands.append(ParallelCommand("color", (*member.device.color, 0)))
        await self.parallel.async_dispatch(tuple(commands))

    def _theme_colors(self, **kwargs: Any) -> tuple[tuple[int, int, int, int], ...]:
        """Resolve a service palette to direct-LAN HSBK values."""
        palette = kwargs.get(ATTR_PALETTE)
        if palette is None:
            theme: Theme = ThemeLibrary().get_theme(kwargs.get(ATTR_THEME, "exciting"))
            palette = theme.colors
        return tuple(_raw_hsbk(color) for color in palette)

    async def _async_paint_theme(self, **kwargs: Any) -> None:
        """Paint one theme color per member at one common deadline."""
        if kwargs.get(ATTR_POWER_ON, True):
            await self._async_set_state(power=True)
        palette = self._theme_colors(**kwargs)
        duration = round(
            kwargs.get(ATTR_TRANSITION, PAINT_THEME_DEFAULT_TRANSITION) * 1000
        )
        states = tuple(
            _ProjectedMemberState(
                palette[index % len(palette)],
                65535 if kwargs.get(ATTR_POWER_ON, True) else member.device.power_level,
            )
            for index, member in enumerate(self.members)
        )
        await self._async_dispatch_projected_states(
            tuple(
                ParallelCommand("color", (*state.color, duration)) for state in states
            ),
            states,
            {(index, 0): state for index, state in enumerate(states)},
            duration,
        )

    async def _async_pulse(self, **kwargs: Any) -> None:
        """Run pulse ticks through the same warmed worker dispatcher."""
        hsbk = find_hsbk(self.hass, **kwargs)
        period = kwargs.get(ATTR_PERIOD, 1.0)
        cycles = kwargs.get(ATTR_CYCLES, 1)
        if kwargs.get(ATTR_POWER_ON, True):
            await self.async_set_state(power=True)
        for _cycle in range(round(cycles)):
            if hsbk is None:
                await self.async_set_state(power=True, brightness=255)
            else:
                await self.async_set_state(power=True, **kwargs)
            await asyncio.sleep(period / 2)
            await self.async_set_state(
                brightness=0,
                transition=period / 2,
            )
            await asyncio.sleep(period / 2)

    async def _async_colorloop(self, **kwargs: Any) -> None:
        """Run colorloop ticks through the same warmed worker dispatcher."""
        if kwargs.get(ATTR_POWER_ON, True):
            await self.async_set_state(power=True)
        period = kwargs.get(ATTR_PERIOD, 60)
        change = kwargs.get(ATTR_CHANGE, 20)
        spread = kwargs.get(ATTR_SPREAD, 30)
        transition = kwargs.get(ATTR_TRANSITION, min(period, 1))
        saturation_min = kwargs.get(ATTR_SATURATION_MIN, 80)
        saturation_max = kwargs.get(ATTR_SATURATION_MAX, 100)
        brightness = kwargs.get("brightness")
        tick = 0
        while True:
            commands = []
            for index, member in enumerate(self.members):
                hue = (
                    member.device.color[0] / 65535 * 360
                    + tick * change
                    + index * spread
                ) % 360
                saturation = saturation_min if tick % 2 else saturation_max
                level = (
                    member.device.color[2] if brightness is None else brightness * 257
                )
                commands.append(
                    ParallelCommand(
                        "color",
                        (
                            round(hue / 360 * 65535),
                            round(saturation / 100 * 65535),
                            level,
                            member.device.color[3],
                            round(transition * 1000),
                        ),
                    )
                )
            async with self._command_lock:
                await self.parallel.async_dispatch(tuple(commands))
            tick += 1
            await asyncio.sleep(period)


class LIFXParallelGroupEntity(Entity):
    """Common virtual device metadata."""

    _attr_has_entity_name = True

    def __init__(self, runtime: LIFXParallelGroupRuntime) -> None:
        """Initialize virtual device metadata."""
        self.runtime = runtime
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"parallel-group-{runtime.group_id}")},
            manufacturer="LIFX",
            name=runtime.entry.title,
            model="Parallel group",
        )

    @property
    @override
    def available(self) -> bool:
        """Return whether the member workers and lights are available."""
        return self.runtime.available

    @override
    async def async_added_to_hass(self) -> None:
        """Keep every virtual group entity in sync with runtime availability."""
        self.async_on_remove(
            self.runtime.async_add_availability_listener(self.async_write_ha_state)
        )
        await super().async_added_to_hass()


class LIFXParallelGroupLight(LIFXParallelGroupEntity, LightEntity):
    """The virtual LIFX light backed by the parallel worker pool."""

    _attr_name = None
    _attr_should_poll = False

    def __init__(self, runtime: LIFXParallelGroupRuntime) -> None:
        """Initialize the virtual light."""
        super().__init__(runtime)
        self._attr_unique_id = f"{runtime.group_id}_light"
        self._attr_supported_color_modes = runtime.supported_color_modes
        self._attr_effect_list = runtime.effect_list
        self._attr_supported_features = LightEntityFeature.TRANSITION
        if self._attr_effect_list:
            self._attr_supported_features |= LightEntityFeature.EFFECT

    @property
    @override
    def is_on(self) -> bool:
        return any(state.power_level for state in self.runtime.member_states)

    @property
    @override
    def brightness(self) -> int | None:
        levels = [
            convert_16_to_8(state.color[2])
            for state in self.runtime.member_states
            if state.power_level
        ]
        return round(fmean(levels)) if levels else None

    @property
    @override
    def color_mode(self) -> ColorMode:
        if ColorMode.HS in self._attr_supported_color_modes and any(
            state.color[1] for state in self.runtime.member_states
        ):
            return ColorMode.HS
        if ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            return ColorMode.COLOR_TEMP
        return ColorMode.BRIGHTNESS

    @property
    @override
    def hs_color(self) -> tuple[float, float] | None:
        if self.color_mode is not ColorMode.HS:
            return None
        hues = [state.color[0] / 65535 * 360 for state in self.runtime.member_states]
        saturations = [
            state.color[1] / 65535 * 100 for state in self.runtime.member_states
        ]
        return (fmean(hues), fmean(saturations))

    @property
    @override
    def color_temp_kelvin(self) -> int | None:
        if self.color_mode is not ColorMode.COLOR_TEMP:
            return None
        return round(fmean(state.color[3] for state in self.runtime.member_states))

    @property
    @override
    def min_color_temp_kelvin(self) -> int | None:
        if ColorMode.COLOR_TEMP not in self._attr_supported_color_modes:
            return None
        return max(
            lifx_features(member.device)["min_kelvin"]
            for member in self.runtime.members
        )

    @property
    @override
    def max_color_temp_kelvin(self) -> int | None:
        if ColorMode.COLOR_TEMP not in self._attr_supported_color_modes:
            return None
        return min(
            lifx_features(member.device)["max_kelvin"]
            for member in self.runtime.members
        )

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        if effect := kwargs.pop(ATTR_EFFECT, None):
            await self.hass.services.async_call(
                DOMAIN, effect, {"entity_id": self.entity_id}, blocking=True
            )
            return
        await self.runtime.async_set_state(power=True, **kwargs)
        self.async_write_ha_state()

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.runtime.async_set_state(power=False, **kwargs)
        self.async_write_ha_state()

    async def set_state(self, **kwargs: Any) -> None:
        """Handle the integration's common set_state entity service."""
        await self.runtime.async_set_state(**kwargs)
        self.async_write_ha_state()

    @override
    async def async_added_to_hass(self) -> None:
        manager = self.hass.data[DATA_LIFX_MANAGER]
        self.async_on_remove(
            manager.async_register_entity(self.entity_id, self.runtime)
        )
        await super().async_added_to_hass()


class LIFXParallelGroupTransitionNumber(LIFXParallelGroupEntity, RestoreNumber):
    """Persistent fade override for a virtual group."""

    _attr_should_poll = False

    def __init__(
        self, runtime: LIFXParallelGroupRuntime, description: NumberEntityDescription
    ) -> None:
        """Initialize a virtual fade override number."""
        super().__init__(runtime)
        self.entity_description = description
        self._attr_unique_id = f"{runtime.group_id}_{description.key}"
        self._attr_native_value = 0.0

    @override
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_data := await self.async_get_last_number_data()) is not None:
            self._attr_native_value = last_data.native_value or 0.0
        self._set_runtime_value(self._attr_native_value)

    @override
    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self._set_runtime_value(value)
        self.async_write_ha_state()

    def _set_runtime_value(self, value: float) -> None:
        if self.entity_description.key == "transition_on_duration":
            self.runtime.transition_on_duration = value
        else:
            self.runtime.transition_off_duration = value


class LIFXParallelGroupButton(LIFXParallelGroupEntity, ButtonEntity):
    """A parallel button action."""

    _attr_should_poll = False

    def __init__(self, runtime: LIFXParallelGroupRuntime, key: str) -> None:
        """Initialize a virtual group action button."""
        super().__init__(runtime)
        self.key = key
        self._attr_unique_id = f"{runtime.group_id}_{key}"
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_device_class = (
            ButtonDeviceClass.IDENTIFY
            if key == "identify"
            else ButtonDeviceClass.RESTART
        )

    @override
    async def async_press(self) -> None:
        if self.key == "identify":
            await self.runtime.async_identify()
        else:
            await self.runtime.async_restart()


async def async_setup_parallel_group_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Set up a virtual group after all of its physical entries are ready."""
    members: list[LIFXUpdateCoordinator] = []
    member_entries: list[ConfigEntry] = []
    for entry_id in entry.data[CONF_MEMBERS]:
        member_entry = hass.config_entries.async_get_entry(entry_id)
        if member_entry is None or not isinstance(
            member_entry.runtime_data, LIFXUpdateCoordinator
        ):
            raise ConfigEntryNotReady("A selected LIFX light is not loaded")
        members.append(member_entry.runtime_data)
        member_entries.append(member_entry)
    runtime = LIFXParallelGroupRuntime(hass, entry, tuple(members))
    await runtime.async_start()
    try:
        for member_entry in member_entries:

            @callback
            def _async_member_unload(
                runtime: LIFXParallelGroupRuntime = runtime,
            ) -> None:
                hass.async_create_background_task(
                    runtime.async_stop(), "lifx-parallel-member-unload"
                )

            @callback
            def _async_member_update(
                runtime: LIFXParallelGroupRuntime = runtime,
            ) -> None:
                runtime.async_update_listeners()
                if not runtime.available and not runtime.stopped:
                    hass.async_create_background_task(
                        runtime.async_stop(), "lifx-parallel-member-unavailable"
                    )

            member_entry.async_on_unload(_async_member_unload)
            entry.async_on_unload(
                member_entry.runtime_data.async_add_listener(_async_member_update)
            )
        entry.runtime_data = runtime
        await hass.config_entries.async_forward_entry_setups(entry, GROUP_PLATFORMS)
    except Exception:
        await runtime.async_stop()
        raise
    return True


async def async_unload_parallel_group_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Unload entities and release the virtual group's worker pool."""
    if unload_ok := await hass.config_entries.async_unload_platforms(
        entry, GROUP_PLATFORMS
    ):
        await entry.runtime_data.async_stop()
    return unload_ok


def async_add_parallel_group_entities(
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    platform: Platform,
) -> None:
    """Add the group entities for one supported platform."""
    runtime = entry.runtime_data
    if platform is Platform.LIGHT:
        async_add_entities([LIFXParallelGroupLight(runtime)])
    elif platform is Platform.BUTTON:
        async_add_entities(
            [
                LIFXParallelGroupButton(runtime, "restart"),
                LIFXParallelGroupButton(runtime, "identify"),
            ]
        )
    else:
        descriptions = (
            NumberEntityDescription(
                key="transition_on_duration",
                translation_key="transition_on_duration",
                entity_category=EntityCategory.CONFIG,
                native_min_value=0,
                native_max_value=300,
                native_step=0.1,
                native_unit_of_measurement="s",
            ),
            NumberEntityDescription(
                key="transition_off_duration",
                translation_key="transition_off_duration",
                entity_category=EntityCategory.CONFIG,
                native_min_value=0,
                native_max_value=300,
                native_step=0.1,
                native_unit_of_measurement="s",
            ),
        )
        async_add_entities(
            LIFXParallelGroupTransitionNumber(runtime, description)
            for description in descriptions
        )

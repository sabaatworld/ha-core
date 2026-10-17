"""Recovery images must survive synchronized device-specific packet planning."""

from lifx.protocol import packets
from lifx.protocol.protocol_types import MultiZoneApplicationRequest

from homeassistant.components.lifx.parallel_commands import (
    align_visible_stages,
    compile_image_stages,
)
from homeassistant.components.lifx.virtual_power import capture_image

from .helpers import (
    create_mock_light,
    create_mock_multizone_light,
    create_reference_ceiling_128_light,
    create_reference_extended_multizone_light,
    create_reference_matrix_light,
)


def test_legacy_recovery_buffers_every_color_before_release() -> None:
    """The apply-only release must not discard a carried last color."""
    state = create_mock_multizone_light(extended=False).state
    state.power = 65535
    stages = compile_image_stages(
        capture_image(state), state, duration_ms=800, power_on=None
    )
    prepared = stages[0].payload[0]
    first = packets.MultiZone.SetColorZones.unpack(prepared[0].payload)
    second = packets.MultiZone.SetColorZones.unpack(prepared[1].payload)
    final = packets.MultiZone.SetColorZones.unpack(stages[-1].payload[0][0].payload)
    assert (first.start_index, first.end_index, first.color.hue) == (0, 0, 54613)
    assert (second.start_index, second.end_index, second.color.hue) == (1, 1, 46421)
    assert first.apply == second.apply == MultiZoneApplicationRequest.NO_APPLY
    assert final.apply == MultiZoneApplicationRequest.APPLY_ONLY
    assert final.duration == 800


def test_extended_recovery_chunks_more_than_82_zones() -> None:
    """Every actual color is buffered; final release only applies that buffer."""
    state = create_reference_extended_multizone_light().state
    state.zones = [*state.zones, *state.zones[:20]]
    state.zone_count = 100
    stages = compile_image_stages(
        capture_image(state), state, duration_ms=900, power_on=None
    )
    raw = stages[0].payload[0]
    first = packets.MultiZone.SetExtendedColorZones.unpack(raw[0].payload)
    second = packets.MultiZone.SetExtendedColorZones.unpack(raw[1].payload)
    assert (first.index, first.colors_count) == (0, 82)
    assert (second.index, second.colors_count) == (82, 18)
    assert second.colors[17] == state.zones[99].to_protocol()
    assert stages[-1].payload[0][0].packet_type == 510


def test_matrix_recovery_addresses_all_tiles() -> None:
    """A solid tile must not be optimized into repainting the entire chain."""
    state = create_reference_matrix_light().state
    stages = compile_image_stages(
        capture_image(state), state, duration_ms=600, power_on=None
    )
    decoded = [packets.Tile.Set64.unpack(raw.payload) for raw in stages[-1].payload[0]]
    assert [packet.tile_index for packet in decoded] == [0, 1, 2, 3, 4]
    assert all(packet.duration == 600 for packet in decoded)


def test_large_ceiling_buffers_complete_pixels_and_uplight() -> None:
    """Both 64-pixel windows, including the uplight, precede visible copy."""
    state = create_reference_ceiling_128_light().state
    stages = compile_image_stages(
        capture_image(state), state, duration_ms=700, power_on=None
    )
    prepared = [packets.Tile.Set64.unpack(raw.payload) for raw in stages[0].payload[0]]
    released = packets.Tile.CopyFrameBuffer.unpack(stages[-1].payload[0][0].payload)
    assert [packet.rect.y for packet in prepared] == [0, 4]
    assert all(packet.rect.fb_index == 1 for packet in prepared)
    assert prepared[-1].colors[-1] == state.uplight_color.to_protocol()
    assert (released.src_fb_index, released.dst_fb_index, released.duration) == (
        1,
        0,
        700,
    )


def test_actual_off_preparation_and_visible_release_align() -> None:
    """Preparation ACKs precede every member's final visible release."""
    state = create_mock_light().state
    image = capture_image(state)
    hidden = compile_image_stages(image, state, duration_ms=100, power_on=True)
    state.power = 65535
    visible = compile_image_stages(image, state, duration_ms=100, power_on=True)
    commands = align_visible_stages((hidden, visible, ()))
    assert commands[0].payload[-1] == 0
    assert commands[0].second.payload == (True, 100)
    assert commands[1].pad_before == 1
    assert commands[1].payload[-1] == 100
    assert commands[2] is None

"""Tests for LIFX Device Group packet dispatch."""

import struct

from homeassistant.components.lifx.parallel import (
    HEADER,
    SET_POWER,
    _set_power,
)


def test_set_power_does_not_request_an_ack() -> None:
    """Device Group packets are fire-and-forget after the shared gate."""
    packet = _set_power(1, 2, bytes(8), (True, 0))
    _size, _frame, _source, _target, _reserved, flags, _sequence, _r2, packet_type, _r3 = (
        HEADER.unpack_from(packet)
    )

    assert packet_type == SET_POWER
    assert flags & 0b10 == 0
    assert len(packet) == HEADER.size + struct.calcsize("<HI")

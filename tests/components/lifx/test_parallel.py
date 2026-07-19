"""Tests for LIFX Device Group packet dispatch."""

import struct
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.components.lifx.parallel import (
    ACKNOWLEDGEMENT,
    HEADER,
    SET_COLOR,
    SET_POWER,
    ParallelCommand,
    _dispatch_prepared,
    _header,
    _set_color,
    _set_power,
    _wait_for_ack,
)


def test_set_power_does_not_request_an_ack() -> None:
    """Final Device Group packets remain fire-and-forget."""
    packet = _set_power(1, 2, bytes(8), (True, 0))
    _size, _frame, _source, _target, _reserved, flags, _sequence, _r2, packet_type, _r3 = (
        HEADER.unpack_from(packet)
    )

    assert packet_type == SET_POWER
    assert flags & 0b10 == 0
    assert len(packet) == HEADER.size + struct.calcsize("<HI")


def test_set_color_can_request_an_ack_for_a_later_stage() -> None:
    """A non-final Device Group command must require a LIFX acknowledgement."""
    packet = _set_color(1, 2, bytes(8), (1, 2, 3, 3500, 0), ack_required=True)
    _size, _frame, _source, _target, _reserved, flags, _sequence, _r2, packet_type, _r3 = (
        HEADER.unpack_from(packet)
    )

    assert packet_type == SET_COLOR
    assert flags & 0b10


def test_command_stages_preserve_member_dependency_order() -> None:
    """A worker must retain the member's ordered dependent commands."""
    command = ParallelCommand(
        "power",
        (True, 0),
        ParallelCommand("color", (1, 2, 3, 3500, 0)),
    )

    assert tuple(stage.kind for stage in command.stages) == ("power", "color")


def test_wait_for_ack_ignores_malformed_response_and_matches_sequence() -> None:
    """Only the matching acknowledgement completes a required stage."""
    target = bytes((1,)) * 8
    udp = MagicMock()
    pipe = MagicMock()
    pipe.poll.return_value = False
    udp.recv.side_effect = (
        b"invalid",
        _header(ACKNOWLEDGEMENT, 1, 2, target, 0),
    )

    assert _wait_for_ack(
        udp,
        pipe,
        1,
        target,
        2,
        3,
        0,
        SimpleNamespace(value=3),
        time.monotonic() + 1,
    )


def test_wait_for_ack_treats_socket_error_as_retryable_stage_failure() -> None:
    """A receive fault must fail only the stage, not the warmed worker."""
    udp = MagicMock()
    pipe = MagicMock()
    pipe.poll.return_value = False
    udp.recv.side_effect = OSError("network changed")

    assert (
        _wait_for_ack(
            udp,
            pipe,
            1,
            bytes(8),
            2,
            3,
            0,
            SimpleNamespace(value=3),
            time.monotonic() + 1,
        )
        is False
    )


def test_worker_keeps_running_after_a_dispatch_socket_error() -> None:
    """A packet send failure must return the worker to its warmed command loop."""
    udp = MagicMock()
    udp.send.return_value = 0
    pipe = MagicMock()
    pipe.poll.side_effect = (False, True, False)
    pipe.recv.return_value = ("DISPATCH", 3, 0, time.monotonic_ns())
    generation = SimpleNamespace(value=3)
    stop_event = MagicMock()
    stop_event.is_set.return_value = False

    assert _dispatch_prepared(
        udp,
        1,
        bytes(8),
        pipe,
        3,
        0,
        ParallelCommand("power", (True, 0)),
        False,
        0,
        generation,
        stop_event,
        lambda: 2,
        False,
    )
    pipe.send.assert_any_call(("ERROR", 3, 0, "short UDP send"))


def test_worker_cancels_an_ack_wait_when_a_newer_generation_arrives() -> None:
    """A superseded ACK wait must not authorize a later stage."""
    udp = MagicMock()
    pipe = MagicMock()

    assert (
        _wait_for_ack(
            udp,
            pipe,
            1,
            bytes(8),
            2,
            3,
            0,
            SimpleNamespace(value=4),
            time.monotonic() + 1,
        )
        is None
    )
    udp.recv.assert_not_called()
